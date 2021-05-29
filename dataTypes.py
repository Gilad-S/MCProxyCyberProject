#######################################
#		Created by Gilad Savoray
#               May 2021
#######################################

import numpy as np
import zlib
import struct
import json


def rshift(val, n): return val >> n if val >= 0 else (val + 0x100000000) >> n


def sign_extend32(binary, lsb=0, msb=32):  # returns binary[lsb:msb]
    new = np.int32(0)
    binary >>= lsb
    mask = 0b1
    for i in range(32):
        new |= mask & binary
        mask *= 2
        if (msb - lsb - 2 < i):
            binary <<= 1
    return np.int32(new)


class VarInt:
    def __init__(self, **kwargs):
        if 'buffer' in kwargs and type(kwargs['buffer']) == Buffer:
            buffer = kwargs['buffer']
            num_read = 0
            result = np.int32(0)
            read = 0b10000000
            while (read & 0b10000000) != 0b0:
                if num_read > 5:
                    raise Exception("VarInt is too big")
                read = buffer.next_byte()
                value = read & 0b01111111
                result |= value << (7 * num_read)
                num_read += 1
            self.value = np.int32(result)

        elif 'value' in kwargs:
            if np.iinfo(np.int32).min <= kwargs['value'] <= np.iinfo(np.int32).max:
                self.value = np.int32(kwargs['value'])
            else:
                raise Exception("Reached max int32")

    def to_bytes(self):
        bytes_ = bytearray()
        value = np.int32(self.value)
        while len(bytes_) == 0 or value != 0:
            tmp = np.byte(value & 0b01111111)
            value = (np.right_shift(value, 7)) & (2 ** (np.iinfo(np.int32).bits - 7) - 1)  # shift LOGICAL right
            if value != 0:
                tmp |= 0b10000000
            bytes_.append(tmp)
        return bytes_

    def to_int32(self):
        return np.int32(self.value)

    def __str__(self):
        return str(self.value)

    def __repr__(self):
        return str(self)


class PositionT:
    # https://wiki.vg/Protocol#Position
    def __init__(self, **kwargs):
        if 'buffer' in kwargs and type(kwargs['buffer']) == Buffer:
            buffer = kwargs['buffer']
            value = int.from_bytes(buffer.next_bytes(8), byteorder='big',
                                   signed=False)  # 64 bit, as unsigned long (int)

            self.y = sign_extend32(value, lsb=0, msb=12)
            self.z = sign_extend32(value, lsb=12, msb=38)
            self.x = sign_extend32(value, lsb=38, msb=64)

        else:
            self.x = 0
            self.y = 0
            self.z = 0
            if 'x' in kwargs and kwargs['x']:
                self.x = sign_extend32(kwargs['x'], msb=26)
            if 'y' in kwargs and kwargs['y']:
                self.y = sign_extend32(kwargs['y'], msb=12)
            if 'z' in kwargs and kwargs['z']:
                self.z = sign_extend32(kwargs['z'], msb=26)

    def __str__(self):
        return "({0}, {1}, {2})".format(self.x, self.y, self.z)

    def pack(self):
        tmp = np.int64(self.x & 0x3FFFFFF) << 38 | (np.int64(self.z & 0x3FFFFFF) << 12) | (self.y & 0xFFF)
        return tmp.byteswap()  # change to big endian

    def __repr__(self):
        return f'(x={self.x}, y={self.y}, z={self.z})'

    def copy(self):
        return PositionT(x=self.x, y=self.y, z=self.z)


class AngleT:
    def __init__(self, **kwargs):
        if 'buffer' in kwargs and type(kwargs['buffer']) == Buffer:
            buffer = kwargs['buffer']
            self.angle = parse_types('byte', buffer)  # unsigned 8 bit
        elif 'degrees' in kwargs:
            degrees = kwargs['degrees']
            self.angle = int((degrees % 360) * (255.0 / 360))  # int from 0 to 255
        elif 'angle' in kwargs:
            angle = kwargs['angle']
            self.angle = int(angle % 255)  # int from 0 to 255
        else:
            self.angle = 0

    def pack(self):
        return serialize_types('byte', self.angle)

    def __str__(self):
        return str(self.angle)

    def __repr__(self):
        return str(self.angle)

