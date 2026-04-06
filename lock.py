"""
文件锁模块
防止并发执行导致的问题

使用文件系统的原子操作（os.O_CREAT | os.O_EXCL）实现锁机制：
- acquire_lock(): 尝试创建锁文件，成功返回True，失败返回False
- release_lock(): 删除锁文件，释放锁
- cleanup_lock(): 清理残留的锁文件（程序启动时调用，防止异常退出导致的锁残留）

性能影响：文件锁操作极快（微秒级），对性能影响可忽略不计
"""
import os

_LOCK_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lock.txt")


def cleanup_lock():
    """清理残留的锁文件（程序启动时调用）"""
    try:
        os.remove(_LOCK_FILE)
    except FileNotFoundError:
        pass


def acquire_lock() -> bool:
    try:
        fd = os.open(_LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
        return True
    except FileExistsError:
        return False


def release_lock():
    try:
        os.remove(_LOCK_FILE)
    except FileNotFoundError:
        pass
