"""
食物数量识别模块
通过OCR识别屏幕上的食物数量
"""
import re
import numpy as np
from PIL import ImageGrab
from config import *

_reader = None


def _get_reader():
    """获取共享的OCR Reader实例"""
    global _reader
    if _reader is None:
        from population_reader import _get_reader as get_pop_reader
        _reader = get_pop_reader()
    return _reader


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
        """截取食物显示区域并放大以提高OCR准确率"""
        left, top, right, bottom = FOOD_REGION
        img = ImageGrab.grab(bbox=(left, top, right, bottom))

        # 保存调试截图（仅在调试模式下）
        if DEBUG_MODE:
            img.save(FOOD_DEBUG_SCREENSHOT)
            if DEBUG_MODE:
                print(f"[食物识别] 已保存检测区域截图到: {FOOD_DEBUG_SCREENSHOT}")

        # 放大4倍以提高OCR准确率
        w, h = img.size
        img = img.resize((w * 4, h * 4))
        return np.array(img)

    def _ocr(self, img):
        """OCR识别文本"""
        results = _get_reader().readtext(img, detail=0, allowlist="0123456789OolIsS ")
        return " ".join(results).strip()

    def _parse(self, text):
        """解析OCR结果，提取食物数量"""
        # 清理常见OCR错误
        cleaned = (text
                   .replace("O", "0").replace("o", "0")
                   .replace("l", "1").replace("I", "1")
                   .replace("S", "5").replace("s", "5")
                   .replace(" ", "")
                   .strip())

        # 提取数字
        m = re.search(r'(\d+)', cleaned)
        if m:
            self.amount = int(m.group(1))
        else:
            self.amount = None

        if DEBUG_MODE:
            print(f"[食物识别] OCR原文: '{text}' -> 清理后: '{cleaned}' -> 食物: {self.amount}")
