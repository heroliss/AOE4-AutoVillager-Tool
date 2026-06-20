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
import time
from typing import Optional

from ..core import Graph, create_node, registry
from ..core.types import PortKind
from ..core.context import ExecutionContext
from ..core.executor import TraceExecutor


def _fmt_value(v):
    """把数据线上的值变成可在前端显示的简短形式（图像/大对象不直接传）。"""
    if v is None or isinstance(v, (bool, int, float)):
        return v
    if isinstance(v, str):
        return v if len(v) <= 48 else v[:48] + "…"
    if isinstance(v, (list, tuple)):
        items = [_fmt_value(x) for x in list(v)[:8]]
        return {"_list": items, "_more": max(0, len(v) - 8)}
    if hasattr(v, "shape"):                     # numpy 图像/数组
        try:
            return "<图像 " + "×".join(str(d) for d in v.shape[:2]) + ">"
        except Exception:
            return "<图像>"
    s = str(v)
    return s if len(s) <= 48 else s[:48] + "…"

# 试运行日志在内存中的封顶条数：长时间逐帧运行也不会无限增长占内存
# （本帧新增的日志会先单独取出回传前端，再裁剪历史）。
_RUN_LOG_CAP = 4000

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
# 内置流程目录（只读模板，随程序分发）与 用户自定义流程目录（用户的另存到这里）。
BUILTIN_FLOWS_DIR = os.path.abspath("flows")
USER_FLOWS_DIR = os.path.abspath("user_flows")
# 截模板的保存目录（与内置模板同目录，节点里按相对路径 templates/xxx.png 读取）。
TEMPLATES_DIR = os.path.abspath("templates")


# ==================== 注册表 -> 前端类型定义 ====================
def _doc_summary(cls) -> str:
    """取类文档字符串的首个非空行作为节点说明（给普通用户看的简介）。"""
    doc = (cls.__doc__ or "").strip()
    if not doc:
        return ""
    return doc.splitlines()[0].strip()


def _doc_full(cls) -> str:
    """完整文档字符串（去公共缩进），用于编辑器帮助面板的详细说明。"""
    import textwrap
    return textwrap.dedent(cls.__doc__ or "").strip()


def node_defs() -> list[dict]:
    defs = []
    for type_id, cls in registry().items():
        defs.append({
            "type": type_id,
            "title": cls.title,
            "category": cls.category,
            "help": _doc_summary(cls),
            "doc": _doc_full(cls),
            "inputs": [{"name": p.name, "kind": p.kind.value, "dtype": p.dtype.value,
                        "label": p.display, "help": p.help, "advanced": p.advanced} for p in cls.inputs],
            "outputs": [{"name": p.name, "kind": p.kind.value, "dtype": p.dtype.value,
                         "label": p.display, "help": p.help, "advanced": p.advanced} for p in cls.outputs],
            "params": [{"key": s.key, "label": s.label, "ptype": s.ptype,
                        "default": _param_to_js_raw(s, s.default), "choices": s.choices,
                        "min": s.minimum, "max": s.maximum, "step": s.step,
                        "help": s.help, "advanced": s.advanced} for s in cls.params],
        })
    return defs


_PORT_KIND_CACHE: Optional[dict] = None


def _port_kind_map() -> dict:
    """(type_id, out_port_name) -> 'exec'|'data'，用于把前端连线归类到 exec/data。
    注册表在运行期是静态的，缓存一次即可——run_update 在交互中会被频繁调用，别每次重建。"""
    global _PORT_KIND_CACHE
    if _PORT_KIND_CACHE is None:
        m = {}
        for type_id, cls in registry().items():
            for p in cls.outputs:
                m[(type_id, p.name)] = p.kind.value
        _PORT_KIND_CACHE = m
    return _PORT_KIND_CACHE


# ==================== 参数值 <-> 前端 ====================
# 修饰键：内部存 csv（"ctrl,shift"），前端下拉显示友好标签（"Ctrl+Shift"）。
_MOD_ORDER = ["ctrl", "shift", "alt", "win"]
_MOD_LABEL = {"ctrl": "Ctrl", "shift": "Shift", "alt": "Alt", "win": "Win"}


