"""
TC选择模块
按H键选中所有TC
"""
import time
import pydirectinput
from config import TC_SELECT_DELAY
from input_config import *  # noqa: F401, F403

DELAY = TC_SELECT_DELAY


class TCSelector(object):
    """按H键选中所有TC"""

    def do(self):
        pydirectinput.press("h")
        time.sleep(DELAY)
