#######################################
#		Created by Gilad Savoray
#               May 2021
#######################################

# Works for Minecraft Java Edition 1.15.2
# The protocol documentation can be found here: https://wiki.vg/index.php?oldid=15901

import socket
from collections import deque
import select
import threading

from dataTypes import *


def istype(object_, class_):
    return type(object_).__name__.split('.')[-1] in class_.__name__


class Proxy(threading.Thread):
    def __init__(self, gui_obj, proxy_ip, proxy_port, server_ip, server_port=25565):
        super().__init__()
        self.server_ip = server_ip
        self.server_port = server_port

        self.proxy_ip = proxy_ip
        self.proxy_port = proxy_port

        self.client_ip = None
        self.client_port = None

        self.c2s_send_queue = None
        self.s2c_send_queue = None

        self.c2s = None
        self.s2c = None
        self.gui_obj = gui_obj

    def run(self):
        print(self.server_ip, self.server_port)
        try:
            server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server_socket.connect((self.server_ip, self.server_port))
        except (ConnectionRefusedError, TimeoutError) as e:
            print("Error: Can't connect to original server.")
            self.gui_obj.change_status_label(-1)  # server offline
        else:
            self.s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                self.s.bind((self.proxy_ip, self.proxy_port))

                self.s.listen()
                self.gui_obj.change_status_label(0)  # waiting for connection
                client_socket, (self.client_ip, self.client_port) = self.s.accept()
                self.c2s_send_queue = MCPacketQueue()
                self.s2c_send_queue = MCPacketQueue()

                game_obj = Game("Gilad")
                self.gui_obj.change_game_obj(game_obj)
                self.s2c = Forward(server_socket, client_socket, 's2c', self.s2c_send_queue, self.c2s_send_queue, game_obj)
                self.c2s = Forward(client_socket, server_socket, 'c2s', self.c2s_send_queue, self.s2c_send_queue, game_obj)

                self.c2s.start()
                self.s2c.start()

                with game_obj.game_stop:
                    game_obj.game_stop.wait()

                self.broadcast_stop_all()
            except OSError:
                self.gui_obj.change_status_label(0)



    def broadcast_stop_all(self):
        self.c2s.broadcast_stop_all()
        self.s2c.broadcast_stop_all()


