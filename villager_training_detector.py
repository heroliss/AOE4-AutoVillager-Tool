"""
村民生产状态检测模块
检测生产队列中是否有村民正在生产，以及UI是否被遮挡
"""
import os
import cv2
import numpy as np
from PIL import ImageGrab
from config import *

REGION = VILLAGER_QUEUE_REGION
BLOCKED_REGION = BLOCKED_DETECT_REGION
TEMPLATE_PATH = VILLAGER_TEMPLATE
BLOCKED_TEMPLATE_PATH = BLOCKED_TEMPLATE
BLOCKED_DEBUG_SCREENSHOT_PATH = BLOCKED_DEBUG_SCREENSHOT
MATCH_THRESHOLD = VILLAGER_MATCH_THRESHOLD
BLOCKED_THRESHOLD = BLOCKED_MATCH_THRESHOLD

_template = None
_blocked_template = None


def _get_template():
    global _template
    if _template is None:
        _template = cv2.imread(TEMPLATE_PATH, cv2.IMREAD_COLOR)
    return _template


def _get_blocked_template():
    global _blocked_template
    if _blocked_template is None:
        if os.path.exists(BLOCKED_TEMPLATE_PATH):
            template = cv2.imread(BLOCKED_TEMPLATE_PATH, cv2.IMREAD_COLOR)

            # 获取目标区域尺寸
            left, top, right, bottom = BLOCKED_REGION
            target_width = right - left
            target_height = bottom - top

            # 如果模板尺寸超过目标区域，自动缩放
            if template.shape[0] > target_height or template.shape[1] > target_width:
                _blocked_template = cv2.resize(template, (target_width, target_height))
                if DEBUG_BLOCKED_DETECTION:
                    print(f"[遮挡检测] 模板已自动缩放: {template.shape[1]}x{template.shape[0]} -> {target_width}x{target_height}")
            else:
                _blocked_template = template
    return _blocked_template


class VillagerTrainingDetector(object):
    """检测生产队列中是否有村民正在生产，使用模板匹配"""

    def __init__(self):
        self.found = False
        self.confidence = 0.0
        self.blocked = False  # UI是否被遮挡
        self.blocked_confidence = 0.0  # 遮挡检测置信度
        self._blocked_check_enabled = False
        self._init_blocked_detection()

    def _init_blocked_detection(self):
        """初始化遮挡检测，检查模板是否可用"""
        if not os.path.exists(BLOCKED_TEMPLATE_PATH):
            raise FileNotFoundError(
                f"错误: 未找到 blocked.png 模板文件！\n"
                f"路径: {BLOCKED_TEMPLATE_PATH}\n"
                f"UI遮挡检测是必需功能，否则会频繁误判为没有村民在生产。\n"
                f"请确保 templates/blocked.png 文件存在。"
            )

        blocked_template = _get_blocked_template()
        if blocked_template is None:
            raise RuntimeError(f"错误: 无法加载 blocked.png 模板文件")

        # 获取截图区域尺寸
        left, top, right, bottom = BLOCKED_REGION
        screenshot_width = right - left
        screenshot_height = bottom - top

        self._blocked_check_enabled = True
        print(f"UI遮挡检测: 已启用 (检测区域: {screenshot_width}x{screenshot_height}, 模板尺寸: {blocked_template.shape[1]}x{blocked_template.shape[0]})")

    def do(self):
        screenshot = self._capture()

        # 遮挡检测使用独立区域
        if self._blocked_check_enabled:
            blocked_screenshot = self._capture_blocked_region()

            # 保存调试截图（仅在调试模式下）
            if DEBUG_BLOCKED_DETECTION:
                from PIL import Image
                img = cv2.cvtColor(blocked_screenshot, cv2.COLOR_BGR2RGB)
                Image.fromarray(img).save(BLOCKED_DEBUG_SCREENSHOT_PATH)
                print(f"[遮挡检测] 已保存检测区域截图到: {BLOCKED_DEBUG_SCREENSHOT_PATH}")
                print(f"[遮挡检测] 截图尺寸: {blocked_screenshot.shape[1]}x{blocked_screenshot.shape[0]}")

            self._check_blocked(blocked_screenshot)

        if not self.blocked:
            self._match(screenshot)

    def _capture(self):
        """截取生产队列区域"""
        left, top, right, bottom = REGION
        img = ImageGrab.grab(bbox=(left, top, right, bottom))
        return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)

    def _capture_blocked_region(self):
        """截取遮挡检测区域"""
        left, top, right, bottom = BLOCKED_REGION
        img = ImageGrab.grab(bbox=(left, top, right, bottom))
        return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)

    def _check_blocked(self, screenshot):
        """检测UI是否被遮挡"""
        blocked_template = _get_blocked_template()

        if DEBUG_BLOCKED_DETECTION:
            print(f"[遮挡检测] 模板尺寸: {blocked_template.shape[1]}x{blocked_template.shape[0]}")

        result = cv2.matchTemplate(screenshot, blocked_template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, _ = cv2.minMaxLoc(result)
        self.blocked_confidence = round(float(max_val), 4)
        self.blocked = max_val >= BLOCKED_THRESHOLD

        if DEBUG_BLOCKED_DETECTION:
            print(f"[遮挡检测] 置信度: {self.blocked_confidence}, 阈值: {BLOCKED_THRESHOLD}, 结果: {'遮挡' if self.blocked else '未遮挡'}")

    def _match(self, screenshot):
        """使用模板匹配检测村民图标"""
        template = _get_template()
        result = cv2.matchTemplate(screenshot, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, _ = cv2.minMaxLoc(result)
        self.confidence = round(float(max_val), 4)
        self.found = max_val >= MATCH_THRESHOLD

        # 调试输出
        if DEBUG_TRAINING_DETECTION:
            print(f"[生产检测] 置信度: {self.confidence}, 阈值: {MATCH_THRESHOLD}, 结果: {'生产中' if self.found else '未生产'}")
