"""统一的资源路径锚点：区分【内置只读资源】与【用户可写数据】，并兼容 PyInstaller 打包。

为什么需要它：
- 打包(onefile)后，内置资源(flows / 网页前端 / 内置模板)被解压到临时目录 ``sys._MEIPASS``——
  只读、且每次启动路径都不同；用户数据(user_flows / 截取的模板)若也落在那里，程序一退出就随
  临时目录被删掉、跨启动还全丢。所以二者必须分开：
    * 内置只读资源  → ``INTERNAL_DIR`` (打包=_MEIPASS 解压目录；开发=项目根)
    * 用户可写数据  → ``APP_DIR``      (打包=exe 同目录；开发=项目根)
- 绝不要再用「相对当前工作目录(CWD)」定位这些资源：CWD 会随启动方式变化(VSCode 终端给小写盘符
  d:\\…，资源管理器给大写 D:\\…)，曾导致内置流程被大小写敏感的路径比较误判为可写而被覆盖。

开发期 INTERNAL_DIR == APP_DIR == 项目根，行为与从前完全一致。
"""
from __future__ import annotations

import os
import sys

if getattr(sys, "frozen", False):
    INTERNAL_DIR = sys._MEIPASS                       # 内置只读资源（打包解压目录）
    APP_DIR = os.path.dirname(sys.executable)         # 用户可写数据（exe 同目录，持久）
else:
    _ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # flow/paths.py → flow → 项目根
    INTERNAL_DIR = APP_DIR = _ROOT


def resolve_resource(path):
    """把可能是相对的资源路径解析成真实绝对路径(供读取/打开用)。

    规则：绝对路径原样返回；相对路径先在【用户可写目录 APP_DIR】下找(让用户的另存/截图能覆盖内置
    同名)，再到【内置目录 INTERNAL_DIR】找；都不存在则按 CWD 兜底(``os.path.abspath``)。
    开发期两根相同，等价于原来的「相对项目根」写法。"""
    if not path:
        return path
    if os.path.isabs(path):
        return path
    rel = path.replace("\\", "/")
    for base in (APP_DIR, INTERNAL_DIR):
        cand = os.path.join(base, rel)
        if os.path.exists(cand):
            return cand
    return os.path.abspath(path)
