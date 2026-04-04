"""
TC选择模块
按H键选中所有TC
"""
import time
import pydirectinput
from config import TC_SELECT_DELAY

# 关闭安全检查
pydirectinput.FAILSAFE = False

# 设置pydirectinput的内置延迟为0（默认是0.1秒）
pydirectinput.PAUSE = 0.0

DELAY = TC_SELECT_DELAY


class TCSelector(object):
    """按H键选中所有TC"""

    def do(self):
        pydirectinput.press("h")
        time.sleep(DELAY)
