"""
OCR 公共工具模块
提供统一的 OCR Reader 实例管理和文本清理函数

避免多个模块重复定义 _get_reader() 和文本清理逻辑
"""
import warnings
import numpy as np
import easyocr
from config import USE_GPU, DEBUG_MODE, OCR_IMAGE_SCALE
from screenshot_util import capture_region

# ==================== OCR Reader 管理 ====================

_reader = None


def get_ocr_reader():
    """
    获取全局唯一的 OCR Reader 实例（懒加载，线程安全由 GIL 保证）

    所有 OCR 模块（population_reader、food_reader、villager_counter）
    都应通过此函数获取 Reader，避免重复初始化
    """
    global _reader
    if _reader is None:
        import os
        # 屏蔽 PyTorch 警告
        warnings.filterwarnings('ignore', category=UserWarning, module='torch')
        os.environ['PYTHONWARNINGS'] = 'ignore::UserWarning'

        # 根据配置决定是否使用 GPU
        if USE_GPU:
            try:
                import torch
                gpu_available = torch.cuda.is_available()
                _reader = easyocr.Reader(["en"], gpu=gpu_available, verbose=False)
                if DEBUG_MODE and gpu_available:
                    print(f"[OCR] GPU加速已启用")
            except Exception:
                if DEBUG_MODE:
                    print(f"[OCR] GPU初始化失败，使用CPU模式")
                _reader = easyocr.Reader(["en"], gpu=False, verbose=False)
        else:
            _reader = easyocr.Reader(["en"], gpu=False, verbose=False)
    return _reader


# ==================== OCR 文本清理 ====================

# 常见 OCR 误识别映射（O→0, l→1 等），统一管理避免各模块重复定义
_OCR_REPLACEMENTS = str.maketrans({
    'O': '0', 'o': '0',
    'l': '1', 'I': '1',
    'S': '5', 's': '5',
})


def clean_ocr_text(text, remove_spaces=False):
    """
    清理 OCR 识别结果中的常见错误

    参数：
        text: OCR 原始文本
        remove_spaces: 是否移除空格（人口/食物识别需要移除，村民计数保留空格）

    返回：
        清理后的文本
    """
    cleaned = text.translate(_OCR_REPLACEMENTS)
    if remove_spaces:
        cleaned = cleaned.replace(" ", "").replace(",", "")
    return cleaned.strip()


# ==================== 截图 + 缩放 公共逻辑 ====================

def capture_and_scale(region, debug_screenshot_path=None, debug_logger=None):
    """
    截取指定区域并根据配置缩放，返回 numpy 数组

    统一了 population_reader、food_reader、villager_counter 的截图逻辑

    参数：
        region: 截图区域 (left, top, right, bottom)
        debug_screenshot_path: 调试截图保存路径（None 则不保存）
        debug_logger: 调试日志函数，如 log_food

    返回：
        numpy 数组（RGB 格式）
    """
    left, top, right, bottom = region
    img = capture_region(left, top, right, bottom)

    # 保存调试截图
    if debug_screenshot_path and debug_logger:
        from config import DEBUG_MODE, DEBUG_SAVE_SCREENSHOTS
        if DEBUG_MODE and DEBUG_SAVE_SCREENSHOTS:
            try:
                img.save(debug_screenshot_path)
                debug_logger("截图", f"{debug_screenshot_path}")
            except Exception as e:
                debug_logger("截图", f"保存失败: {e}")

    # 根据配置缩放图片
    if OCR_IMAGE_SCALE != 1.0:
        w, h = img.size
        new_w = int(w * OCR_IMAGE_SCALE)
        new_h = int(h * OCR_IMAGE_SCALE)
        img = img.resize((new_w, new_h))

    return np.array(img)
