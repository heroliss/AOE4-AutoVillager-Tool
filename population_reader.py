"""
人口数量识别模块
通过OCR识别屏幕上的人口数字（如 "50/200"）

使用EasyOCR进行文字识别，支持GPU加速（可选）
默认使用CPU模式，因为小图片OCR时CPU通常比GPU更快

特殊情况：
- 游牧开局：开局无TC时人口上限显示为0（如 5/0），属于正常现象
- 此时人口已满，需等待建造TC后人口上限才会变为正常值
"""
import re
from config import POPULATION_REGION, DEBUG_MODE
from logger import log_main
from ocr_util import get_ocr_reader, clean_ocr_text, capture_and_scale


class PopulationReader(object):
    """OCR 识别当前人口与人口上限，结果存入 self.current 和 self.limit"""

    def __init__(self):
        self.current = None
        self.limit = None

    def do(self):
        img = self._capture()
        raw = self._ocr(img)
        self._parse(raw)

    def _capture(self):
        """截取人口显示区域并根据配置缩放"""
        return capture_and_scale(POPULATION_REGION)

    def _ocr(self, img):
        results = get_ocr_reader().readtext(img, detail=0, allowlist="0123456789/\\|OolIsS ")
        return " ".join(results).strip()

    def _parse(self, text):
        """解析OCR结果，提取当前人口和人口上限"""
        cleaned = clean_ocr_text(text, remove_spaces=True)
        m = re.search(r"(\d+)[/\\|](\d+)", cleaned)
        if m:
            self.current = int(m.group(1))
            self.limit   = int(m.group(2))
            if DEBUG_MODE:
                log_main("人口", f"原文='{text}' 清理='{cleaned}' 人口={self.current}/{self.limit}")
        else:
            self.current = None
            self.limit   = None
            if DEBUG_MODE:
                log_main("人口", f"识别失败 原文='{text}' 清理='{cleaned}'")
