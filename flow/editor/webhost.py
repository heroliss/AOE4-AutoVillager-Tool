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
import threading
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
# data 键里 node_id 与 port 的分隔符：与前端 String.fromCharCode(1) 一致，渲染不可见。
_RUNSEP = "\x01"

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
# 内置流程目录（只读模板，随程序分发）与 用户自定义流程目录（用户的另存到这里）。
BUILTIN_FLOWS_DIR = os.path.abspath("flows")
USER_FLOWS_DIR = os.path.abspath("user_flows")
# 截模板的保存目录（与内置模板同目录，节点里按相对路径 templates/xxx.png 读取）。
TEMPLATES_DIR = os.path.abspath("templates")


def _to_rel_path(path):
    """文件在工程目录内时转成相对路径(正斜杠，如 templates/xxx.png)，便于跨机器/打包；
    工程目录之外则原样保留绝对路径。"""
    if not path:
        return path
    try:
        base = os.path.dirname(TEMPLATES_DIR)   # 工程根 = templates 的上级目录
        rel = os.path.relpath(os.path.abspath(path), base)
        return path if rel.startswith("..") else rel.replace("\\", "/")
    except Exception:
        return path
# 记住上次打开的流程，下次启动自动载入（让编辑器更像“成品工具”：开机即用）。
# 会话状态存到 %APPDATA%（见 user_settings），不再放程序目录——避免污染仓库 / 重装丢失 / 权限问题。
import user_settings

_OLD_STATE_FILE = os.path.abspath(".editor_state.json")   # 旧版状态文件，仅用于一次性迁移


def _save_last_flow(path):
    if path:
        user_settings.update_settings(last_flow=os.path.abspath(path))


