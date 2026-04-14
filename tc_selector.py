"""
TC选择模块
按H键选中所有TC
"""
import time
import pydirectinput
import config
from input_config import *  # noqa: F401, F403


class TCSelector(object):
    """按H键选中所有TC"""

    def do(self):
        pydirectinput.press("h")
        time.sleep(config.TC_SELECT_DELAY)
