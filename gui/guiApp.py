#######################################
#		Created by Gilad Savoray
#               May 2021
#######################################

from dearpygui.core import *
from dearpygui.core import mvGuiCol_Button, mvGuiCol_ButtonHovered, mvGuiCol_ButtonActive, mvGuiCol_Text
from dearpygui.simple import *
import threading

import mc_proxy as main
from dataTypes import *


class GuiApp:
    names = ['clientIP', 'clientPort', 'serverIP', 'serverPort', 'CustomMOTD', 'CustomHeader', 'EnableFakename',
             'FakenameInput', 'EnableFlying', 'movementSpeed', 'BuildingRadio', 'DropSteering', 'DropEntityMovement',
             'EnableCamera', "clientIP", "clientPort", "serverIP", "serverPort"]

    def __init__(self, fake_game):
        self._local_preferences = {}
        self.proxy_obj = None
        self.run_proxy = False
        self.setup_gui()
        set_global_font_scale(2)
        add_additional_font("gui/segoeui.ttf")
        set_main_window_size(500, 800)
        set_main_window_pos(-493, 165)
        set_main_window_title("Minecraft Proxy Console")
        set_main_window_resizable(False)
        set_primary_window(window="MainConsole", value=True)
        self.change_status_label(-2)  # not running status
        if fake_game is not None:  # the GUI will store preferences in fake_game until there is a connection to the Proxy
            self.game = fake_game
            self.load_preferences_from_file()
        else:
            raise ValueError

    '''
    Move preferences to new_game_obj
    '''

    def change_game_obj(self, new_game_obj):
        for item in self.names:
            old_data = self.game.get_mod(item)
            if old_data is not None:
                new_game_obj.set_mod(item, old_data)
            else:
                new_game_obj.set_mod(item, get_value(item))
        self.game = new_game_obj
        self.game.gui_obj = self

    def run(self):
        start_dearpygui()

    def load_preferences_from_file(self):
        try:
            with open('gui/preferences.gui') as json_file:
                self._local_preferences = json.load(json_file)
        except FileNotFoundError:
            pass
        finally:
            for item_name in self.names:  # in file
                if item_name in self._local_preferences.keys():
                    item_value = self._local_preferences[item_name]
                    set_value(item_name, item_value)
                    self.game.set_mod(item_name, item_value)
                else:  # not in file
                    item_value = get_value(item_name)
                    self.game.set_mod(item_name, item_value)

    def save_preferences_to_file(self):
        with open('gui/preferences.gui', "w+") as json_file:
            json_file.write(json.dumps(self._local_preferences))

    def setup_gui(self):
        with window("MainConsole"):
            add_text("Proxy Connection Info")

            with child("ConnectionInfo", width=450):
                add_text("Client connection")
                add_input_text("clientIP", label="", hint="Client IP", callback=self.update_item)
                add_same_line()
                add_text("Port")
                add_same_line()
                add_input_int("clientPort", label="", callback=self.update_item, min_value=0, max_value=65535, step=0)

                add_text("Server connection")
                add_input_text("serverIP", label="", hint="Server IP", callback=self.update_item)
                add_same_line()
                add_text("Port")
                add_same_line()

                add_input_int("serverPort", label="", callback=self.update_item, min_value=0, max_value=65535, step=0)

            add_spacing(count=10)

            add_indent(name="center_bu", offset=170)
            add_button(name="startProxy", label="Start Proxy", callback=self.start_proxy_bu)

            set_item_color("startProxy", mvGuiCol_Button, color=[17, 130, 11])
            set_item_color("startProxy", mvGuiCol_ButtonActive, color=[17, 130, 11])
            set_item_color("startProxy", mvGuiCol_ButtonHovered, color=[75, 239, 67])

            add_button("stopProxy", label="Stop Proxy", callback=self.stop_proxy_bu)
            set_item_color("stopProxy", mvGuiCol_Button, color=[221, 34, 37])
            set_item_color("stopProxy", mvGuiCol_ButtonActive, color=[221, 34, 37])
            set_item_color("stopProxy", mvGuiCol_ButtonHovered, color=[233, 112, 115])
            hide_item("stopProxy")
            unindent(name="center_bu")
            add_spacing(count=2)
            add_indent(name="center_l", offset=10)
            add_text(name="statusLabel", default_value="Initializing...")
            unindent(name="center_l")

            add_spacing(count=10)
            add_text(name="modLabel", default_value="Packet Modifications:")

            with tab_bar("Modifications"):
                add_indent(offset=10)
                with tab("Status"):
                    add_checkbox(name="CustomMOTD", label="Custom MOTD", callback=self.update_item)
                    add_checkbox(name="CustomHeader", label="Custom tab-list header", callback=self.update_item)

                with tab("Login"):
                    add_text(name="note1", default_value="Note:  Changes in this tab require re-logging!",
                             color=[240, 100, 100])
                    add_checkbox(name="EnableFakename", label="Enable", callback=self.update_item)
                    add_same_line(spacing=50)
                    add_input_text(name="FakenameInput", label="", hint="Fake Username", callback=self.update_item)

                with tab("Position"):
                    add_checkbox(name="EnableFlying", label="Enable flying", callback=self.update_item)
                    add_spacing(count=3)
                    add_text(name='speed1', default_value="Movement Speed")
                    add_slider_float(name="movementSpeed", label="", callback=self.update_item, min_value=0.0,
                                     max_value=10.0, default_value=0.7)

                with tab("Building"):
                    add_radio_button(name="BuildingRadio", items=["Normal", "Two blocks", "3x3"],
                                     callback=self.update_item)

                with tab("Environment"):
                    add_checkbox(name="DropSteering", label="Drop horse steer packets", callback=self.update_item)
                    add_checkbox(name="DropEntityMovement", label="Drop other entity movement",
                                 callback=self.update_item)

    def update_item(self, caller, data_):
        msg = main.PreferenceUpdateMessage(caller)
        self.game.preference_update_queue.append_one(msg)
        item_value = get_value(caller)
        self.game.set_mod(caller, item_value)
        self._local_preferences[caller] = item_value
        self.save_preferences_to_file()

    '''
    Callback of 'start proxy' Button
    '''

    def start_proxy_bu(self, caller, data_):
        hide_item("startProxy")
        show_item("stopProxy")

        [configure_item(x, enabled=False) for x in ["clientIP", "clientPort", "serverIP", "serverPort"]]
        self.run_proxy = True

        proxy_run_thread = threading.Thread(target=main.start_proxy, args=(self,))
        proxy_run_thread.start()

    '''
    Callback of 'stop proxy' Button
    '''

    def stop_proxy_bu(self, caller, data_):
        self.run_proxy = False
        hide_item("stopProxy")
        show_item("startProxy")
        # disable inputs
        [configure_item(x, enabled=True) for x in ["clientIP", "clientPort", "serverIP", "serverPort"]]

        with self.game.game_stop:
            try:
                if self.proxy_obj.c2s_send_queue == None:
                    self.proxy_obj.s.close()
            except AttributeError:
                pass
            self.game.game_stop.notify_all()

    '''
    Updates the status label + color
    gets a status id
    '''

    def change_status_label(self, status):
        color = [255, 255, 255]
        if not self.run_proxy:  # proxy is offline
            set_value(name="statusLabel", value="Not running")
            color = [160, 160, 160]  # gray
        elif status == -1:  # server is offline
            set_value(name="statusLabel", value="Can't connect to server")
            color = [255, 85, 81]  # red
        elif status == 0:  # idle
            set_value(name="statusLabel", value="Waiting for a client connection")
            color = [250, 234, 86]  # yellow
        elif status == 1:  # ping
            set_value(name="statusLabel", value="Sending pong to client")
            color = [99, 234, 237]  # cyan
        elif status == 2:  # login
            set_value(name="statusLabel", value="Logging in...")
            color = [254, 160, 80]  # orange
        elif status == 3:  # play
            set_value(name="statusLabel",
                      value=f"Connected to {self.proxy_obj.server_ip}:{self.proxy_obj.server_port}\nUsername: {self.game.login_username}\nPID: {self.game.pid}")
            color = [81, 251, 119]  # green
        configure_item("statusLabel", color=color)
