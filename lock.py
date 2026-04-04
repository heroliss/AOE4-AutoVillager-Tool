"""
文件锁模块
防止并发执行导致的问题
"""
import os

_LOCK_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lock.txt")


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
