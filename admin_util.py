"""
管理员权限检测与自提升。

为什么需要：输入屏蔽用的 Windows BlockInput API 只有在【管理员权限】下才生效——
普通权限下它静默失败（返回 0），自动操作期间无法屏蔽鼠标键盘，容易被人为误触打断。
因此启动时若发现不是管理员，就尝试用 UAC 提升重新启动一份管理员进程。

开发期想跳过提升（避免反复弹 UAC）：设环境变量 AOE4_NO_ELEVATE=1。
"""
from __future__ import annotations

import ctypes
import os
import sys


def is_admin() -> bool:
    """当前进程是否拥有管理员权限。"""
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _relaunch_command() -> tuple[str, str]:
    """返回 (要执行的程序, 参数串)，用于以管理员身份重启“当前这次启动”。
    兼容两种情形：源码运行（python run_editor.py ...）与 PyInstaller 打包后的单 exe。"""
    def q(a: str) -> str:
        return f'"{a}"'

    if getattr(sys, "frozen", False):
        # 打包成 exe：sys.executable 就是本程序，argv[1:] 是参数
        exe = sys.executable
        args = " ".join(q(a) for a in sys.argv[1:])
    else:
        # 源码运行：用同一个 python 解释器，argv 含脚本路径
        exe = sys.executable
        args = " ".join(q(a) for a in sys.argv)
    return exe, args


def relaunch_as_admin() -> bool:
    """以管理员权限重新启动“当前这次启动”。
    返回 True=已成功发起提升（调用方应立即退出本进程，交给新进程）；
    False=提升失败或用户在 UAC 弹窗点了“否”（调用方可带警告继续以普通权限运行）。"""
    try:
        exe, args = _relaunch_command()
        # ShellExecuteW 的 "runas" 动作触发 UAC 提升；返回值 >32 表示成功
        r = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, args, os.getcwd(), 1)
        return int(r) > 32
    except Exception:
        return False


def ensure_admin_or_warn(prompt=print) -> bool:
    """启动时调用：保证以管理员身份运行，否则尝试自提升。

    返回值语义（供调用方决定后续动作）：
    - True ：已是管理员，照常继续。
    - False：未提升成功——要么设了 AOE4_NO_ELEVATE，要么用户拒绝了 UAC。
             调用方应【带警告继续】以普通权限运行（编辑仍可用，但“输入屏蔽”不会生效）。

    若自提升【成功】，本函数会直接 sys.exit(0) 退出当前普通权限进程
    （新的管理员进程已接管），不会返回。
    """
    if is_admin():
        return True

    if os.environ.get("AOE4_NO_ELEVATE"):
        prompt("[提示] 未以管理员身份运行（AOE4_NO_ELEVATE 已设，跳过自提升）——"
               "“输入屏蔽”将不生效，仅供编辑/调试。")
        return False

    prompt("[提示] 未以管理员身份运行——输入屏蔽(BlockInput)需要管理员权限才生效。")
    prompt("       正在尝试以管理员身份重新启动……（UAC 弹窗请选“是”）")
    if relaunch_as_admin():
        prompt("       已发起管理员提升，本进程退出，交给新进程。")
        sys.exit(0)

    prompt("[警告] 未能提升为管理员（可能点了“否”）。将以普通权限继续——"
           "编辑可用，但运行流程时“输入屏蔽”不会生效。")
    return False
