"""
游戏窗口检测模块
结合窗口标题和像素点颜色双重检测

性能优化：
- 使用 Windows GetPixel API 直接读取像素，绕过截图（< 0.1ms vs 5-15ms）
- 只在窗口标题匹配时读取像素，避免不必要的 API 调用
"""
import ctypes
from ctypes import wintypes
import config

# Windows API 函数
user32 = ctypes.windll.user32
GetForegroundWindow = user32.GetForegroundWindow
GetWindowTextW = user32.GetWindowTextW
GetWindowTextLengthW = user32.GetWindowTextLengthW
GetDC = user32.GetDC
ReleaseDC = user32.ReleaseDC

# GetPixel 函数（优先Unicode版本GetPixelW，回退到ANSI版本GetPixelA）
try:
    GetPixel = user32.GetPixelW
except AttributeError:
    try:
        GetPixel = user32.GetPixelA
    except AttributeError:
        GetPixel = None

# 设置 GetPixel 函数签名
if GetPixel:
    GetPixel.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    GetPixel.restype = wintypes.DWORD

# 预计算游戏窗口关键词（小写），避免每次检测时重复转换
_GAME_KEYWORDS_LOWER = [kw.lower() for kw in [
    "Age of Empires IV",
    "帝国时代IV",
    "帝国时代4",
]]


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
        # 动态读取配置（HDR开关/颜色值可能运行时变更）
        pixel_x, pixel_y = config.GAME_DETECT_PIXEL
        expected_color = config._get_game_detect_color()

        # 1. 检测活跃窗口标题（极快，API调用）
        self.window_title = self._get_active_window_title()
        self.window_active = self._is_game_window(self.window_title)

        # 2. 只有窗口标题匹配时才读取像素颜色
        if self.window_active:
            self.color = self._get_pixel_color(pixel_x, pixel_y)
            self.pixel_match = self._match_pixel(self.color, expected_color)
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
        title_lower = title.lower()
        return any(keyword in title_lower for keyword in _GAME_KEYWORDS_LOWER)

    def _get_pixel_color(self, x, y):
        """
        直接使用 Windows API 读取像素颜色（绕过截图）
        性能：< 0.1ms（vs mss 截图 5-15ms）

        返回：RGB 元组 (r, g, b)
        """
        if GetPixel is None:
            # GetPixel 不可用，回退到截图方式
            from screenshot_util import capture_region
            img = capture_region(x, y, x + 1, y + 1)
            return img.getpixel((0, 0))[:3]

        try:
            hdc = GetDC(None)  # 获取整个屏幕的 DC
            pixel = GetPixel(hdc, x, y)
            ReleaseDC(None, hdc)

            # GetPixel 返回值格式：0x00BBGGRR（注意是 BGR 顺序）
            if pixel == 0xFFFFFFFF:  # CLR_INVALID (-1)，说明坐标超出屏幕
                return (0, 0, 0)

            b = pixel & 0xFF
            g = (pixel >> 8) & 0xFF
            r = (pixel >> 16) & 0xFF
            return (r, g, b)
        except Exception:
            return (0, 0, 0)

    def _match_pixel(self, color, expected_color):
        """判断颜色是否匹配"""
        return color == expected_color