#  side == True  =>   s2c;      side == False   =>   c2s
class Forward(threading.Thread):
    def __init__(self, in_socket, out_socket, side, my_send_queue, other_send_queue, game_obj):
        threading.Thread.__init__(self)

        self.in_socket = in_socket
        self.out_socket = out_socket
        self.side = side.lower()
        if self.side not in ['s2c', 'c2s']:
            raise ValueError

        self.in_queue = MCPacketQueue()
        self.out_queue = my_send_queue
        if self.side.startswith('c2s'):  # צד שרירותי. החבילות מתמיינות בהמשך
            game_obj.preference_update_queue = self.in_queue
            print("Open {0} <-> {1} \t\t {3} <-> {2}".format(in_socket.getpeername()[1], in_socket.getsockname()[1],
                                                             out_socket.getpeername()[1], out_socket.getsockname()[1]))
        self.other_out_queue = other_send_queue
        self.__stop = False
        self.game = game_obj

        self.process_thread = Process(self.in_queue, self.out_queue, self.side, self.game)

        self.send_thread = threading.Thread(target=self.send)

    def run(self):
        self.process_thread.start()
        self.send_thread.start()
        self.receive()

    def receive(self):  # Receive to in_buffer
        self.in_socket.setblocking(True)

        while True:
            try:
                ready_to_read, ready_to_write, in_error = select.select([self.in_socket, ], [], [])
            except (select.error, ValueError) as e:
                self.in_socket.close()
                with self.game.game_stop:
                    self.game.game_stop.notify_all()
                break
            assert len(ready_to_read) > 0

            # Get next packet, get its length [VarInt]
            next_packet_length_buff = Buffer()
            last_byte = [0b10000000]
            try:
                while (last_byte[0] & 0b10000000) != 0b0:
                    last_byte = self.in_socket.recv(1)
                    if len(last_byte) == 0:
                        pass  # no bytes were received
                    else:
                        next_packet_length_buff.add_byte(last_byte[0])
                next_packet_length = VarInt(buffer=next_packet_length_buff)

                if self.side == "c2s" and self.game.state == 0 and next_packet_length.value == 254:
                    # Legacy PING
                    lp_data_start_buff = Buffer(
                        self.in_socket.recv(3))  # Get '0xFA' and length [short]
                    lp_id, lp_str_len = parse_types(['byte', 'short'], lp_data_start_buff)
                    lp_data_str_buff = Buffer(
                        self.in_socket.recv(lp_str_len*2 + 2))  # string and short, should be 'MC|PingHost'
                                                                    # and the rest of the length
                    lp_str, lp_rest_len = parse_types([[lp_str_len, 'short'], 'short'], lp_data_str_buff)
                    lp_data_end_buff = Buffer(self.in_socket.recv(lp_rest_len))
                    print(f"IGNORED LEGACY PING")

                else:
                    next_packet_data_buff = Buffer()
                    recv_left_len = next_packet_length.value
                    while recv_left_len > 0:  # Get data from next packet
                        tmp = self.in_socket.recv(recv_left_len)
                        recv_left_len -= len(tmp)
                        next_packet_data_buff.add_bytes(tmp)
                    next_packet = MCPacket(game=self.game, length=next_packet_length, data=next_packet_data_buff,
                                           side=self.side)
                    self.in_queue.append_one(next_packet)

            except (OSError, IndexError) as e:
                with self.game.game_stop:
                    self.game.game_stop.notify_all()
                break

    def send(self):
        while not self.__stop:
            with self.out_queue.new_packet:
                self.out_queue.new_packet.wait()

            while not self.out_queue.empty():
                send_data, other_side_packets, stop_flag = self.out_queue.pack_all(self.side)

                try:
                    ready_to_read, ready_to_write, in_error = select.select([], [self.out_socket], [])
                    self.out_socket.send(send_data)
                    self.other_out_queue.append_all(other_side_packets)
                except (select.error, ValueError, OSError) as e:
                    self.__stop = True
                    with self.game.game_stop:
                        self.game.game_stop.notify_all()
                    break
                assert len(ready_to_write) > 0

                if stop_flag:
                    self.__stop = True

        self.out_socket.close()

    def broadcast_stop_all(self):
        try:
            self.in_socket.close()
        finally:
            self.in_queue.send_stop_signal()
            with self.out_queue.new_packet:
                self.out_queue.new_packet.notify_all()


