"""
游戏窗口检测模块
通过检测特定像素点颜色判断是否在游戏中
"""
from PIL import ImageGrab
from config import GAME_DETECT_PIXEL, GAME_DETECT_COLOR

PIXEL_X, PIXEL_Y = GAME_DETECT_PIXEL
EXPECTED_COLOR = GAME_DETECT_COLOR


class GameDetector(object):
    """检测当前是否处于游戏画面，通过检测特定像素点颜色判断"""

    def __init__(self):
        self.in_game = False
        self.color = None

    def do(self):
        self.color = self._capture_pixel()
        self.in_game = self._match(self.color)

    def _capture_pixel(self):
        """截取特定像素点的颜色"""
        img = ImageGrab.grab(bbox=(PIXEL_X, PIXEL_Y, PIXEL_X + 1, PIXEL_Y + 1))
        return img.getpixel((0, 0))[:3]

    def _match(self, color):
        """判断颜色是否匹配"""
        return color == EXPECTED_COLOR
