"""
flow.nodes —— 节点库。

导入本包即注册所有内置节点（通过各模块顶部的 @register）。
后续会按类别拆分模块：basic（控制/逻辑/算式/调试）、sense（截图/匹配/OCR/像素）、
action（按键/鼠标）、game（窗口检测/三态遮挡/多TC计数）等。
"""
from . import basic  # noqa: F401

__all__ = ["basic"]
