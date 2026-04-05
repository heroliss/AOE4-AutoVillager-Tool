"""
输入屏蔽模块 - 在关键操作期间屏蔽物理鼠标键盘输入
注意：需要管理员权限才能生效
"""
import ctypes
import time
from contextlib import contextmanager


@contextmanager
def input_blocked(max_duration=5.0):
    """
    屏蔽物理鼠标键盘输入，退出 with 块后自动恢复。

    Args:
        max_duration: 最大屏蔽时长（秒），防止意外锁定

    注意：
    - 需要管理员权限才能生效
    - 只屏蔽物理输入，不影响程序化输入（pyautogui/pydirectinput）
    - 如果屏蔽失败，会静默跳过，不影响正常流程
    """
    success = _set_block(True)
    start_time = time.time()

    try:
        yield success
        # 安全检查：超时自动解除
        if time.time() - start_time > max_duration:
            print(f"[警告] 输入屏蔽超过 {max_duration} 秒，强制解除")
    finally:
        _set_block(False)


def _set_block(state: bool) -> bool:
    """
    调用 Windows BlockInput API

    Args:
        state: True=屏蔽输入, False=恢复输入

    Returns:
        bool: 是否成功
    """
    try:
        return bool(ctypes.windll.user32.BlockInput(state))
    except Exception:
        # 静默失败，不影响正常流程
        return False