class SlotT:
    # https://wiki.vg/Slot_Data
    # Inventory slot:  Boolean, VarInt, Byte, NBT
    def __init__(self, **kwargs):
        self.present, self.item_ID, self.item_count, self.NBT = False, None, None, None
        if 'buffer' in kwargs and type(kwargs['buffer']) == Buffer:
            buffer = kwargs['buffer']

            self.present = parse_types('boolean', buffer) == 0x1
            if self.present:
                self.item_ID, self.item_count = parse_types(['varint', 'byte'], buffer)
                self.NBT = buffer  # temporary, explore NBT

    def __str__(self):
        return "Slot (ID {0}, Count {1}, NBT {2})".format(self.item_ID, self.item_count, self.NBT)

    def to_bytes(self):
        if not self.present:
            return serialize_types('boolean', 0x0)
        else:
            return serialize_types(['boolean', 'varint', 'byte', 'NBT'], (0x1, self.item_ID, self.item_count, self.NBT))

    def __repr__(self):
        return str(self)


class Buffer:
    def __init__(self, _bytes=None):
        self.__bytes = bytearray()
        self.add_bytes(_bytes)

    def next_byte(self):
        return self.next_bytes(1)[0]

    def next_bytes(self, size):
        if self.length() < size:  # if there isn't enough data
            raise Exception("No bytes left in buffer:", self.to_bytes())

        tmp = self.__bytes[:size]
        self.__bytes = self.__bytes[size:]
        return bytes(tmp)

    def add_byte(self, byte):
        tmp = bytearray()
        tmp.append(byte)
        self.add_bytes(tmp)

    def add_bytes(self, byte_arr):
        if byte_arr is not None:
            self.__bytes += byte_arr

    def length(self):
        return len(self.__bytes)

    def empty(self):
        return self.next_bytes(self.length())

    def copy(self):
        return Buffer(self.__bytes)

    def to_bytes(self):
        return self.__bytes

    def var_int_length(self):
        return VarInt(value=self.length())

    def __str__(self):
        return f'Buffer[{self.to_bytes()}]'

    '''
    Uncompress with zlib
    '''

    def uncompress(self):
        tmp = zlib.decompress(self.to_bytes())
        self.__bytes = tmp


TYPES = ['byte', 'varint', 'float', 'string', 'chat', 'opt|chat', 'slot', 'boolean', [3, 'float'], 'position',
         'opt|position', 'varint', 'opt|string', 'opt|varint', None, None, [3, 'varint'], 'opt|varint', 'varint']


def parse_types(types_obj, buff):
    if type(types_obj) == str:
        type_str = types_obj
        if type_str.startswith('opt|'):  # optional type (Boolean + type)
            opt = parse_types('boolean', buff)
            if opt == 0x1:
                return parse_types(type_str[4:], buff)
            else:
                return None


        elif type_str == 'varint':
            return VarInt(buffer=buff)
        elif type_str == 'position':
            return PositionT(buffer=buff)
        elif type_str == 'angle':
            return AngleT(buffer=buff)


        elif type_str == 'boolean':
            tmp = buff.next_bytes(1)
            return np.int8(int.from_bytes(tmp, byteorder='big', signed=False))
        elif type_str == 'byte':
            tmp = buff.next_bytes(1)
            return np.int8(int.from_bytes(tmp, byteorder='big', signed=True))
        elif type_str == 'ubyte':
            tmp = buff.next_bytes(1)
            return np.int8(int.from_bytes(tmp, byteorder='big', signed=False))
        elif type_str == 'short':
            tmp = buff.next_bytes(2)
            return np.short(int.from_bytes(tmp, byteorder='big', signed=True))
        elif type_str == 'ushort':
            tmp = buff.next_bytes(2)
            return np.ushort(int.from_bytes(tmp, byteorder='big', signed=False))
        elif type_str == 'int':
            tmp = buff.next_bytes(4)
            return np.int32(int.from_bytes(tmp, byteorder='big', signed=True))
        elif type_str == 'long':
            tmp = buff.next_bytes(8)
            return np.int64(int.from_bytes(tmp, byteorder='big', signed=True))
        elif type_str == 'double':
            tmp = buff.next_bytes(8)
            return np.float64(struct.unpack('!d', tmp)[0])
        elif type_str == 'float':
            tmp = buff.next_bytes(4)
            return np.float32(struct.unpack('!f', tmp)[0])


        elif type_str == 'string':
            length = VarInt(buffer=buff).value
            return buff.next_bytes(length)
        elif type_str == 'json':
            str_ = parse_types('string', buff)
            return json.loads(str_)
        elif type_str == 'uuid':
            tmp = parse_types(['double', 'double'], buff)
            return tmp
        elif type_str == 'chat':  # basically json
            return parse_types('json', buff)
        elif type_str == 'slot':
            return SlotT(buffer=buff)

        elif type_str == "entity_metadata":
            arr = [None] * 7  # an array of [[data_type_index(varint), value(sometype)] of the 7 first metadata values,
            # otherwise, None
            leftover = None

            index = None
            while index is None or 0 <= index < 7:  # index limit (currently 7 TYPES)
                index = parse_types('ubyte', buff)
                if 0 <= index < 0x7:
                    data_type_index = parse_types('varint', buff)
                    data_type = TYPES[data_type_index.value]
                    arr[index] = [data_type_index, parse_types([data_type], buff)[0]]
                else:
                    leftover = serialize_types('ubyte',
                                               index) + buff.to_bytes()  # leftover, undecoded metadata as bytes
            return [arr, leftover]

        else:
            raise ValueError("Unidentified type to parse")
            return None

    elif type(types_obj) == list and len(types_obj) > 0:
        if type(types_obj[0]) == int or type(types_obj[0]) == np.int16:  # first item is pre-known array length / the length is the first VarInt in buff
            record_count = 0
            if types_obj[0] < 1:  # means that the array's length is not known. decode it from the first VarInt
                record_count = VarInt(buffer=buff).value
            else:
                record_count = types_obj[0]  # get the length from that int (pre-known length)
            types_obj.pop(0)

            result_array = []
            for i in range(record_count):
                result_array += parse_types(types_obj, buff)
            return result_array

        else:  # not an actual array, just a couple of types
            # ['varint', 'varint', 'byte']   =>   [45, 50, 0x5a]
            result_array = []
            for item_type in types_obj:
                result_array.append(parse_types(item_type[:], buff))
            return result_array
    else:
        raise ValueError("Unidentified type to parse")


