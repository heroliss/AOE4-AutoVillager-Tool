"""
村民总数统计模块
通过OCR识别左下角区域的所有数字并求和
"""
import os
import re
import numpy as np
from PIL import ImageGrab
import easyocr
from config import *

REGION = VILLAGER_COUNT_REGION
DEBUG_SCREENSHOT_PATH = VILLAGER_COUNT_DEBUG_SCREENSHOT

_reader = None


def _get_reader():
    global _reader
    if _reader is None:
        import warnings
        warnings.filterwarnings('ignore', category=UserWarning, module='torch')
        # 使用与population_reader相同的reader
        from population_reader import _get_reader as get_pop_reader
        _reader = get_pop_reader()
    return _reader


class VillagerCounter(object):
    """统计当前村民总数，通过OCR识别左下角区域的所有数字并求和"""

    def __init__(self):
        self.total = 0
        self.numbers = []

    def do(self):
        img = self._capture()
        raw = self._ocr(img)
        self._parse(raw)

    def _capture(self):
        """截取村民数量显示区域"""
        left, top, right, bottom = REGION
        img = ImageGrab.grab(bbox=(left, top, right, bottom))

        # 保存调试截图（仅在调试模式下）
        if DEBUG_MODE:
            img.save(DEBUG_SCREENSHOT_PATH)
            print(f"[村民计数] 已保存检测区域截图到: {DEBUG_SCREENSHOT_PATH}")
            print(f"[村民计数] 截图区域: ({left}, {top}, {right}, {bottom})")
            print(f"[村民计数] 原始尺寸: {img.size[0]}x{img.size[1]}")

        # 放大4倍以提高OCR准确率
        w, h = img.size
        img = img.resize((w * 4, h * 4))

        if DEBUG_MODE:
            print(f"[村民计数] 放大后尺寸: {img.size[0]}x{img.size[1]}")

        return np.array(img)

    def _ocr(self, img):
        """OCR识别所有数字"""
        # 使用更宽松的参数以识别更多文本
        results = _get_reader().readtext(img, detail=1, allowlist="0123456789OolIsS ")

        if DEBUG_MODE:
            print(f"[村民计数] OCR检测到 {len(results)} 个文本块")
            for i, (bbox, text, conf) in enumerate(results):
                print(f"[村民计数]   文本块{i+1}: '{text}' (置信度: {conf:.3f})")

        # 提取所有文本
        texts = [text for bbox, text, conf in results]
        return " ".join(texts).strip()

    def _parse(self, text):
        """提取所有数字并求和"""
        # 清理常见OCR错误
        cleaned = (text
                   .replace("O", "0").replace("o", "0")
                   .replace("l", "1").replace("I", "1")
                   .replace("S", "5").replace("s", "5")
                   .replace(" ", " ")
                   .strip())

        # 提取所有数字
        numbers = re.findall(r'\d+', cleaned)
        self.numbers = [int(n) for n in numbers]
        self.total = sum(self.numbers)

        if DEBUG_MODE:
            print(f"[村民计数] OCR原文: {text}")
            print(f"[村民计数] 清理后: {cleaned}")
            print(f"[村民计数] 提取数字: {self.numbers}")
            print(f"[村民计数] 总数: {self.total}")