def _keys_to_label(v) -> str:
    if isinstance(v, (list, tuple)):
        toks = [str(x).strip().lower() for x in v if str(x).strip()]
    else:
        toks = [t.strip().lower() for t in str(v or "").split(",") if t.strip()]
    toks = [t for t in _MOD_ORDER if t in toks] + [t for t in toks if t not in _MOD_ORDER]
    return "+".join(_MOD_LABEL.get(t, t.capitalize()) for t in toks) if toks else "（无）"


def _label_to_keys(s) -> str:
    s = str(s or "").strip()
    if s in ("", "（无）", "(无)", "无"):
        return ""
    parts = [p.strip().lower() for p in s.replace("，", "+").replace(",", "+").split("+") if p.strip()]
    parts = [t for t in _MOD_ORDER if t in parts] + [t for t in parts if t not in _MOD_ORDER]
    return ",".join(parts)


def _param_to_js_raw(spec, v):
    """供前端控件显示：列表类（region/point/color/templates）转成字符串；keys 转友好标签。"""
    if spec.ptype in ("region", "point", "color"):
        return ",".join(str(x) for x in (v or []))
    if spec.ptype == "templates":
        return ",".join(str(x) for x in (v or []))
    if spec.ptype == "keys":
        return _keys_to_label(v)
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
    if t == "keys":
        return _label_to_keys(v)   # 友好标签 -> csv（"Ctrl+Shift" -> "ctrl,shift"）
    return v  # enum / str / regex / key / template


# ==================== Graph <-> 载荷 ====================
def graph_to_payload(graph: Graph) -> dict:
    nodes = []
    for nid, node in graph.nodes.items():
        params = {}
        for s in node.params:
            params[s.key] = _param_to_js_raw(s, node.values.get(s.key))
        nd = {"id": nid, "type": node.type_id,
              "pos": list(graph.positions.get(nid, (0, 0))), "params": params}
        if graph.notes.get(nid):
            nd["note"] = graph.notes[nid]
        nodes.append(nd)
    edges = [{"src": e.src_id, "src_port": e.src_port, "dst": e.dst_id, "dst_port": e.dst_port,
              "kind": "exec"} for e in graph.exec_edges]
    edges += [{"src": e.src_id, "src_port": e.src_port, "dst": e.dst_id, "dst_port": e.dst_port,
               "kind": "data"} for e in graph.data_edges]
    return {"name": graph.name, "description": graph.description,
            "panel": [list(x) for x in graph.panel],
            "groups": [dict(x) for x in graph.groups],
            "nodes": nodes, "edges": edges}