class MCPacket:
    # [length] : VarInt; [data] : Buffer; [side] : c2s/s2c
    # [raw_data] : Buffer; [pID] : VarInt; [side] : c2s/s2c
    def __init__(self, side, game, length=None, data=None, p_ID=None, raw_data=None):
        if length is not None and data is not None:
            self.p_length = length
            self.p_data = data
            assert self.p_data.length() == length.value, "MCPacket length doesn't match given length!" + str(
                self.p_data.to_bytes())
        elif p_ID is not None and raw_data is not None:
            self.p_ID = p_ID
            self.raw_data = raw_data
        if not side.lower() in ['s2c', 'c2s']:
            raise ValueError
        self.side = side

        self.with_compression = None
        self.uncompressed_load_length = None
        self.is_compressed = None
        self.game = game

        self._children = []  # can be changed with self.add_child_packet(...)
        self._send_self = True  # can be changed with self.drop_packet() & self.pickup_packet()

    '''
    [with_compression] : boolean;
    Creates p_code, raw_data, p_data_length
    '''

    def unpack(self, with_compression):
        self.with_compression = with_compression

        self.raw_data = self.p_data.copy()
        if self.with_compression:  # p_length = len( [Uncompressed Data Length] [Compressed data] )
            # when Compressed data is (Packet ID + Data)
            self.uncompressed_load_length = VarInt(
                buffer=self.raw_data)  # Length of uncompressed (Packet ID + Data) or 0
            self.is_compressed = self.uncompressed_load_length.value != 0  # If uncompressed_load_length is set to zero, then the packet is uncompressed;
            # otherwise it is the size of the uncompressed packet.

            if self.is_compressed:  # uncompress p_data to raw_data
                try:
                    self.raw_data.uncompress()
                except zlib.error:
                    self.is_compressed = False
                    print('Decompression error!')

        self.p_ID = VarInt(buffer=self.raw_data)

    def __str__(self):
        return (self.side[0].upper()) + ' ' + hex(self.p_ID.value) + ' ' + str(self.raw_data.to_bytes())

    def matches(self, side, packet_id):  # returns true if self matched these specifications
        return self.side[0].lower() == side[0].lower() and self.p_ID.value == packet_id

    def handle(self):

        #       --- IDLE STATE ---
        if self.game.state == 0:
            # server list ping req, Handshake
            # switch to STATUS/LOGIN state
            if self.matches('c2s', 0x0) and self.raw_data.length() > 0:
                self.game.gui_obj.change_status_label(1)  # ping
                protocol_number, ip, port, next_state = parse_types(['varint', 'string', 'ushort', 'varint'],
                                                                    self.raw_data)
                self.game.state = next_state.value
                self.raw_data = Buffer(
                    serialize_types(['varint', 'string', 'ushort', 'varint'], (protocol_number, ip, port, next_state)))


        #       --- STATUS STATE ---
        elif self.game.state == 1:
            # server list ping req
            # switch to STATUS state
            if self.game.get_mod('CustomMOTD') and self.matches('s2c', 0x0) and self.raw_data.length() > 0:
                json_ = parse_types('json', self.raw_data)
                self.game.state = 0

                from datetime import datetime
                now = datetime.now()
                current_time = now.strftime("%H:%M:%S")
                json_['description'] = {'text': '§2§l§n' + current_time + '§r'}
                self.raw_data = Buffer(serialize_types('json', json_))



        #       --- LOGIN STATE ---
        elif self.game.state == 2:
            # set compression
            if self.matches('s2c', 0x3):
                compression_set = parse_types('varint', self.raw_data)
                self.game.compression_size = compression_set.value
                self.raw_data = Buffer(serialize_types('varint', compression_set))

            # login start
            elif self.matches('c2s', 0x0):
                username = parse_types('string', self.raw_data)

                if self.game.get_mod('EnableFakename'):
                    username = self.game.get_mod('FakenameInput')

                self.game.gui_obj.change_status_label(2)  # login
                self.game.login_username = username
                self.raw_data = Buffer(serialize_types('string', username))

            # login success
            # switch to PLAY state
            elif self.matches('s2c', 0x02):
                self.game.state = 3
                self.game.gui_obj.change_status_label(3)  # play
                self.game.set_mod('Camera', {})


        #       --- PLAY STATE ---
        elif self.game.state == 3:
            # Chat Message
            if self.matches('c2s', 0x03):
                msg = parse_types('string', self.raw_data.copy())

                if msg.startswith(b'/camera'):
                    if 'ID' in self.game.target.keys():  # already selected an entity
                        entity_id = self.game.target['ID']  # int
                        if 'EntityID' in self.game.get_mod('Camera').keys() and self.game.get_mod('Camera')[
                            'EntityID'] != self.game.pid:
                            entity_id = int(self.game.pid)

                        self.game.get_mod('Camera')['EntityID'] = entity_id
                        camera_bytes = Buffer(serialize_types('varint', entity_id))
                        camera_packet = MCPacket(game=self.game, p_ID=VarInt(value=0x3F), raw_data=camera_bytes,
                                                 side='s2c')
                        camera_packet.with_compression = self.game.with_compression
                        self.add_child_packet(camera_packet)

                    else:
                        error_msg_bytes = Buffer(
                            b'\x02X{"italic":true,"color":"red","text":"Unable to switch camera. First, select an entity."}')
                        error_msg_packet = MCPacket(game=self.game, p_ID=VarInt(value=0x50), raw_data=error_msg_bytes,
                                                    side='s2c')
                        error_msg_packet.with_compression = self.game.with_compression
                        self.add_child_packet(error_msg_packet)
                    self.drop_packet()  # don't send /camera to the server

                elif msg.startswith(b'/state'):
                    tmp = msg.split(b' ')
                    if len(tmp) >= 3:
                        state_bytes = Buffer(serialize_types(['ubyte', 'float'], (int(tmp[1]), float(tmp[2]))))
                        state_packet = MCPacket(game=self.game, p_ID=VarInt(value=0x1F), raw_data=state_bytes,
                                                side='s2c')
                        state_packet.with_compression = self.game.with_compression
                        self.add_child_packet(state_packet)
                        self.drop_packet()  # don't send /state to the server

                elif msg.startswith(b'/giants'):  # create giants as entities
                    current = False
                    try:
                        current = self.game.get_mod("giants")
                    except:
                        pass
                    self.game.set_mod("giants", not current)
                    self.drop_packet()

                self.raw_data = Buffer(serialize_types('string', msg))


            # Join Game
            elif self.matches('s2c', 0x26):
                eid, gm, dim, seed, max_players, level, view, debug_info, respawn_screen = parse_types(
                    ['int', 'ubyte', 'int', 'long', 'ubyte', 'string', 'varint', 'boolean', 'boolean'], self.raw_data)
                self.game.pid = eid
                self.game.gui_obj.change_status_label(3)  # play
                self.raw_data = Buffer(serialize_types(['int', 'ubyte', 'int', 'long', 'ubyte', 'string', 'varint',
                                                        'boolean', 'boolean'], (eid, gm, dim, seed, max_players, level,
                                                                                view, debug_info, respawn_screen)))
                self.add_child_packet(get_tab_header_packet(self.game))

            # Rightclick detection
            elif self.matches('c2s', 0x2d):
                msg = "I right clicked!"
                msg_bytes = Buffer(serialize_types('string', msg))
                chat_packet = MCPacket(game=self.game, p_ID=VarInt(value=0x03), raw_data=msg_bytes, side='c2s')
                chat_packet.with_compression = True
                self.add_child_packet(chat_packet)

            # Client Block placement
            # face enum: {down_face, up_face, north_face, south_face, west_face, east_face}
            elif self.matches('c2s', 0x2c):
                hand, location, face, cursor, inside_block = parse_types(
                    ['varint', 'position', 'varint', [3, 'float'], 'boolean'], self.raw_data)
                if self.game.get_mod('BuildingRadio') == 2:
                    for y in [0, 1, 2]:
                        for x in [-1, 0, 1]:
                            tmp_location = location.copy()
                            tmp_location.x += x
                            tmp_location.y += y
                            msg_bytes = Buffer(
                                serialize_types(['varint', 'position', 'varint', [3, 'float'], 'boolean'],
                                                (hand, tmp_location, 1, cursor, inside_block)))
                            chat_packet = MCPacket(game=self.game, p_ID=VarInt(value=0x2c), raw_data=msg_bytes,
                                                   side='c2s')
                            chat_packet.with_compression = True
                            self.add_child_packet(chat_packet)
                elif self.game.get_mod('BuildingRadio') == 1:
                    tmp_location = location.copy()
                    tmp_location.y += 5
                    msg_bytes = Buffer(serialize_types(['varint', 'position', 'varint', [3, 'float'], 'boolean'],
                                                       (hand, tmp_location, face, cursor, inside_block)))
                    chat_packet = MCPacket(game=self.game, p_ID=VarInt(value=0x2c), raw_data=msg_bytes, side='c2s')
                    chat_packet.with_compression = True
                    self.add_child_packet(chat_packet)

                # location.y += 0
                self.raw_data = Buffer(serialize_types(['varint', 'position', 'varint', [3, 'float'], 'boolean'],
                                                       (hand, location, face, cursor, inside_block)))

            # Enable Flying
            elif self.matches('s2c', 0x32):
                flags, flying_speed, fov = parse_types(['byte', 'float', 'float'], self.raw_data)
                self.game.set_mod("_Abilities", (flags, flying_speed, fov))
                if self.game.get_mod("EnableFlying"):
                    flags = flags | 6
                self.raw_data = Buffer(serialize_types(['byte', 'float', 'float'], (flags, flying_speed, fov)))

            # client movement speed
            elif self.matches('s2c', 0x59):
                eid, length = parse_types(['varint', 'int'], self.raw_data)
                if eid.value == int(self.game.pid):
                    properties = parse_types([int(length), ['string', 'double', [-1, 'uuid', 'double', 'byte']]],
                                             self.raw_data)
                    for p in properties:
                        if p[0] == b'generic.movementSpeed':
                            p[1] = self.game.get_mod("movementSpeed")

                    self.raw_data = Buffer(serialize_types(
                        ['varint', 'int', [int(length), ['string', 'double', [-1, 'uuid', 'double', 'byte']]]],
                        (eid, length, properties)))
                else:
                    tmp = self.raw_data.to_bytes()
                    self.raw_data = Buffer(serialize_types(['varint', 'int'], (eid, length)))
                    self.raw_data.add_bytes(tmp)

            # Interact Entity
            # type_enum: {interact, attack, interact_at}
            # hand_enum: {main_hand, off_hand}
            elif self.matches('c2s', 0x0E):
                entity_id, interaction_type = parse_types(['varint', 'varint'], self.raw_data)
                target = None
                hand = None
                if interaction_type.value == 2:
                    target = parse_types([3, 'float'], self.raw_data)
                if interaction_type.value != 1:
                    hand = parse_types('varint', self.raw_data)

                metadata_array = [None] * 7
                last_effect_metadata = self.game.last_effect_metadata[entity_id.value]
                last_effect_metadata[1] |= 0x40  # glowing flag is on
                metadata_array[0] = last_effect_metadata  # effect index in metadata
                glow_data = serialize_types(['varint', 'entity_metadata'], (entity_id, [metadata_array, b'\xff']))
                glow_bytes = Buffer(glow_data)
                glow_packet = MCPacket(game=self.game, p_ID=VarInt(value=0x44), raw_data=glow_bytes, side='s2c')
                glow_packet.with_compression = True
                self.add_child_packet(glow_packet)
                self.game.target['ID'] = entity_id

                self.raw_data = Buffer(serialize_types(['varint', 'varint'], (entity_id, interaction_type)))
                if interaction_type.value == 2:
                    self.raw_data.add_bytes(serialize_types([3, 'float'], target))
                if interaction_type.value != 1:
                    self.raw_data.add_bytes(serialize_types('varint', hand))

            # Entity Metadata
            elif self.matches('s2c', 0x44):
                entity_id, metadata = parse_types(['varint', 'entity_metadata'], self.raw_data)

                if metadata[0][0] is not None:
                    self.game.last_effect_metadata[entity_id.value] = metadata[0][0]
                if entity_id.value in self.game.last_effect_metadata.keys() and 'ID' in self.game.target.keys() \
                        and self.game.target['ID'].value == entity_id.value:
                    # metadata[0][0][1] |= 0x40
                    last_effect_metadata = self.game.last_effect_metadata[entity_id.value]
                    last_effect_metadata[1] |= 0x40  # glowing flag is on
                    metadata[0][0] = last_effect_metadata
                self.raw_data = Buffer(
                    serialize_types(['varint', 'entity_metadata'],
                                    (entity_id, metadata)))

            # Vehicle Move
            elif self.matches('c2s', 0x15):
                pos, ang = parse_types([[3, 'double'], [2, 'float']], self.raw_data)
                self.raw_data = Buffer(serialize_types([[3, 'double'], [2, 'float']], (pos, ang)))
                if self.game.get_mod('DropSteering'):
                    self.drop_packet()

            # Spawn Entity
            elif self.matches('s2c', 0x03):
                entity_id, obj_uuid, type_, position, ang, velocity = parse_types(
                    ['varint', 'uuid', 'varint', [3, 'double'], [3, 'angle'], [3, 'short']],
                    self.raw_data)
                try:
                    if self.game.get_mod("giants"):
                        type_ = 30  # giant
                except:
                    pass

                self.raw_data = Buffer(
                    serialize_types(['varint', 'uuid', 'varint', [3, 'double'], [3, 'angle'], [3, 'short']],
                                    (entity_id, obj_uuid, type_, position, ang, velocity)))

            # Entity Position
            elif self.matches('s2c', 0x29):
                entity_id, delta, on_ground = parse_types(['varint', [3, 'short'], 'boolean'], self.raw_data.copy())
                if self.game.get_mod('DropEntityMovement'):
                    self.drop_packet()

            # Entity Position and Rotation
            elif self.matches('s2c', 0x2A):
                entity_id, delta, yaw, pitch, on_ground = parse_types(
                    ['varint', [3, 'short'], 'angle', 'angle', 'boolean'], self.raw_data.copy())

                if self.game.get_mod('DropEntityMovement'):
                    self.drop_packet()

            # Entity Position and Rotation
            elif self.matches('s2c', 0x57) and False:
                entity_id, pos, yaw, pitch, on_ground = parse_types(
                    ['varint', [3, 'double'], 'angle', 'angle', 'boolean'], self.raw_data.copy())
                if self.game.get_mod('DropEntityMovement'):
                    self.drop_packet()

    '''
    Packs self to bytes
    '''

    def pack(self):
        self_data = b''
        if self._send_self:
            load_data = self.p_ID.to_bytes() + self.raw_data.to_bytes()  # ID & raw_data
            if self.with_compression:
                uncompressed_load_length = len(load_data)
                if uncompressed_load_length >= self.game.compression_size:  # if need compression (bigger than threshold)
                    compressed_data = zlib.compress(load_data)
                    load_data = VarInt(value=uncompressed_load_length).to_bytes() + compressed_data
                else:  # no compression is needed (smaller than threshold)
                    load_data = VarInt(value=0).to_bytes() + load_data

            self_data = VarInt(value=len(load_data)).to_bytes() + load_data

            # down also returns a tuple!
        other_side_children = []
        my_children_data = b''
        for child in self._children:
            if child.side == self.side:  # good side, pack him/her
                child_data, other_child_child = child.pack()
                my_children_data += child_data
                other_side_children += other_child_child
            else:
                other_side_children.append(child)

        return self_data + my_children_data, other_side_children

    '''
    Appends a 'child' packet to the current packet, that will be sent as well.
    '''

    def add_child_packet(self, child_packet):
        if type(child_packet) == MCPacket:
            self._children.append(child_packet)
        else:
            raise ValueError

    '''
    Don't send self, but do send my children (if there are any)
    '''

    def drop_packet(self):
        self._send_self = False

    '''
    SEND self (undo self.drop_packet())
    '''

    def pickup_packet(self):
        self._send_self = True