def serialize_types(types_obj, variables_tup):
    result = b''
    if type(types_obj) == str:
        type_str = types_obj
        single_value = variables_tup

        if type_str.startswith('opt|'):  # optional type (Boolean + type)
            if single_value is None:
                result += serialize_types('boolean', 0x0)  # False
            else:
                result += serialize_types('boolean', 0x1)  # True
                result += serialize_types(type_str[4:], single_value)  # type

        elif type_str == 'varint':
            if type(single_value) == int:
                result += VarInt(value=single_value).to_bytes()
            else:
                result += single_value.to_bytes()
        elif type_str == 'position':
            result += single_value.pack()
        elif type_str == 'angle':
            result += single_value.pack()


        elif type_str == 'boolean':
            result += np.int8(single_value).tobytes()
        elif type_str == 'byte':
            result += np.int8(single_value).tobytes()
        elif type_str == 'ubyte':
            result += np.int8(single_value).tobytes()
        elif type_str == 'short':
            result += np.short(single_value).tobytes()
        elif type_str == 'ushort':
            result += np.ushort(single_value).tobytes()
        elif type_str == 'int':
            result += struct.pack('>i', single_value)
        elif type_str == 'long':
            result += np.int64(single_value).tobytes()
        elif type_str == 'double':
            result += struct.pack('>d', single_value)
        elif type_str == 'float':
            result += struct.pack('>f', single_value)


        elif type_str == 'string':
            if type(single_value) != bytes:
                single_value = single_value.encode()
            length = VarInt(value=len(single_value))
            result += length.to_bytes() + single_value
        elif type_str == 'json':
            result += serialize_types('string', json.dumps(single_value))
        elif type_str == 'uuid':
            result += serialize_types(['double', 'double'], single_value)
        elif type_str == 'chat':
            result += serialize_types('json', single_value)
        elif type_str == 'slot':
            result += single_value.to_bytes()


        elif type_str == "entity_metadata":
            arr_values = single_value[0]
            leftover = single_value[1]

            for index, item in enumerate(arr_values):
                if item is not None:
                    data_type_index = item[0]  # index from VarInt
                    result += serialize_types(['ubyte', 'varint'], (index, data_type_index))
                    item_value = item[1]
                    data_type = TYPES[data_type_index.value]
                    result += serialize_types([data_type], [item_value])
            result += leftover

        else:
            raise ValueError("Unidentified type to parse")

    elif type(types_obj) == list and len(types_obj) > 0:
        if type(types_obj[0]) == int:  # first item is pre-known array length / the length is the first VarInt in buff

            sub_list_len = len(types_obj[1:])  # without the int at the beginning
            separated_items = [variables_tup[x:x + sub_list_len] for x in range(0, len(variables_tup), sub_list_len)]
            record_count = len(separated_items)
            if types_obj[0] < 1:  # means that the array's length is not known. encode it to a VarInt
                result += serialize_types('varint', record_count)  # prefix VarInt length

            types_obj.pop(0)
            for item in separated_items:
                result += serialize_types(types_obj, item)

        else:  # not an actual array, just a couple of types
            # ['varint', 'varint', 'byte']   =>   [45, 50, 0x5a]
            for item_type, item_value in zip(types_obj, variables_tup):
                result += serialize_types(item_type[:], item_value)
    return result
