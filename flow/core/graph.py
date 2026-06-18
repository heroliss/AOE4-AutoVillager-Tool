"""
图（Graph）：节点实例 + 连线，可序列化为 JSON。

连线分两类：
- exec_edges：执行流连线，(src_id, src_port) -> (dst_id, dst_port)
- data_edges：数据流连线，(src_id, src_port) -> (dst_id, dst_port)
  其中 src 为数据"输出"端口，dst 为数据"输入"端口；
  一个数据输入只能接一个来源，一个数据输出可扇出到多个输入。

JSON 结构::

    {
      "version": 1,
      "name": "普通出农",
      "nodes": [{"id","type","pos":[x,y],"params":{...}}, ...],
      "exec_edges": [[src_id, src_port, dst_id, dst_port], ...],
      "data_edges": [[src_id, src_port, dst_id, dst_port], ...]
    }
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

from .node import Node, create_node

SCHEMA_VERSION = 1


@dataclass
class Edge:
    src_id: str
    src_port: str
    dst_id: str
    dst_port: str

    def as_list(self) -> list[str]:
        return [self.src_id, self.src_port, self.dst_id, self.dst_port]


class Graph:
    def __init__(self, name: str = "") -> None:
        self.name = name
        self.nodes: dict[str, Node] = {}
        self.positions: dict[str, tuple[float, float]] = {}
        self.exec_edges: list[Edge] = []
        self.data_edges: list[Edge] = []

    # ==================== 构建 ====================
    def add(self, node_id: str, node: Node, pos: tuple[float, float] = (0.0, 0.0)) -> Node:
        if node_id in self.nodes:
            raise ValueError(f"重复的节点 id: {node_id}")
        self.nodes[node_id] = node
        self.positions[node_id] = pos
        return node

    def connect_exec(self, src_id: str, src_port: str, dst_id: str, dst_port: str = "in") -> None:
        self.exec_edges.append(Edge(src_id, src_port, dst_id, dst_port))

    def connect_data(self, src_id: str, src_port: str, dst_id: str, dst_port: str) -> None:
        # 一个数据输入只允许一个来源：覆盖已有连接
        self.data_edges = [
            e for e in self.data_edges
            if not (e.dst_id == dst_id and e.dst_port == dst_port)
        ]
        self.data_edges.append(Edge(src_id, src_port, dst_id, dst_port))

    # ==================== 查询 ====================
    def exec_target(self, src_id: str, src_port: str) -> Optional[tuple[str, str]]:
        for e in self.exec_edges:
            if e.src_id == src_id and e.src_port == src_port:
                return e.dst_id, e.dst_port
        return None

    def data_source(self, dst_id: str, dst_port: str) -> Optional[tuple[str, str]]:
        for e in self.data_edges:
            if e.dst_id == dst_id and e.dst_port == dst_port:
                return e.src_id, e.src_port
        return None

    def entry_id(self) -> Optional[str]:
        """返回入口节点 id（约定 type_id 以 'event.' 开头，如 event.on_tick）。"""
        for node_id, node in self.nodes.items():
            if node.type_id.startswith("event."):
                return node_id
        return None

    # ==================== 序列化 ====================
    def to_dict(self) -> dict:
        return {
            "version": SCHEMA_VERSION,
            "name": self.name,
            "nodes": [
                {
                    "id": node_id,
                    "type": node.type_id,
                    "pos": list(self.positions.get(node_id, (0.0, 0.0))),
                    "params": dict(node.values),
                }
                for node_id, node in self.nodes.items()
            ],
            "exec_edges": [e.as_list() for e in self.exec_edges],
            "data_edges": [e.as_list() for e in self.data_edges],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Graph":
        g = cls(name=data.get("name", ""))
        for nd in data.get("nodes", []):
            node = create_node(nd["type"])
            # 仅写入已声明的参数，忽略多余键，缺失键保留默认
            for k, v in nd.get("params", {}).items():
                if k in node.values:
                    node.values[k] = v
            pos = nd.get("pos", [0.0, 0.0])
            g.add(nd["id"], node, (pos[0], pos[1]))
        for e in data.get("exec_edges", []):
            g.connect_exec(e[0], e[1], e[2], e[3])
        for e in data.get("data_edges", []):
            g.connect_data(e[0], e[1], e[2], e[3])
        return g

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, path: str) -> "Graph":
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_dict(json.load(f))