class StopMessage:
    pass


class PreferenceUpdateMessage:
    def __init__(self, mod_name):
        self.mod_name = mod_name  # changed property
        self.payload = None  # array of MCPackets

    def handle(self, game):
        self.payload = []
        if self.mod_name == 'CustomHeader':
            self.payload.append(get_tab_header_packet(game))

        elif self.mod_name == 'EnableFlying':
            tmp = list(game.get_mod("_Abilities"))
            if game.get_mod("EnableFlying"):
                tmp[0] = tmp[0] | 6
                tmp[1] = 1
            abilities_bytes = Buffer(serialize_types(['byte', 'float', 'float'], tmp))
            abilities_packet = MCPacket(game=game, p_ID=VarInt(value=0x32), raw_data=abilities_bytes, side='s2c')
            abilities_packet.with_compression = game.with_compression
            self.payload.append(abilities_packet)

        elif self.mod_name == 'movementSpeed':
            tmp = [[b'generic.movementSpeed', game.get_mod("movementSpeed"), []]]
            speed_bytes = Buffer(
                serialize_types(['varint', 'int', [1, ['string', 'double', [-1, 'uuid', 'double', 'byte']]]],
                                (int(game.pid), 1, tmp)))
            speed_packet = MCPacket(game=game, p_ID=VarInt(value=0x59), raw_data=speed_bytes, side='s2c')
            speed_packet.with_compression = game.with_compression
            self.payload.append(speed_packet)


