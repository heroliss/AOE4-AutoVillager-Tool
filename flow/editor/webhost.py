"""
网页节点编辑器（LiteGraph.js + pywebview）的 Python 宿主。

职责：
- node_defs()：把节点注册表导出为 JS 可用的类型定义（端口/参数）。
- graph_to_payload() / payload_to_graph()：在"我们的 Graph(JSON)"与"编辑器载荷"间转换。
  关键：连线 exec/data 的区分由 Python 依据注册表的端口种类判定（前端只需报告"从哪个
  端口名连到哪个端口名"），因此这层逻辑可在无界面下单元测试。
- Api：暴露给 JS 的接口（取定义/取流程/保存/打开/内置列表）。
- launch()：用 pywebview 打开承载 LiteGraph 的本地页面。

参数在前端用文本/数值/开关/下拉控件编辑；区域/坐标/颜色/多模板等以字符串呈现，
保存时由 _param_from_js 依据 ParamSpec 类型回解析（与经典编辑器一致）。
"""
from __future__ import annotations

import os
from typing import Optional

from ..core import Graph, create_node, registry
from ..core.types import PortKind

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")


# ==================== 注册表 -> 前端类型定义 ====================
def node_defs() -> list[dict]:
    defs = []
    for type_id, cls in registry().items():
        defs.append({
            "type": type_id,
            "title": cls.title,
            "category": cls.category,
            "inputs": [{"name": p.name, "kind": p.kind.value, "dtype": p.dtype.value,
                        "label": p.display} for p in cls.inputs],
            "outputs": [{"name": p.name, "kind": p.kind.value, "dtype": p.dtype.value,
                         "label": p.display} for p in cls.outputs],
            "params": [{"key": s.key, "label": s.label, "ptype": s.ptype,
                        "default": _param_to_js_raw(s, s.default), "choices": s.choices,
                        "min": s.minimum, "max": s.maximum, "step": s.step} for s in cls.params],
        })
    return defs


def _port_kind_map() -> dict:
    """(type_id, out_port_name) -> 'exec'|'data'，用于把前端连线归类到 exec/data。"""
    m = {}
    for type_id, cls in registry().items():
        for p in cls.outputs:
            m[(type_id, p.name)] = p.kind.value
    return m


# ==================== 参数值 <-> 前端 ====================
def _param_to_js_raw(spec, v):
    """供前端控件显示：列表类（region/point/color/templates）转成字符串。"""
    if spec.ptype in ("region", "point", "color"):
        return ",".join(str(x) for x in (v or []))
    if spec.ptype == "templates":
        return ",".join(str(x) for x in (v or []))
    return v


def _param_from_js(spec, v):
    """前端回传 -> 我们的类型。"""
    t = spec.ptype
    if t == "int":
        try: return int(v)
        except (TypeError, ValueError): return spec.default
    if t == "float":
        try: return float(v)
        except (TypeError, ValueError): return spec.default
    if t == "bool":
        return bool(v)
    if t in ("region", "point", "color"):
        out = []
        for p in str(v).split(","):
            p = p.strip()
            if p == "":
                continue
            try: out.append(int(p))
            except ValueError:
                try: out.append(float(p))
                except ValueError: out.append(p)
        return out
    if t == "templates":
        return [p.strip() for p in str(v).replace("\n", ",").split(",") if p.strip()]
    return v  # enum / str / regex / key / keys / template


# ==================== Graph <-> 载荷 ====================
def graph_to_payload(graph: Graph) -> dict:
    nodes = []
    for nid, node in graph.nodes.items():
        params = {}
        for s in node.params:
            params[s.key] = _param_to_js_raw(s, node.values.get(s.key))
        nodes.append({"id": nid, "type": node.type_id,
                      "pos": list(graph.positions.get(nid, (0, 0))), "params": params})
    edges = [{"src": e.src_id, "src_port": e.src_port, "dst": e.dst_id, "dst_port": e.dst_port,
              "kind": "exec"} for e in graph.exec_edges]
    edges += [{"src": e.src_id, "src_port": e.src_port, "dst": e.dst_id, "dst_port": e.dst_port,
               "kind": "data"} for e in graph.data_edges]
    return {"name": graph.name, "nodes": nodes, "edges": edges}


def payload_to_graph(payload: dict) -> Graph:
    g = Graph(name=payload.get("name", "未命名流程"))
    reg = registry()
    for nd in payload.get("nodes", []):
        type_id = nd["type"]
        if type_id not in reg:
            continue
        node = create_node(type_id)
        specs = {s.key: s for s in node.params}
        for k, v in (nd.get("params") or {}).items():
            if k in specs:
                node.values[k] = _param_from_js(specs[k], v)
        pos = nd.get("pos", [0, 0])
        g.add(nd["id"], node, (pos[0], pos[1]))
    kinds = _port_kind_map()
    for e in payload.get("edges", []):
        src_type = next((n["type"] for n in payload["nodes"] if n["id"] == e["src"]), None)
        kind = e.get("kind") or kinds.get((src_type, e["src_port"]), "data")
        if kind == "exec":
            g.connect_exec(e["src"], e["src_port"], e["dst"], e["dst_port"])
        else:
            g.connect_data(e["src"], e["src_port"], e["dst"], e["dst_port"])
    return g


# ==================== pywebview Api ====================
class Api:
    def __init__(self):
        self.window = None
        self.graph: Optional[Graph] = None
        self.path: Optional[str] = None

    def get_defs(self):
        return node_defs()

    def get_flow(self):
        return graph_to_payload(self.graph) if self.graph else {"name": "未命名流程", "nodes": [], "edges": []}

    def list_builtin(self):
        d = "flows"
        if not os.path.isdir(d):
            return []
        return [f"flows/{f}" for f in sorted(os.listdir(d)) if f.endswith(".flow.json")]

    def open_path(self, path):
        if path and os.path.exists(path):
            self.graph = Graph.load(path)
            self.path = path
            return graph_to_payload(self.graph)
        return None

    def open_dialog(self):
        import webview
        res = self.window.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=False,
                                             file_types=("Flow (*.json)",))
        if res:
            return self.open_path(res[0])
        return None

    def autolayout(self, payload):
        from ..layout import layered_layout
        g = payload_to_graph(payload)
        layered_layout(g)
        self.graph = g
        return graph_to_payload(g)

    def save(self, payload):
        self.graph = payload_to_graph(payload)
        if not self.path:
            return self.save_as(payload)
        self.graph.save(self.path)
        return self.path

    def save_as(self, payload):
        import webview
        self.graph = payload_to_graph(payload)
        res = self.window.create_file_dialog(webview.SAVE_DIALOG, save_filename="flow.flow.json",
                                             file_types=("Flow (*.json)",))
        if res:
            path = res if isinstance(res, str) else res[0]
            self.graph.save(path)
            self.path = path
            return path
        return None


def launch(graph: Optional[Graph] = None, path: Optional[str] = None):
    import webview
    api = Api()
    api.graph = graph
    api.path = path
    index = os.path.join(WEB_DIR, "index.html")
    api.window = webview.create_window("AOE4 Flow Editor", url=index, js_api=api,
                                       width=1320, height=820)
    webview.start()
