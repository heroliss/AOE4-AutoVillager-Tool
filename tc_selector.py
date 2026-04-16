"""
TC选择模块
按配置的快捷键选中所有TC
"""
import time
import pydirectinput
import config
from input_config import *  # noqa: F401, F403


class TCSelector(object):
    """按快捷键选中所有TC"""

    def do(self):
        pydirectinput.press(config.TC_SELECT_KEY)
        time.sleep(config.TC_SELECT_DELAY)