class MCPacketQueue:
    def __init__(self):
        self._q = deque()
        self.lock = threading.Lock()
        self.new_packet = threading.Condition()

    def pop_one(self):
        with self.lock:
            return self._q.popleft()

    def pop_all(self):
        with self.lock:
            items = []
            while bool(self._q):
                items.append(self._q.popleft())
            return items

    def append_one(self, obj):
        with self.lock:
            if type(obj) in [MCPacket, StopMessage]:
                self._q.append(obj)
                with self.new_packet:
                    self.new_packet.notify_all()
            elif istype(obj, PreferenceUpdateMessage):  # add payload to queue, remove the shell (PrefUpdatePacket)

                if obj.payload is not None:
                    for child in obj.payload:
                        if child is not None and istype(child, MCPacket):
                            self._q.append(child)
                else:
                    self._q.append(obj)
            else:
                raise ValueError

    def append_all(self, obj_list):
        with self.lock:
            for obj in obj_list:
                if istype(obj, MCPacket) or istype(obj, StopMessage):
                    self._q.append(obj)
                elif istype(obj, PreferenceUpdateMessage):  # add payload to queue, remove the shell (PrefUpdatePacket)
                    if obj.payload is not None:
                        for child in obj.payload:
                            if child is not None and istype(child, MCPacket):
                                self._q.append(child)
                    else:
                        self._q.append(obj)
                else:
                    raise Exception("UNKNOWN TYPE " + type(obj))
            with self.new_packet:
                self.new_packet.notify_all()

    def empty(self):
        with self.lock:
            return not bool(self._q)

    '''
    Pops out all MCPackets and packs into bytes
    Return a tuple:  ([bytes] send_to_client_queue,  [bytes] send_to_server_queue)
                    Or in reverse order, depending on priority_side
    '''

    def pack_all(self, priority_side='c2s'):
        all_packets = self.pop_all()
        send_data = b''
        other_packets = []
        stop_flag = False
        for packet in all_packets:
            if istype(packet, MCPacket):
                if packet.side.startswith(priority_side):
                    packet_data, packet_other_packets = packet.pack()
                    send_data += packet_data
                    other_packets += packet_other_packets
                else:
                    other_packets.append(packet)
            elif type(packet) == StopMessage:
                stop_flag = True
            else:
                raise Exception("UNKNOWN PACKET TYPE")
        return send_data, other_packets, stop_flag

    def send_stop_signal(self):
        self.append_one(StopMessage())


