"""
人口数量识别模块
通过OCR识别屏幕上的人口数字（如 "50/200"）
"""
import re
import numpy as np
from PIL import ImageGrab
import easyocr
from config import POPULATION_REGION

REGION = POPULATION_REGION

_reader = None


def _get_reader():
    global _reader
    if _reader is None:
        import warnings
        import os
        # 屏蔽PyTorch警告
        warnings.filterwarnings('ignore', category=UserWarning, module='torch')
        os.environ['PYTHONWARNINGS'] = 'ignore::UserWarning'

        # 尝试使用GPU，如果失败则回退到CPU
        try:
            import torch
            gpu_available = torch.cuda.is_available()
            _reader = easyocr.Reader(["en"], gpu=gpu_available, verbose=False)
            if gpu_available:
                print(f"[OCR] GPU加速已启用")
            else:
                print(f"[OCR] 使用CPU模式（未检测到CUDA GPU）")
        except Exception as e:
            print(f"[OCR] GPU初始化失败，使用CPU模式: {e}")
            _reader = easyocr.Reader(["en"], gpu=False, verbose=False)
    return _reader


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
        """截取人口显示区域并放大3倍以提高OCR准确率"""
        left, top, right, bottom = REGION
        img = ImageGrab.grab(bbox=(left, top, right, bottom))
        w, h = img.size
        img = img.resize((w * 3, h * 3))
        return np.array(img)

    def _ocr(self, img):
        results = _get_reader().readtext(img, detail=0, allowlist="0123456789/\\|OolIsS ")
        return " ".join(results).strip()

    def _parse(self, text):
        """解析OCR结果，提取当前人口和人口上限"""
        cleaned = (text
                   .replace("O", "0").replace("o", "0")
                   .replace("l", "1").replace("I", "1")
                   .replace("S", "5").replace("s", "5")
                   .replace(" ", "").replace(",", "")
                   .strip())
        m = re.search(r"(\d+)[/\\|](\d+)", cleaned)
        if m:
            self.current = int(m.group(1))
            self.limit   = int(m.group(2))
        else:
            self.current = None
            self.limit   = None
