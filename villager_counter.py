"""
村民总数统计模块
通过OCR识别左下角区域的所有数字并求和

性能优化：
- 使用mss库替代PIL.ImageGrab（2-3x提升）
"""
import re
from config import (
    VILLAGER_COUNT_REGION,
    VILLAGER_COUNT_DEBUG_SCREENSHOT
)
from logger import log_villager
from ocr_util import get_ocr_reader, clean_ocr_text, capture_and_scale


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
        return capture_and_scale(VILLAGER_COUNT_REGION, debug_screenshot_path=VILLAGER_COUNT_DEBUG_SCREENSHOT, debug_logger=log_villager)

    def _ocr(self, img):
        """OCR识别所有数字"""
        # 使用更宽松的参数以识别更多文本
        results = get_ocr_reader().readtext(img, detail=1, allowlist="0123456789OolIsS ")

        log_villager("OCR", f"检测到{len(results)}个文本块")
        for i, (_, text, conf) in enumerate(results):
            log_villager("OCR", f"  #{i+1} 文本='{text}' 置信度={conf:.3f}")

        # 提取所有文本
        texts = [text for _, text, _ in results]
        return " ".join(texts).strip()

    def _parse(self, text):
        """提取所有数字并求和"""
        # 清理常见OCR错误（保留空格，因为可能有多个数字块）
        cleaned = clean_ocr_text(text, remove_spaces=False)

        # 提取所有数字
        numbers = re.findall(r'\d+', cleaned)
        self.numbers = [int(n) for n in numbers]
        self.total = sum(self.numbers)

        log_villager("解析", f"原文='{text}' 清理='{cleaned}'")
        log_villager("解析", f"数字={self.numbers} 总数={self.total}")
