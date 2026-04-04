"""
TC数量检测模块
通过模板匹配检测左下角TC图标数量
"""
import os
import cv2
import numpy as np
from PIL import ImageGrab
from config import *

# 左下角区域，需要根据实际情况调整
REGION = TC_ICON_REGION
TEMPLATE_PATH = TC_ICON_TEMPLATE
DEBUG_SCREENSHOT_PATH = TC_DEBUG_SCREENSHOT
MATCH_THRESHOLD = TC_MATCH_THRESHOLD

_template = None


def _get_template():
    global _template
    if _template is None:
        if not os.path.exists(TEMPLATE_PATH):
            return None
        _template = cv2.imread(TEMPLATE_PATH, cv2.IMREAD_COLOR)
    return _template


class TCCounter(object):
    """检测左下角区域的TC图标数量，结果存入 self.count"""

    def __init__(self):
        self.count = 1  # 默认至少有1个TC
        self.locations = []

    def do(self):
        template = _get_template()
        if template is None:
            # 如果没有模板图片，默认返回1个TC
            if DEBUG_MODE:
                print(f"[TC计数] 未找到模板图片 {TEMPLATE_PATH}，默认1个TC")
            self.count = 1
            return

        if DEBUG_MODE:
            print(f"[TC计数] 模板图片尺寸: {template.shape[1]}x{template.shape[0]}")
        screenshot = self._capture()
        if DEBUG_MODE:
            print(f"[TC计数] 截图区域尺寸: {screenshot.shape[1]}x{screenshot.shape[0]}")
        self._match_all(screenshot, template)

    def _capture(self):
        left, top, right, bottom = REGION
        img = ImageGrab.grab(bbox=(left, top, right, bottom))

        # 保存调试截图（仅在调试模式下）
        if DEBUG_MODE:
            img.save(DEBUG_SCREENSHOT_PATH)
            print(f"[TC计数] 已保存检测区域截图到: {DEBUG_SCREENSHOT_PATH}")

        return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)

    def _match_all(self, screenshot, template):
        """查找所有匹配的TC图标"""
        result = cv2.matchTemplate(screenshot, template, cv2.TM_CCOEFF_NORMED)

        # 获取最大匹配值
        min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)
        if DEBUG_MODE:
            print(f"[TC计数] 最大匹配相似度: {max_val:.4f}, 阈值: {MATCH_THRESHOLD}")

        # 找到所有超过阈值的位置
        locations = np.where(result >= MATCH_THRESHOLD)
        locations = list(zip(*locations[::-1]))  # 转换为 (x, y) 格式
        if DEBUG_MODE:
            print(f"[TC计数] 找到 {len(locations)} 个匹配点（阈值前）")

        if not locations:
            # 没找到图标，可能是单TC（不显示图标）
            if DEBUG_MODE:
                print(f"[TC计数] 未找到匹配图标，默认1个TC")
            self.count = 1
            self.locations = []
            return

        # 去除重复检测（同一个图标可能被多次匹配）
        filtered_locations = self._filter_nearby_locations(locations, template.shape)
        if DEBUG_MODE:
            print(f"[TC计数] 去重后剩余 {len(filtered_locations)} 个TC图标")
            print(f"[TC计数] TC位置: {filtered_locations}")

        self.locations = filtered_locations
        self.count = len(filtered_locations)

    def _filter_nearby_locations(self, locations, template_shape):
        """过滤掉距离太近的重复匹配点，使用更高效的算法"""
        if not locations:
            return []

        h, w = template_shape[:2]
        min_distance = w * 0.5  # 两个图标之间的最小距离

        # 转换为numpy数组以提高计算效率
        locations_array = np.array(locations)
        filtered = []

        for i, loc in enumerate(locations_array):
            # 检查是否与已有位置太近
            if filtered:
                distances = np.sqrt(np.sum((np.array(filtered) - loc)**2, axis=1))
                if np.min(distances) < min_distance:
                    continue

            filtered.append(tuple(loc))

        return filtered
