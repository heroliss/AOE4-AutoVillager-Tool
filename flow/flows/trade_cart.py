"""
默认流程：市场出商人。

与出农同构，差异仅在参数：占用检测换成商人图标、资源换成黄金、按键换成
选市场键/出商人键、按单一市场计数（不做多建筑计数）。

注意：以下区域/模板/按键为占位默认值，需用户用编辑器的"框选/吸色/截取"工具
按自己的分辨率与界面填入真实值（尤其 templates/trade_cart.png 需自行截取）。
"""
from __future__ import annotations

from ..core import Graph
from ._common import default_cfg, build_single_type


def build_trade_cart_graph() -> Graph:
    cfg = default_cfg()
    cfg.update({
        "name": "市场出商人",
        # 队列占用：检测商人图标（占位，需自行截取模板）
        "queue_region": [10, 970, 500, 1025],
        "occupancy_templates": ["templates/trade_cart.png"],
        # 资源换成黄金（区域为占位）
        "resource_region": [50, 1180, 140, 1206],
        "resource_regex": r"(\d+)",
        "min_resource": 100,
        "res_key": "gold",            # 商人吃黄金
        "cost_per_unit": 100,         # 每个商人所需黄金（占位）
        "per_building": 5,            # 每个市场排队数量
        "building": None,             # 按单一市场，不做多建筑计数
        "select_key": "g",           # 选中市场的快捷键（占位，需游戏内设置）
        "queue_key": "q",            # 出商人快捷键（占位）
    })
    return build_single_type(cfg)
