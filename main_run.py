#######################################
#		Created by Gilad Savoray
#               May 2021
#######################################

from mc_proxy import Game
from gui import guiApp as ga

proxy_obj = None
gui = None

if __name__ == "__main__":
    gui = ga.GuiApp(Game("fake_game_object"))
    gui.run()
