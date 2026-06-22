"""
用户级设置 / 会话状态的持久化（与程序代码、流程文件分开存）。

存什么：使用过程中的“临时信息”——上次打开的流程、使用模式下的临时调参、窗口偏好等。
        这些既不属于流程定义(.flow.json)，也不该污染程序目录或代码仓库。

存哪里：%APPDATA%\\AOE4AutoVillager\\settings.json（Windows 约定的“漫游”用户配置目录）。
为什么不放程序同目录：(1) 程序可能装在 Program Files，普通权限写不了；
                      (2) 重装/更新会被清掉；(3) 会污染代码仓库（旧版 .editor_state.json 一直显示为已修改）。
        流程文件与模板仍留在程序目录——那是用户整理的“内容”，不是会话状态。
"""
from __future__ import annotations

import json
import os

APP_NAME = "AOE4AutoVillager"


def settings_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(base, APP_NAME)
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def settings_path() -> str:
    return os.path.join(settings_dir(), "settings.json")


def load_settings() -> dict:
    try:
        with open(settings_path(), "r", encoding="utf-8") as fp:
            d = json.load(fp)
            return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save_settings(data: dict) -> None:
    """原子写（先写 .tmp 再 os.replace），避免崩溃时留下半截文件。"""
    try:
        p = settings_path()
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fp:
            json.dump(data, fp, ensure_ascii=False, indent=0)
        os.replace(tmp, p)
    except Exception:
        pass


def get_setting(key: str, default=None):
    return load_settings().get(key, default)


def update_settings(**kw) -> dict:
    """读-改-写若干键（值为 None 表示删除该键）。返回更新后的完整设置。"""
    d = load_settings()
    for k, v in kw.items():
        if v is None:
            d.pop(k, None)
        else:
            d[k] = v
    save_settings(d)
    return d
