"""
村民总数统计模块
通过OCR识别左下角区域的所有数字并求和

性能优化：
- 使用mss库替代PIL.ImageGrab（2-3x提升）
"""
import os
import re
import numpy as np
import easyocr
from config import *
from screenshot_util import capture_region
from logger import log_villager

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
        """截取村民数量显示区域并根据配置缩放"""
        from config import OCR_IMAGE_SCALE
        left, top, right, bottom = REGION
        img = capture_region(left, top, right, bottom)

        # 保存调试截图（仅在调试模式下）
        if DEBUG_MODE and DEBUG_SAVE_SCREENSHOTS:
            try:
                img.save(DEBUG_SCREENSHOT_PATH)
                log_villager("截图", f"{DEBUG_SCREENSHOT_PATH}")
                log_villager("截图", f"区域=({left},{top},{right},{bottom}) 尺寸={img.size[0]}x{img.size[1]}")
            except Exception as e:
                log_villager("截图", f"保存失败: {e}")

        # 根据配置缩放图片
        if OCR_IMAGE_SCALE != 1.0:
            w, h = img.size
            new_w = int(w * OCR_IMAGE_SCALE)
            new_h = int(h * OCR_IMAGE_SCALE)
            img = img.resize((new_w, new_h))
            log_villager("截图", f"缩放后={new_w}x{new_h}")

        return np.array(img)

    def _ocr(self, img):
        """OCR识别所有数字"""
        # 使用更宽松的参数以识别更多文本
        results = _get_reader().readtext(img, detail=1, allowlist="0123456789OolIsS ")

        log_villager("OCR", f"检测到{len(results)}个文本块")
        for i, (bbox, text, conf) in enumerate(results):
            log_villager("OCR", f"  #{i+1} 文本='{text}' 置信度={conf:.3f}")

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

        log_villager("解析", f"原文='{text}' 清理='{cleaned}'")
        log_villager("解析", f"数字={self.numbers} 总数={self.total}")