class Process(threading.Thread):
    def __init__(self, in_queue, out_queue, side, game):
        threading.Thread.__init__(self)
        self.in_queue = in_queue
        self.out_queue = out_queue
        self.side = side
        self.__stop = False
        self.game = game

    def run(self):
        while not self.__stop:
            with self.in_queue.new_packet:  # Wait for new packets
                self.in_queue.new_packet.wait()
            packets = self.in_queue.pop_all()

            for p in packets:
                if istype(p, MCPacket):
                    p.unpack(self.game.with_compression)
                    p.handle()
                elif type(p) == StopMessage:
                    self.__stop = True
                elif istype(p, PreferenceUpdateMessage):
                    p.handle(self.game)
                else:
                    raise Exception(f"UNKNOWN TYPE {type(p)} IN QUEUE")

            self.out_queue.append_all(packets)


class Game:
    # states: 0 = idle? ; 1 = status ; 2 = login ; 3 = play
    def __init__(self, fake_username=None):
        self.__lock = threading.Lock()
        self._game_stop = threading.Condition()

        self._gui_obj = None

        self._mods = {}
        self._state = 0
        self._player_id = 0
        self.set_mod('EnableFakename', False)  # is enabled?
        self.set_mod('FakenameInput', 'Pr0xyUs3r')  # fake name
        self._compression = [False, 0]  # is enabled?   compression size

        self._last_effect_metadata = {}  # for glowing effect after an interaction
        self._target = {}

        self.preference_update_queue = MCPacketQueue()  # tmp one
        self.set_mod('EnableFakename', fake_username is not None)
        if self.get_mod('EnableFakename'):
            self.set_mod('FakenameInput', fake_username)
        self._login_username = None

    # CONNECTION-GAME STATE PROPERTY
    @property
    def state(self):
        with self.__lock:
            return self._state

    @state.setter
    def state(self, new_state):
        with self.__lock:
            self._state = new_state

    # GUI OBJECT PROPERTY
    @property
    def gui_obj(self):
        with self.__lock:
            return self._gui_obj

    @gui_obj.setter
    def gui_obj(self, gui_obj):
        with self.__lock:
            self._gui_obj = gui_obj

    # ACTUAL LOGIN USERNAME PROPERTY
    @property
    def login_username(self):
        with self.__lock:
            return self._login_username

    @login_username.setter
    def login_username(self, login_username):
        with self.__lock:
            if type(login_username) == str:
                self._login_username = login_username
            else:
                self._login_username = login_username.decode()

    # COMPRESSION PROPERTY
    @property
    def with_compression(self):
        with self.__lock:
            return self._compression[0]

    @with_compression.setter
    def with_compression(self, with_compression):
        with self.__lock:
            if type(with_compression) == bool:
                self._compression[0] = with_compression
            else:
                raise ValueError

    @property
    def compression_size(self):
        with self.__lock:
            return self._compression[1]

    @compression_size.setter
    def compression_size(self, compression_size):
        with self.__lock:
            if type(compression_size) == np.int32 and compression_size >= 0:
                self._compression[1] = compression_size
                self._compression[0] = compression_size > 0
            else:
                raise ValueError

    # GAME STOP PROPERTY
    @property
    def game_stop(self):
        with self.__lock:
            return self._game_stop

    # PLAYER_ID PROPERTY
    @property
    def pid(self):
        with self.__lock:
            return self._player_id

    @pid.setter
    def pid(self, pid):
        with self.__lock:
            if type(pid) == np.int32 and pid >= 0:
                self._player_id = pid
            else:
                raise ValueError

    # LAST EFFECT METADATA PROPERTY
    @property
    def last_effect_metadata(self):
        with self.__lock:
            return self._last_effect_metadata

    # TARGET PROPERTY
    @property
    def target(self):
        with self.__lock:
            return self._target

    def set_mod(self, mod_name, value):
        with self.__lock:
            if type(mod_name) == str:
                if mod_name == 'FakenameInput':
                    if type(value) == str and len(value) > 1:
                        self._mods[mod_name] = value
                else:
                    self._mods[mod_name] = value
            else:
                raise ValueError

    def get_mod(self, mod_name):
        with self.__lock:
            if type(mod_name) == str and mod_name in self._mods.keys():
                return self._mods[mod_name]
            raise ValueError

    # GET IP & PORTS OF SERVER & CLIENT
    def sockets_info(self):
        return [self.get_mod(x) for x in ['clientIP', 'clientPort', 'serverIP', 'serverPort']]