def payload_to_graph(payload: dict) -> Graph:
    g = Graph(name=payload.get("name", "未命名流程"), description=payload.get("description", ""))
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
        if nd.get("note"):
            g.notes[nd["id"]] = nd["note"]
    # 面板置顶项：仅保留指向现存节点的 [node_id, key]
    g.panel = [list(p) for p in payload.get("panel", []) if len(p) >= 2 and p[0] in g.nodes]
    # 分组：成员只保留现存节点；丢弃空组
    for gr in payload.get("groups", []):
        members = [m for m in (gr.get("members") or []) if m in g.nodes]
        if members:
            g.groups.append({"title": gr.get("title", "分组"),
                             "color": gr.get("color", ""), "members": members})
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
    # 注意：对象型成员一律用下划线前缀。pywebview 注入 window.pywebview.api 时会用
    # dir()+getattr() 递归遍历本对象的所有"非下划线"属性去找可调用方法；若把 Window
    # （其 .native 是 WinForms .NET 控件）或 Graph 暴露为公开属性，它会递归爬整个 .NET
    # 对象图（AccessibilityObject.Bounds.Empty.Empty… 无限递归），既慢（卡死）又拖慢
    # api 就绪（偶发 get_defs is not a function）。下划线前缀让 pywebview 跳过它们。
    def __init__(self):
        self._window = None
        self._graph: Optional[Graph] = None
        self._path: Optional[str] = None
        self._dirty = False        # 前端镜像过来的“有未保存修改”，关闭窗口时据此弹确认
        # —— 编辑器内“运行可视化”（干跑：只读屏/识别、不发按键鼠标），由前端逐帧轮询驱动 ——
        self._run_graph: Optional[Graph] = None
        self._run_ctx: Optional[ExecutionContext] = None
        self._run_exec: Optional[TraceExecutor] = None
        self._run_logs: list = []
        import threading
        self._run_lock = threading.RLock()   # run_tick / run_update / begin / end 互斥（前端不同线程过桥）

    def get_defs(self):
        return node_defs()

    def set_dirty(self, flag):
        """前端在 ●未保存 状态变化时调用，使关闭窗口能弹保存确认。"""
        self._dirty = bool(flag)
        return True

    # ==================== 运行可视化（干跑，逐帧）====================
    def run_begin(self, payload, real=False):
        """用当前编辑器里的图开一次运行。
        real=False(默认)＝干跑：只识别、不发任何输入；real=True＝真跑：真正发按键/鼠标。"""
        with self._run_lock:
            self._run_graph = payload_to_graph(payload)
            self._run_logs = []

            def _log(level, message, node_id=None):
                self._run_logs.append({"level": level, "msg": message, "node": node_id})

            self._run_ctx = ExecutionContext(on_log=_log, dry_run=(not real))
            self._run_exec = TraceExecutor(self._run_graph)
            self._run_logs.append({
                "level": "WARN" if real else "INFO",
                "msg": ("⚠ 真跑模式：将真正向游戏发送按键/鼠标操作" if real
                        else "干跑模式：只识别、不发送任何输入"),
                "node": None})
            return {"ok": True, "nodes": len(self._run_graph.nodes), "real": bool(real)}

    def run_update(self, payload):
        """运行中热更新：参数改值【且】结构(增删节点/改连线)也实时同步——

        - 仍存在且类型不变的节点：复用原节点对象（保留其内部状态/记忆，如三态遮挡的历史）。
        - 新增的节点：新建；删除的：移除。
        - 连线整体按载荷重建（连线无状态）。黑板变量(ctx.vars)与帧序号(tick_index)保持不变。
        与 run_tick 用同一把锁，避免跑帧中途结构被改而读到半成品。
        """
        if not self._run_graph:
            return False
        with self._run_lock:
            g = self._run_graph
            if g is None:                       # 期间被 run_end 结束了
                return False
            reg = registry()
            want = {nd["id"]: nd for nd in payload.get("nodes", []) if nd.get("type") in reg}
            # 删除：图里有、载荷里没有的节点
            for nid in [n for n in g.nodes if n not in want]:
                g.nodes.pop(nid, None)
                g.positions.pop(nid, None)
            # 新增 / 复用并更新参数
            for nid, nd in want.items():
                node = g.nodes.get(nid)
                if node is None or node.type_id != nd["type"]:
                    node = create_node(nd["type"])
                    g.nodes[nid] = node
                    pos = nd.get("pos", [0, 0])
                    g.positions[nid] = (pos[0], pos[1])
                specs = {s.key: s for s in node.params}
                for k, v in (nd.get("params") or {}).items():
                    if k in specs:
                        node.values[k] = _param_from_js(specs[k], v)
            # 连线整体重建（exec/data 由注册表端口种类判定，与 payload_to_graph 一致）
            g.exec_edges = []
            g.data_edges = []
            kinds = _port_kind_map()
            type_of = {nd["id"]: nd.get("type") for nd in payload.get("nodes", [])}
            for e in payload.get("edges", []):
                if e["src"] not in g.nodes or e["dst"] not in g.nodes:
                    continue
                kind = e.get("kind") or kinds.get((type_of.get(e["src"]), e["src_port"]), "data")
                if kind == "exec":
                    g.connect_exec(e["src"], e["src_port"], e["dst"], e["dst_port"])
                else:
                    g.connect_data(e["src"], e["src_port"], e["dst"], e["dst_port"])
        return True

    def run_tick(self):
        """跑一帧，返回执行轨迹 + 数据线上的值 + 本帧日志。前端据此高亮/显示。"""
        with self._run_lock:
            if not (self._run_exec and self._run_ctx):   # 还没开始 / 期间被 run_end 结束了
                return None
            before = len(self._run_logs)
            try:
                self._run_exec.run_tick(self._run_ctx, dt=0.0)
            except Exception as e:  # 单帧异常不致命：报到日志，让前端继续/停止
                self._run_logs.append({"level": "ERROR", "msg": f"运行异常：{e}", "node": None})
            new_logs = self._run_logs[before:]
            # 本帧新增日志已取出；历史只保留最近 N 条，避免长时间运行无限增长占内存
            if len(self._run_logs) > _RUN_LOG_CAP:
                del self._run_logs[: len(self._run_logs) - _RUN_LOG_CAP]
            data = {}
            for (nid, port), val in self._run_ctx.memo_snapshot().items():
                data[nid + "" + port] = _fmt_value(val)   # 用 0x01 分隔 node_id 与 port
            return {
                "tick": self._run_ctx.tick_index,
                "path": list(self._run_exec.trace_path),
                "ports": dict(self._run_exec.trace_ports),
                "data": data,
                "logs": self._run_logs[before:],
            }

    def run_end(self):
        """结束试运行：清理可能持有的输入屏蔽/锁（干跑下只是清标记）。"""
        with self._run_lock:
            if self._run_ctx:
                try:
                    self._run_ctx.cleanup_tick()
                except Exception:
                    pass
            self._run_graph = self._run_ctx = self._run_exec = None
            self._run_logs = []
            return True

    def _payload(self, graph):
        """流程载荷 + 元信息（当前文件路径、是否内置只读），供前端显示文件来源与只读提示。"""
        p = graph_to_payload(graph) if graph else {"name": "未命名流程", "description": "", "nodes": [], "edges": []}
        p["path"] = self._path
        p["readonly"] = self._is_builtin(self._path)
        return p

    def get_flow(self):
        return self._payload(self._graph)

    @staticmethod
    def _flow_name(path):
        """只读出流程文件里的 name（中文流程名），失败则返回空串。"""
        try:
            import json
            with open(path, "r", encoding="utf-8") as fp:
                return (json.load(fp).get("name") or "").strip()
        except Exception:
            return ""

    def list_builtin(self):
        # 同时列出内置流程(flows/)与用户另存的流程(user_flows/)；返回 {path, name} 供前端按中文名显示。
        out = []
        for d in ("flows", "user_flows"):
            if os.path.isdir(d):
                for f in sorted(os.listdir(d)):
                    if f.endswith(".flow.json"):
                        full = os.path.join(d, f)
                        out.append({"path": f"{d}/{f}",
                                    "name": self._flow_name(full) or f[:-len(".flow.json")]})
        return out

    def _is_builtin(self, path):
        """该路径是否在内置流程目录下（内置=只读，保存时改为另存，避免被覆盖）。"""
        try:
            return os.path.abspath(path).startswith(BUILTIN_FLOWS_DIR + os.sep)
        except Exception:
            return False

    def open_path(self, path):
        if path and os.path.exists(path):
            self._graph = Graph.load(path)
            self._path = path
            return self._payload(self._graph)
        return None

    def open_dialog(self):
        import webview
        res = self._window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=False,
                                              file_types=("Flow (*.json)",))
        if res:
            return self.open_path(res[0])
        return None

    def pick_templates(self, multiple=True):
        """选择一个或多个模板图片，返回路径列表（供 template/templates 参数填入）。

        与可正常工作的 open_dialog 用同一条路（pywebview 自带 create_file_dialog）。
        注意：file_types 只用【一项】——之前加了 "所有文件 (*.*)" 这第二项时点击没反应，
        改回单项即恢复正常。
        """
        import webview
        os.makedirs(TEMPLATES_DIR, exist_ok=True)
        res = self._window.create_file_dialog(
            webview.FileDialog.OPEN, allow_multiple=bool(multiple),
            directory=TEMPLATES_DIR,   # 默认定位到模板目录（模板大多放这里）
            file_types=("图片 (*.png;*.jpg;*.jpeg;*.bmp;*.gif)",))
        if not res:
            return []
        return list(res) if isinstance(res, (list, tuple)) else [res]

    def image_data_url(self, path):
        """把模板图片读成 data URL（base64），供前端在节点上画缩略图预览。

        WebView2 出于安全不让页面随意读本地任意文件，所以由 Python 读字节再回传。
        找不到/读失败返回空串（前端据此不画预览）。
        """
        import base64
        try:
            p = path if os.path.isabs(path) else os.path.abspath(path)
            if not os.path.isfile(p):
                return ""
            ext = os.path.splitext(p)[1].lower().lstrip(".") or "png"
            mime = {"jpg": "jpeg"}.get(ext, ext)
            with open(p, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
            return f"data:image/{mime};base64,{b64}"
        except Exception:
            return ""

    # ---------- 采集工具（框选/取点/吸色/截模板/捕获按键）----------
    def _capture(self, fn):
        """统一外壳：采集前把编辑器让开（最小化→等游戏重绘→抓屏），采完再恢复。

        采集覆盖层是 topmost 全屏窗，盖住一切；要点只是抓屏那一刻编辑器别挡着游戏。
        fn 在本（worker）线程内自建 Tk root 跑 mainloop，阻塞到覆盖层关闭再返回。
        """
        win = self._window
        try:
            if win:
                try:
                    win.minimize()
                    time.sleep(0.35)  # 等最小化动画结束 + 游戏画面重绘出来再抓屏
                except Exception:
                    pass  # 最小化失败也继续采集（最多是编辑器没让开，覆盖层仍置顶）
            return fn()
        finally:
            try:
                if win:
                    win.restore()
            except Exception:
                pass

    def pick_region(self):
        from . import capture
        return self._capture(capture.pick_region)

    def pick_point(self):
        from . import capture
        return self._capture(capture.pick_point)

    def pick_color(self):
        from . import capture
        return self._capture(capture.pick_color)

    def pick_key(self):
        from . import capture
        # 捕获按键不抓屏、不必让开编辑器，但仍走 worker 线程自建 Tk root。
        return capture.capture_key()

    def capture_template(self):
        from . import capture
        os.makedirs(TEMPLATES_DIR, exist_ok=True)
        return self._capture(lambda: capture.capture_template(TEMPLATES_DIR))

    def autolayout(self, payload):
        from ..layout import mainline_layout
        g = payload_to_graph(payload)
        mainline_layout(g)
        self._graph = g
        return self._payload(g)

    def save(self, payload):
        self._graph = payload_to_graph(payload)
        # 没有路径，或当前是内置流程 -> 一律走"另存为"（内置只读，避免覆盖随程序分发的模板）。
        if not self._path or self._is_builtin(self._path):
            return self.save_as(payload)
        self._graph.save(self._path)
        return self._path

    def save_as(self, payload):
        import webview
        self._graph = payload_to_graph(payload)
        os.makedirs(USER_FLOWS_DIR, exist_ok=True)
        # 默认存到用户目录、用原文件名（内置改名另存就不会动到内置）。
        default_name = os.path.basename(self._path) if self._path else "我的流程.flow.json"
        res = self._window.create_file_dialog(webview.FileDialog.SAVE, directory=USER_FLOWS_DIR,
                                              save_filename=default_name, file_types=("Flow (*.json)",))
        if res:
            path = res if isinstance(res, str) else res[0]
            self._graph.save(path)
            self._path = path
            return path
        return None


def launch(graph: Optional[Graph] = None, path: Optional[str] = None):
    import webview
    api = Api()
    api._graph = graph
    api._path = path
    index = os.path.join(WEB_DIR, "index.html")
    api._window = webview.create_window("AOE4 Flow Editor", url=index, js_api=api,
                                        width=1320, height=820)

    # 关闭窗口时，若有未保存修改则弹原生确认（返回 False 取消关闭）。前端通过 set_dirty 同步脏标记。
    def _confirm_close():
        if not getattr(api, "_dirty", False):
            return True
        try:
            return bool(api._window.create_confirmation_dialog(
                "未保存的修改", "当前流程有未保存的修改，确定退出吗？\n（取消可返回编辑器再保存）"))
        except Exception:
            return True   # 对话框不可用就不阻拦关闭
    try:
        api._window.events.closing += _confirm_close
    except Exception:
        pass
    # 前端通过 js_api 轮询拉取启动数据（pywebview 注入 api 有延迟，前端会等到就绪再拉），
    # 不再用 evaluate_js 主动 push —— 那条路在 WebView2 上有跨线程 COM 报错且不稳定。
    debug = os.environ.get("AOE4_EDITOR_DEBUG") == "1"  # 置 1 可开 WebView2 开发者工具
    webview.start(debug=debug)
