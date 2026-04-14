"""
OCR 公共工具模块
提供统一的 OCR Reader 实例管理和文本清理函数

避免多个模块重复定义 _get_reader() 和文本清理逻辑

兼容说明：
- 当 torch 未安装时（如CPU精简版exe），自动回退到CPU模式
- easyocr 内部依赖 torch，因此 import easyocr 也在函数内延迟执行
"""
import warnings
import numpy as np
from config import USE_GPU
import config
from screenshot_util import capture_region

# ==================== OCR Reader 管理 ====================

_reader = None


def _check_torch_available():
    """检测 torch 是否可用（包括 import 后 DLL 加载失败的情况）"""
    try:
        import torch
        # 验证 torch 真正可用（不仅仅是 import 成功）
        # 某些情况下 import 成功但 CUDA DLL 缺失导致后续调用崩溃
        _ = torch.__version__
        return True, torch
    except (ImportError, OSError, ModuleNotFoundError):
        return False, None


def _create_easyocr_reader(gpu=False):
    """
    创建 easyocr.Reader 实例

    当 torch 不可用时，easyocr 的 import 也会失败。
    此函数通过模拟 torch 的最小接口来绕过 easyocr 的顶层 import 检查。
    """
    # 先检查 torch 是否可用
    torch_available, torch_module = _check_torch_available()

    if torch_available:
        # torch 可用，正常创建
        if gpu:
            gpu_available = torch_module.cuda.is_available()
            return __import__('easyocr').Reader(["en"], gpu=gpu_available, verbose=False)
        else:
            return __import__('easyocr').Reader(["en"], gpu=False, verbose=False)

    # torch 不可用，需要创建 mock 模块让 easyocr 能正常 import
    import types
    import sys

    # 创建 torch mock 模块的最小接口
    if 'torch' not in sys.modules:
        torch_mock = types.ModuleType('torch')
        torch_mock.__version__ = '2.0.0'
        torch_mock.cuda = types.ModuleType('torch.cuda')
        torch_mock.cuda.is_available = lambda: False
        torch_mock.nn = types.ModuleType('torch.nn')
        torch_mock.nn.Module = type('Module', (), {})
        torch_mock.Tensor = type('Tensor', (), {})
        torch_mock.no_grad = lambda: type('no_grad', (), {'__enter__': lambda s: None, '__exit__': lambda s, *a: None})()
        torch_mock.device = lambda x: x
        # easyocr 内部可能需要的子模块
        sys.modules['torch'] = torch_mock
        sys.modules['torch.cuda'] = torch_mock.cuda
        sys.modules['torch.nn'] = torch_mock.nn

    try:
        import easyocr
        return easyocr.Reader(["en"], gpu=False, verbose=False)
    except Exception as e:
        print(f"[OCR] easyocr 初始化失败: {e}")
        raise


def get_ocr_reader():
    """
    获取全局唯一的 OCR Reader 实例（懒加载，线程安全由 GIL 保证）

    所有 OCR 模块（population_reader、food_reader、villager_counter）
    都应通过此函数获取 Reader，避免重复初始化
    """
    global _reader
    if _reader is None:
        import os
        # 屏蔽警告
        warnings.filterwarnings('ignore', category=UserWarning)
        os.environ['PYTHONWARNINGS'] = 'ignore::UserWarning'

        # 检测 torch 是否可用
        torch_available, _ = _check_torch_available()

        if not torch_available:
            print(f"[OCR] torch未安装，使用CPU模式（需要mock torch模块）")

        # 根据配置和 torch 可用性决定是否使用 GPU
        if USE_GPU and torch_available:
            try:
                import torch
                gpu_available = torch.cuda.is_available()
                _reader = _create_easyocr_reader(gpu=gpu_available)
                if gpu_available:
                    print(f"[OCR] GPU加速已启用")
            except Exception:
                print(f"[OCR] GPU初始化失败，使用CPU模式")
                _reader = _create_easyocr_reader(gpu=False)
        else:
            if USE_GPU and not torch_available:
                print(f"[OCR] 配置了GPU模式但torch未安装，强制CPU模式")
            _reader = _create_easyocr_reader(gpu=False)

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
        if config.DEBUG_MODE and config.DEBUG_SAVE_SCREENSHOTS:
            try:
                img.save(debug_screenshot_path)
                debug_logger("截图", f"{debug_screenshot_path}")
            except Exception as e:
                debug_logger("截图", f"保存失败: {e}")

    # 根据配置缩放图片
    if config.OCR_IMAGE_SCALE != 1.0:
        w, h = img.size
        new_w = int(w * config.OCR_IMAGE_SCALE)
        new_h = int(h * config.OCR_IMAGE_SCALE)
        img = img.resize((new_w, new_h))

    return np.array(img)
