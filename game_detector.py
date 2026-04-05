"""
游戏窗口检测模块
结合窗口标题和像素点颜色双重检测

性能优化：
- 使用mss库替代PIL.ImageGrab
"""
import ctypes
from ctypes import wintypes
from config import GAME_DETECT_PIXEL, GAME_DETECT_COLOR
from screenshot_util import capture_region

# Windows API 函数
user32 = ctypes.windll.user32
GetForegroundWindow = user32.GetForegroundWindow
GetWindowTextW = user32.GetWindowTextW
GetWindowTextLengthW = user32.GetWindowTextLengthW

PIXEL_X, PIXEL_Y = GAME_DETECT_PIXEL
EXPECTED_COLOR = GAME_DETECT_COLOR


class GameDetector(object):
    """检测当前是否在游戏中（双重检测：窗口标题 + 像素颜色）"""

    def __init__(self):
        self.in_game = False
        self.window_title = ""
        self.color = None
        self.window_active = False
        self.pixel_match = False

    def do(self):
        """执行双重检测"""
        # 1. 检测活跃窗口标题
        self.window_title = self._get_active_window_title()
        self.window_active = self._is_game_window(self.window_title)

        # 2. 只有窗口标题匹配时才检测像素点颜色（避免不必要的截图）
        if self.window_active:
            self.color = self._capture_pixel()
            self.pixel_match = self._match_pixel(self.color)
        else:
            self.color = None
            self.pixel_match = False

        # 3. 两个条件都满足才认为在游戏中
        self.in_game = self.window_active and self.pixel_match

    def _get_active_window_title(self):
        """获取当前活跃窗口的标题"""
        try:
            hwnd = GetForegroundWindow()
            if hwnd == 0:
                return ""

            length = GetWindowTextLengthW(hwnd)
            if length == 0:
                return ""

            buffer = ctypes.create_unicode_buffer(length + 1)
            GetWindowTextW(hwnd, buffer, length + 1)
            return buffer.value
        except Exception:
            return ""

    def _is_game_window(self, title):
        """判断窗口标题是否为游戏窗口"""
        if not title:
            return False

        # AOE4 的窗口标题通常包含 "Age of Empires IV"
        game_keywords = [
            "Age of Empires IV",
            "帝国时代IV",  # 中文标题
            "帝国时代4",
        ]

        title_lower = title.lower()
        return any(keyword.lower() in title_lower for keyword in game_keywords)

    def _capture_pixel(self):
        """截取特定像素点的颜色"""
        try:
            img = capture_region(PIXEL_X, PIXEL_Y, PIXEL_X + 1, PIXEL_Y + 1)
            return img.getpixel((0, 0))[:3]
        except Exception:
            return (0, 0, 0)

    def _match_pixel(self, color):
        """判断颜色是否匹配"""
        return color == EXPECTED_COLOR