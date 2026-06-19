"""
默认流程：普通出农（复刻当前 main.py 主循环）。

结构由 _common.build_single_type 按配置生成；本模块仅提供出农所需的参数。
流程骨架（执行流；[X?] 表示"检测节点 + 分支"）::

    每帧触发 -> [按住修饰键?] -> [游戏中?] -> [遮挡?] -> [渐变?] -> [队列有村民?]
      -> 整屏预取 -> [人口识别成功?] -> [食物足?] -> [有空位?]
      -> 获取操作锁 -> 输入屏蔽开始 -> 存编组(Ctrl+0) -> 释放修饰键 -> 选中所有TC(H)
      -> [检测到TC?] -> 排队出农(Shift+Q, 数量=产能) -> 恢复(0) -> 取消编组(Ctrl+Alt+0)
      -> 释放修饰键 -> 输入屏蔽结束 -> 延时 -> 释放操作锁
"""
from __future__ import annotations

from ..core import Graph
from ._common import default_cfg, build_single_type


def build_villager_graph() -> Graph:
    cfg = default_cfg()
    cfg["name"] = "普通出农"
    return build_single_type(cfg)
