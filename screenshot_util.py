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

# 进程内共享单个 mss 实例（单例）。
# 早期是“每线程一个实例”，但 pywebview 的 js_api 桥接会为【每一次】调用新建一个线程：
# 试运行里每帧 run_tick 都落在新线程上 → 每帧都新建一个 mss 实例并分配整屏大小的位图，
# 旧实例随死线程滞留，GDI 句柄/内存不断累积（表现为“走到整屏预取节点后 CPU 飙高、内存快速上升”）。
# 改为共享单例 + 抓取串行化（mss.grab 非并发安全，用锁保护）：实例只建一次、整屏位图只分配一次。
_sct = None
_sct_lock = threading.Lock()
_use_mss = True  # 是否使用mss库
_fallback_notified = False  # 是否已通知用户回退


def get_sct():
    """获取进程内共享的 mss 实例（单例，懒加载，线程安全）。"""
    global _sct
    if _sct is None:
        with _sct_lock:
            if _sct is None:
                _sct = mss.mss()
    return _sct


def grab(monitor):
    """线程安全地抓取指定 monitor(dict) 区域，返回 mss 截图对象。
    并发调用由 _sct_lock 串行化，避免共享实例被并发 grab 破坏。"""
    sct = get_sct()
    with _sct_lock:
        return sct.grab(monitor)


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
    """使用mss截图，返回 mss 截图对象（共享实例 + 锁，见 grab()）。"""
    monitor = {
        "left": left,
        "top": top,
        "width": right - left,
        "height": bottom - top
    }
    return grab(monitor)


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