def _load_last_flow():
    p = user_settings.get_setting("last_flow")
    if not p:   # 迁移旧的程序目录状态文件（.editor_state.json）到 %APPDATA%
        try:
            if os.path.exists(_OLD_STATE_FILE):
                import json
                with open(_OLD_STATE_FILE, "r", encoding="utf-8") as fp:
                    p = json.load(fp).get("last_flow")
                if p:
                    user_settings.update_settings(last_flow=os.path.abspath(p))
        except Exception:
            p = None
    return p if p and os.path.exists(p) else None


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
            "foldparams": [list(x) for x in getattr(graph, "foldparams", [])],
            "groupexpose": [list(x) for x in getattr(graph, "groupexpose", [])],
            "labels": dict(getattr(graph, "labels", {}) or {}),
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
    # “显示到折叠节点”的参数：仅保留指向现存节点的 [node_id, key]
    g.foldparams = [list(p)[:2] for p in payload.get("foldparams", []) if len(p) >= 2 and p[0] in g.nodes]
    # 组“再向上一级暴露”的参数 [group_id, node_id, key]：仅保留指向现存节点的
    g.groupexpose = [list(p)[:3] for p in payload.get("groupexpose", []) if len(p) >= 3 and p[1] in g.nodes]
    # 参数自定义显示名 {"node_id|key": "名"}：仅保留指向现存节点的（面板/折叠箱体共用）
    g.labels = {k: str(v) for k, v in (payload.get("labels") or {}).items()
                if isinstance(k, str) and v and k.split("|", 1)[0] in g.nodes}
    # 分组（容器树）：直接成员只保留现存节点。支持空组——保留所有组（含无成员的空组），存盘/重开后仍在。
    _grp_raw = payload.get("groups", [])
    for gr in _grp_raw:
        members = [m for m in (gr.get("members") or []) if m in g.nodes]
        g.groups.append({"id": gr.get("id"),
                         "title": gr.get("title", "分组"),
                         "color": gr.get("color", ""),
                         "collapsed": bool(gr.get("collapsed", False)),   # 可折叠子图：保留折叠态，存盘/重开后仍折叠
                         "parent": gr.get("parent"),
                         "members": members,
                         "desc": gr.get("desc", ""),   # 分组描述（仅展示）
                         "pos": gr.get("pos"),     # 空组兜底定位（有成员时由前端按包围盒每帧刷新）
                         "size": gr.get("size")})
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
        self._change_summary = ""  # 前端镜像过来的“本次改动清单”文本，关闭确认框里展示详情
        # —— 编辑器内“运行可视化” —— 引擎在【常驻后台线程】里自行全速跑（不被前端节奏拖慢），
        #    前端只轻量轮询 run_poll 取最近一帧轨迹 + 增量日志，UI 不影响底层执行速度。
        self._run_graph: Optional[Graph] = None
        self._run_ctx: Optional[ExecutionContext] = None
        self._run_exec: Optional[TraceExecutor] = None
        self._run_logs: list = []            # 全量历史（封顶 _RUN_LOG_CAP），仅引擎线程写
        self._run_lock = threading.RLock()   # 保护图结构：引擎跑帧 vs run_update 改图 互斥
        self._snap_lock = threading.Lock()   # 保护“最近一帧快照 + 待取日志 + 断点集”，引擎/轮询短暂占用
        self._run_thread: Optional[threading.Thread] = None
        self._run_paused = True              # 引擎线程是否暂停（创建即暂停，等前端 run_resume）
        self._run_stop = False               # 引擎线程退出标志
        self._run_real = False               # 当前会话是否真跑（发输入）
        self._run_interval = 0.1             # 每帧间隔(秒)，取自流程「每帧触发」节点；run_update 时刷新
        self._run_bps: set = set()           # 断点节点 id 集（命中即自停，精确不依赖前端轮询）
        self._run_until: Optional[str] = None  # “运行到此节点”一次性目标
        self._run_profile = False            # 性能监控：开启后每帧附带各节点「自身/累计」耗时
        self._run_preview = False            # 截图预览：开启后每帧附带各感知节点「截到的区域图」(base64 PNG)
        self._latest: Optional[dict] = None  # 最近一帧轨迹快照（path/ports/data/tick）
        self._pending_logs: list = []        # 自上次轮询以来的新日志（取走即清）
        self._bp_hit: Optional[str] = None   # 本次暂停命中的断点节点（供前端提示）
        self._sysmon_proc = None             # 资源监控：缓存本进程的 psutil.Process（cpu_percent 需要复用同一对象建基准）
        self._sysmon_game = {}               # 游戏进程监控：pid -> psutil.Process（找到即缓存复用，省 process_iter）
        self._sysmon_game_scan = 0.0         # 上次扫描新游戏进程的时刻（每~2s 扫一次）
        self._mon_window = None              # 独立「资源监控」浮窗（单独系统窗口；下划线前缀避免 js_api 递归爬 .NET）
        self._overlay_window = None          # 游戏内覆盖层（无边框/透明/置顶/毛玻璃；单独系统窗口）
        self._overlay_cfg_window = None      # 覆盖层【设置】小窗（同款玻璃；调背景/内容透明度/毛度）
        self._overlay_bg_alpha = 55          # 覆盖层【背景】着色不透明度 0~255（毛玻璃 tint alpha；0=背景全透；以透明为主故默认很淡）
        self._overlay_content_alpha = 100    # 覆盖层【内容】不透明度 30~100（文字等；与背景独立）
        self._overlay_frost = 1              # 毛度：0=无(透明渐变) 1=轻(模糊) 2=重(亚克力)；默认只一点点
        self._overlay_rect = [0, 0, 200, 22] # [x, y, w, h] 当前几何（自适应缩放/拖拽时更新；供前端钳制不出屏）
        self._overlay_screen = [1920, 1080]  # 屏幕逻辑尺寸（拖拽钳制用）
        self._overlay_user_moved = False     # 用户是否手动拖过；没拖过则自适应缩放时保持顶端居中

    def get_defs(self):
        return node_defs()

    # ==================== 资源监控（编辑器角落的小窗轮询）====================
    def sys_stats(self):
        """采样本工具进程与系统的 CPU / 内存，供编辑器角落的监控小窗显示。

        前端约每秒轮询一次；cpu_percent(None) 返回「上次调用至今」的占用率（首次为 0，
        因此构造时先预热一次建立基准点）。psutil 读的是系统性能计数器、非线程亲和资源，
        故跨 pywebview 线程复用同一个 Process 对象是安全的（不像 mss 的 GDI DC）。
        """
        try:
            import psutil
        except Exception:
            return {"ok": False}
        try:
            if self._sysmon_proc is None:
                self._sysmon_proc = psutil.Process(os.getpid())
                self._sysmon_proc.cpu_percent(None)   # 预热：建立基准，下次调用才有意义
                psutil.cpu_percent(None)
            ncpu = psutil.cpu_count() or 1
            tool_cpu = self._sysmon_proc.cpu_percent(None) / ncpu   # 归一到「占整机」0~100%
            tool_mem = self._sysmon_proc.memory_info().rss / 1048576.0
            vm = psutil.virtual_memory()
            # —— 游戏进程 CPU/内存（沿用经典版：按 exe 名匹配 AOE4，缓存进程对象复用）——
            game_names = ("ageofempires4.exe", "age4_x64.exe", "reliccardinal.exe")
            now = time.time()
            if now - self._sysmon_game_scan > 2.0:   # 每~2s 重扫一次新进程；已知的继续复用，避免每次 process_iter
                self._sysmon_game_scan = now
                known = set(self._sysmon_game)
                for pr in psutil.process_iter(["name"]):
                    try:
                        if (pr.info.get("name") or "").lower() in game_names and pr.pid not in known:
                            pr.cpu_percent(None)            # 预热：首次返回 0
                            self._sysmon_game[pr.pid] = pr
                    except Exception:
                        pass
            game_cpu, game_mem, alive = 0.0, 0.0, {}
            for pid, pr in self._sysmon_game.items():
                try:
                    game_cpu += pr.cpu_percent(None) / ncpu
                    game_mem += pr.memory_info().rss / 1048576.0
                    alive[pid] = pr
                except Exception:
                    pass                                    # 进程已退出 → 丢弃
            self._sysmon_game = alive
            return {
                "ok": True,
                "tool_cpu": round(tool_cpu, 1),
                "tool_mem": round(tool_mem, 1),
                "game_cpu": round(game_cpu, 1),
                "game_mem": round(game_mem, 1),
                "game_running": bool(alive),
                "sys_cpu": round(psutil.cpu_percent(None), 1),
                "sys_used_pct": round(vm.percent, 1),
                "sys_avail": round(vm.available / 1048576.0, 1),
                "ncpu": ncpu,
            }
        except Exception:
            return {"ok": False}

    def set_dirty(self, flag):
        """前端在 ●未保存 状态变化时调用，使关闭窗口能弹保存确认。"""
        self._dirty = bool(flag)
        return True

    def set_change_summary(self, text):
        """前端推来的“本次改动清单”文本（summarizeChanges 的内容），关闭窗口确认时展示，
        让退出确认与编辑器内的「修改变化详情」窗口同源、一样详细。"""
        self._change_summary = str(text or "")
        return True

    @staticmethod
    def _form_set(native, attr, value):
        """跨线程安全地设置原生 WinForms 窗体属性（TopMost / Opacity 等）——投递到 UI 线程执行。

        ⚠ js_api 处理器跑在【工作线程】上（pywebview 每次调用新开线程）。直接在工作线程上写
        .NET 控件属性（如 Form.TopMost）会触发窗口句柄重建、与 WebView2 消息循环互等死锁——
        表现就是「点一下就卡死」。pywebview 自带的 set_on_top 恰恰没切回 UI 线程，故这里统一用
        原生 Form.BeginInvoke 把改动异步投递到 UI 线程（立即返回、不阻塞工作线程）。"""
        if native is None or not hasattr(native, "InvokeRequired"):
            return False
        try:
            from System import Action

            def _apply():
                try:
                    setattr(native, attr, value)
                except Exception:
                    pass
            if native.InvokeRequired:
                native.BeginInvoke(Action(_apply))
            else:
                _apply()
            return True
        except Exception:
            return False

    def set_on_top(self, flag):
        """主编辑器窗口置顶（保留备用；资源监控已改为独立窗口，见 toggle_monitor）。"""
        w = self._window
        return self._form_set(getattr(w, "native", None), "TopMost", bool(flag)) if w else False

    # ==================== 独立「资源监控」浮窗（单独系统窗口）====================
    # 监控做成另一个 OS 窗口：只它能置顶、可拖到屏幕任意处（含游戏上方/副屏）、原生缩放，
    # 主编辑器不受影响（解决“整个编辑器被一起置顶 / 监控拖不出主窗”）。
    def toggle_monitor(self):
        """开/关独立资源监控窗口；返回 {open: bool}。供主编辑器右上角迷你窗点击调用。"""
        if self._mon_window is not None:
            try:
                self._mon_window.destroy()
            except Exception:
                pass
            self._mon_window = None
            return {"open": False}
        return self._open_monitor()

    def _open_monitor(self):
        import webview
        page = os.path.join(WEB_DIR, "sysmon.html")
        kw = dict(width=360, height=430, js_api=self, on_top=True, min_size=(260, 300))
        try:                                   # 默认摆到主屏右上角
            scr = webview.screens[0]
            kw["x"] = max(0, int(scr.width) - 392)
            kw["y"] = 64
        except Exception:
            pass
        try:
            self._mon_window = webview.create_window("资源监控", url=page, **kw)
            try:
                self._mon_window.events.closed += self._on_monitor_closed
            except Exception:
                pass
            return {"open": True}
        except Exception as e:
            self._mon_window = None
            return {"open": False, "reason": str(e)}

    def _on_monitor_closed(self):
        self._mon_window = None

    def mon_set_on_top(self, flag):
        """资源监控窗口自己的置顶开关（只影响它，不动主编辑器）。"""
        w = self._mon_window
        return self._form_set(getattr(w, "native", None), "TopMost", bool(flag)) if w else False

    def mon_set_opacity(self, level):
        """level: 0=不透明 1=70% 2=40%。设原生 Form.Opacity 做真·半透明（透出后面的游戏/桌面）。"""
        op = {0: 1.0, 1: 0.7, 2: 0.4}.get(int(level or 0), 1.0)
        w = self._mon_window
        return self._form_set(getattr(w, "native", None), "Opacity", float(op)) if w else False

    # ==================== 游戏内覆盖层（共用「玻璃」窗口技术）====================
    # 无边框 + 透明 + 置顶 + Windows 亚克力毛玻璃的独立系统窗口，悬在游戏上方。
    # 背景透明度 = 亚克力 tint 的 alpha；内容透明度由页面 CSS 单独控制（两者独立）。
    # 默认摆成贴屏幕顶边的窄条（左1/8~右7/8、高≤40px），中间主视野不被遮挡。
    @staticmethod
    def _form_invoke(native, fn):
        """把任意函数投递到原生窗体的 UI 线程执行（亚克力等 Win32 调用需在创建窗口的线程上做）。"""
        if native is None or not hasattr(native, "InvokeRequired"):
            try:
                fn()
            except Exception:
                pass
            return
        try:
            from System import Action
            if native.InvokeRequired:
                native.BeginInvoke(Action(fn))
            else:
                fn()
        except Exception:
            try:
                fn()
            except Exception:
                pass

    @staticmethod
    def _apply_acrylic(native, accent_state, tint_argb):
        """对原生窗口启用 Windows 亚克力/模糊（毛玻璃），透出并模糊背后的游戏画面。
        accent_state: 0=禁用 3=普通模糊 4=亚克力；tint_argb: 0xAARRGGBB（A=背景着色不透明度）。
        失败静默返回 False（降级为不模糊的普通透明窗，功能不受影响）。"""
        if native is None:
            return False
        try:
            import ctypes
            from ctypes import Structure, POINTER, c_int, c_uint, c_void_p, pointer, byref, sizeof
            hwnd = int(native.Handle.ToInt64())

            class ACCENTPOLICY(Structure):
                _fields_ = [("AccentState", c_int), ("AccentFlags", c_int),
                            ("GradientColor", c_uint), ("AnimationId", c_int)]

            class WINCOMPATTRDATA(Structure):
                _fields_ = [("Attribute", c_int), ("Data", POINTER(ACCENTPOLICY)), ("SizeOfData", c_int)]

            a = (tint_argb >> 24) & 0xFF; r = (tint_argb >> 16) & 0xFF
            g = (tint_argb >> 8) & 0xFF; b = tint_argb & 0xFF
            gradient = (a << 24) | (b << 16) | (g << 8) | r   # SetWindowCompositionAttribute 用 ABGR
            accent = ACCENTPOLICY(int(accent_state), 2, gradient, 0)   # flags=2 → GradientColor 生效
            data = WINCOMPATTRDATA(19, pointer(accent), sizeof(accent))  # 19 = WCA_ACCENT_POLICY
            ok = ctypes.windll.user32.SetWindowCompositionAttribute(c_void_p(hwnd), byref(data))
            return bool(ok)
        except Exception:
            return False

    # 毛度→AccentState：0无=透明渐变(2)，1轻/2重=亚克力(4)。
    # 注意：故意【不用】状态3(ACCENT_ENABLE_BLURBEHIND)——它在 Win10/11 上会把背景渲染成黑色（已知坑）。
    _FROST_STATE = {0: 2, 1: 4, 2: 4}

    def _glass(self, win, frost, alpha):
        """给任意覆盖窗上玻璃：frost 决定档（无=只透明着色 / 轻·重=亚克力），alpha=背景着色不透明度。
        重档把着色调实一些（亚克力本身模糊半径由系统定、不可调，用着色浓淡区分轻/重）。"""
        if win is None:
            return
        native = getattr(win, "native", None)
        frost = int(frost)
        state = self._FROST_STATE.get(frost, 4)
        a = max(int(alpha), 140) if frost == 2 else int(alpha)   # 重：着色更浓、更“毛”
        tint = ((a & 0xFF) << 24) | 0x35435C   # 中性蓝灰着色（偏淡、不发黑）
        self._form_invoke(native, lambda: self._apply_acrylic(native, state, tint))

    def _overlay_glass(self):
        """主窄条上玻璃。窗口 shown 后调用（句柄已就绪）。"""
        self._glass(self._overlay_window, self._overlay_frost, self._overlay_bg_alpha)

    def toggle_overlay(self):
        """开/关游戏内覆盖层窗口；返回 {open: bool}。"""
        if self._overlay_window is not None:
            try:
                self._overlay_window.destroy()
            except Exception:
                pass
            self._overlay_window = None
            return {"open": False}
        return self._open_overlay()

    def _open_overlay(self):
        import webview
        page = os.path.join(WEB_DIR, "overlay.html")
        try:
            scr = webview.screens[0]; sw = int(scr.width); sh = int(scr.height)
        except Exception:
            sw, sh = 1920, 1080
        self._overlay_screen = [sw, sh]
        self._overlay_user_moved = False
        # 顶端居中；宽度先给个保守初值，页面渲染完会按内容自适应收窄(overlay_resize)并重新居中。高度做成和按钮一样窄。
        # ⚠ 必须给 min_size 很小，否则 pywebview 默认最小高(100)会让 26px 的窄条根本压不下去。
        w0, h0 = 360, 22
        x0, y0 = max(0, (sw - w0) // 2), 0
        self._overlay_rect = [x0, y0, w0, h0]
        kw = dict(width=w0, height=h0, x=x0, y=y0, js_api=self,
                  on_top=True, frameless=True, easy_drag=False, transparent=True,
                  min_size=(80, 20), background_color="#0B0E14")
        try:
            self._overlay_window = webview.create_window("AOE4 Overlay", url=page, **kw)
            try:
                self._overlay_window.events.closed += self._on_overlay_closed
            except Exception:
                pass
            try:
                self._overlay_window.events.shown += self._overlay_glass   # 显示后再上亚克力
            except Exception:
                pass
            return {"open": True}
        except Exception as e:
            self._overlay_window = None
            return {"open": False, "reason": str(e)}

    def _on_overlay_closed(self):
        self._overlay_window = None
        if self._overlay_cfg_window is not None:   # 主窄条没了，孤儿设置窗也关掉
            try:
                self._overlay_cfg_window.destroy()
            except Exception:
                pass
            self._overlay_cfg_window = None

    def overlay_set_on_top(self, flag):
        w = self._overlay_window
        return self._form_set(getattr(w, "native", None), "TopMost", bool(flag)) if w else False

    def overlay_set_bg(self, alpha):
        """调背景（毛玻璃）不透明度 0~255；0=背景全透明、只剩内容。立即重上玻璃。"""
        try:
            self._overlay_bg_alpha = max(0, min(255, int(alpha)))
        except Exception:
            return False
        self._overlay_glass()
        return True

    def overlay_set_content(self, alpha):
        """调内容不透明度 30~100（文字等）。仅存状态，窄条页面轮询读到后用 CSS 生效。"""
        try:
            self._overlay_content_alpha = max(30, min(100, int(alpha)))
            return True
        except Exception:
            return False

    def overlay_set_frost(self, level):
        """调毛度：0=无(透明渐变) 1=轻(模糊) 2=重(亚克力)。立即重上玻璃。"""
        try:
            self._overlay_frost = max(0, min(2, int(level)))
        except Exception:
            return False
        self._overlay_glass()
        return True

    # ---------- 覆盖层【设置】小窗：同款玻璃，放调背景/内容透明度/毛度的滑杆（窄条太小放不下）----------
    def toggle_overlay_cfg(self):
        if self._overlay_cfg_window is not None:
            try:
                self._overlay_cfg_window.destroy()
            except Exception:
                pass
            self._overlay_cfg_window = None
            return {"open": False}
        if self._overlay_window is None:
            return {"open": False, "reason": "先打开覆盖层"}
        import webview
        page = os.path.join(WEB_DIR, "overlay_cfg.html")
        x = max(0, min(self._overlay_rect[0], self._overlay_screen[0] - 230))
        y = min(self._overlay_rect[1] + 34, self._overlay_screen[1] - 170)
        # easy_drag=False：否则拖滑杆时整窗也跟着被拖；只让标题栏(带 pywebview-drag-region)拖动。
        kw = dict(width=224, height=158, x=x, y=y, js_api=self, on_top=True,
                  frameless=True, easy_drag=False, transparent=True,
                  min_size=(120, 80), background_color="#0B0E14")
        try:
            self._overlay_cfg_window = webview.create_window("AOE4 Overlay 设置", url=page, **kw)
            try:
                self._overlay_cfg_window.events.closed += self._on_overlay_cfg_closed
            except Exception:
                pass
            try:    # 设置窗自身也上一层轻玻璃，风格统一
                self._overlay_cfg_window.events.shown += (lambda: self._glass(self._overlay_cfg_window, 1, 130))
            except Exception:
                pass
            return {"open": True}
        except Exception as e:
            self._overlay_cfg_window = None
            return {"open": False, "reason": str(e)}

    def _on_overlay_cfg_closed(self):
        self._overlay_cfg_window = None

    def overlay_cfg_state(self):
        """设置窗读取当前值，初始化滑杆。"""
        return {"bg": self._overlay_bg_alpha, "content": self._overlay_content_alpha, "frost": self._overlay_frost}

    def overlay_data(self):
        """覆盖层轮询：要显示的面板项 + 运行态 + 弹信息(命中断点/自动改写开关) + 几何(供前端自适应/钳制)。
        当前只显示开关(bool)项——即出村民/出乡骑/出商人三个开关；子集自选下一步做。"""
        g = self._run_graph or self._graph
        items = []
        if g is not None:
            for row in getattr(g, "panel", []):
                if len(row) < 2:
                    continue
                nid, key = row[0], row[1]
                node = g.nodes.get(nid)
                if node is None:
                    continue
                if node.type_id != "data.switch":   # 暂时只上生产开关(出村民/出乡骑/出商人)，排除 HDR 等其它布尔项
                    continue
                items.append({"id": nid, "label": (row[2] if len(row) > 2 else key),
                              "value": bool(node.values.get(key)), "kind": "bool"})
        alive = bool(self._run_thread and self._run_thread.is_alive())
        with self._snap_lock:
            paused = alive and self._run_paused   # 没在跑时 _run_paused 恒为 True，别误报「已暂停」
            bp = self._bp_hit if alive else None
        return {"items": items, "running": alive and not paused, "paused": paused, "bp_hit": bp,
                "bg_alpha": self._overlay_bg_alpha, "content_alpha": self._overlay_content_alpha,
                "frost": self._overlay_frost,
                "rect": list(self._overlay_rect), "screen": list(self._overlay_screen)}

    def overlay_resize(self, w, h):
        """页面按内容测得宽高后调用：把窗口收成贴合内容的小窄条。位置保持(并保证不出屏右/下)。"""
        win = self._overlay_window
        if win is None:
            return False
        try:
            w = max(80, min(int(w), int(self._overlay_screen[0])))
            h = max(20, min(int(h), 200))
        except Exception:
            return False
        x, y = self._overlay_rect[0], self._overlay_rect[1]
        if not self._overlay_user_moved:        # 没手动拖过 → 自适应后保持顶端居中
            x = max(0, (self._overlay_screen[0] - w) // 2)
        x = max(0, min(x, self._overlay_screen[0] - w))   # 越界则拉回屏内
        y = max(0, min(y, self._overlay_screen[1] - h))
        self._overlay_rect = [x, y, w, h]
        def _do():
            try:
                win.resize(w, h); win.move(x, y)
            except Exception:
                pass
        self._form_invoke(getattr(win, "native", None), _do)
        return True

    def overlay_move(self, x, y):
        """拖拽时调用：把窗口移到(x,y)，钳制在屏幕内（不允许任何部分移出屏幕）。"""
        win = self._overlay_window
        if win is None:
            return False
        try:
            w, h = self._overlay_rect[2], self._overlay_rect[3]
            sw, sh = self._overlay_screen
            x = max(0, min(int(x), sw - w))
            y = max(0, min(int(y), sh - h))
        except Exception:
            return False
        self._overlay_user_moved = True         # 用户手动拖过 → 之后自适应不再强行居中
        self._overlay_rect[0] = x; self._overlay_rect[1] = y
        def _do():
            try:
                win.move(x, y)
            except Exception:
                pass
        self._form_invoke(getattr(win, "native", None), _do)
        return True

    # ==================== 运行可视化（引擎在后台线程自行全速跑，前端只轮询）====================
    @staticmethod
    def _interval_of(graph) -> float:
        """从图里读「每帧触发」节点的循环间隔(秒)，作为引擎循环节奏。"""
        for node in graph.nodes.values():
            if node.type_id == "event.on_tick":
                try:
                    return max(0.0, float(node.values.get("interval", 0.1)))
                except (TypeError, ValueError):
                    return 0.1
        return 0.1

    def _stop_thread(self):
        """停掉引擎线程并等它退出——务必【不持 _run_lock】调用，否则与线程争锁死锁。
        线程在自己的 finally 里关 mss（与 grab 同线程），所以这里只发停止信号 + join。"""
        th = self._run_thread
        self._run_stop = True
        self._run_paused = False          # 唤醒可能在暂停轮询里的循环，让它看到停止标志
        if th and th.is_alive() and th is not threading.current_thread():
            th.join(timeout=2.0)
        self._run_thread = None

    def run_begin(self, payload, real=False):
        """用当前编辑器里的图开一次运行：建图 + 启动【后台引擎线程】（创建即暂停，等 run_resume）。
        real=False(默认)＝干跑：只识别、不发任何输入；real=True＝真跑：真正发按键/鼠标。
        引擎线程自行按「循环间隔」全速跑，UI 只通过 run_poll 取最近一帧，不拖慢底层执行。"""
        self._stop_thread()               # 先停掉上一个会话（不持锁，避免死锁）
        with self._run_lock:
            self._run_graph = payload_to_graph(payload)
            self._run_real = bool(real)
            self._run_interval = self._interval_of(self._run_graph)

            def _log(level, message, node_id=None):
                self._run_logs.append({"level": level, "msg": message, "node": node_id})

            self._run_ctx = ExecutionContext(on_log=_log, dry_run=(not real))
            self._run_ctx.profile_enabled = self._run_profile   # 沿用当前「性能监控」开关
            self._run_ctx.preview_enabled = self._run_preview   # 沿用当前「截图预览」开关
            self._run_exec = TraceExecutor(self._run_graph)
            begin = {"level": "INFO", "tick": 0,
                     "msg": ("开始运行：执行流程并向游戏发送按键/鼠标操作" if real
                             else "开始试运行：只识别、不发送任何输入"), "node": None}
            self._run_logs = [begin]
            with self._snap_lock:
                self._pending_logs = [begin]   # 让首次轮询就拿到“开始”这条
                self._latest = None
                self._bp_hit = None
            self._run_stop = False
            self._run_paused = True
            self._run_thread = threading.Thread(target=self._run_loop, name="flow-engine", daemon=True)
            self._run_thread.start()
            return {"ok": True, "nodes": len(self._run_graph.nodes), "real": bool(real)}

    def _run_loop(self):
        """后台引擎循环：暂停时轻睡；否则按「循环间隔」全速跑帧、把轨迹/日志发布到快照供前端轮询。
        断点命中即在本线程自停（精确，不依赖前端轮询节奏）。退出时在【本线程】关 mss。"""
        try:
            while not self._run_stop:
                if self._run_paused:
                    time.sleep(0.02)
                    continue
                t0 = time.time()
                with self._run_lock:
                    if self._run_exec is None or self._run_ctx is None:
                        break
                    before = len(self._run_logs)
                    try:
                        self._run_exec.run_tick(self._run_ctx, dt=self._run_interval)
                    except Exception as e:   # 单帧异常不致命：记日志，循环继续
                        self._run_logs.append({"level": "ERROR", "msg": f"运行异常：{e}", "node": None})
                    tick = self._run_ctx.tick_index
                    new_logs = self._run_logs[before:]
                    for l in new_logs:
                        l["tick"] = tick
                    if len(self._run_logs) > _RUN_LOG_CAP:
                        del self._run_logs[: len(self._run_logs) - _RUN_LOG_CAP]
                    data = {}
                    for (nid, port), val in self._run_ctx.memo_snapshot().items():
                        data[nid + _RUNSEP + port] = _fmt_value(val)
                    times = None
                    if self._run_ctx.profile_enabled:   # 性能监控开启时附带各节点 [自身ms, 累计ms]
                        times = {nid: [round(s, 2), round(c, 2)]
                                 for nid, (s, c) in self._run_ctx.profile_snapshot().items()}
                    previews = None
                    preview_labels = None
                    if self._run_ctx.preview_enabled:   # 截图预览开启时附带各感知节点「截到的区域图」+ 一行清晰标签(置信度/识别值等)
                        previews = {}
                        preview_labels = {}
                        for nid, node in self._run_graph.nodes.items():
                            live = getattr(node, "live", None) or {}
                            b64 = live.get("preview")
                            if b64:
                                previews[nid] = b64
                            lbl = live.get("preview_label")
                            if lbl:
                                preview_labels[nid] = lbl
                    # 运行时被节点改写过的别人参数（如「关闭开关」自动关掉某开关）：推给前端，让界面控件也显示成新值
                    pwrites = {nid: dict(kv) for nid, kv in self._run_ctx.param_writes.items()} \
                        if self._run_ctx.param_writes else None
                    path = list(self._run_exec.trace_path)
                    ports = dict(self._run_exec.trace_ports)
                    # 断点 / 运行到此节点：本帧路径命中即自停。
                    # visited = 执行节点(有序) + 本帧求值过的纯数据节点(算式/匹配/OCR…)，让数据节点也能命中断点。
                    hit = None
                    data_hit = getattr(self._run_exec, "trace_data", None) or set()
                    visited = path + [d for d in data_hit if d not in path]
                    if self._run_until and self._run_until in visited:
                        hit, self._run_until = self._run_until, None
                    elif self._run_bps:
                        for nid in visited:
                            if nid in self._run_bps:
                                hit = nid
                                break
                with self._snap_lock:        # 出 _run_lock 后再短暂占快照锁发布（轮询绝不阻塞跑帧）
                    self._latest = {"tick": tick, "path": path, "ports": ports, "data": data,
                                    "times": times, "previews": previews, "preview_labels": preview_labels,
                                    "param_writes": pwrites}
                    self._pending_logs.extend(new_logs)
                    if len(self._pending_logs) > _RUN_LOG_CAP:   # 前端长时间不取（窗口最小化时 rAF 暂停）也不无限堆积
                        del self._pending_logs[: len(self._pending_logs) - _RUN_LOG_CAP]
                    if hit:
                        self._bp_hit = hit
                        self._run_paused = True
                if not self._run_paused:
                    time.sleep(max(0.0, self._run_interval - (time.time() - t0)))
        finally:
            ctx = self._run_ctx
            if ctx is not None:
                try:
                    ctx.cleanup_tick()       # 释放可能持有的输入屏蔽/锁（本线程，BlockInput 同线程才能解）
                except Exception:
                    pass
                try:
                    ctx.close_capture()      # 关 mss（与 grab 同线程，避免跨线程 BitBlt）
                except Exception:
                    pass

    def run_poll(self):
        """前端轮询：取最近一帧轨迹 + 自上次以来的新日志 + 是否暂停/命中断点。
        极轻量——只在 _snap_lock 下读，不触发任何引擎计算，因此 UI 轮询不影响底层执行速度。"""
        with self._snap_lock:
            logs = self._pending_logs
            self._pending_logs = []
            hit = self._bp_hit
            self._bp_hit = None
            trace = self._latest
            paused = self._run_paused
        alive = bool(self._run_thread and self._run_thread.is_alive())
        return {"trace": trace, "logs": logs, "paused": paused,
                "running": alive and not paused, "bp_hit": hit}

    def run_pause(self):
        self._run_paused = True
        return True

    def run_resume(self):
        if self._run_thread and self._run_thread.is_alive():
            self._run_paused = False
            return True
        return False

    def run_set_breakpoints(self, bps=None, run_until=None):
        """前端切换断点 / “运行到此节点” 时同步给引擎线程（引擎据此精确自停）。"""
        with self._snap_lock:
            self._run_bps = set(bps or [])
            self._run_until = run_until
        return True

    def run_set_profile(self, on=True):
        """前端切换「性能监控」开关。开启后引擎每帧计时并在快照里附带各节点 [自身ms, 累计ms]。
        计时仅 perf_counter 取差（~0.1µs/节点），关闭时零开销；可在运行中随时开/关。"""
        self._run_profile = bool(on)
        ctx = self._run_ctx
        if ctx is not None:
            ctx.profile_enabled = self._run_profile   # 简单 bool 写入，引擎线程下一帧即生效
        return True

    def run_set_preview(self, on=True):
        """前端切换「截图预览」开关。开启后感知节点把截到的区域图(base64 PNG)写进 live，
        引擎每帧在快照里附带各节点的预览图，前端画到节点上——一眼看出“它到底截到了什么”。
        仅编码开销（关闭时零开销）；可在运行/试运行中随时开关。"""
        self._run_preview = bool(on)
        ctx = self._run_ctx
        if ctx is not None:
            ctx.preview_enabled = self._run_preview
        return True

    def run_update(self, payload):
        """运行中热更新：参数改值【且】结构(增删节点/改连线)也实时同步——

        - 仍存在且类型不变的节点：复用原节点对象（保留其内部状态/记忆，如三态遮挡的历史）。
        - 新增的节点：新建；删除的：移除。
        - 连线整体按载荷重建（连线无状态）。黑板变量(ctx.vars)与帧序号(tick_index)保持不变。
        与引擎跑帧共用 _run_lock，避免跑帧中途结构被改而读到半成品（引擎会等本次改图完成）。
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
            self._run_interval = self._interval_of(g)   # 间隔可能被改（每帧触发节点），同步给引擎循环
        return True

    def run_end(self):
        """结束运行：停掉引擎线程（它在自己的 finally 里解屏蔽/锁、关 mss），再清空状态。"""
        self._stop_thread()                  # 不持 _run_lock（线程跑帧时也要这把锁，否则 join 死锁）
        with self._run_lock:
            self._run_graph = self._run_ctx = self._run_exec = None
            self._run_logs = []
        with self._snap_lock:
            self._latest = None
            self._pending_logs = []
            self._bp_hit = None
            self._run_bps = set()
            self._run_until = None
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

    def delete_flow(self, path=None):
        """删除一个【我的流程】文件（仅限 user_flows 目录；内置只读流程拒绝删除）。
        返回 {ok, reason?}。删的若是当前打开的流程，清掉 _path 与“上次流程”记录。"""
        p = path or self._path
        try:
            ap = os.path.abspath(p)
        except Exception:
            return {"ok": False, "reason": "路径无效"}
        if self._is_builtin(p) or not ap.startswith(USER_FLOWS_DIR + os.sep):
            return {"ok": False, "reason": "只能删除「我的流程」（内置流程为只读）"}
        try:
            if os.path.isfile(ap):
                os.remove(ap)
            if self._path and os.path.abspath(self._path) == ap:
                self._path = None
                user_settings.update_settings(last_flow=None)   # 当前流程被删 → 清“上次打开”记录，免得下次启动指向已删文件
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "reason": str(e)}

    def reveal_path(self, path):
        """在系统文件浏览器中定位并选中该文件（Windows: explorer /select,<路径>）。返回 {ok, reason?}。"""
        try:
            ap = os.path.abspath(path) if path else ""
            if not ap or not os.path.exists(ap):
                return {"ok": False, "reason": "文件不存在（可能尚未保存）"}
            import subprocess
            # explorer 的 /select 用逗号紧跟路径；explorer 即便成功也常返回非 0，故用 Popen 不校验返回码。
            subprocess.Popen(["explorer", "/select,", ap])
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "reason": str(e)}

    def open_path(self, path):
        if path and os.path.exists(path):
            self._graph = Graph.load(path)
            self._path = path
            _save_last_flow(path)
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
        paths = list(res) if isinstance(res, (list, tuple)) else [res]
        return [_to_rel_path(p) for p in paths]   # 工程内的图片转相对路径(templates/xxx.png)，跨机可用

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
        """统一外壳：采集前把编辑器让开（隐藏→等游戏重绘→抓屏），采完再显示出来。

        采集覆盖层是 topmost 全屏窗，盖住一切；要点只是抓屏那一刻编辑器别挡着游戏。
        fn 在本（worker）线程内自建 Tk root 跑 mainloop，阻塞到覆盖层关闭再返回。

        用 hide()/show() 而不是 minimize()/restore()：后者的 restore 会把窗口恢复成“普通”状态，
        若采集前是【最大化】的，回来就缩小了（用户反馈）。hide/show 只切换可见性、不动窗口状态，
        最大化/普通都原样保留。"""
        win = self._window
        try:
            if win:
                try:
                    win.hide()
                    time.sleep(0.35)  # 等编辑器隐藏 + 游戏画面重绘出来再抓屏
                except Exception:
                    pass  # 隐藏失败也继续采集（最多是编辑器没让开，覆盖层仍置顶）
            return fn()
        finally:
            try:
                if win:
                    win.show()   # 原样显示出来（最大化状态保留）
            except Exception:
                pass

    def pick_region(self, initial=None):
        from . import capture
        return self._capture(lambda: capture.pick_region(initial))   # initial=[l,t,r,b] 当前框，预显示+可微调

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
        _save_last_flow(self._path)
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
            _save_last_flow(path)
            return path
        return None


def launch(graph: Optional[Graph] = None, path: Optional[str] = None):
    import webview
    if graph is None and path is None:          # 未指定文件 → 自动载入上次打开的流程（开机即用）
        last = _load_last_flow()
        if not last:                            # 没有上次记录 → 默认打开内置“统一生产”组合流程
            cand = os.path.join(BUILTIN_FLOWS_DIR, "combined.flow.json")
            if os.path.exists(cand):
                last = cand
        if last:
            try:
                graph, path = Graph.load(last), last
            except Exception:
                graph, path = None, None
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
            base = "当前流程有未保存的修改，确定退出吗？\n（取消可返回编辑器再保存）"
            detail = (getattr(api, "_change_summary", "") or "").strip()
            if detail:
                if len(detail) > 1500:
                    detail = detail[:1500] + "\n…（更多省略）"
                base += "\n\n本次改动：\n" + detail
            return bool(api._window.create_confirmation_dialog("未保存的修改", base))
        except Exception:
            return True   # 对话框不可用就不阻拦关闭
    try:
        api._window.events.closing += _confirm_close
    except Exception:
        pass

    def _close_monitor():           # 主编辑器关掉时，连带关掉独立浮窗（资源监控 / 覆盖层 / 覆盖层设置），否则它们会让进程不退出
        for attr in ("_mon_window", "_overlay_window", "_overlay_cfg_window"):
            w = getattr(api, attr, None)
            if w is not None:
                try:
                    w.destroy()
                except Exception:
                    pass
                setattr(api, attr, None)
    try:
        api._window.events.closed += _close_monitor
    except Exception:
        pass
    # 前端通过 js_api 轮询拉取启动数据（pywebview 注入 api 有延迟，前端会等到就绪再拉），
    # 不再用 evaluate_js 主动 push —— 那条路在 WebView2 上有跨线程 COM 报错且不稳定。
    debug = os.environ.get("AOE4_EDITOR_DEBUG") == "1"  # 置 1 可开 WebView2 开发者工具
    webview.start(debug=debug)
