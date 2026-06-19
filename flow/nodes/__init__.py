"""
flow.nodes —— 节点库。

导入本包即注册所有内置节点（各模块顶部用 @register 注册到全局表）。
分类：
    basic   —— 事件 / 数据常量 / 逻辑 / 算式 / 分支 / 变量 / 调试
    sense   —— 截图 / 模板匹配 / OCR / 像素 / 窗口检测 / 修饰键检测
    game    —— 三态遮挡检测 / 多TC计数 / 产能计算
    action  —— 按键 / 释放修饰键 / 鼠标点击
    control —— 整屏预取 / 定时门 / 输入屏蔽 / 文件锁 / 延时
"""
from . import basic, sense, game, action, control  # noqa: F401

__all__ = ["basic", "sense", "game", "action", "control"]
