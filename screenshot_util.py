"""
统一截图工具模块
使用mss库替代PIL.ImageGrab，性能提升2-3倍

性能对比：
- PIL.ImageGrab: 50-70ms
- mss: 15-25ms

注意：mss库在多线程环境下需要每个线程创建独立实例
"""
import numpy as np
import mss
from PIL import Image
import threading

# 线程局部存储，每个线程有独立的mss实例
_thread_local = threading.local()
_use_mss = True  # 是否使用mss库
_fallback_notified = False  # 是否已通知用户回退


def get_sct():
    """获取当前线程的mss实例"""
    if not hasattr(_thread_local, 'sct') or _thread_local.sct is None:
        _thread_local.sct = mss.mss()
    return _thread_local.sct


def _fallback_notify(error):
    """通知用户mss回退到PIL（仅通知一次）"""
    global _use_mss, _fallback_notified
    _use_mss = False
    if not _fallback_notified:
        print(f"\n[警告] mss库截图失败，已切换到PIL.ImageGrab模式")
        print(f"[警告] 原因: {error}")
        print(f"[警告] 性能可能下降，但功能正常\n")
        _fallback_notified = True


def _grab_mss(left, top, right, bottom):
    """使用mss截图，返回 (screenshot_object, success)"""
    sct = get_sct()
    monitor = {
        "left": left,
        "top": top,
        "width": right - left,
        "height": bottom - top
    }
    return sct.grab(monitor)


def capture_region(left, top, right, bottom):
    """
    截取指定区域

    参数：
        left, top, right, bottom: 区域坐标

    返回：
        PIL.Image对象（RGB格式）
    """
    global _use_mss

    if _use_mss:
        try:
            screenshot = _grab_mss(left, top, right, bottom)
            # mss返回的是BGRA格式，转换为RGB
            return Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")
        except Exception as e:
            _fallback_notify(e)

    # 回退到PIL.ImageGrab（延迟导入，仅在需要时加载）
    from PIL import ImageGrab
    return ImageGrab.grab(bbox=(left, top, right, bottom))


def capture_region_np(left, top, right, bottom):
    """
    截取指定区域并返回numpy数组（BGR格式，适合OpenCV）

    参数：
        left, top, right, bottom: 区域坐标

    返回：
        numpy数组（BGR格式）
    """
    global _use_mss

    if _use_mss:
        try:
            screenshot = _grab_mss(left, top, right, bottom)
            # 转换为numpy数组（BGRA -> BGR）
            img = np.array(screenshot)
            return img[:, :, :3]  # 去掉Alpha通道
        except Exception as e:
            _fallback_notify(e)

    # 回退到PIL.ImageGrab（延迟导入，仅在需要时加载）
    from PIL import ImageGrab
    img_pil = ImageGrab.grab(bbox=(left, top, right, bottom))
    img_array = np.array(img_pil)
    # PIL返回RGB，转换为BGR
    return img_array[:, :, ::-1]