'''
    Returns the tab header packet
'''


def get_tab_header_packet(game):
    header = {"translate": ""}
    if game.get_mod('CustomHeader'):
        header = {'extra': [{'bold': True, 'obfuscated': True, 'color': 'gold', 'text': 'p '},
                            {'bold': True, 'italic': True, 'color': 'dark_green', 'text': 'Python '},
                            {'bold': True, 'italic': True, 'color': 'red', 'text': 'MC'},
                            {'bold': True, 'italic': True, 'color': 'dark_red', 'text': 'Proxy'},
                            {'bold': True, 'obfuscated': True, 'color': 'gold', 'text': ' p\n'}], 'text': ''}
    footer = {"translate": ""}
    tab_list_info_bytes = Buffer(serialize_types(['chat', 'chat'], (header, footer)))
    tab_list_packet = MCPacket(game=game, p_ID=VarInt(value=0x54), raw_data=tab_list_info_bytes, side='s2c')
    tab_list_packet.with_compression = game.with_compression
    return tab_list_packet


def start_proxy(gui_obj):
    gui_obj.change_status_label(-2)

    while gui_obj.run_proxy:
        proxy_obj = Proxy(gui_obj, *gui_obj.game.sockets_info())
        gui_obj.proxy_obj = proxy_obj
        print(f"\n<== Starting PROXY ==>")
        proxy_obj.run()
        print("~=~ Stopped PROXY ~=~")
        gui_obj.change_status_label(-2)  # proxy offline
        gui_obj.proxy_obj = None
    gui_obj.change_status_label(-2)  # proxy offline
