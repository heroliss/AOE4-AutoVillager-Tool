"""
食物数量识别模块
通过OCR识别屏幕上的食物数量

性能优化：
- 使用mss库替代PIL.ImageGrab（2-3x提升）
"""
import re
import numpy as np
from config import *
from screenshot_util import capture_region
from logger import log_food

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
        """截取食物显示区域并根据配置缩放"""
        from config import OCR_IMAGE_SCALE
        left, top, right, bottom = FOOD_REGION
        img = capture_region(left, top, right, bottom)

        # 保存调试截图（仅在调试模式下）
        if DEBUG_MODE and DEBUG_SAVE_SCREENSHOTS:
            try:
                img.save(FOOD_DEBUG_SCREENSHOT)
                log_food("截图", f"{FOOD_DEBUG_SCREENSHOT}")
            except Exception as e:
                log_food("截图", f"保存失败: {e}")

        # 根据配置缩放图片
        if OCR_IMAGE_SCALE != 1.0:
            w, h = img.size
            new_w = int(w * OCR_IMAGE_SCALE)
            new_h = int(h * OCR_IMAGE_SCALE)
            img = img.resize((new_w, new_h))

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

        log_food("解析", f"原文='{text}' 清理='{cleaned}' 食物={self.amount}")
