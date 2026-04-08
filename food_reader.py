"""
食物数量识别模块
通过OCR识别屏幕上的食物数量

性能优化：
- 使用mss库替代PIL.ImageGrab（2-3x提升）
- 共享OCR Reader实例，避免重复初始化

食物为0的特殊处理：
- EasyOCR对单个"0"字符识别能力差，容易漏识别
- 使用三级兜底策略：标准识别 → 无限制识别 → 像素分析推测
- 像素分析：当OCR完全无结果但截图有可见内容时，推测为0
"""
import re
from config import FOOD_REGION, FOOD_DEBUG_SCREENSHOT
from logger import log_food
from ocr_util import get_ocr_reader, clean_ocr_text, capture_and_scale


class FoodReader(object):
    """OCR识别当前食物数量"""

    def __init__(self):
        self.amount = None

    def do(self):
        """执行食物数量识别"""
        img = self._capture()
        raw = self._ocr(img)
        self._parse(raw)

    def _capture(self):
        """截取食物显示区域并根据配置缩放"""
        return capture_and_scale(FOOD_REGION, debug_screenshot_path=FOOD_DEBUG_SCREENSHOT, debug_logger=log_food)

    def _ocr(self, img):
        """
        OCR识别文本，三级兜底策略确保食物为0时也能正确识别

        第一级：标准识别（带allowlist，精度优先）
        第二级：无allowlist重试（提高召回率，可能识别到"O"/"o"等）
        第三级：像素分析推测（OCR完全无结果时，检查是否有可见内容，推测为0）
        """
        # 第一级：标准识别（带allowlist，精度优先）
        results = get_ocr_reader().readtext(img, detail=1, allowlist="0123456789OolIsS ")
        texts = [text for _, text, _ in results]
        raw_text = " ".join(texts).strip()

        if raw_text:
            return raw_text

        # 第二级：不带allowlist重新识别（提高召回率）
        # 单个"0"字符EasyOCR容易漏掉，去掉字符限制可能识别为"O"或"o"
        fallback_results = get_ocr_reader().readtext(img, detail=1)
        fallback_texts = [text for _, text, _ in fallback_results]
        fallback_text = " ".join(fallback_texts).strip()

        if fallback_text:
            log_food("OCR", f"标准识别无结果，fallback识别到: '{fallback_text}'")
            return fallback_text

        # 第三级兜底：OCR完全无结果时，通过像素分析判断是否有内容
        # 食物区域只会显示数字，如果OCR完全检测不到，最可能是单个"0"
        # 判断依据：截图中有足够多的非纯色像素，说明有数字渲染
        if self._has_visible_content(img):
            log_food("OCR", "OCR无结果但检测到像素内容，推测为0")
            return "0"

        return ""

    def _has_visible_content(self, img):
        """
        检查图像中是否有可见的数字内容（非纯色背景）

        通过像素标准差判断：标准差>5说明有文字渲染（非纯色背景）
        食物区域只显示数字，OCR完全检测不到且有内容时，最可能是单个"0"
        """
        import numpy as np
        if isinstance(img, np.ndarray):
            arr = img
        else:
            arr = np.array(img)
        # 如果是RGB图，转灰度
        if arr.ndim == 3:
            arr = arr.mean(axis=2)
        # 计算像素值的标准差，标准差大说明有内容（非纯色背景）
        return arr.std() > 5

    def _parse(self, text):
        """解析OCR结果，提取食物数量"""
        # 清理常见OCR错误（移除空格，因为是纯数字）
        cleaned = clean_ocr_text(text, remove_spaces=True)

        # 提取数字
        m = re.search(r'(\d+)', cleaned)
        if m:
            self.amount = int(m.group(1))
        else:
            self.amount = None

        log_food("解析", f"原文='{text}' 清理='{cleaned}' 食物={self.amount}")
