// AOE4 Flow Editor —— LiteGraph 前端胶水
// 职责：注册 LiteGraph 节点类型并建图；编辑后回传保存。
// 启动数据（节点定义/初始流程/内置列表）由 Python 主动 push 进来（window.__bootstrap__），
// 若未收到再回退到 js_api 拉取。连线 exec/data 归类、参数类型回解析都在 Python 侧完成。

const ED = (function () {
  let graph, canvas, defs = [];
  let typeKeyByType = {};   // our type_id -> LiteGraph 注册名
  let defByType = {};       // our type_id -> 定义（含 help/参数说明），用于选中节点时显示说明
  let helpEl = null;
  let selectedNode = null;   // 当前选中的节点（用于参数变动时刷新“已修改”列表）
  let seq = 1;
  let booted = false;
  // 当前流程的元信息：名称/说明/文件路径/是否内置只读（保存时内置会被改为另存）
  let flowMeta = { name: "", desc: "", path: null, readonly: false };
  // 控制面板置顶项：有序的 [nodeId, paramKey, 自定义显示名?]（随流程保存）。
  let panelPins = [];
  // 记住每个参数填过的“面板显示名”（键= nodeId|key）：取消勾选不丢，重新勾选自动回填；清空文本即“手动删除”。
  let pinLabels = {};
  let _panelDrag = null;                        // 控制面板项拖动调序：正在拖的项 "nodeId|key"（仅编辑模式）
  // 可视化分组：[{title, color, collapsed?, members:[ourNodeId...]}]，框随成员节点自动包裹（仅展示，随流程保存）。
  // collapsed=true 时该组折叠成一个紧凑“子图节点”：隐藏成员、把跨边界连线汇成箱体输入/输出端口（见“可折叠子图”一节）。
  let groupDefs = [];
  let foldHidden = new Set();   // 当前被折叠组隐藏的成员 ourId（仅影响显示/命中；collect 仍输出完整扁平图供引擎用）
  let groupDlgRender = null;   // 分组弹窗打开时的重绘函数（撤销/重做后用于刷新弹窗）
  const GROUP_COLORS = ["#3a6ea5", "#5a9367", "#a5793a", "#8a5a9a", "#b05a5a", "#4a8a8a"];
  // —— 试运行（干跑）可视化状态 ——
  let running = false, runSession = false, pollTimer = null, realRun = false, _lastTick = 0;   // realRun: true=真跑(真发输入)
  let runPath = new Set(), runPathArr = [], runPorts = {}, runData = {}, runLogs = [];
  let runDataNodes = new Set();                 // 本帧产生过数据的节点（用于高亮/不被压暗）
  let profileOn = false, runTimes = {};         // 性能监控：开关 + 本帧各节点 [自身ms, 累计ms]
  let breakpoints = new Set();                  // 试运行断点：命中(出现在执行路径)即暂停；会话级，不随流程保存
  let runUntil = null;                          // “运行到此节点”一次性目标（命中即暂停并清除）
  let simpleMode = false;                       // 使用模式：画布只读（仅控制面板+运行+日志），面向“只想用”的用户
  let simpleEntrySig = null;                    // 进入使用模式时的图快照：退出时据此还原（使用模式里的拖动/调参不落盘）
  let _lastRunStatus = "";                       // 上一条“本轮结果/原因”状态，变化时才记日志，避免刷屏
  let runAnimRAF = null, runPhase = 0, _runAnimLast = 0;   // 动画（脉冲发光 / 连线流动）
  const RUNSEP = String.fromCharCode(1);        // 运行轨迹里 data 的键 = nodeId + RUNSEP + port（与 Python \x01 一致）
  // 撤销/重做（快照式）
  let undoStack = [], redoStack = [], snapTimer = null, suppressSnap = false, building = false;

  // ---- 错误可视化（界面里看不到控制台，所以把错误显示出来）----
  function showError(msg) {
    try { console.error(msg); } catch (e) {}
    let el = document.getElementById("errbox");
    if (!el) {
      el = document.createElement("div");
      el.id = "errbox";
      el.style.cssText = "position:absolute;left:10px;bottom:10px;max-width:70%;max-height:45%;" +
        "overflow:auto;background:#3a1212;color:#ffd7d7;border:1px solid #a33;border-radius:6px;" +
        "padding:8px 10px;font:12px/1.5 Consolas,monospace;white-space:pre-wrap;z-index:9999;";
      el.title = "点击关闭";
      el.onclick = () => el.remove();
      document.body.appendChild(el);
    }
    el.textContent = "⚠ 出错（点击关闭）：\n" + msg;
  }
  // "ResizeObserver loop ..." 是浏览器在一帧内多次布局时发出的【良性】告警（规范建议忽略），
  // 不是真错误；否则一启动就被弹成红框。其余错误照常显示。
  window.addEventListener("error", (e) => {
    const m = String(e && e.message || "");
    if (m.indexOf("ResizeObserver loop") >= 0) return;
    showError((e.message || e.error || "脚本错误") + "\n  " + (e.filename || "") + ":" + (e.lineno || ""));
  });
  window.addEventListener("unhandledrejection", (e) => {
    const r = e.reason;
    showError("Promise 被拒绝：" + (r && (r.stack || r.message) || r));
  });

  // 连线按类型上色
  function setupColors() {
    const C = LGraphCanvas.link_type_colors;
    C["exec"] = "#FFFFFF";   // 执行流＝白线（与虚幻蓝图一致）
    C["number"] = "#7AB0EE";
    C["bool"] = "#E0A85A";
    C["string"] = "#9AD08A";
    C["image"] = "#C792DF";
    C["region"] = "#69b0a0"; C["point"] = "#69b0a0"; C["color"] = "#cf8a6a";
    C["list"] = "#d6c15a";   // 列表（如识别到的多个数字）
  }

  function slotType(p) {
    if (p.kind === "exec") return "exec";
    return p.dtype === "any" ? 0 : p.dtype;   // 0 = 通配
  }

  // 文本像素宽（CJK 17、其余 9），与 layout.py 的估算一致，保证编辑器节点宽度=排版预留宽度
  function textW(s) {
    let w = 0;
    for (const c of String(s == null ? "" : s)) w += c.charCodeAt(0) > 0x2E80 ? 17 : 9;
    return w;
  }
  // 节点最小宽度：容纳 标题 / 端口名 / "参数名+输入框"，与 layout.estimate_size 同公式
  function nodeMinWidth(def) {
    if (!def) return 160;
    let w = textW(def.title) + 46;
    for (const p of (def.inputs || []).concat(def.outputs || [])) w = Math.max(w, textW(p.label) + 46);
    for (const p of (def.params || [])) w = Math.max(w, textW(p.label) + 196);
    return Math.max(160, w);
  }

  // 精简/定制 LiteGraph 菜单与交互（去重、去掉用不到的 group/subgraph、连线菜单不再混入"添加节点"）
  function installEditorTweaks() {
    // 连线中点菜单：只留"删除连线"（不再混入与空白处重复的 Add Node）
    LGraphCanvas.prototype.showLinkMenu = function (link, e) {
      if (simpleMode) return false;   // 使用模式：连线只读，不提供“删除连线”
      const that = this;
      new LiteGraph.ContextMenu(["删除连线"], {
        event: e, title: "连线",
        callback: (v) => { if (v === "删除连线") that.graph.removeLink(link.id); },
      });
      return false;
    };
    // 画布空白右键：只留"添加节点"（分组操作改到“节点右键”里，更符合直觉）
    LGraphCanvas.prototype.getCanvasMenuOptions = function () {
      if (simpleMode) return [{ content: "使用模式（画布只读）· 切到编辑模式可改图", disabled: true }];
      return [{ content: "添加节点", has_submenu: true, callback: LGraphCanvas.onMenuAdd }];
    };
    // 节点右键：精简到 克隆 / 删除 + 节点自定义项（去掉 Inputs/Outputs/Properties/Title/Mode/Resize/
    // Collapse/Pin/Colors/Shapes 等堆叠项）。务必调用 node.getExtraMenuOptions，否则“添加/编辑描述”不出现。
    LGraphCanvas.prototype.getNodeMenuOptions = function (node) {
      if (simpleMode) return [{ content: "使用模式（画布只读）", disabled: true }];
      const opts = [];
      if (node.clonable !== false)
        opts.push({ content: "克隆", callback: LGraphCanvas.onMenuNodeClone });
      opts.push({
        content: "删除",
        disabled: !(node.removable !== false && !node.block_delete),
        callback: LGraphCanvas.onMenuNodeRemove,
      });
      if (node.getExtraMenuOptions) {
        const extra = node.getExtraMenuOptions(this, opts);   // nodeExtraMenu 直接 push 进 opts
        if (Array.isArray(extra)) for (const e of extra) opts.push(e);
      }
      return opts;
    };
    // 鼠标光标随「左键点下去会发生什么」变化，让用户下意识知道当前操作类型（#9/#10）：
    //   · 节点上的控件/按钮、端口(编辑模式可连线) = 可点击 → 小手(pointer)
    //   · 节点标题/空白节点体、分组(含组名) = 可拖动 → 十字(crosshair)
    //   · 右下角缩放角 → se-resize（LiteGraph 已设，不覆盖）
    // 做法：包裹 processMouseMove，在它(已)按粗粒度设好光标后，用更细的命中测试微调；
    // 拖动/连线/缩放/平移/框选/拖组进行中则交给 LiteGraph、不干预。
    if (!LGraphCanvas.prototype.__cursorHooked) {
      const _NWH = (LiteGraph.NODE_WIDGET_HEIGHT || 20);
      const _hitWidget = (node, lx, ly) => {
        const ws = node.widgets;
        if (!ws || !ws.length) return false;
        const W = node.size[0];
        for (const w of ws) {
          if (!w || w.hidden || w.last_y == null) continue;
          if (lx >= 6 && lx <= W - 12 && ly >= w.last_y && ly <= w.last_y + _NWH) return true;
        }
        return false;
      };
      const _origPMM = LGraphCanvas.prototype.processMouseMove;
      LGraphCanvas.prototype.processMouseMove = function (e) {
        const ret = _origPMM.call(this, e);
        try {
          const cv = this.canvas;
          if (!cv) return ret;
          if (this.node_dragged || this.resizing_node || this.connecting_node ||
              this.dragging_canvas || this.dragging_rectangle || this.selected_group) return ret;
          if (cv.style.cursor === "se-resize") return ret;            // 缩放角保持
          const x = e.canvasX, y = e.canvasY;
          const node = this.graph && this.graph.getNodeOnPos(x, y, this.visible_nodes);
          if (node) {
            // 可点击区（仅编辑模式有意义：使用模式下控件/端口不响应，整节点只能拖动）
            if (!simpleMode && node.getSlotInPosition && node.getSlotInPosition(x, y)) { cv.style.cursor = "pointer"; return ret; }
            if (!simpleMode && _hitWidget(node, x - node.pos[0], y - node.pos[1])) { cv.style.cursor = "pointer"; return ret; }
            return ret;                                                // 标题/节点体：维持 crosshair（可拖动）
          }
          if (this.graph && this.graph.getGroupOnPos && this.graph.getGroupOnPos(x, y)) { cv.style.cursor = "crosshair"; return ret; }
        } catch (_) { }
        return ret;
      };
      LGraphCanvas.prototype.__cursorHooked = true;
    }
    // 可折叠子图：从“可见节点”里剔除被折叠组隐藏的成员——一处即同时管住【渲染】与【鼠标命中】
    // （visible_nodes 同时用于绘制与 getNodeOnPos）。节点仍留在 graph._nodes 里，collect() 照常输出。
    if (!LGraphCanvas.prototype.__foldHooked) {
      const _origCVN = LGraphCanvas.prototype.computeVisibleNodes;
      LGraphCanvas.prototype.computeVisibleNodes = function (nodes, out) {
        const vis = _origCVN.call(this, nodes, out);
        if (!foldHidden.size) return vis;
        let w = 0;
        for (let i = 0; i < vis.length; i++) if (!foldHidden.has(vis[i]._id)) vis[w++] = vis[i];
        vis.length = w;
        return vis;
      };
      LGraphCanvas.prototype.__foldHooked = true;
    }
    // 值编辑浮框：去掉 OK 按钮，输入即"实时生效"；回车/失焦/点外部即关闭。
    LGraphCanvas.prototype.prompt = function (title, value, callback, event, multiline) {
      const that = this;
      const dialog = document.createElement("div");
      dialog.className = "graphdialog rounded";
      dialog.innerHTML = multiline
        ? "<span class='name'></span><textarea autofocus class='value'></textarea>"
        : "<span class='name'></span><input autofocus type='text' class='value'/>";
      let closed = false;
      dialog.close = function () {
        if (closed) return;             // 失焦/点外部/回车可能重复触发，幂等避免 removeChild 抛错
        closed = true;
        if (that.prompt_box === dialog) that.prompt_box = null;
        dialog.remove();                // 安全：已移除时为 no-op
      };
      const canvasEl = this.canvas;
      canvasEl.parentNode.appendChild(dialog);
      if (this.ds.scale > 1) dialog.style.transform = "scale(" + this.ds.scale + ")";
      if (that.prompt_box) that.prompt_box.close();
      that.prompt_box = dialog;

      dialog.querySelector(".name").innerText = title || "";
      const input = dialog.querySelector(".value");
      input.value = value == null ? "" : value;

      const commit = () => { try { if (callback) callback(input.value); } catch (e) {} that.dirty_canvas = true; };
      input.addEventListener("input", commit);          // 实时生效
      input.addEventListener("keydown", (e) => {
        e.stopPropagation();                            // 不触发画布快捷键/撤销
        if (e.keyCode === 27) dialog.close();           // Esc 关闭
        else if (e.keyCode === 13 && !multiline) { commit(); dialog.close(); e.preventDefault(); }  // 回车提交并关闭
      });
      input.addEventListener("focusout", () => { commit(); dialog.close(); });  // 失焦提交并关闭

      const rect = canvasEl.getBoundingClientRect();
      if (event) {
        dialog.style.left = (event.clientX - rect.left - 20) + "px";
        dialog.style.top = (event.clientY - rect.top) + "px";
      } else {
        dialog.style.left = (canvasEl.width * 0.5 - 40) + "px";
        dialog.style.top = (canvasEl.height * 0.5) + "px";
      }
      setTimeout(() => { input.focus(); if (!multiline) input.select(); }, 10);
      return dialog;
    };

    // 超长文本参数在节点上"截断显示"（带省略号），不改变真实值（编辑/保存仍是完整值）。
    // 按"该控件可用值宽 = 节点宽 - 参数名宽 - 留白"计算可显示字数，避免与参数名重叠。
    const _origDrawWidgets = LGraphCanvas.prototype.drawNodeWidgets;
    LGraphCanvas.prototype.drawNodeWidgets = function (node, posY, ctx, active_widget) {
      const saved = [];
      for (const w of (node.widgets || [])) {
        if ((w.type === "text" || w.type === "string") && w.value != null) {
          const avail = node.size[0] - textW(w.name) - 40;
          const maxChars = Math.max(3, Math.floor(avail / 8));
          const sv = String(w.value);
          if (sv.length > maxChars) { saved.push([w, w.value]); w.value = sv.slice(0, maxChars) + "…"; }
        }
      }
      try { _origDrawWidgets.call(this, node, posY, ctx, active_widget); }
      finally { for (const s of saved) s[0].value = s[1]; }
    };

    // —— 使用模式（画布只读）的三道闸：图上控件不可编辑 / 不能新建连线 / 不能断开连线 ——
    // （新建/删除节点、删除连线走的是上面已拦截的右键菜单；这里补上“直接在图上拖拽改图”的几条路径。）
    const _origProcWidgets = LGraphCanvas.prototype.processNodeWidgets;
    LGraphCanvas.prototype.processNodeWidgets = function (node, pos, e, aw) {
      if (simpleMode) return null;   // 改参数请走顶部“控制面板”
      return _origProcWidgets.call(this, node, pos, e, aw);
    };
    const _origDisIn = LGraphNode.prototype.disconnectInput;
    LGraphNode.prototype.disconnectInput = function () {
      if (simpleMode) return false;
      return _origDisIn.apply(this, arguments);
    };
    const _origDisOut = LGraphNode.prototype.disconnectOutput;
    LGraphNode.prototype.disconnectOutput = function () {
      if (simpleMode) return false;
      return _origDisOut.apply(this, arguments);
    };

    // 去掉“节点折叠”功能：折叠后运行高亮(金框/端口)的几何没适配、价值也不大，统一禁用（点折叠圈无效）。
    LGraphNode.prototype.collapse = function () {};

    // 分组节点的标题栏是分组色（偏亮），默认灰色标题字会被冲淡 -> 按底色亮度临时换成黑/白标题字。
    const _origDrawNode = LGraphCanvas.prototype.drawNode;
    LGraphCanvas.prototype.drawNode = function (node, ctx) {
      const saved = this.node_title_color;
      if (node._titleTextColor) this.node_title_color = node._titleTextColor;
      try { return _origDrawNode.call(this, node, ctx); }
      finally { this.node_title_color = saved; }
    };

    // 右键菜单过高时"内部滚动"而非整体平移：LiteGraph 给菜单根加了 wheel 监听把整个菜单
    // top 上下移动，这里在菜单构造期吞掉 wheel/mousewheel 监听，并设最大高度+overflow，
    // 让滚轮滚动内容。（用临时替换 addEventListener 实现，构造结束即还原。）
    if (!LiteGraph.__menuScrollPatched) {
      LiteGraph.__menuScrollPatched = true;
      const OrigCM = LiteGraph.ContextMenu;
      const CM = function (values, options) {
        const proto = Element.prototype, origAdd = proto.addEventListener;
        proto.addEventListener = function (type, fn, opts) {
          if (type === "wheel" || type === "mousewheel") return;
          return origAdd.call(this, type, fn, opts);
        };
        try { OrigCM.call(this, values, options); }
        finally { proto.addEventListener = origAdd; }
        if (this.root) {
          this.root.style.maxHeight = "84vh";
          this.root.style.overflowY = "auto";
          this.root.style.overflowX = "hidden";
        }
        return this;
      };
      CM.prototype = OrigCM.prototype;
      for (const k in OrigCM) { try { CM[k] = OrigCM[k]; } catch (e) {} }
      LiteGraph.ContextMenu = CM;
    }

    // 多选后整体拖动：点击"已在多选中的节点"且无修饰键时，保留整个多选（默认会清空只留这个，导致只拖动一个）
    const _origProcNodeSel = LGraphCanvas.prototype.processNodeSelected;
    LGraphCanvas.prototype.processNodeSelected = function (node, e) {
      if (node && this.selected_nodes && this.selected_nodes[node.id] &&
          Object.keys(this.selected_nodes).length > 1 &&
          !(e && (e.shiftKey || e.ctrlKey || e.metaKey))) {
        if (this.onNodeSelected) this.onNodeSelected(node);
        return;   // 保留多选，使后续拖动移动全部选中节点
      }
      return _origProcNodeSel.call(this, node, e);
    };

    // 右键菜单过高时内部滚动（而不是整体上下移动）
    if (!document.getElementById("aoe4-style")) {
      const st = document.createElement("style");
      st.id = "aoe4-style";
      st.textContent = ".litecontextmenu{max-height:84vh!important;overflow-y:auto!important;overflow-x:hidden;}";
      document.head.appendChild(st);
    }

    // 所有弹窗“点外部即关闭”：值编辑框(.graphdialog) + 编辑类弹窗(.popdlg：分组/流程信息/帮助/描述/图片列表/取键)。
    // 点在任一 .popdlg 内则全部保留（支持嵌套，如分组弹窗里再开“改名”框）；点在外部则一起收起。
    if (!window.__dlgCloseHooked) {
      window.__dlgCloseHooked = true;
      document.addEventListener("pointerdown", (e) => {
        document.querySelectorAll(".graphdialog").forEach((d) => {
          if (!d.contains(e.target) && typeof d.close === "function") d.close();
        });
        const inPop = e.target && e.target.closest && e.target.closest(".popdlg");
        if (!inPop && document.querySelector(".popdlg")) {
          document.querySelectorAll(".popdlg").forEach((d) => d.remove());
          groupDlgRender = null; helpModal = null;
        }
      }, true);
    }
  }

  function addParamWidget(node, p, def) {
    // 构造期（new base_class）createNode 尚未给 node.properties 赋初值，需自行兜底，
    // 否则末尾 node.properties[key]=... 会抛 TypeError，导致带参数的节点整体创建失败。
    if (!node.properties) node.properties = {};
    const cb = (v) => { if (!node.properties) node.properties = {}; node.properties[p.key] = v; scheduleSnap(); };
    let w;
    if (p.ptype === "int")
      w = node.addWidget("number", p.label, Number(p.default ?? 0), cb, { step: 10, precision: 0 });
    else if (p.ptype === "float")
      w = node.addWidget("number", p.label, Number(p.default ?? 0), cb, { step: (p.step || 0.1) * 10, precision: 2 });
    else if (p.ptype === "bool")
      w = node.addWidget("toggle", p.label, !!p.default, cb);
    else if (p.ptype === "enum")
      w = node.addWidget("combo", p.label, String(p.default ?? ""), cb, { values: (p.choices || []).map(String) });
    else if (p.ptype === "keys") {
      // 修饰键：直接用下拉菜单（值在 Python 侧与 csv 互转）。容纳已存的非标准组合。
      const vals = MOD_LABELS.slice();
      const dv = p.default == null ? "（无）" : String(p.default);
      if (!vals.includes(dv)) vals.unshift(dv);
      w = node.addWidget("combo", p.label, dv, cb, { values: vals });
    }
    else
      w = node.addWidget("text", p.label, p.default == null ? "" : String(p.default), cb);
    w._key = p.key;                  // 保存时用作参数键（不动 w.name，它是显示标签）
    node.properties[p.key] = w.value;

    // 设一个值进控件并同步（用于"吸色"顺带回填同节点的坐标参数）。
    const setByKey = (key, val) => {
      const w2 = (node.widgets || []).find((x) => x._key === key);
      if (w2) { w2.value = val; if (node.properties) node.properties[key] = val; return true; }
      return false;
    };
    // 关键：按钮回调发生在画布鼠标交互未结束时（画布仍持有指针捕获），若此刻直接弹
    // 系统【模态】框/全屏覆盖层会冲突、"只闪一下"。统一延迟到交互结束后再调后端。
    const defer = (openingMsg, runner, onResult) => {
      setStatus(openingMsg);
      setTimeout(() => {
        Promise.resolve(runner()).then(onResult)
          .catch((e) => showError("采集失败：" + (e && (e.stack || e.message) || e)));
      }, 120);
    };
    const apply = (val, msg) => { w.value = val; cb(val); if (canvas) canvas.setDirty(true, true); setStatus(msg); };
    const mkBtn = (label, run) => {
      const btn = node.addWidget("button", label, null, run);
      btn._noSave = true;            // 按钮不是参数，保存时跳过（collect 按 _key 取值）
    };
    const csv = (arr) => (arr || []).join(",");

    // 各参数类型挂上对应的"点一下就采集"按钮（采集时编辑器会自动让开、截到游戏画面）。
    if (p.ptype === "template" || p.ptype === "templates") {
      const multi = p.ptype === "templates";
      // 从已有图片文件选择（系统文件框）
      mkBtn("选择图片…", () => defer("正在打开图片选择框…", () => api().pick_templates(multi), (paths) => {
        if (!paths || !paths.length) { setStatus("已取消选择图片"); return; }
        const picked = paths.join(",");
        apply((multi && w.value) ? (w.value + "," + picked) : picked, "已选择 " + paths.length + " 张图片");
      }));
      // 直接在游戏画面上框选裁出模板
      mkBtn("截取模板…", () => defer("框选模板区域…（Enter 保存 / Esc 取消）", () => api().capture_template(), (path) => {
        if (!path) { setStatus("已取消截取模板"); return; }
        apply((multi && w.value) ? (w.value + "," + path) : path, "已截取模板：" + path);
      }));
      // 多图：打开"列表编辑器"，逐条增删/排序更方便
      if (multi) mkBtn("编辑列表…", () => editImageList(node, w, p));
    } else if (p.ptype === "region") {
      mkBtn("框选区域…", () => defer("框选区域…（Enter 确认 / Esc 取消）", () => api().pick_region(), (box) => {
        if (!box) { setStatus("已取消框选"); return; }
        apply(csv(box), "已框选区域：" + csv(box));
      }));
    } else if (p.ptype === "point") {
      // 若本节点存在配套的颜色参数（color↔pixel / color_hdr↔pixel_hdr），坐标由「取点吸色」一并采集，
      // 这里就不再单独放「取点」按钮（如游戏窗口检测）。仅当是独立坐标时才显示。
      const pairedColor = (def && def.params || []).some(
        (q) => q.ptype === "color" && q.key === p.key.replace("pixel", "color"));
      if (!pairedColor) {
        mkBtn("取点…", () => defer("点击取点…（Esc 取消）", () => api().pick_point(), (pt) => {
          if (!pt) { setStatus("已取消取点"); return; }
          apply(csv(pt), "已取点：" + csv(pt));
        }));
      }
    } else if (p.ptype === "color") {
      mkBtn("取点吸色…", () => defer("点击取色（坐标+颜色）…（Esc 取消）", () => api().pick_color(), (r) => {
        if (!r) { setStatus("已取消吸色"); return; }
        apply(csv(r.color), "已吸色：" + csv(r.color));
        // 顺带回填同节点配套的坐标参数（color↔pixel / color_hdr↔pixel_hdr）。
        if (r.point && setByKey(p.key.replace("color", "pixel"), csv(r.point))) {
          if (canvas) canvas.setDirty(true, true);
        }
      }));
    } else if (p.ptype === "key") {
      mkBtn("捕获按键…", () => defer("请按下按键…（Esc 取消）", () => api().pick_key(), (k) => {
        if (!k) { setStatus("已取消捕获"); return; }
        apply(k, "已捕获按键：" + k);
      }));
      // ESC/回车/F1 等无法被“捕获按键”录到（Esc 是采集的取消键），用特殊键面板直接选。
      mkBtn("特殊键…", () => specialKeyMenu((name) => apply(name, "已设为特殊键：" + name)));
    }
    // 注：修饰键(keys)已是下拉控件（见上方控件创建），无需额外按钮。

    // 图片参数：在节点下方的卡片里画出模板缩略图预览（值变化即刷新；见 nodeDrawForeground）。
    if (p.ptype === "template" || p.ptype === "templates")
      (node._previewKeys = node._previewKeys || []).push(p.key);
  }

  // 与 layout.py 的卡片尺寸常量保持一致（排版预留高度 = 这里画出的高度）。
  // 模板按"缩略图网格"画（仅图片，不显示文件名——增删/查看名字用节点上的"编辑列表…"按钮）。
  const CARD = { TH: 44, GAP: 6, PAD: 8, CGAP: 4, NOTE_LH: 16, DIV: 6, CAP: 12 };
  // 修饰键下拉选项（友好标签；Python 侧与 csv "ctrl,shift" 互转）。
  const MOD_LABELS = ["（无）", "Shift", "Ctrl", "Alt", "Win",
    "Ctrl+Shift", "Ctrl+Alt", "Shift+Alt", "Ctrl+Shift+Alt"];
  // 无法用“捕获按键”录入的特殊键（名字＝pydirectinput/采集工具的规范名，见 capture.py）。
  const SPECIAL_KEYS = [
    ["esc", "Esc"], ["enter", "回车"], ["tab", "Tab"], ["space", "空格"],
    ["backspace", "退格"], ["delete", "Delete"], ["insert", "Insert"],
    ["up", "↑"], ["down", "↓"], ["left", "←"], ["right", "→"],
    ["home", "Home"], ["end", "End"], ["pageup", "PgUp"], ["pagedown", "PgDn"],
    ["f1", "F1"], ["f2", "F2"], ["f3", "F3"], ["f4", "F4"], ["f5", "F5"], ["f6", "F6"],
    ["f7", "F7"], ["f8", "F8"], ["f9", "F9"], ["f10", "F10"], ["f11", "F11"], ["f12", "F12"],
  ];
  // 弹出“特殊键”选择面板：点一个就回调其规范名（用于 ESC/回车/F1 等无法捕获的键）。
  function specialKeyMenu(onPick) {
    document.getElementById("speckey")?.remove();
    const box = document.createElement("div");
    box.id = "speckey"; box.className = "popdlg";
    box.style.cssText = "position:absolute;left:50%;top:46px;transform:translateX(-50%);width:min(360px,92vw);" +
      "background:#23272f;color:#cfd3da;border:1px solid #3a404a;border-radius:8px;padding:12px 14px;z-index:140;" +
      "box-shadow:0 8px 30px #000a;font:13px/1.6 'Microsoft YaHei',sans-serif;";
    let h = "<b style='color:#e6c07b'>选择特殊键</b>（ESC/回车/F1 等无法用“捕获按键”录入的键）" +
            "<div style='display:flex;flex-wrap:wrap;gap:6px;margin-top:10px'>";
    for (const [name, label] of SPECIAL_KEYS)
      h += `<button data-k="${name}" style="background:#2f343d;color:#cfd3da;border:1px solid #444;` +
           `border-radius:4px;padding:3px 8px;cursor:pointer;min-width:42px">${label}</button>`;
    h += "</div><div style='margin-top:10px;text-align:right'>" +
         "<button id='speckcancel' style='background:#2f343d;color:#cfd3da;border:1px solid #444;border-radius:4px;padding:3px 12px;cursor:pointer'>取消</button></div>";
    box.innerHTML = h;
    document.body.appendChild(box);
    box.querySelectorAll("[data-k]").forEach((b) =>
      b.onclick = () => { box.remove(); onPick(b.getAttribute("data-k")); });
    box.querySelector("#speckcancel").onclick = () => box.remove();
  }

  // ---- 模板缩略图：本地文件不让网页直接读，由 Python 读成 data URL 回传，这里缓存 ----
  const imgCache = {};   // path -> HTMLImageElement | "loading" | "fail"
  function getThumb(path) {
    if (!path) return null;
    const c = imgCache[path];
    if (c === "loading" || c === "fail") return null;
    if (c) return c;
    imgCache[path] = "loading";
    try {
      Promise.resolve(api().image_data_url(path)).then((url) => {
        if (!url) { imgCache[path] = "fail"; return; }
        const im = new Image();
        im.onload = () => { imgCache[path] = im; if (canvas) canvas.setDirty(true, true); };
        im.onerror = () => { imgCache[path] = "fail"; };
        im.src = url;
      }).catch(() => { imgCache[path] = "fail"; });
    } catch (e) { delete imgCache[path]; }   // api 暂不可用：清掉标记，下帧再试
    return null;
  }
  function wrapText(ctx, text, maxW) {
    const out = [];
    for (const para of String(text).split("\n")) {
      let line = "";
      for (const ch of para) {
        if (ctx.measureText(line + ch).width > maxW && line) { out.push(line); line = ch; }
        else line += ch;
      }
      out.push(line);
    }
    return out;
  }
  function roundRect(ctx, x, y, w, h, r) {
    r = Math.min(r, w / 2, h / 2);
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }
  // 收集本节点所有图片参数当前指向的路径。
  function nodePreviewPaths(node) {
    const out = [];
    for (const k of (node._previewKeys || [])) {
      const w = (node.widgets || []).find((x) => x._key === k);
      if (w && w.value) for (const p of String(w.value).split(",").map((s) => s.trim())) if (p) out.push(p);
    }
    return out;
  }
  function baseName(p) { return String(p).split(/[\\/]/).pop(); }

  // 节点下方“附属卡片”（描述+缩略图）的高度，与 nodeDrawForeground 的画法一致；用于分组框包住卡片。
  function cardHeightOf(node, ctx) {
    const note = node._note || "";
    const paths = nodePreviewPaths(node);
    if (!note && !paths.length) return 0;
    const inner = Math.max(1, node.size[0] - 2 * CARD.PAD);
    ctx.font = "12px 'Microsoft YaHei',sans-serif";
    const noteLines = note ? wrapText(ctx, "📝 " + note, inner).length : 0;
    const shown = Math.min(paths.length, CARD.CAP);
    const perRow = Math.max(1, Math.floor(inner / (CARD.TH + CARD.GAP)));
    const rows = paths.length ? Math.ceil(shown / perRow) : 0;
    const extra = paths.length > shown ? CARD.NOTE_LH : 0;
    let bodyH = noteLines * CARD.NOTE_LH + rows * (CARD.TH + CARD.GAP) + extra;
    if (noteLines && rows) bodyH += CARD.DIV;
    return CARD.CGAP + CARD.PAD * 2 + bodyH;
  }

  // 画“分组框”（在节点后面，随成员节点自动包裹）。onDrawBackground 在画布变换内调用，用图坐标。
  const GROUP_PAD = 16, GROUP_TOP = 14;   // 四周留白（顶部留少许，组名做成“标签页”放在框上沿之上，不挤占内部）
  function groupColor(g, i) { return g.color || GROUP_COLORS[i % GROUP_COLORS.length]; }
  function groupTabText(g) { return "⠿ " + (g.title || "分组"); }   // 标签页带抓手图标，提示这一小块可拖动整组
  // 依据背景色亮度选黑/白文字，保证标题在分组色上清晰可读。
  function contrastText(hex) {
    const m = /^#?([0-9a-f]{6})$/i.exec(String(hex || ""));
    if (!m) return "#ffffff";
    const n = parseInt(m[1], 16), r = (n >> 16) & 255, g = (n >> 8) & 255, b = n & 255;
    return (0.299 * r + 0.587 * g + 0.114 * b) > 150 ? "#15181d" : "#ffffff";
  }
  function groupBox(g, ctx) {
    let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9, any = false;
    for (const mid of (g.members || [])) {
      const n = nodeByOurId(mid);
      if (!n) continue;
      any = true;
      const top = n.pos[1] - (LiteGraph.NODE_TITLE_HEIGHT || 30);
      const bottom = n.pos[1] + n.size[1] + ((n.flags && n.flags.collapsed) ? 0 : cardHeightOf(n, ctx));
      x0 = Math.min(x0, n.pos[0]); y0 = Math.min(y0, top);
      x1 = Math.max(x1, n.pos[0] + n.size[0]); y1 = Math.max(y1, bottom);
    }
    if (!any) return null;
    return [x0 - GROUP_PAD, y0 - GROUP_TOP, (x1 - x0) + 2 * GROUP_PAD, (y1 - y0) + GROUP_TOP + GROUP_PAD];
  }

  // ============ 可折叠子图（把一个分组折叠成一个紧凑“子图节点”）============
  // 纯编辑器视图层：折叠只隐藏成员节点的“显示与命中”，collect() 输出的底层扁平图不变（引擎照常跑）。
  // 折叠后，把“跨越分组边界”的连线汇成箱体左/右侧的输入/输出端口，并列出该组被钉选的参数，
  // 看起来像一个独立节点；双击箱体即展开。组内连线在折叠态隐藏。
  const SUBG = { TITLE_H: 28, PORT_GAP: 22, PORT_R: 4.5, PAD: 10, MINW: 156, EXEC: "#e6a23c", DATA: "#59b6c7" };
  function collapsedGroupList() {           // [{g, i}]：collapsed 且仍有成员的组
    const out = [];
    groupDefs.forEach((g, i) => { if (g.collapsed && (g.members || []).length) out.push({ g, i }); });
    return out;
  }
  function subgPortLabel(node, slot, isInput) {
    if (slot.type === "exec") return node.title || (isInput ? "入口" : "出口");   // exec 端口用成员节点名更达意
    return slot.label || slot.name || (isInput ? "入" : "出");                    // 数据端口用槽位名（如“食物”）
  }
  // 某折叠组的边界端口：外部→组内=输入端口；组内→外部=输出端口。按 (成员,槽位) 去重，每个唯一槽位一个端口。
  function subgPorts(g) {
    const members = new Set(g.members || []);
    const ins = [], outs = [], seenI = new Set(), seenO = new Set();
    for (const k in (graph.links || {})) {
      const l = graph.links[k]; if (!l) continue;
      const oIn = members.has(l.origin_id), tIn = members.has(l.target_id);
      if (oIn === tIn) continue;   // 都在组内（内部线）或都在组外（无关）→ 非边界
      if (tIn) {
        const n = graph.getNodeById(l.target_id); if (!n || !n.inputs || !n.inputs[l.target_slot]) continue;
        const key = l.target_id + "#i#" + l.target_slot; if (seenI.has(key)) continue; seenI.add(key);
        const slot = n.inputs[l.target_slot];
        ins.push({ key, label: subgPortLabel(n, slot, true), type: slot.type });
      } else {
        const n = graph.getNodeById(l.origin_id); if (!n || !n.outputs || !n.outputs[l.origin_slot]) continue;
        const key = l.origin_id + "#o#" + l.origin_slot; if (seenO.has(key)) continue; seenO.add(key);
        const slot = n.outputs[l.origin_slot];
        outs.push({ key, label: subgPortLabel(n, slot, false), type: slot.type });
      }
    }
    return { ins, outs };
  }
  // 该组被钉选（出现在控制面板）的参数：折叠箱体上列出 [{label, val}]，即“挑选出的一部分有用参数”。
  function subgPinnedParams(g) {
    const members = new Set(g.members || []), out = [];
    for (const [nid, key, custom] of panelPins) {
      if (!members.has(nid)) continue;
      const n = nodeByOurId(nid); if (!n) continue;
      const w = (n.widgets || []).find((x) => x._key === key);
      const label = custom || (w && w.name) || key;
      let val = w ? w.value : (n.properties ? n.properties[key] : undefined);
      if (val === true) val = "是"; else if (val === false) val = "否";
      out.push({ label, val: (val == null ? "" : String(val)) });
    }
    return out;
  }
  // 折叠箱体几何：锚定在成员包围盒左上角（成员仍保留 pos），尺寸按端口数 / 标题 / 参数自适应。
  function subgBox(g, ctx) {
    const bb = groupBox(g, ctx); if (!bb) return null;
    const ports = subgPorts(g), pins = subgPinnedParams(g);
    const rows = Math.max(ports.ins.length, ports.outs.length, 1);
    ctx.font = "bold 13px 'Microsoft YaHei',sans-serif";
    const titleW = ctx.measureText("◳ " + (g.title || "子图") + "  ▸").width;
    ctx.font = "12px 'Microsoft YaHei',sans-serif";
    let li = 0, lo = 0, lp = 0;
    for (const p of ports.ins) li = Math.max(li, ctx.measureText(p.label).width);
    for (const p of ports.outs) lo = Math.max(lo, ctx.measureText(p.label).width);
    for (const p of pins) lp = Math.max(lp, ctx.measureText(p.label + "：" + p.val).width);
    const w = Math.max(SUBG.MINW, titleW + 24, li + lo + 34, lp + 22);
    const h = SUBG.TITLE_H + rows * SUBG.PORT_GAP + (pins.length ? pins.length * 17 + 8 : SUBG.PAD);
    return { x: bb[0], y: bb[1], w, h, ports, pins };
  }
  function subgPortPos(box, side, idx) {
    const y = box.y + SUBG.TITLE_H + SUBG.PORT_GAP * (idx + 0.5);
    return side === "in" ? [box.x, y] : [box.x + box.w, y];
  }
  // 一次性算出本帧折叠所需：每个组的箱体 + 边界端口 key→屏幕坐标（供连线改接）。图很小，按需重算即可。
  function foldInfo() {
    const ctx = _measCtx(), boxes = [], portPos = new Map();
    if (!ctx) return { boxes, portPos };
    for (const { g, i } of collapsedGroupList()) {
      const box = subgBox(g, ctx); if (!box) continue;
      box.ports.ins.forEach((p, idx) => portPos.set(p.key, subgPortPos(box, "in", idx)));
      box.ports.outs.forEach((p, idx) => portPos.set(p.key, subgPortPos(box, "out", idx)));
      boxes.push({ g, i, box });
    }
    return { boxes, portPos };
  }
  // 折叠态的连线绘制：跳过“内部线”，把跨边界线的隐藏端改接到箱体端口（其余正常）。复用 LiteGraph 的 renderLink。
  function drawFoldedConnections(ctx) {
    const fi = foldInfo();
    ctx.lineWidth = this.connections_width; ctx.globalAlpha = this.editor_alpha;
    for (const node of this.graph._nodes) {
      if (!node.inputs) continue;
      for (let i = 0; i < node.inputs.length; i++) {
        const input = node.inputs[i]; if (!input || input.link == null) continue;
        const link = this.graph.links[input.link]; if (!link) continue;
        const start = this.graph.getNodeById(link.origin_id); if (!start) continue;
        const oH = foldHidden.has(link.origin_id), tH = foldHidden.has(node._id);
        if (oH && tH) continue;   // 内部线：折叠态不画
        let a = oH ? fi.portPos.get(link.origin_id + "#o#" + link.origin_slot)
                   : start.getConnectionPos(false, link.origin_slot, [0, 0]);
        let b = tH ? fi.portPos.get(node._id + "#i#" + i)
                   : node.getConnectionPos(true, i, [0, 0]);
        if (!a || !b) continue;
        const ss = start.outputs[link.origin_slot];
        const sdir = (ss && ss.dir) || LiteGraph.RIGHT, edir = (input && input.dir) || LiteGraph.LEFT;
        this.renderLink(ctx, [a[0], a[1]], [b[0], b[1]], link, false, 0, null, sdir, edir);
      }
    }
    ctx.globalAlpha = 1;
  }
  // 画一个折叠子图箱体（标题栏=拖动手柄/双击展开；左输入、右输出端口；下方列出钉选参数）。
  function drawSubgBox(ctx, g, i, box) {
    const col = groupColor(g, i), tcol = contrastText(col);
    roundRect(ctx, box.x, box.y, box.w, box.h, 9);
    ctx.fillStyle = "#20242c"; ctx.globalAlpha = 0.98; ctx.fill(); ctx.globalAlpha = 1;
    ctx.fillStyle = col + "26"; ctx.fill();                          // 组色淡填充
    ctx.lineWidth = 1.5; ctx.strokeStyle = col; ctx.stroke();
    roundRect(ctx, box.x, box.y, box.w, SUBG.TITLE_H, 9);            // 标题栏（实色）
    ctx.fillStyle = col; ctx.fill();
    ctx.fillStyle = tcol; ctx.font = "bold 13px 'Microsoft YaHei',sans-serif";
    ctx.textBaseline = "middle"; ctx.textAlign = "left";
    ctx.fillText("◳ " + (g.title || "子图"), box.x + 10, box.y + SUBG.TITLE_H / 2);
    ctx.textAlign = "right"; ctx.fillText("▸", box.x + box.w - 9, box.y + SUBG.TITLE_H / 2);  // ▸=可双击展开
    // 端口：exec=三角、data=圆点；标签在内侧
    const drawPort = (p, pos, side) => {
      const c = p.type === "exec" ? SUBG.EXEC : SUBG.DATA;
      ctx.fillStyle = c; ctx.strokeStyle = "#11141a"; ctx.lineWidth = 1;
      ctx.beginPath();
      if (p.type === "exec") {
        const d = SUBG.PORT_R + 1, sx = side === "in" ? 1 : -1;
        ctx.moveTo(pos[0] - sx * d, pos[1] - d); ctx.lineTo(pos[0] + sx * d, pos[1]); ctx.lineTo(pos[0] - sx * d, pos[1] + d); ctx.closePath();
      } else { ctx.arc(pos[0], pos[1], SUBG.PORT_R, 0, Math.PI * 2); }
      ctx.fill(); ctx.stroke();
      ctx.fillStyle = "#c7ccd6"; ctx.font = "12px 'Microsoft YaHei',sans-serif"; ctx.textBaseline = "middle";
      if (side === "in") { ctx.textAlign = "left"; ctx.fillText(p.label, pos[0] + 9, pos[1]); }
      else { ctx.textAlign = "right"; ctx.fillText(p.label, pos[0] - 9, pos[1]); }
    };
    box.ports.ins.forEach((p, idx) => drawPort(p, subgPortPos(box, "in", idx), "in"));
    box.ports.outs.forEach((p, idx) => drawPort(p, subgPortPos(box, "out", idx), "out"));
    // 钉选参数（“挑选出的一部分有用参数”）：标签:值，逐行列在端口下方
    let py = box.y + SUBG.TITLE_H + Math.max(box.ports.ins.length, box.ports.outs.length, 1) * SUBG.PORT_GAP + 4;
    ctx.font = "12px 'Microsoft YaHei',sans-serif"; ctx.textAlign = "left"; ctx.textBaseline = "middle";
    for (const pp of box.pins) {
      ctx.fillStyle = "#8b929e"; ctx.fillText(pp.label + "：", box.x + 10, py + 8);
      const lw = ctx.measureText(pp.label + "：").width;
      ctx.fillStyle = "#dfe4ec"; ctx.fillText(pp.val, box.x + 10 + lw, py + 8);
      py += 17;
    }
    // 运行时若组内有节点正在本帧路径上，箱体描金光提示“此处有活动”
    if (runSession) {
      let active = false;
      for (const m of (g.members || [])) { if (runPath.has(m) || runDataNodes.has(m)) { active = true; break; } }
      if (active) {
        const pulse = 0.5 + 0.5 * Math.sin(runPhase * 3.0);
        ctx.save(); ctx.strokeStyle = "#ffd23f"; ctx.lineWidth = 2 + 2 * pulse;
        ctx.shadowColor = "#ffb300"; ctx.shadowBlur = 10 + 14 * pulse;
        roundRect(ctx, box.x - 1, box.y - 1, box.w + 2, box.h + 2, 9); ctx.stroke(); ctx.restore();
      }
    }
  }
  // 性能监控 × 折叠：在子图箱体上汇总「组内成员自身耗时之和 · 组内最后完成时刻的累计」，
  // 这样把一段折叠起来后，仍能一眼看出这一整段本帧花了多少（便于定位耗时大头的“段”）。
  function drawFoldedTimePills(ctx) {
    if (!runSession) return;
    const mctx = _measCtx(); if (!mctx) return;
    for (const { g } of collapsedGroupList()) {
      let sumSelf = 0, maxCum = 0, any = false;
      for (const m of (g.members || [])) {
        const tm = runTimes[m]; if (!tm) continue;
        any = true; sumSelf += tm[0]; if (tm[1] > maxCum) maxCum = tm[1];
      }
      if (!any) continue;
      const box = subgBox(g, mctx); if (!box) continue;
      drawTimePill(ctx, box.x + box.w, box.y - 3, sumSelf, maxCum);
    }
  }
  // 补画 LiteGraph 漏掉的“汇入同一入口”的执行连线：它的 drawConnections 每个输入口只画 input.link 那一条，
  // 而我们允许 exec 输入多条汇入(见 vendor 改动)，这里把其余 exec 连线按相同样条补上，保证都可见。
  const EXEC_FANIN_PAL = ["#e6a23c", "#9b8cff", "#5ad1a0", "#e36a9e", "#5ab0e6"];  // 汇入线配色，便于分清
  function drawExtraExecLinks(ctx) {
    if (!graph) return;
    const links = graph.links || {};
    // 按“目标入口”分组：同一入口的多条汇入线分别配色 + 纵向错开弯曲，避免挤在一起分不清。
    const byTarget = {};
    for (const k in links) {
      const l = links[k];
      if (!l || l.type !== "exec") continue;
      const b = graph.getNodeById(l.target_id);
      if (!b || !b.inputs || !b.inputs[l.target_slot]) continue;
      if (b.inputs[l.target_slot].link === l.id) continue;     // 这条 LiteGraph 已画
      const key = l.target_id + "" + l.target_slot;
      (byTarget[key] = byTarget[key] || []).push(l);
    }
    const fi = foldHidden.size ? foldInfo() : null;   // 折叠态：补画的汇入线同样要跳过内部线 / 改接箱体端口
    ctx.save();
    for (const key in byTarget) {
      byTarget[key].forEach((l, j) => {
        const a = graph.getNodeById(l.origin_id), b = graph.getNodeById(l.target_id);
        if (!a || !b || !a.outputs || !a.outputs[l.origin_slot]) return;
        const oH = foldHidden.has(l.origin_id), tH = foldHidden.has(l.target_id);
        if (oH && tH) return;     // 内部汇入线：折叠态不画
        let pa, pb;
        try {
          pa = oH ? fi.portPos.get(l.origin_id + "#o#" + l.origin_slot) : a.getConnectionPos(false, l.origin_slot, [0, 0]);
          pb = tH ? fi.portPos.get(l.target_id + "#i#" + l.target_slot) : b.getConnectionPos(true, l.target_slot, [0, 0]);
        } catch (e) { return; }
        if (!pa || !pb) return;
        const cc = linkCtrlPts([pa[0], pa[1]], [pb[0], pb[1]]);
        const bow = ((j % 2 === 0) ? 1 : -1) * (Math.floor(j / 2) + 1) * 24;   // 交错上下弯，错开各条线
        ctx.strokeStyle = EXEC_FANIN_PAL[j % EXEC_FANIN_PAL.length]; ctx.lineWidth = 2.5;
        ctx.beginPath(); ctx.moveTo(pa[0], pa[1]);
        ctx.bezierCurveTo(cc[0][0], cc[0][1] + bow, cc[1][0], cc[1][1] + bow, pb[0], pb[1]); ctx.stroke();
      });
    }
    ctx.restore();
  }
  function drawGroups(ctx) {
    drawExtraExecLinks(ctx);     // 先补画 LiteGraph 漏掉的“汇入”执行连线（与分组无关，总要画）
    if (!groupDefs.length) return;
    ctx.save();
    groupDefs.forEach((g, i) => {
      if (g.collapsed) {         // 折叠态：画紧凑“子图节点”箱体（替代成员包裹框）
        const sbox = subgBox(g, ctx);
        if (sbox) drawSubgBox(ctx, g, i, sbox);
        return;
      }
      const box = groupBox(g, ctx);
      if (!box) return;
      const [x, y, w, h] = box, col = groupColor(g, i), name = groupTabText(g);
      roundRect(ctx, x, y, w, h, 10);
      ctx.fillStyle = col + "22"; ctx.fill();          // 半透明填充
      ctx.strokeStyle = col + "cc"; ctx.lineWidth = 2; ctx.stroke();
      // 组名做成“标签页”贴在框的左上沿之上——不会和框内第一个节点重叠
      ctx.font = "bold 13px 'Microsoft YaHei',sans-serif";
      ctx.textBaseline = "alphabetic";
      const tw = ctx.measureText(name).width;
      roundRect(ctx, x, y - 18, tw + 18, 20, 5);
      ctx.fillStyle = col; ctx.fill();
      ctx.fillStyle = contrastText(col);
      ctx.fillText(name, x + 9, y - 4);
    });
    ctx.restore();
  }

  // 通用的小输入对话框（返回 Promise<string|null>，取消为 null）。
  function askText(title, value) {
    return new Promise((resolve) => {
      document.getElementById("askdlg")?.remove();
      const box = document.createElement("div");
      box.id = "askdlg"; box.className = "popdlg";
      box.style.cssText = "position:absolute;left:50%;top:46px;transform:translateX(-50%);width:min(360px,90vw);" +
        "background:#23272f;color:#cfd3da;border:1px solid #3a404a;border-radius:8px;padding:14px 16px;z-index:150;" +
        "box-shadow:0 8px 30px #000a;font:13px/1.6 'Microsoft YaHei',sans-serif;";
      box.innerHTML = `<b style='color:#e6c07b'>${esc(title)}</b>` +
        `<input id='ask_in' style='width:100%;margin-top:8px;background:#15171c;color:#cfd3da;border:1px solid #444;` +
        `border-radius:4px;padding:6px;box-sizing:border-box;font:13px "Microsoft YaHei",sans-serif'/>` +
        "<div style='margin-top:10px;text-align:right'>" +
        "<button id='ask_ok' style='background:#2f343d;color:#cfd3da;border:1px solid #444;border-radius:4px;padding:3px 12px;cursor:pointer'>确定</button> " +
        "<button id='ask_cancel' style='background:#2f343d;color:#cfd3da;border:1px solid #444;border-radius:4px;padding:3px 12px;cursor:pointer'>取消</button></div>";
      document.body.appendChild(box);
      const inp = box.querySelector("#ask_in");
      inp.value = value == null ? "" : value; inp.focus(); inp.select();
      const done = (v) => { box.remove(); resolve(v); };
      inp.onkeydown = (e) => { e.stopPropagation(); if (e.key === "Enter") done(inp.value); else if (e.key === "Escape") done(null); };
      box.querySelector("#ask_ok").onclick = () => done(inp.value);
      box.querySelector("#ask_cancel").onclick = () => done(null);
    });
  }

  // 一个节点最多属于一个分组（便于在节点上用单一颜色标记）。返回所属分组下标，无则 -1。
  function nodeGroupIndex(ourId) {
    return groupDefs.findIndex((g) => (g.members || []).includes(ourId));
  }
  function groupColorOf(ourId) {
    const i = nodeGroupIndex(ourId);
    return i >= 0 ? groupColor(groupDefs[i], i) : null;
  }
  // 把若干节点设为某分组（gi=null 表示移出所有分组）：先从所有组移除，再加入目标；清掉空组。
  function setNodesGroup(ourIds, gi) {
    for (const g of groupDefs) g.members = (g.members || []).filter((m) => !ourIds.includes(m));
    if (gi != null && groupDefs[gi]) {
      for (const id of ourIds) if (!groupDefs[gi].members.includes(id)) groupDefs[gi].members.push(id);
    }
    groupDefs = groupDefs.filter((g) => (g.members || []).length);
    refreshGroups();
  }
  // 分组变化后：把每个节点的标题栏染成所属分组色 + 重绘 + 计脏。
  function refreshFold() {   // 重算“被折叠隐藏的成员”集合；任何改动分组的地方都应调用
    foldHidden = new Set();
    for (const g of groupDefs) if (g.collapsed) for (const m of (g.members || [])) foldHidden.add(m);
  }
  function refreshGroups() {
    refreshFold();
    applyGroupColors();
    if (canvas) canvas.setDirty(true, true);
    scheduleSnap(); refreshDirty();
  }
  function applyGroupColors() {
    for (const n of (graph && graph._nodes) || []) {
      const c = groupColorOf(n._id);
      if (c) { n.color = c; n.bgcolor = c + "1f"; n._titleTextColor = contrastText(c); }
      else { delete n.color; delete n.bgcolor; delete n._titleTextColor; }
    }
  }

  // 节点右键“分组…”：把当前(或选中的多个)节点指派到某分组 / 新建 / 移出；并可管理分组。
  async function assignGroupDialog(nodes) {
    const ids = nodes.map((n) => n._id);
    document.getElementById("grpdlg")?.remove();
    const box = document.createElement("div");
    box.id = "grpdlg"; box.className = "popdlg";
    box.style.cssText = "position:absolute;left:50%;top:46px;transform:translateX(-50%);width:min(460px,92vw);" +
      "max-height:80vh;overflow:auto;background:#23272f;color:#cfd3da;border:1px solid #3a404a;border-radius:8px;" +
      "padding:14px 16px;z-index:150;box-shadow:0 8px 30px #000a;font:13px/1.6 'Microsoft YaHei',sans-serif;";
    document.body.appendChild(box);
    const render = () => {
      // 每次重绘都重算“当前所属分组”，使单选框跟着实时变化（多选不一致则不预选）
      const curIdx = ids.map(nodeGroupIndex);
      const same = curIdx.every((x) => x === curIdx[0]) ? curIdx[0] : -2;
      let h = `<b style='color:#e6c07b'>分组</b>（已选 ${ids.length} 个节点）` +
              "<div style='color:#7f8895;margin:2px 0 8px'>一个节点只属于一个分组；选择即生效。</div>";
      h += `<label style="display:block;cursor:pointer;padding:4px 0"><input type="radio" name="grp" value="-1" ${same === -1 ? "checked" : ""}> 不分组</label>`;
      groupDefs.forEach((g, i) => {
        const col = groupColor(g, i);
        h += `<label style="display:flex;align-items:center;gap:6px;cursor:pointer;padding:4px 0">` +
             `<input type="radio" name="grp" value="${i}" ${same === i ? "checked" : ""}>` +
             `<span data-swatch="${i}" style="width:12px;height:12px;border-radius:3px;background:${col};flex:none"></span>` +
             `<span style="flex:1">${esc(g.title || "分组")}</span>` +
             `<span style="color:#7f8895">${(g.members || []).length}个</span>` +
             `<input type="color" data-color="${i}" value="${col}" title="自定义颜色" style="width:24px;height:20px;padding:0;border:1px solid #444;background:#15171c;cursor:pointer">` +
             `<button data-fold="${i}" title="折叠成一个紧凑子图节点 / 展开还原" style="background:${g.collapsed ? "#314a6b" : "#2f343d"};color:${g.collapsed ? "#cfe3ff" : "#cfd3da"};border:1px solid #444;border-radius:4px;padding:1px 7px;cursor:pointer">${g.collapsed ? "展开" : "折叠"}</button>` +
             `<button data-ren="${i}" style="background:#2f343d;color:#cfd3da;border:1px solid #444;border-radius:4px;padding:1px 7px;cursor:pointer">改名</button>` +
             `<button data-del="${i}" style="background:#3a2222;color:#ffb3b3;border:1px solid #a33;border-radius:4px;padding:1px 7px;cursor:pointer">解散</button></label>`;
      });
      h += "<div style='margin-top:10px'><button id='grp_new' style='background:#2f343d;color:#cfd3da;border:1px solid #444;border-radius:4px;padding:3px 10px;cursor:pointer'>＋ 新建分组（含选中节点）</button>" +
        "<span style='float:right;color:#6b727d;font-size:12px;padding-top:5px'>点窗口外关闭</span></div>";
      box.innerHTML = h;
      box.querySelectorAll("input[name='grp']").forEach((r) =>
        r.onchange = () => { const v = +r.value; setNodesGroup(ids, v < 0 ? null : v); render(); });
      box.querySelectorAll("[data-color]").forEach((c) =>
        c.oninput = () => {
          const i = +c.getAttribute("data-color");
          groupDefs[i].color = c.value;
          const sw = box.querySelector(`[data-swatch="${i}"]`);
          if (sw) sw.style.background = c.value;     // 弹窗里的色块也实时跟随
          refreshGroups();                            // 画布上的节点标题色/分组框实时跟随
        });
      box.querySelectorAll("[data-fold]").forEach((b) => b.onclick = () => {
        const i = +b.getAttribute("data-fold");
        setGroupCollapsed(i, !groupDefs[i].collapsed); render();
      });
      box.querySelectorAll("[data-ren]").forEach((b) => b.onclick = async () => {
        const i = +b.getAttribute("data-ren");
        const name = await askText("分组改名", groupDefs[i].title || "");
        if (name != null) { groupDefs[i].title = name.trim() || "分组"; refreshGroups(); render(); }
      });
      box.querySelectorAll("[data-del]").forEach((b) => b.onclick = () => {
        groupDefs.splice(+b.getAttribute("data-del"), 1); refreshGroups(); render();
      });
      box.querySelector("#grp_new").onclick = async () => {
        const name = await askText("新建分组", "分组" + (groupDefs.length + 1));
        if (name == null) return;
        groupDefs.push({ title: name.trim() || "分组", color: GROUP_COLORS[groupDefs.length % GROUP_COLORS.length], members: [] });
        setNodesGroup(ids, groupDefs.length - 1); render();
      };
    };
    groupDlgRender = render;   // 撤销/重做后若弹窗仍开着，据此刷新
    render();
  }

  // 在节点上画：①已改参数的橙色小点；②节点下方“附属卡片”（描述 📝 + 模板缩略图网格）。
  function nodeDrawForeground(ctx) {
    if (this.flags && this.flags.collapsed) return;
    // ① 已修改参数标记：在该参数控件行右侧画橙点（last_y 是 LiteGraph 画该控件时记下的 y）
    for (const w of (this.widgets || [])) {
      if (paramChanged(w) && w.last_y != null) {
        ctx.save();
        ctx.fillStyle = "#e6a23c";
        ctx.beginPath();
        ctx.arc(this.size[0] - 7, w.last_y + (LiteGraph.NODE_WIDGET_HEIGHT || 20) / 2, 3.5, 0, Math.PI * 2);
        ctx.fill();
        ctx.restore();
      }
    }
    // 节点的分组归属用“标题栏底色”表达（见 applyGroupColors）；组名只在分组框的标签页上显示一次，
    // 不在每个节点上重复，避免同名标签堆叠、保持简洁。
    // 试运行的高亮/数据值/连线流动统一画在所有节点之上（见 canvas.onDrawForeground = drawRunOverlay）。
    // ③ 附属卡片
    const note = this._note || "";
    const paths = nodePreviewPaths(this);
    if (!note && !paths.length) return;
    const W = this.size[0], inner = Math.max(1, W - 2 * CARD.PAD);
    ctx.font = "12px 'Microsoft YaHei',sans-serif";
    const noteLines = note ? wrapText(ctx, "📝 " + note, inner) : [];
    const shown = Math.min(paths.length, CARD.CAP);
    const perRow = Math.max(1, Math.floor(inner / (CARD.TH + CARD.GAP)));
    const rows = paths.length ? Math.ceil(shown / perRow) : 0;
    const extraLine = paths.length > shown ? CARD.NOTE_LH : 0;
    let bodyH = noteLines.length * CARD.NOTE_LH + rows * (CARD.TH + CARD.GAP) + extraLine;
    if (noteLines.length && rows) bodyH += CARD.DIV;
    const cardY = this.size[1] + CARD.CGAP, cardH = CARD.PAD * 2 + bodyH;
    // 卡片背景（圆角 + 细边，和节点连成一体的观感）
    ctx.save();
    roundRect(ctx, 0, cardY, W, cardH, 6);
    ctx.fillStyle = "#1b1f27"; ctx.fill();
    ctx.strokeStyle = "#3a404a"; ctx.lineWidth = 1; ctx.stroke();
    let y = cardY + CARD.PAD;
    if (noteLines.length) {
      ctx.fillStyle = "#c9b87a"; ctx.font = "12px 'Microsoft YaHei',sans-serif";
      for (const ln of noteLines) { ctx.fillText(ln, CARD.PAD, y + 11); y += CARD.NOTE_LH; }
      if (rows) {
        y += CARD.DIV / 2;
        ctx.strokeStyle = "#2c323c"; ctx.beginPath();
        ctx.moveTo(CARD.PAD, y); ctx.lineTo(W - CARD.PAD, y); ctx.stroke();
        y += CARD.DIV / 2;
      }
    }
    let x = CARD.PAD, c = 0;
    for (let i = 0; i < shown; i++) {
      const pth = paths[i], im = getThumb(pth);
      roundRect(ctx, x, y, CARD.TH, CARD.TH, 4);
      ctx.fillStyle = "#11141a"; ctx.fill();
      if (im) {
        ctx.save(); ctx.clip();
        const r = Math.min(CARD.TH / im.width, CARD.TH / im.height);
        const dw = im.width * r, dh = im.height * r;
        ctx.drawImage(im, x + (CARD.TH - dw) / 2, y + (CARD.TH - dh) / 2, dw, dh);
        ctx.restore();
        roundRect(ctx, x, y, CARD.TH, CARD.TH, 4);
      } else {
        ctx.fillStyle = "#666"; ctx.font = "11px sans-serif";
        ctx.fillText(imgCache[pth] === "fail" ? "?" : "…", x + CARD.TH / 2 - 3, y + CARD.TH / 2 + 4);
      }
      ctx.strokeStyle = "#3a404a"; ctx.lineWidth = 1; ctx.stroke();
      x += CARD.TH + CARD.GAP;
      if (++c >= perRow) { c = 0; x = CARD.PAD; y += CARD.TH + CARD.GAP; }
    }
    if (extraLine) {
      if (c !== 0) y += CARD.TH + CARD.GAP;
      ctx.fillStyle = "#7f8895"; ctx.font = "11px sans-serif";
      ctx.fillText("+" + (paths.length - shown) + " 张", CARD.PAD, y + 11);
    }
    ctx.restore();
  }
  // 节点右键菜单追加“编辑描述/清除描述”。
  function nodeExtraMenu(_canvas, options) {
    const node = this;
    options.push(null, {
      content: node._note ? "编辑描述…" : "添加描述…",   // 清空描述在编辑框里删文字即可，不再单列“清除描述”
      callback: () => editNote(node),
    });
    // 分组：作用于“当前选中的多个节点”（若本节点在选区内），否则就本节点。
    const sel = Object.values((canvas && canvas.selected_nodes) || {});
    const targets = (sel.length > 1 && sel.includes(node)) ? sel : [node];
    const gi = nodeGroupIndex(node._id);
    options.push({
      content: gi >= 0 ? `分组：${groupDefs[gi].title}…` : "分组…",
      callback: () => assignGroupDialog(targets),
    });
    // —— 试运行调试：断点 / 运行到此节点 ——
    options.push(null, {
      content: breakpoints.has(node._id) ? "取消断点 ⭕" : "设为断点 🔴",
      callback: () => toggleBreakpoint(node._id),
    }, {
      content: "运行到此节点 ⏭",
      callback: () => runToNode(node._id),
    });
  }
  function editNote(node) {
    document.getElementById("notedlg")?.remove();
    const box = document.createElement("div");
    box.id = "notedlg"; box.className = "popdlg";
    box.style.cssText = "position:absolute;left:50%;top:46px;transform:translateX(-50%);width:min(420px,90vw);" +
      "background:#23272f;color:#cfd3da;border:1px solid #3a404a;border-radius:8px;padding:14px 16px;z-index:130;" +
      "box-shadow:0 8px 30px #000a;font:13px/1.6 'Microsoft YaHei',sans-serif;";
    box.innerHTML = "<b style='color:#e6c07b'>节点描述</b>（说明这个节点的作用，仅展示、不影响运行）<br>" +
      "<textarea id='notetext' style='width:100%;height:84px;margin-top:8px;background:#15171c;color:#cfd3da;" +
      "border:1px solid #444;border-radius:4px;padding:6px;font:13px/1.5 \"Microsoft YaHei\",sans-serif;box-sizing:border-box'></textarea>" +
      "<div style='margin-top:8px;text-align:right'>" +
      "<button id='noteok' style='background:#2f343d;color:#cfd3da;border:1px solid #444;border-radius:4px;padding:3px 12px;cursor:pointer'>确定</button> " +
      "<button id='notecancel' style='background:#2f343d;color:#cfd3da;border:1px solid #444;border-radius:4px;padding:3px 12px;cursor:pointer'>取消</button></div>";
    document.body.appendChild(box);
    const ta = box.querySelector("#notetext");
    ta.value = node._note || "";
    ta.focus();
    box.querySelector("#noteok").onclick = () => {
      node._note = ta.value.trim();
      box.remove();
      if (canvas) canvas.setDirty(true, true);
      scheduleSnap();
    };
    box.querySelector("#notecancel").onclick = () => box.remove();
  }

  // 多图参数的"列表编辑器"：逐条看缩略图+路径，可删除/上移/下移，并直接添加（选图/截模板）。
  // 所有改动即时写回控件（节点上的列表预览同步刷新），点关闭即结束。
  function editImageList(node, widget, p) {
    document.getElementById("imglist")?.remove();
    let paths = String(widget.value || "").split(",").map((s) => s.trim()).filter(Boolean);
    const box = document.createElement("div");
    box.id = "imglist"; box.className = "popdlg";
    box.style.cssText = "position:absolute;left:50%;top:46px;transform:translateX(-50%);width:min(520px,94vw);" +
      "max-height:80vh;overflow:auto;background:#23272f;color:#cfd3da;border:1px solid #3a404a;border-radius:8px;" +
      "padding:14px 16px;z-index:140;box-shadow:0 8px 30px #000a;font:13px/1.6 'Microsoft YaHei',sans-serif;";
    document.body.appendChild(box);
    const btnCss = "background:#2f343d;color:#cfd3da;border:1px solid #444;border-radius:4px;padding:2px 9px;cursor:pointer;margin-left:5px";
    const apply = () => {           // 即时写回控件 + 标脏 + 刷新画布
      widget.value = paths.join(",");
      if (node.properties) node.properties[p.key] = widget.value;
      if (canvas) canvas.setDirty(true, true);
      scheduleSnap(); refreshDirty();
    };
    function render() {
      let h = "<b style='color:#e6c07b'>编辑图片列表</b>（" + esc(p.label) + "，共 " + paths.length + " 张）" +
        "<div style='margin-top:8px'>";
      if (!paths.length) h += "<div style='color:#6b727d'>（空，点下方按钮添加图片）</div>";
      paths.forEach((pth, i) => {
        h += "<div data-i='" + i + "' style='display:flex;align-items:center;gap:8px;padding:5px 0;border-bottom:1px solid #2c323c'>" +
          "<img class='th' data-p='" + esc(pth) + "' style='width:40px;height:40px;object-fit:contain;flex:none;" +
          "background:#11141a;border:1px solid #3a404a;border-radius:4px'/>" +
          "<span style='flex:1;word-break:break-all;color:#aeb6c2' title='" + esc(pth) + "'>" + esc(baseName(pth)) + "</span>" +
          "<button data-act='up' " + (i === 0 ? "disabled" : "") + " style='" + btnCss + "'>↑</button>" +
          "<button data-act='down' " + (i === paths.length - 1 ? "disabled" : "") + " style='" + btnCss + "'>↓</button>" +
          "<button data-act='del' style='" + btnCss + ";color:#e88'>删除</button></div>";
      });
      h += "</div><div style='margin-top:12px;text-align:right'>" +
        "<button id='il_pick' style='" + btnCss + "'>＋ 选择图片</button>" +
        "<button id='il_cap' style='" + btnCss + "'>＋ 截取模板</button>" +
        "<button id='il_close' style='" + btnCss + ";border-color:#5a6' >关闭</button></div>";
      box.innerHTML = h;
      // 缩略图：本地文件不让网页直接读，向 Python 取 data URL（已缓存的直接复用 getThumb 的结果）
      box.querySelectorAll("img.th").forEach((im) => {
        const pp = im.getAttribute("data-p"), c = imgCache[pp];
        if (c && c !== "loading" && c !== "fail") im.src = c.src;
        else Promise.resolve(api().image_data_url(pp)).then((u) => { if (u) im.src = u; }).catch(() => {});
      });
      box.querySelectorAll("[data-act]").forEach((b) => {
        b.onclick = () => {
          const i = +b.closest("[data-i]").getAttribute("data-i"), act = b.getAttribute("data-act");
          if (act === "del") paths.splice(i, 1);
          else if (act === "up" && i > 0) { const t = paths[i - 1]; paths[i - 1] = paths[i]; paths[i] = t; }
          else if (act === "down" && i < paths.length - 1) { const t = paths[i + 1]; paths[i + 1] = paths[i]; paths[i] = t; }
          apply(); render();
        };
      });
      box.querySelector("#il_pick").onclick = () => {
        setStatus("正在打开图片选择框…");
        Promise.resolve(api().pick_templates(true)).then((arr) => {
          if (arr && arr.length) { paths = paths.concat(arr); apply(); render(); setStatus("已添加 " + arr.length + " 张图片"); }
          else setStatus("已取消选择图片");
        }).catch((e) => showError("选择图片失败：" + (e && (e.stack || e.message) || e)));
      };
      box.querySelector("#il_cap").onclick = () => {
        setStatus("框选模板区域…（Enter 保存 / Esc 取消）");
        Promise.resolve(api().capture_template()).then((path) => {
          if (path) { paths.push(path); apply(); render(); setStatus("已截取模板：" + path); }
          else setStatus("已取消截取模板");
        }).catch((e) => showError("截取模板失败：" + (e && (e.stack || e.message) || e)));
      };
      box.querySelector("#il_close").onclick = () => box.remove();
    }
    render();
  }

  // ---- 未保存修改标记（全局 ●未保存 + 每个参数的橙点/恢复）----
  let savedSig = null;           // 全局：上次保存/载入时 collect() 的签名
  let savedBaseline = null;      // 上次保存/载入时的规范化流程对象（解析自 savedSig），用于"修改内容"对比
  let savedParams = {};          // 每参数基线：nodeId -> { key: 基线值 }
  // 规范化签名：按 id/连线排序后再 JSON，使"点选节点导致的 z 序变化"(LiteGraph bringToFront 会重排
  // graph._nodes)不被误判为"未保存"。只有真正的参数/连线/位置/名称改动才算改动。
  function curSig() {
    try {
      const c = collect();
      const nodes = c.nodes.slice().sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
      const edges = c.edges.slice().sort((a, b) => {
        const ka = a.src + "|" + a.src_port + "|" + a.dst + "|" + a.dst_port;
        const kb = b.src + "|" + b.src_port + "|" + b.dst + "|" + b.dst_port;
        return ka < kb ? -1 : ka > kb ? 1 : 0;
      });
      const groups = (c.groups || []).map((g) => ({ title: g.title, color: g.color, collapsed: !!g.collapsed, members: g.members.slice().sort() }))
        .sort((a, b) => (a.title < b.title ? -1 : a.title > b.title ? 1 : 0));
      return JSON.stringify({ name: c.name, description: c.description, panel: c.panel, groups, nodes, edges });
    } catch (e) { return null; }
  }
  function markSaved() {         // 保存/载入后：把“当前”设为基线，清除所有标记
    savedParams = {};
    for (const n of graph._nodes) {
      const m = {}; for (const w of (n.widgets || [])) if (w._key) m[w._key] = w.value;
      savedParams[n._id] = m;
    }
    savedSig = curSig();
    try { savedBaseline = savedSig ? JSON.parse(savedSig) : null; } catch (e) { savedBaseline = null; }
    attachBaselineRefs();
    showDirty(false);
  }
  function attachBaselineRefs() { // 撤销/重做/排版会重建控件 -> 从 savedParams 重新挂上各控件的基线值
    for (const n of graph._nodes) {
      const m = savedParams[n._id] || {};
      for (const w of (n.widgets || [])) if (w._key) w._saved = (w._key in m) ? m[w._key] : w.value;
    }
  }
  function paramChanged(w) { return !!(w && w._key && w._saved !== undefined && String(w.value) !== String(w._saved)); }
  // 使用模式不追踪“未保存”：里头的调参/拖动都是临时的（退出使用模式会还原），不应弹保存提示。
  function refreshDirty() { if (simpleMode) return; showDirty(savedSig !== null && curSig() !== savedSig); }
  let _lastDirty = null;
  function showDirty(d) {
    const el = document.getElementById("dirty");
    if (el) el.textContent = d ? "●未保存" : "";
    try { document.title = (d ? "*" : "") + "AOE4 Flow Editor"; } catch (e) {}
    // 把“是否有未保存修改”同步给 Python，供关闭窗口时弹确认（仅在状态变化时调，省得频繁过桥）
    if (d !== _lastDirty) {
      _lastDirty = d;
      try { api().set_dirty(!!d); } catch (e) {}
    }
  }
  function isDirty() { return savedSig !== null && curSig() !== savedSig; }

  // 列出“与上次保存相比改了什么”（给切换/退出时的确认框显示）。
  function paramLabelByType(type, key) {
    const d = defByType[type];
    const p = d && (d.params || []).find((q) => q.key === key);
    return (p && p.label) || key;
  }
  function summarizeChanges() {
    if (!savedBaseline) return [];
    const cur = collect(), out = [];
    const titleOf = (n) => (defByType[n.type] && defByType[n.type].title) || n.type;
    if (cur.name !== savedBaseline.name) out.push(`流程名称：${savedBaseline.name} → ${cur.name}`);
    if ((cur.description || "") !== (savedBaseline.description || "")) out.push("流程说明已修改");
    const baseN = {}; for (const n of savedBaseline.nodes) baseN[n.id] = n;
    const curN = {}; for (const n of cur.nodes) curN[n.id] = n;
    for (const n of cur.nodes) if (!baseN[n.id]) out.push(`＋ 新增节点：${titleOf(n)}`);
    for (const n of savedBaseline.nodes) if (!curN[n.id]) out.push(`－ 删除节点：${titleOf(n)}`);
    let moved = 0;
    for (const n of cur.nodes) {
      const b = baseN[n.id]; if (!b) continue;
      for (const k in (n.params || {}))
        if (String(n.params[k]) !== String((b.params || {})[k]))
          out.push(`◇ ${titleOf(n)}·${paramLabelByType(n.type, k)}：${(b.params || {})[k]} → ${n.params[k]}`);
      if ((n.note || "") !== (b.note || "")) out.push(`◇ ${titleOf(n)}：描述已修改`);
      const bp = b.pos || [0, 0], np = n.pos || [0, 0];
      if (Math.round(bp[0]) !== Math.round(np[0]) || Math.round(bp[1]) !== Math.round(np[1])) moved++;
    }
    if (moved) out.push(`◇ ${moved} 个节点位置移动`);
    const ek = (e) => e.src + "|" + e.src_port + "|" + e.dst + "|" + e.dst_port;
    const baseE = new Set(savedBaseline.edges.map(ek)), curE = new Set(cur.edges.map(ek));
    const ea = cur.edges.filter((e) => !baseE.has(ek(e))).length;
    const er = savedBaseline.edges.filter((e) => !curE.has(ek(e))).length;
    if (ea) out.push(`＋ 新增连线 ${ea} 条`);
    if (er) out.push(`－ 删除连线 ${er} 条`);
    if (JSON.stringify(cur.panel) !== JSON.stringify(savedBaseline.panel)) out.push("◇ 控制面板项已修改");
    const gsig = (gs) => JSON.stringify((gs || []).map((g) => ({ t: g.title, c: !!g.collapsed, m: (g.members || []).slice().sort() }))
      .sort((a, b) => (a.t < b.t ? -1 : 1)));
    if (gsig(cur.groups) !== gsig(savedBaseline.groups)) out.push("◇ 分组已修改");
    return out;
  }

  // 有未保存修改时弹确认框（列出修改内容），返回 Promise<"save"|"discard"|"cancel">。
  function confirmUnsaved(actionLabel) {
    return new Promise((resolve) => {
      if (!isDirty()) { resolve("discard"); return; }
      document.getElementById("unsaveddlg")?.remove();
      const changes = summarizeChanges();
      const box = document.createElement("div");
      box.id = "unsaveddlg";
      box.style.cssText = "position:absolute;left:50%;top:46px;transform:translateX(-50%);width:min(520px,94vw);" +
        "max-height:80vh;overflow:auto;background:#23272f;color:#cfd3da;border:1px solid #3a404a;border-radius:8px;" +
        "padding:14px 16px;z-index:200;box-shadow:0 8px 30px #000a;font:13px/1.6 'Microsoft YaHei',sans-serif;";
      let h = `<b style='color:#e6c07b'>有未保存的修改</b><div style='color:#9aa3af;margin-top:4px'>${esc(actionLabel || "继续操作")}前要保存吗？</div>`;
      h += "<div style='margin-top:8px;max-height:40vh;overflow:auto;background:#1b1f27;border:1px solid #2c323c;border-radius:6px;padding:8px 10px;color:#bcd'>";
      if (changes.length) h += changes.map((c) => `<div style="margin:2px 0;white-space:pre-wrap">${esc(c)}</div>`).join("");
      else h += "<div style='color:#7f8895'>（有改动，但无法逐项列出）</div>";
      h += "</div><div style='margin-top:12px;text-align:right'>" +
        "<button id='us_save' style='background:#3a5a3a;color:#dfe;border:1px solid #5a6;border-radius:4px;padding:4px 14px;cursor:pointer'>保存并继续</button> " +
        "<button id='us_discard' style='background:#2f343d;color:#ffb3b3;border:1px solid #a33;border-radius:4px;padding:4px 14px;cursor:pointer'>不保存</button> " +
        "<button id='us_cancel' style='background:#2f343d;color:#cfd3da;border:1px solid #444;border-radius:4px;padding:4px 14px;cursor:pointer'>取消</button></div>";
      box.innerHTML = h;
      document.body.appendChild(box);
      const done = (v) => { box.remove(); document.removeEventListener("keydown", onKey, true); resolve(v); };
      box.querySelector("#us_save").onclick = () => done("save");
      box.querySelector("#us_discard").onclick = () => done("discard");
      box.querySelector("#us_cancel").onclick = () => done("cancel");
      // 默认焦点放在“取消”（最安全）：直接回车不会误触发保存/丢弃；Esc 也等于取消
      const cancelBtn = box.querySelector("#us_cancel");
      setTimeout(() => cancelBtn.focus(), 0);
      const onKey = (e) => {
        if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); done("cancel"); }
        else if (e.key === "Enter") { e.preventDefault(); e.stopPropagation(); document.activeElement && document.activeElement.click(); }
      };
      document.addEventListener("keydown", onKey, true);
    });
  }
  function labelOf(node, key) {
    const d = defByType[node && node._typeId];
    const p = d && (d.params || []).find((q) => q.key === key);
    return (p && p.label) || key;
  }
  function revertParam(node, key) {   // 把某参数恢复为基线（上次保存/载入）值
    const w = (node.widgets || []).find((x) => x._key === key);
    if (!w || w._saved === undefined) return;
    w.value = w._saved;
    if (node.properties) node.properties[key] = w.value;
    if (canvas) canvas.setDirty(true, true);
    scheduleSnap();
    refreshDirty();                 // 立刻更新顶部“未保存”（不等 250ms 防抖快照）
    showNodeHelp(node);             // 刷新说明面板里的“已修改”列表
  }
  function revertNode(node) {
    for (const w of (node.widgets || [])) if (paramChanged(w)) revertParam(node, w._key);
    refreshDirty();
  }

  function registerTypes(list) {
    defs = list || [];
    // 只保留本工具的节点：清掉 litegraph 自带的 const/subgraph/group 等，避免"添加节点"菜单混乱、与本工具结构重复
    LiteGraph.registered_node_types = {};
    LiteGraph.Nodes = {};
    let okN = 0, failN = 0, lastErr = "";
    for (const def of defs) {
      try {
        const key = def.category + "/" + def.title;   // 顶层菜单直接按中文分类（事件/数据/逻辑/…）
        typeKeyByType[def.type] = key;
        defByType[def.type] = def;
        const D = def;
        const Ctor = function () {
          this.title = D.title;
          for (const p of D.inputs) this.addInput(p.name, slotType(p), { label: p.label });
          for (const p of D.params) addParamWidget(this, p, D);
          for (const p of D.outputs) this.addOutput(p.name, slotType(p), { label: p.label });
          // 进阶/次要端口：把端口点画灰，视觉上降级、提示一般用不到（详细在说明的“进阶”区折叠）。
          // 只改颜色、不改 label，保证排版预留宽度与实际渲染一致。
          const dimSlot = (slot) => { if (slot) { slot.color_on = "#6b7280"; slot.color_off = "#3f444d"; } };
          (D.inputs || []).forEach((p, i) => { if (p.advanced) dimSlot(this.inputs[i]); });
          (D.outputs || []).forEach((p, i) => { if (p.advanced) dimSlot(this.outputs[i]); });
          this.size[0] = Math.max(this.size[0] || 0, nodeMinWidth(D));  // 加宽容纳"参数名+值"，与排版预留一致
          this._typeId = D.type;
          if (!this._id) this._id = D.type.split(".").pop() + "_" + (seq++);
        };
        Ctor.title = def.title;
        Ctor.prototype.onDrawForeground = nodeDrawForeground;   // 节点下方画描述+模板缩略图
        Ctor.prototype.getExtraMenuOptions = nodeExtraMenu;     // 右键菜单加“编辑描述”
        // 使用模式：拒绝用户新建/改连线（建图/还原期 building=true 时照常放行，否则会载入成“没有连线”的空图）
        Ctor.prototype.onConnectInput = function () { return building || !simpleMode; };
        Ctor.prototype.onConnectOutput = function () { return building || !simpleMode; };
        if (!LiteGraph.registered_node_types[key])
          LiteGraph.registerNodeType(key, Ctor);
        okN++;
      } catch (err) {
        failN++; lastErr = (def && def.type) + " → " + (err && (err.stack || err.message) || err);
      }
    }
    if (failN) showError(`注册节点类型失败 ${failN}/${defs.length}，最后一个：\n${lastErr}`);
    return okN;
  }

  function buildGraph(flow) {
    if (!graph) return 0;
    building = true;   // 建图期间抑制撤销快照
    graph.clear();
    const idMap = {};
    const missing = {};
    let added = 0, lastErr = "";
    for (const nd of flow.nodes || []) {
      try {
        const key = typeKeyByType[nd.type];
        if (!key) { missing[nd.type] = (missing[nd.type] || 0) + 1; continue; }
        const n = LiteGraph.createNode(key);
        if (!n) { missing[nd.type] = (missing[nd.type] || 0) + 1; continue; }
        n._id = nd.id;
        n._typeId = nd.type;
        n._note = nd.note || "";
        n.pos = [nd.pos ? nd.pos[0] : 0, nd.pos ? nd.pos[1] : 0];
        for (const w of (n.widgets || [])) {
          if (nd.params && w._key in nd.params) {
            let v = nd.params[w._key];
            w.value = (w.type === "combo") ? String(v) : v;
            n.properties[w._key] = w.value;
          }
        }
        graph.add(n);
        idMap[nd.id] = n;
        added++;
      } catch (err) {
        lastErr = (nd && nd.type) + " → " + (err && (err.stack || err.message) || err);
      }
    }
    // 执行(exec)输入已支持多条汇入(见 vendor litegraph.js 改动 + drawExtraExecLinks 补画)，
    // 直接按载荷连线即可；多段流程“跳过/完成”汇到同一入口也能正确显示与保存/运行。
    for (const e of flow.edges || []) {
      try {
        const a = idMap[e.src], b = idMap[e.dst];
        if (!a || !b) continue;
        const so = a.findOutputSlot(e.src_port);
        const si = b.findInputSlot(e.dst_port);
        if (so >= 0 && si >= 0) a.connect(so, b, si);
      } catch (err) { /* 单条连线失败不致命 */ }
    }
    graph.setDirtyCanvas(true, true);
    const miss = Object.keys(missing);
    if (miss.length)
      showError("未注册的节点类型（未渲染）：" + miss.map((t) => t + "×" + missing[t]).join(", "));
    if (lastErr) showError("建图时有节点失败，最后一个：\n" + lastErr);
    building = false;
    return added;
  }

  function collect() {
    const nodes = [], edges = [];
    for (const n of graph._nodes) {
      const params = {};
      for (const w of (n.widgets || [])) if (w._key) params[w._key] = w.value;  // 跳过按钮等无_key控件
      const nd = { id: n._id, type: n._typeId, pos: [Math.round(n.pos[0]), Math.round(n.pos[1])], params };
      if (n._note) nd.note = n._note;
      nodes.push(nd);
    }
    for (const k in graph.links) {
      const l = graph.links[k];
      if (!l) continue;
      const a = graph.getNodeById(l.origin_id), b = graph.getNodeById(l.target_id);
      if (!a || !b) continue;
      edges.push({
        src: a._id, src_port: a.outputs[l.origin_slot].name,
        dst: b._id, dst_port: b.inputs[l.target_slot].name,
      });
    }
    const panel = panelPins.filter(([nid]) => graph._nodes.some((n) => n._id === nid));
    const ids = new Set(graph._nodes.map((n) => n._id));
    const groups = groupDefs
      .map((g) => ({ title: g.title, color: g.color, collapsed: !!g.collapsed, members: (g.members || []).filter((m) => ids.has(m)) }))
      .filter((g) => g.members.length);
    return { name: flowMeta.name || "未命名流程", description: flowMeta.desc || "", panel, groups, nodes, edges };
  }

  // 操作提示走底部居中的临时浮层（toast），与工具栏的“文件信息”分开、互不覆盖；几秒后淡出。
  let toastTimer = null;
  function setStatus(t) {
    const el = document.getElementById("toast");
    if (!el) return;
    el.textContent = t; el.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove("show"), 2600);
  }

  // minScale：可读下限。适应窗口按钮用 0.15（真·全图）；载入用较大的下限（可读，
  // 大图放不下时锚定左上角=流程起点，用户再平移/缩放）。
  function fit(minScale) {
    const ns = graph._nodes;
    if (!ns.length) return;
    let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9;
    for (const n of ns) {
      x0 = Math.min(x0, n.pos[0]); y0 = Math.min(y0, n.pos[1] - 20);
      x1 = Math.max(x1, n.pos[0] + n.size[0]); y1 = Math.max(y1, n.pos[1] + n.size[1]);
    }
    const cw = canvas.canvas.width, ch = canvas.canvas.height;
    const lo = (typeof minScale === "number") ? minScale : 0.15;
    const s = Math.max(lo, Math.min(1.4, Math.min((cw - 60) / (x1 - x0), (ch - 60) / (y1 - y0))));
    canvas.ds.scale = s;
    canvas.ds.offset = [-x0 + 30 / s, -y0 + 30 / s];
    canvas.setDirty(true, true);
  }

  // ---- 与 Python 交互 ----
  function api() {
    if (!(window.pywebview && window.pywebview.api))
      throw new Error("pywebview.api 尚不可用");
    return window.pywebview.api;
  }

  function load(flow, opts) {
    // 载入新流程前先停掉试运行（除非是自动排版那种“原地刷新”——keepHistory 标记）
    if (runSession && !(opts && opts.keepHistory)) {
      running = false; stopPoll(); stopRunAnim();
      try { api().run_end(); } catch (e) {}
      runSession = false; runPath = new Set(); runPathArr = []; runPorts = {}; runData = {}; runDataNodes = new Set(); runTimes = {}; setRunUI();
    }
    const clean = !opts || opts.clean !== false;   // 打开/内置=干净基线；自动排版=保留原基线(版面变了仍算未保存)
    const keepHistory = !!(opts && opts.keepHistory);   // 自动排版：保留撤销历史（这样排版可被 Ctrl+Z 撤销）
    flowMeta = {
      name: flow.name || "未命名流程",
      desc: flow.description || "",
      // 自动排版回传仍带 path/readonly；缺失时沿用当前值
      path: ("path" in flow) ? flow.path : flowMeta.path,
      readonly: ("readonly" in flow) ? !!flow.readonly : flowMeta.readonly,
    };
    if (!keepHistory) { undoStack = []; redoStack = []; breakpoints = new Set(); runUntil = null; }   // 新流程：清空撤销历史与断点（排版除外）
    panelPins = Array.isArray(flow.panel) ? flow.panel.map((x) => x.slice(0, 3)) : [];   // [nodeId, key, 自定义显示名?]
    for (const p of panelPins) if (p[2]) pinLabels[p[0] + "|" + p[1]] = p[2];   // 把已保存的显示名种进记忆
    groupDefs = Array.isArray(flow.groups)
      ? flow.groups.map((g) => ({ title: g.title || "分组", color: g.color || "", collapsed: !!g.collapsed, members: (g.members || []).slice() }))
      : [];
    refreshFold();
    const added = buildGraph(flow);
    applyGroupColors();   // 按分组给节点标题栏染色
    const total = (flow.nodes || []).length;
    fit(0.5);   // 载入用可读下限；适应窗口按钮(ED.fit())仍为真·全图
    setStatus(`流程：${flowMeta.name} ｜ 节点 ${added}/${total}`);
    updateFlowMeta();
    renderPanel();
    snapshotNow();   // 记录初始快照，作为撤销的基线
    if (clean) markSaved();                         // 刚载入＝与磁盘一致，清除“未保存”标记
    else { attachBaselineRefs(); refreshDirty(); }  // 排版后保留原基线，重新挂上控件引用
    if (simpleMode) simpleEntrySig = JSON.stringify(collect());   // 使用模式下载入/切换流程：刷新“退出时还原”的基线
  }

  // 顶部工具栏：显示当前流程名 + 来源（内置只读 / 我的流程）；说明作为悬浮提示。
  function updateFlowMeta() {
    const el = document.getElementById("flowmeta");
    if (!el) return;
    let tag = "";
    if (flowMeta.readonly) tag = " · <span class='ro'>内置·只读</span>";
    else if (flowMeta.path) tag = " · 我的流程";
    el.innerHTML = "📄 " + esc(flowMeta.name) + tag;
    el.title = (flowMeta.readonly ? "内置流程（只读）：保存会另存到「我的流程」。\n\n" : "") +
               (flowMeta.desc || "（暂无流程说明，点顶部「流程信息…」添加）");
    selectCurrentInList();   // 下拉同步显示当前流程
  }

  // 顶部下拉：列出内置/我的流程，按【中文流程名】显示（值仍是完整相对路径，供 openBuiltin 打开）。
  // 后端返回 [{path, name}]；按路径前缀分组成「内置流程（只读）」「我的流程」。
  function fillFlowList(list) {
    const sel = document.getElementById("builtin");
    if (!sel) return;
    sel.innerHTML = "";   // 无提示项：下拉直接“显示当前打开的流程”，切换即载入
    const groups = { "flows": [], "user_flows": [] };
    for (const it of (list || [])) {
      const item = (typeof it === "string") ? { path: it, name: it } : it;   // 兼容旧的纯字符串
      const dir = String(item.path).split("/")[0];
      (groups[dir] || (groups[dir] = [])).push(item);
    }
    const mk = (label, items) => {
      if (!items.length) return;
      const og = document.createElement("optgroup");
      og.label = label;
      for (const item of items) {
        const o = document.createElement("option");
        o.value = item.path;
        o.textContent = item.name || item.path.split("/").pop().replace(/\.flow\.json$/, "");
        og.appendChild(o);
      }
      sel.appendChild(og);
    };
    mk("内置流程（只读）", groups["flows"]);
    mk("我的流程", groups["user_flows"]);
    selectCurrentInList();
  }

  // ---- 控制面板（顶部，置顶常用参数；勾选在节点说明里）----
  function nodeByOurId(id) { return (graph && graph._nodes || []).find((n) => n._id === id) || null; }
  function isPinned(nid, key) { return panelPins.some((p) => p[0] === nid && p[1] === key); }
  function pinEntry(nid, key) { return panelPins.find((p) => p[0] === nid && p[1] === key); }
  function togglePin(nid, key) {
    const lk = nid + "|" + key;
    const i = panelPins.findIndex((p) => p[0] === nid && p[1] === key);
    if (i >= 0) {
      if (panelPins[i][2]) pinLabels[lk] = panelPins[i][2];   // 取消勾选：记住已填的显示名，重新勾选时回填
      panelPins.splice(i, 1);
    } else {
      panelPins.push([nid, key, pinLabels[lk] || ""]);        // 重新勾选：恢复上次填过的显示名
    }
    renderPanel(); scheduleSnap(); refreshDirty();
    if (selectedNode) showNodeHelp(selectedNode);   // 同步说明里勾选框状态
  }
  // 设置某置顶项“在面板上显示的名称”（空＝用默认名）。同步记进 pinLabels，取消勾选后仍保留。
  function setPinLabel(nid, key, name) {
    const e = pinEntry(nid, key);
    if (!e) return;
    e[2] = String(name || "").trim();
    pinLabels[nid + "|" + key] = e[2];
    renderPanel(); scheduleSnap(); refreshDirty();
  }
  // 默认显示名：「节点标题 · 参数标签」——自带节点上下文，多个节点置顶同名参数（区域/阈值/间隔(秒)…）也不混淆，
  // 用户无需逐个起名即可看懂。仍可在节点说明里给某项填更短的“自定义显示名”（随流程一起保存）。
  function defaultPinLabel(node, key) {
    const d = defByType[node._typeId];
    const p = d && (d.params || []).find((q) => q.key === key);
    const plabel = (p && p.label) || key;
    return ((d && d.title) || node._typeId) + " · " + plabel;
  }
  function panelLabel(node, key) {
    const e = pinEntry(node._id, key);
    return (e && e[2]) ? e[2] : defaultPinLabel(node, key);   // 自定义名优先
  }
  // 在节点图里定位到某节点：选中并居中（取代“从面板移除”按钮，避免误点删除）。
  function locateNode(node) {
    if (!node || !canvas) return;
    try { canvas.centerOnNode(node); } catch (e) {}
    try { canvas.selectNode(node, false); } catch (e) {}
    selectedNode = node; showNodeHelp(node);   // 顺带展开右下角说明（在那里可取消显示）
    canvas.setDirty(true, true);
  }
  function setPanelValue(node, key, val) {
    const w = (node.widgets || []).find((x) => x._key === key);
    if (!w) return;
    w.value = val;
    if (node.properties) node.properties[key] = val;
    if (canvas) canvas.setDirty(true, true);
    scheduleSnap(); refreshDirty();
  }
  // 面板上的“采集型”参数控件：显示当前值 + 用游戏内采集按钮设置（按键用“捕获”，区域/坐标/颜色用“采集”）。
  function captureControl(node, key, pt) {
    const wrap = document.createElement("span");
    wrap.style.cssText = "display:inline-flex;align-items:center;gap:4px";
    const val = document.createElement("span");
    val.style.cssText = "color:#cfd3da;font-size:12px;max-width:130px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap";
    const refresh = () => {
      const w = (node.widgets || []).find((x) => x._key === key);
      const v = w && w.value;
      val.textContent = (v == null || String(v) === "") ? "（未设置）" : String(v);
    };
    refresh();
    wrap.appendChild(val);
    const mkb = (label, fn) => {
      const b = document.createElement("button");
      b.textContent = label;
      b.style.cssText = "background:#2f343d;color:#cfd3da;border:1px solid #444;border-radius:4px;padding:1px 8px;cursor:pointer;font-size:12px";
      b.onclick = fn; wrap.appendChild(b);
    };
    const apply = (v) => { setPanelValue(node, key, v); refresh(); };
    if (pt === "key") {
      mkb("捕获", () => { setStatus("请按下按键…（Esc 取消）"); Promise.resolve(api().pick_key()).then((k) => { if (k) apply(k); }).catch((e) => showError("捕获失败：" + e)); });
      mkb("特殊键", () => specialKeyMenu((name) => apply(name)));
    } else if (pt === "region") {
      mkb("框选", () => { setStatus("框选区域…（Enter 确认 / Esc 取消）"); Promise.resolve(api().pick_region()).then((b) => { if (b) apply(b.join(",")); }).catch((e) => showError("采集失败：" + e)); });
    } else if (pt === "point") {
      mkb("取点", () => { setStatus("点击取点…（Esc 取消）"); Promise.resolve(api().pick_point()).then((p) => { if (p) apply(p.join(",")); }).catch((e) => showError("采集失败：" + e)); });
    } else if (pt === "color") {
      mkb("吸色", () => {
        setStatus("点击取色…（Esc 取消）");
        Promise.resolve(api().pick_color()).then((r) => {
          if (!r) return;
          apply(r.color.join(","));
          if (r.point) setPanelValue(node, key.replace("color", "pixel"), r.point.join(","));   // 顺带回填配套坐标
        }).catch((e) => showError("采集失败：" + e));
      });
    }
    return wrap;
  }

  // 拖动调序：把 src 项移到 dst 项的位置（顺序就是 panelPins 顺序 → 随流程保存）。仅编辑模式调用。
  function reorderPanel(srcPk, dstPk) {
    if (!srcPk || srcPk === dstPk) return;
    const idx = (pk) => panelPins.findIndex((p) => p[0] + "|" + p[1] === pk);
    const si = idx(srcPk);
    if (si < 0) return;
    const [m] = panelPins.splice(si, 1);
    let di = idx(dstPk);                 // 删除后重新定位目标，插到它前面
    if (di < 0) di = panelPins.length;
    panelPins.splice(di, 0, m);
    renderPanel(); scheduleSnap(); refreshDirty();
  }
  function renderPanel() {
    const el = document.getElementById("panel");
    if (!el) return;
    el.innerHTML = "";
    const live = panelPins.filter((p) => nodeByOurId(p[0]));
    if (!live.length) {
      if (simpleMode) {   // 使用模式下即使没有面板项也给提示，别一片空白
        el.style.display = "flex";
        el.innerHTML = "<span class='phint'>这个流程还没有“控制面板项”。切到「编辑模式」，在节点右下角说明里勾选要显示到面板的开关/数值。</span>";
      } else { el.style.display = "none"; }
      return;
    }
    el.style.display = "flex";
    const title = document.createElement("span");
    title.className = "ptitle"; title.textContent = "控制面板";
    el.appendChild(title);
    for (const [nid, key] of live) {
      const node = nodeByOurId(nid);
      const w = (node.widgets || []).find((x) => x._key === key);
      if (!w) continue;
      const item = document.createElement("span"); item.className = "pitem";
      item.dataset.pk = nid + "|" + key;
      if (!simpleMode) {                 // 编辑模式：左侧小拖柄，拖动可调整该项在面板里的顺序（随流程保存）
        const grip = document.createElement("span");
        grip.className = "pgrip"; grip.textContent = "⠿"; grip.title = "拖动调整顺序"; grip.draggable = true;
        grip.addEventListener("dragstart", (e) => {
          _panelDrag = item.dataset.pk; item.classList.add("dragging");
          try { e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", _panelDrag); } catch (_) {}
        });
        grip.addEventListener("dragend", () => { item.classList.remove("dragging"); _panelDrag = null; });
        item.appendChild(grip);
        item.addEventListener("dragover", (e) => { if (_panelDrag) { e.preventDefault(); try { e.dataTransfer.dropEffect = "move"; } catch (_) {} } });
        item.addEventListener("drop", (e) => { e.preventDefault(); reorderPanel(_panelDrag, item.dataset.pk); });
      }
      const lab = document.createElement("label");
      lab.textContent = panelLabel(node, key);
      // 悬停才显示节点描述（描述可能很长，平时不占地方）
      lab.title = (node._note || "").trim() || panelLabel(node, key);
      item.appendChild(lab);
      const def = defByType[node._typeId];
      const pspec = def && (def.params || []).find((q) => q.key === key);
      const pt = pspec && pspec.ptype;
      let ctrl;
      if (pt === "key" || pt === "region" || pt === "point" || pt === "color") {
        ctrl = captureControl(node, key, pt);   // 用“捕获/采集”按钮，并显示当前值（最易用）
      } else if (w.type === "toggle") {
        ctrl = document.createElement("input"); ctrl.type = "checkbox"; ctrl.checked = !!w.value;
        ctrl.onchange = () => setPanelValue(node, key, ctrl.checked);
      } else if (w.type === "combo") {
        ctrl = document.createElement("select");
        for (const v of ((w.options && w.options.values) || [])) {
          const o = document.createElement("option"); o.value = v; o.textContent = v;
          if (String(v) === String(w.value)) o.selected = true;
          ctrl.appendChild(o);
        }
        ctrl.onchange = () => setPanelValue(node, key, ctrl.value);
      } else if (w.type === "number") {
        ctrl = document.createElement("input"); ctrl.type = "number"; ctrl.value = w.value;
        if (w.options && w.options.precision === 0) ctrl.step = "1";
        ctrl.onchange = () => setPanelValue(node, key, Number(ctrl.value));
      } else {
        ctrl = document.createElement("input"); ctrl.type = "text";
        ctrl.value = w.value == null ? "" : String(w.value);
        ctrl.onchange = () => setPanelValue(node, key, ctrl.value);
      }
      item.appendChild(ctrl);
      // 🎯 定位：选中并居中到该节点（取代“移除”按钮，避免误点删除；移除在右下角说明里取消勾选）
      const loc = document.createElement("span");
      loc.className = "ploc"; loc.textContent = "🎯";
      loc.title = "定位到此节点（在右下角说明里可取消显示 / 改显示名）";
      loc.onclick = () => locateNode(node);
      item.appendChild(loc);
      el.appendChild(item);
    }
  }

  // 让下拉显示“当前打开的流程”：按路径精确匹配，匹配不到再按文件名，仍不到则置空(-1)。
  function selectCurrentInList() {
    const sel = document.getElementById("builtin");
    if (!sel) return;
    const key = String(flowMeta.path || "").replace(/\\/g, "/");
    const base = key.split("/").pop();
    let idx = -1;
    for (let i = 0; i < sel.options.length; i++) {
      const v = sel.options[i].value;
      if (v && (v === key || (base && v.split("/").pop() === base))) { idx = i; break; }
    }
    sel.selectedIndex = idx;
  }

  // 关掉所有“编辑相关”的浮层（分组/流程信息/帮助/描述/图片列表/取键 等弹窗 + 右键菜单 + 值编辑框）。
  // 进只读模式、或点击弹窗外部时调用。这些弹窗统一带 class="popdlg"。
  function closeEditPopups() {
    document.querySelectorAll(".popdlg").forEach((d) => d.remove());
    groupDlgRender = null; helpModal = null;
    try { LiteGraph.closeAllContextMenus(window); } catch (e) {}
    document.querySelectorAll(".graphdialog").forEach((d) => { if (typeof d.close === "function") d.close(); });
  }

  // 使用模式（简单模式）：画布只读，只用控制面板 + 运行 + 日志
  function applySimpleMode() {
    document.body.classList.toggle("simple", simpleMode);
    const b = document.getElementById("modebtn");
    if (b) {
      b.textContent = simpleMode ? "🔒 使用模式" : "✎ 编辑模式";   // 显示“当前”模式（不再显示要切到的模式，消除歧义）
      b.classList.toggle("inuse", simpleMode);
      b.title = simpleMode
        ? "当前：使用模式（画布只读，只用控制面板调参 + 运行）。点击切换到「编辑模式」可改节点/连线/参数。"
        : "当前：编辑模式（可改节点图：增删改节点/连线/参数）。点击切换到「使用模式」只读运行，适合日常使用。";
    }
    if (simpleMode) closeEditPopups();                         // 进只读模式：关掉所有编辑相关的弹窗/右键菜单
    if (simpleMode && helpEl) helpEl.style.display = "none";   // 使用模式不显示可编辑的节点说明
    renderPanel();
    if (canvas) { canvas.resize(); canvas.setDirty(true, true); }   // 两种模式都显示画布：切换后重算尺寸
  }
  function toggleSimple() {
    if (!simpleMode) {                         // 编辑 → 使用：记下此刻的图，退出使用模式时据此还原（调参/拖动不落盘）
      simpleEntrySig = JSON.stringify(collect());
      simpleMode = true;
    } else {                                    // 使用 → 编辑：还原到“进入使用模式时”的样子（位置/参数都不保存）
      simpleMode = false;
      if (simpleEntrySig) { applySnapshot(simpleEntrySig); simpleEntrySig = null; }
    }
    try { localStorage.setItem("flow.simpleMode", simpleMode ? "1" : "0"); } catch (e) {}
    applySimpleMode();
    refreshDirty();
    setStatus(simpleMode
      ? "已进入使用模式：画布只读，用控制面板调参并运行（改动不会动到已保存的流程）；想改流程点左上角切回编辑模式"
      : "已进入编辑模式：可增删改节点/连线/参数，改完记得保存");
  }

  // ---- 启动：优先用 Python push 进来的数据 ----
  function boot(data) {
    if (booted) return;
    booted = true;
    try {
      const okN = registerTypes(data.defs);
      fillFlowList(data.builtin || []);
      if (data.flow) load(data.flow);
      else setStatus(`已就绪 ｜ 已注册 ${okN} 种节点（右键空白处添加）`);
      try { const v = localStorage.getItem("flow.simpleMode"); simpleMode = (v === null) ? true : v === "1"; }
      catch (e) { simpleMode = true; }   // 默认进【使用模式】（成品工具默认只读运行）；之后记住用户的选择
      applySimpleMode();
      if (simpleMode) simpleEntrySig = JSON.stringify(collect());   // 开机即处于使用模式：记下“退出时还原”的基线
      startSysMon();       // 启动右下角资源监控小窗（轮询后端 sys_stats）
    } catch (err) {
      showError("启动失败：\n" + (err && (err.stack || err.message) || err));
    }
  }
  window.__bootstrap__ = function (data) { boot(data); return true; };

  // 保存/另存成功后：清除“未保存”、刷新元信息（保存后一定写到可写的用户文件，故不再只读），
  // 并刷新下拉列表（新另存的“我的流程”会出现在列表里）。
  async function afterSaved(p) {
    flowMeta.path = p;
    flowMeta.readonly = false;
    markSaved();
    try { fillFlowList(await api().list_builtin()); } catch (e) {}   // 新另存的流程进入列表
    updateFlowMeta();
  }

  // 打开/切换其它流程前：若有未保存修改先确认（保存并继续 / 不保存 / 取消）。
  // 返回 true＝可以继续；false＝用户取消（调用方应中止）。
  async function guardUnsaved(actionLabel) {
    const choice = await confirmUnsaved(actionLabel);
    if (choice === "cancel") return false;
    if (choice === "save") {
      const p = await api().save(collect());
      if (!p) return false;            // 在系统保存框里取消了 -> 不继续
      await afterSaved(p);
    }
    return true;                       // 已保存 或 选择不保存
  }

  // ============ 试运行（干跑）：逐帧轮询引擎，画出走过的节点 + 数据线上的值 + 日志 ============
  function fmtRunVal(v) {
    if (v === null || v === undefined) return "—";
    if (v === true) return "真"; if (v === false) return "假";
    if (typeof v === "number") return Number.isInteger(v) ? String(v) : (Math.round(v * 1000) / 1000) + "";
    if (typeof v === "object" && v._list) {
      let s = v._list.map(fmtRunVal).join(", ");
      if (v._more) s += ", +" + v._more;
      return "[" + s + "]";
    }
    return String(v);
  }
  // ---- 试运行可视化：聚光灯压暗 + 脉冲发光 + 连线流动 + 彩色值标签（画在所有节点之上）----
  const _t0 = [0, 0], _t1 = [0, 0];
  function outSlotIndex(n, portName) { return (n.outputs || []).findIndex((o) => o.name === portName); }
  function execInSlotIndex(n) { const i = (n.inputs || []).findIndex((p) => p.type === "exec"); return i < 0 ? 0 : i; }
  function drawValPill(ctx, x, y, v) {
    const text = fmtRunVal(v);
    let bg = "#27496b", fg = "#dff0ff";                 // 默认（数值/其它）
    if (v === true) { bg = "#1f8a47"; fg = "#eafff0"; }
    else if (v === false) { bg = "#a83a3a"; fg = "#ffe6e6"; }
    else if (v && typeof v === "object" && v._list) { bg = "#5a4a85"; fg = "#efe8ff"; }
    else if (typeof v === "string") { bg = "#3f6a4a"; fg = "#e7ffe7"; }
    ctx.save();
    ctx.font = "bold 12px Consolas, monospace";
    const tw = ctx.measureText(text).width;
    roundRect(ctx, x, y - 9, tw + 12, 18, 5);
    ctx.shadowColor = "#000a"; ctx.shadowBlur = 5; ctx.fillStyle = bg; ctx.fill();
    ctx.shadowBlur = 0; ctx.strokeStyle = "#0007"; ctx.lineWidth = 1; ctx.stroke();
    ctx.fillStyle = fg; ctx.textBaseline = "middle"; ctx.textAlign = "left";
    ctx.fillText(text, x + 6, y + 0.5);
    ctx.restore();
  }
  // 性能监控药丸：节点上方标「自身ms · Σ帧内累计ms」。rx=右边界（与节点右沿对齐），by=底边（节点顶部上方）。
  // 自身耗时越大数字越偏红（>8ms≈一次截图量级），一眼看出本帧耗时大头落在哪个节点。
  function drawTimePill(ctx, rx, by, selfMs, cumMs) {
    const fmt = (v) => (v >= 100 ? Math.round(v) + "" : v.toFixed(1));
    const t1 = fmt(selfMs), t2 = "Σ" + fmt(cumMs) + "ms";
    ctx.save();
    ctx.font = "bold 11px Consolas, monospace";
    const w1 = ctx.measureText(t1).width, w2 = ctx.measureText(t2).width;
    const pad = 6, gap = 8, w = pad + w1 + gap + w2 + pad, h = 16, x = rx - w, y = by - h;
    roundRect(ctx, x, y, w, h, 5);
    ctx.fillStyle = "rgba(20,26,36,0.92)"; ctx.shadowColor = "#000a"; ctx.shadowBlur = 5; ctx.fill();
    ctx.shadowBlur = 0; ctx.strokeStyle = "#0008"; ctx.lineWidth = 1; ctx.stroke();
    ctx.textBaseline = "middle"; ctx.textAlign = "left";
    const cy = y + h / 2 + 0.5, hot = Math.min(1, selfMs / 16);
    ctx.fillStyle = `rgb(${160 + hot * 95 | 0},${200 - hot * 120 | 0},${255 - hot * 200 | 0})`;  // 蓝→红
    ctx.fillText(t1, x + pad, cy);                                   // 自身耗时
    ctx.fillStyle = "#ffd23f"; ctx.fillText(t2, x + pad + w1 + gap, cy);   // Σ 帧内累计
    ctx.restore();
  }
  // 与 LiteGraph SPLINE_LINK 完全一致的贝塞尔控制点：输出在右、输入在左，
  // 偏移量 = 两端点欧氏距离 × 0.25（LiteGraph: distance(a,b)*0.25）。这样高亮/流动曲线与底层连线严丝合缝。
  function linkCtrlPts(pa, pb) {
    const d = Math.hypot(pb[0] - pa[0], pb[1] - pa[1]) * 0.25;
    return [[pa[0] + d, pa[1]], [pb[0] - d, pb[1]]];
  }
  // 分支结果小标签：画在“本帧实际走的那个出口”上，标明这一支走了真/假（成功/占用…）。
  // 它只是把“沿哪条连线走的”显式标出来——并没有任何“跳过”的特殊操作，全是节点+连线。
  function drawBranchTag(ctx, p, label, pname) {
    const yes = (pname === "true" || pname === "ok" || pname === "due");
    const bg = yes ? "#1f8a47" : "#9a4a2f", fg = yes ? "#eafff0" : "#ffe7d8";
    ctx.save();
    ctx.font = "bold 11px 'Microsoft YaHei', sans-serif";
    const tw = ctx.measureText(label).width;
    const x = p[0] + 12, y = p[1] - 13;          // 端口右上方，避开端口光点与数据值标签
    roundRect(ctx, x, y - 8, tw + 10, 16, 4);
    ctx.fillStyle = bg; ctx.fill();
    ctx.strokeStyle = "#0006"; ctx.lineWidth = 1; ctx.stroke();
    ctx.fillStyle = fg; ctx.textBaseline = "middle"; ctx.textAlign = "left";
    ctx.fillText(label, x + 5, y);
    ctx.restore();
  }
  // 执行路径终点的“结束”端帽：红点 + 标签，画在走到头节点选中的那个出口上。
  function drawEndCap(ctx, p) {
    ctx.save();
    ctx.fillStyle = "#ff5d5d"; ctx.shadowColor = "#ff5d5d"; ctx.shadowBlur = 10;   // 端口上的红色实心点
    ctx.beginPath(); ctx.arc(p[0], p[1], 5, 0, Math.PI * 2); ctx.fill();
    ctx.shadowBlur = 0;
    const text = "⏹ 结束", x = p[0] + 12, y = p[1];
    ctx.font = "bold 12px 'Microsoft YaHei', sans-serif";
    const tw = ctx.measureText(text).width;
    roundRect(ctx, x, y - 9, tw + 12, 18, 5);
    ctx.fillStyle = "#7a1f1f"; ctx.fill();                                          // 暗红底
    ctx.strokeStyle = "#ff8a8a"; ctx.lineWidth = 1; ctx.stroke();
    ctx.fillStyle = "#ffd8d8"; ctx.textBaseline = "middle"; ctx.textAlign = "left";
    ctx.fillText(text, x + 6, y + 0.5);
    ctx.restore();
  }
  function drawFlowWire(ctx, p0, p1) {
    const cc = linkCtrlPts(p0, p1), c0 = cc[0], c1 = cc[1];
    const bez = (t) => { const u = 1 - t, a = u * u * u, b = 3 * u * u * t, c = 3 * u * t * t, d = t * t * t;
      return [a * p0[0] + b * c0[0] + c * c1[0] + d * p1[0], a * p0[1] + b * c0[1] + c * c1[1] + d * p1[1]]; };
    ctx.save();
    ctx.strokeStyle = "rgba(255,210,63,0.5)"; ctx.lineWidth = 3.5;
    ctx.beginPath(); ctx.moveTo(p0[0], p0[1]);
    ctx.bezierCurveTo(c0[0], c0[1], c1[0], c1[1], p1[0], p1[1]); ctx.stroke();
    ctx.shadowColor = "#ffd23f"; ctx.shadowBlur = 9;
    for (let k = 0; k < 4; k++) {
      const q = bez(((runPhase * 0.18) + (k / 4)) % 1);
      ctx.beginPath(); ctx.arc(q[0], q[1], 3.4, 0, Math.PI * 2); ctx.fillStyle = "#fff3c0"; ctx.fill();
    }
    ctx.restore();
  }
  function nodeFullRect(n, ctx) {
    const th = LiteGraph.NODE_TITLE_HEIGHT || 30;
    const ch = (n.flags && n.flags.collapsed) ? 0 : cardHeightOf(n, ctx);
    return [n.pos[0], n.pos[1] - th, n.size[0], n.size[1] + th + ch];
  }
  // 点亮“本帧活动”的端口：呼吸光环 + 实心亮点 + 白圈，明显区别于普通“已连线但未激活”的端口小点。
  function lightPort(ctx, p, color) {
    const pulse = 0.5 + 0.5 * Math.sin(runPhase * 3.0);
    ctx.save();
    ctx.fillStyle = color;
    ctx.globalAlpha = 0.45 - 0.28 * pulse;                       // 外圈呼吸光环
    ctx.beginPath(); ctx.arc(p[0], p[1], 9 + 4 * pulse, 0, Math.PI * 2); ctx.fill();
    ctx.globalAlpha = 1;
    ctx.shadowColor = color; ctx.shadowBlur = 14;                // 实心点 + 强光晕
    ctx.beginPath(); ctx.arc(p[0], p[1], 5.5, 0, Math.PI * 2); ctx.fill();
    ctx.shadowBlur = 0; ctx.lineWidth = 2; ctx.strokeStyle = "#ffffff";   // 白圈（与普通端口点拉开差距）
    ctx.beginPath(); ctx.arc(p[0], p[1], 5.5, 0, Math.PI * 2); ctx.stroke();
    ctx.restore();
  }
  // 断点标记：节点左上角一个红点（始终可见，不依赖是否在试运行）
  function drawBreakpoints(ctx) {
    if (!breakpoints.size || !graph) return;
    const th = LiteGraph.NODE_TITLE_HEIGHT || 30;
    for (const n of (graph._nodes || [])) {
      if (!breakpoints.has(n._id)) continue;
      const x = n.pos[0] - 8, y = n.pos[1] - th + 9;
      ctx.save();
      ctx.fillStyle = "#ff4d4d"; ctx.shadowColor = "#ff0000"; ctx.shadowBlur = 8;
      ctx.beginPath(); ctx.arc(x, y, 5.5, 0, Math.PI * 2); ctx.fill();
      ctx.shadowBlur = 0; ctx.strokeStyle = "#fff"; ctx.lineWidth = 1.5; ctx.stroke();
      ctx.restore();
    }
  }
  function drawRunOverlay(ctx) {
    drawBreakpoints(ctx);                 // 断点红点：与是否试运行无关，始终显示
    if (!runSession || !graph) return;
    const nodes = graph._nodes || [];
    const byId = new Map(); for (const n of nodes) byId.set(n._id, n);   // 本帧建一次，省掉路径循环里的线性查找
    const active = (id) => runPath.has(id) || runDataNodes.has(id);
    const th = LiteGraph.NODE_TITLE_HEIGHT || 30;
    // ① 聚光灯：压暗未参与本帧的节点，让走过/取数的节点凸显
    ctx.save();
    ctx.fillStyle = "rgba(11,13,18,0.62)";
    for (const n of nodes) {
      if (active(n._id) || foldHidden.has(n._id)) continue;   // 折叠隐藏的成员不参与压暗（其区域由子图箱体占据）
      const r = nodeFullRect(n, ctx);
      roundRect(ctx, r[0] - 3, r[1] - 3, r[2] + 6, r[3] + 6, 9); ctx.fill();
    }
    ctx.restore();
    // ② 走过的执行连线：流动亮点（沿样条移动）
    for (let i = 0; i < runPathArr.length - 1; i++) {
      const A = byId.get(runPathArr[i]), B = byId.get(runPathArr[i + 1]);
      if (!A || !B) continue;
      if (foldHidden.has(A._id) || foldHidden.has(B._id)) continue;   // 折叠态：跨/入隐藏成员的流动线不画
      const oi = outSlotIndex(A, runPorts[A._id]); if (oi < 0) continue;
      const p0 = A.getConnectionPos(false, oi, _t0), p1 = B.getConnectionPos(true, execInSlotIndex(B), _t1);
      drawFlowWire(ctx, [p0[0], p0[1]], [p1[0], p1[1]]);
    }
    // ③ 执行节点：脉冲发光边框（醒目的琥珀色，和分组色区分开 + 会呼吸）
    const pulse = 0.5 + 0.5 * Math.sin(runPhase * 3.0);
    for (const n of nodes) {
      if (!runPath.has(n._id) || foldHidden.has(n._id)) continue;   // 折叠隐藏成员：高亮交给子图箱体描边
      ctx.save();
      ctx.strokeStyle = "#ffd23f"; ctx.lineWidth = 2.5 + 2.5 * pulse;
      ctx.shadowColor = "#ffb300"; ctx.shadowBlur = 12 + 18 * pulse;
      roundRect(ctx, n.pos[0] - 2, n.pos[1] - th - 2, n.size[0] + 4, n.size[1] + th + 4, 9); ctx.stroke();
      ctx.restore();
    }
    // ④ 点亮“本帧活动”的连线两端端口（输入+输出都醒目）：执行=绿、数据=青；
    //    活动数据线再描一条淡青高亮（执行线已有流动点），与“已连线但未激活”的端口拉开差距。
    for (const k in (graph.links || {})) {
      const l = graph.links[k]; if (!l) continue;
      const A = graph.getNodeById(l.origin_id), B = graph.getNodeById(l.target_id);
      if (!A || !B) continue;
      if (foldHidden.has(A._id) || foldHidden.has(B._id)) continue;   // 折叠态：触及隐藏成员的端口高亮交给箱体
      const oslot = A.outputs && A.outputs[l.origin_slot]; if (!oslot) continue;
      const isExec = oslot.type === "exec";
      const on = isExec ? (runPath.has(A._id) && runPorts[A._id] === oslot.name)
                        : ((A._id + RUNSEP + oslot.name) in runData);
      if (!on) continue;
      const pa = A.getConnectionPos(false, l.origin_slot, _t0);
      const pb = B.getConnectionPos(true, l.target_slot, _t1);
      const col = isExec ? "#7CFF9B" : "#6fe0ff";
      if (!isExec) {                                   // 活动数据线：青色高亮，曲线与底层连线完全重合并盖住它
        const cc = linkCtrlPts(pa, pb);
        ctx.save();
        ctx.strokeStyle = "rgba(111,224,255,0.92)"; ctx.lineWidth = 5;   // 底层连线宽 3，这里 5px 完全覆盖
        ctx.lineCap = "round";
        ctx.beginPath(); ctx.moveTo(pa[0], pa[1]);
        ctx.bezierCurveTo(cc[0][0], cc[0][1], cc[1][0], cc[1][1], pb[0], pb[1]); ctx.stroke();
        ctx.restore();
      }
      lightPort(ctx, [pa[0], pa[1]], col);   // 输出端
      lightPort(ctx, [pb[0], pb[1]], col);   // 输入端
    }
    // ⑤ 数据输出值（彩色标签）
    for (const n of nodes) {
      if (foldHidden.has(n._id)) continue;   // 折叠隐藏成员：数据值标签不画
      const def = defByType[n._typeId]; if (!def) continue;
      (def.outputs || []).forEach((p, i) => {
        if (p.kind === "exec") return;
        const key = n._id + RUNSEP + p.name;
        if (!(key in runData)) return;
        const ap = n.getConnectionPos(false, i, _t0);
        drawValPill(ctx, ap[0] + 12, ap[1], runData[key]);
      });
    }
    // ⑥ 走过的“分支/多出口”节点：在它本帧实际走的那个出口上标结果（真/假、成功/占用…）。
    //    我们的逻辑全是节点+连线：开关为“假”时，只是沿“假”这条线走到作者接的下一个节点（这里接到下一段入口），
    //    并不存在“跳过”这种特殊操作。标出真/假，只是让“到底沿哪条线走的”一目了然。终点除外(它有 ⏹ 结束)。
    const termId = runPathArr.length ? runPathArr[runPathArr.length - 1] : null;
    for (const id of runPathArr) {
      if (id === termId) continue;                       // 终点节点交给 ⑦ 的“结束”端帽
      const n = byId.get(id); if (!n || foldHidden.has(id)) continue;
      const execOuts = (n.outputs || []).filter((o) => o.type === "exec");
      if (execOuts.length < 2) continue;                 // 只标“有分叉”的节点（如「分支」/获取锁/定时门）
      const pname = runPorts[id]; if (!pname) continue;
      const oi = outSlotIndex(n, pname); if (oi < 0) continue;
      const out = n.outputs[oi];
      drawBranchTag(ctx, n.getConnectionPos(false, oi, _t0), (out && out.label) || pname, pname);
    }
    // ⑦ 本帧执行“走到头”的节点：在它选中的那个出口上标“结束”，让终点一目了然
    //    （选了某个分支但没接下游 → 流程在此中断；这条最容易被忽略，用红色端帽点明）。
    if (termId && !foldHidden.has(termId)) {
      const termNode = byId.get(termId);
      if (termNode) {
        let oi = outSlotIndex(termNode, runPorts[termNode._id]);   // 优先：本帧实际选中的出口
        if (oi < 0) oi = (termNode.outputs || []).findIndex((o) => o.type === "exec");  // 否则：第一个执行出口
        if (oi >= 0) drawEndCap(ctx, termNode.getConnectionPos(false, oi, _t0));
      }
    }
    // ⑧ 性能监控（开关开启时）：每个本帧跑过的节点上方标「本节点ms · Σ帧内累计ms」。
    //    自身耗时＝该节点自己这段（控制节点不含其数据输入的求值，截图/OCR 等大头各自单列）；
    //    累计＝从本帧开始到该节点跑完的总耗时（沿执行链单调递增，终点≈整帧总耗时）。
    if (profileOn) {
      for (const n of nodes) {
        if (foldHidden.has(n._id)) continue;   // 折叠隐藏成员：耗时不在此画（汇总见下）
        const tm = runTimes[n._id]; if (!tm) continue;
        drawTimePill(ctx, n.pos[0] + n.size[0], n.pos[1] - th - 3, tm[0], tm[1]);
      }
      drawFoldedTimePills(ctx);   // 折叠组：在子图箱体上汇总「组内自身耗时 · 组终累计」
    }
  }

  // 试运行动画刷新上限：运行中 120fps、暂停 20fps（暂停画面基本静止，只留呼吸感）。
  // 多屏观战：即使切到游戏、编辑器在后台仍照常刷新（rAF 在窗口真正最小化/隐藏时会自动降频）。
  const RUN_FPS = 120, PAUSE_FPS = 20;
  const PHASE_SPEED = 1.8;            // 相位每秒推进量，与帧率解耦——改上限不会让动画忽快忽慢
  function animInterval() { return 1000 / (running ? RUN_FPS : PAUSE_FPS); }
  function startRunAnim() {
    if (runAnimRAF) return;
    _runAnimLast = 0;
    const step = (ts) => {
      if (!runSession) { runAnimRAF = null; return; }
      const gap = _runAnimLast ? (ts - _runAnimLast) : 999;
      if (gap >= animInterval()) {
        runPhase += PHASE_SPEED * Math.min(gap, 200) / 1000;   // 按真实时间步进，掉帧也不变速
        _runAnimLast = ts;
        if (canvas) canvas.setDirty(true, false);
      }
      runAnimRAF = requestAnimationFrame(step);
    };
    runAnimRAF = requestAnimationFrame(step);
  }
  function stopRunAnim() { if (runAnimRAF) cancelAnimationFrame(runAnimRAF); runAnimRAF = null; }

  function setRunUI() {
    const btn = document.getElementById("runbtn"), stop = document.getElementById("stopbtn"),
          dry = document.getElementById("dryrunbtn");
    if (btn) {
      btn.textContent = running ? "⏸ 暂停" : (runSession ? "▶ 继续" : "▶ 运行");
      btn.classList.toggle("running", running);
    }
    if (stop) stop.disabled = !runSession;
    if (dry) dry.disabled = runSession;     // 会话进行/暂停中不能再起一个干跑
    const lp = document.getElementById("logpanel");
    if (lp) lp.style.display = runSession ? "block" : "none";
    const lr = document.getElementById("logresize");
    if (lr) lr.style.display = runSession ? "block" : "none";
  }
  // —— 运行日志面板：增量追加，避免每帧重建整面板（800 行 × 4fps 的 DOM 抖动是“一分钟后变卡”的元凶之一）——
  const LOG_CAP = 800;
  const LOG_DOT = { INFO: "•", WARN: "▲", ERROR: "✕" };
  function nowHMS() {
    const d = new Date(), p = (n) => (n < 10 ? "0" : "") + n;
    return p(d.getHours()) + ":" + p(d.getMinutes()) + ":" + p(d.getSeconds());
  }
  function logRowHTML(l) {
    const lv = (l.level === "WARN" || l.level === "ERROR") ? l.level : "INFO";
    return `<div class="lp-row lv-${lv}" title="第 ${l.tick} 帧">` +
           `<span class="lp-time">${esc(l.ts || "")}</span>` +
           `<span class="lp-dot">${LOG_DOT[lv]}</span>` +
           `<span class="lp-msg">${esc(l.msg)}</span></div>`;
  }
  function logHeadHTML() {
    const mode = realRun ? "<span style='color:#e6c07b'>● 正式运行（向游戏发送操作）</span>"
                         : "<span style='color:#7fb0ee'>● 试运行（只识别，不发送输入）</span>";
    return `<span class="lp-title">运行日志</span>${mode}<span class="lp-count">· ${runLogs.length} 条</span>` +
           `<span class="spacer"></span><span class="lp-clear" onclick="ED.clearLog()">清空</span>`;
  }
  function ensureLogDom() {
    const lp = document.getElementById("logpanel");
    if (!lp) return null;
    let head = lp.querySelector(".lp-head"), rows = lp.querySelector(".lp-rows");
    if (!head || !rows) {
      lp.innerHTML = `<div class="lp-head"></div><div class="lp-rows"></div>`;
      head = lp.querySelector(".lp-head"); rows = lp.querySelector(".lp-rows");
    }
    return { lp, head, rows };
  }
  function renderLog() {            // 全量重建：清空 / 会话开始 / 回到前台补全 时调用
    const d = ensureLogDom(); if (!d) return;
    d.head.innerHTML = logHeadHTML();
    let h = ""; for (const l of runLogs) h += logRowHTML(l);
    d.rows.innerHTML = h;
    d.lp.scrollTop = d.lp.scrollHeight;
  }
  function appendLogRows(rows) {    // 每帧只追加本帧新增行，不重建整面板
    const d = ensureLogDom(); if (!d) return;
    // 用户若上滚查看/选中文本，则不强行拽回底部（贴底时才自动跟随）
    const atBottom = d.lp.scrollTop + d.lp.clientHeight >= d.lp.scrollHeight - 6;
    if (rows && rows.length) {
      let h = ""; for (const l of rows) h += logRowHTML(l);
      d.rows.insertAdjacentHTML("beforeend", h);
      while (d.rows.childElementCount > LOG_CAP) d.rows.removeChild(d.rows.firstChild);
    }
    d.head.innerHTML = logHeadHTML();
    if (atBottom) d.lp.scrollTop = d.lp.scrollHeight;
  }
  // 拖动日志面板与画布间的分隔条改变日志高度（向上拖变高）
  function setupLogResize() {
    const bar = document.getElementById("logresize"), lp = document.getElementById("logpanel");
    if (!bar || !lp) return;
    let dragging = false, startY = 0, startH = 0;
    bar.addEventListener("pointerdown", (e) => {
      dragging = true; startY = e.clientY; startH = lp.offsetHeight || 150;
      try { bar.setPointerCapture(e.pointerId); } catch (_) {}
      e.preventDefault();
    });
    bar.addEventListener("pointermove", (e) => {
      if (!dragging) return;
      lp.style.height = Math.max(60, Math.min(window.innerHeight - 160, startH + (startY - e.clientY))) + "px";
      if (canvas) canvas.resize();   // 画布跟随调整尺寸
    });
    const end = (e) => { if (dragging) { dragging = false; try { bar.releasePointerCapture(e.pointerId); } catch (_) {} } };
    bar.addEventListener("pointerup", end);
    bar.addEventListener("pointercancel", end);
  }
  async function ensureRunSession() {
    if (runSession) return true;
    try {
      const r = await api().run_begin(collect(), realRun);
      runSession = !!(r && r.ok);
      runLogs = []; _lastRunStatus = ""; _lastTick = 0; renderLog();
      if (runSession) {
        startRunAnim();                                  // 启动脉冲/流动动画
        try { api().run_set_breakpoints([...breakpoints], runUntil); } catch (e) {}   // 把断点同步给引擎
        try { api().run_set_profile(profileOn); } catch (e) {}   // 把「性能监控」开关同步给引擎
      }
      return runSession;
    } catch (e) { showError("启动运行失败：" + (e && (e.stack || e.message) || e)); return false; }
  }
  // 引擎在 Python 后台线程里按流程「每帧触发」的间隔【自行全速跑】；前端只轮询取最近一帧，
  // 与引擎执行解耦——UI 再慢/暂停也不会拖慢底层逻辑的执行速度。
  // 这一轮“为什么没动作”：停在某个判断分支＝被它拦下。原因优先用作者写在节点上的描述（如“不在游戏中”
  // “修饰键被按下”），否则退而用“接到分支条件的那个检测节点”的标题，保持一句话、简短直白。
  function branchStopReason(term) {
    const note = (term._note || "").trim();
    if (note) {                            // 约定：节点描述写成「短原因：详细…」——日志只取冒号/句号前那句短原因，一眼可读
      const lead = note.split(/[：:。\n]/)[0].trim();
      if (lead) return lead.length > 18 ? lead.slice(0, 18) + "…" : lead;
    }
    const ci = (term.inputs || []).findIndex((p) => p.name === "cond");
    if (ci >= 0 && term.inputs[ci].link != null && graph) {
      const l = graph.links[term.inputs[ci].link];
      const src = l && graph.getNodeById(l.origin_id);
      if (src) return "「" + (src.title || "条件") + "」" + (runPorts[term._id] === "true" ? "成立" : "不成立");
    }
    return "卡在「" + (term.title || "分支") + "」";
  }
  // 用“本帧走到头的节点”一句话说明这轮结果/原因（通用，不绑定具体流程）。
  function runStatusLine() {
    if (!runPathArr.length) return null;
    const term = nodeByOurId(runPathArr[runPathArr.length - 1]);
    if (!term) return null;
    const execOuts = (term.outputs || []).filter((o) => o.type === "exec");
    if (execOuts.length >= 2)              // 停在判断分支 = 这轮被它拦下、没执行后续操作
      return { level: "INFO", msg: "本轮未操作 · " + branchStopReason(term) };
    return { level: "INFO", msg: "本轮已完成一轮" };
  }
  // 处理一次轮询结果：刷新高亮状态 + 追加增量日志 + 命中断点则暂停（断点判定已在引擎侧精确完成）。
  function applyPoll(r) {
    if (!r) return;
    const t = r.trace;
    if (t && t.tick === _lastTick && !(r.logs && r.logs.length) && !r.bp_hit) return;  // 帧未推进、无新日志/断点 → 跳过，免去高频轮询下的无谓重绘与集合重建
    if (t) {
      runPathArr = t.path || [];
      runPath = new Set(runPathArr);
      runPorts = t.ports || {};
      runData = t.data || runData;       // 无人观看时引擎可能略过 data；保留上次，避免标签闪烁
      runDataNodes = new Set(Object.keys(runData).map((k) => k.split(RUNSEP)[0]));
      runTimes = t.times || (profileOn ? runTimes : {});   // 仅在“性能监控”开启时引擎才附带耗时
      _lastTick = t.tick;
    }
    const ts = nowHMS();
    const added = (r.logs || []).map((l) => ({ tick: l.tick, ts, level: l.level, msg: l.msg, node: l.node }));
    const st = runStatusLine();          // 合成“本轮结果/为什么没动作”，变化时才记一行，避免刷屏
    if (st && st.msg !== _lastRunStatus) { _lastRunStatus = st.msg; added.push({ tick: _lastTick, ts, level: st.level, msg: st.msg }); }
    for (const l of added) runLogs.push(l);
    if (runLogs.length > LOG_CAP) runLogs.splice(0, runLogs.length - LOG_CAP);   // 原地裁剪，免得新建长数组
    if (added.length) appendLogRows(added);        // 增量追加（不重建整面板）
    if (r.bp_hit && running) {           // 引擎已自停在断点；前端同步暂停 UI
      running = false; stopPoll(); setRunUI();
      const n = nodeByOurId(r.bp_hit);
      setStatus("⏸ 命中断点：" + (n ? n.title : r.bp_hit) + " · 第 " + _lastTick + " 帧");
    } else if (t) {
      setStatus(`运行中 · 第 ${_lastTick} 帧 · 经过 ${runPathArr.length} 个节点`);
    }
    if (canvas) canvas.setDirty(true, false);      // 背景(网格/分组)不随帧变 → 只刷前景，省一半重绘
  }
  // 轮询循环：用 requestAnimationFrame 驱动——自动贴合显示器刷新率（这里≈120Hz），取完一帧再约下一帧，
  // 既不堆叠/超采样，也尽量让画面贴近刷新率上限；窗口最小化时 rAF 自动暂停省 CPU（引擎仍在后台线程全速跑，
  // _pending_logs 有上限不会堆积）。run_poll 极轻量、只读最近快照、不触发任何引擎计算，故再快也不影响底层执行速度。
  function startPoll() {
    if (pollTimer) return;
    const tick = async () => {
      if (!runSession || !running) { pollTimer = null; return; }
      let r = null;
      try { r = await api().run_poll(); } catch (e) {}
      if (r) applyPoll(r);
      if (runSession && running) pollTimer = requestAnimationFrame(tick); else pollTimer = null;
    };
    pollTimer = requestAnimationFrame(tick);
  }
  function stopPoll() { if (pollTimer) cancelAnimationFrame(pollTimer); pollTimer = null; }
  async function startRun() {
    if (running) return;
    if (!(await ensureRunSession())) return;
    try { await api().run_resume(); } catch (e) {}   // 让后台引擎线程开始/继续跑
    running = true; setRunUI();
    startPoll();
  }
  function pauseRun() { running = false; stopPoll(); try { api().run_pause(); } catch (e) {} setRunUI(); }
  function toggleBreakpoint(id) {
    if (breakpoints.has(id)) breakpoints.delete(id); else breakpoints.add(id);
    if (canvas) canvas.setDirty(true, true);
    if (runSession) { try { api().run_set_breakpoints([...breakpoints], runUntil); } catch (e) {} }
    setStatus(breakpoints.has(id) ? "已设断点 🔴（运行命中即暂停）" : "已取消断点");
  }
  function runToNode(id) {              // 连续运行直到该节点被执行到，然后暂停（一次性）
    runUntil = id;
    if (runSession) { try { api().run_set_breakpoints([...breakpoints], runUntil); } catch (e) {} }
    setStatus("运行到此节点…（命中即暂停）");
    if (!running) startRun(); else setRunUI();
  }
  function toggleRun() {                 // 主按钮：运行（向游戏发输入）。暂停中点「继续」沿用原会话模式
    if (running) { pauseRun(); return; }
    if (!runSession) realRun = true;     // 新会话＝正式运行
    startRun();
  }
  function dryRun() {                     // 次按钮：试运行（干跑，只识别不发输入），调试核对用
    if (runSession) return;              // 已有会话时按钮已禁用，这里兜底
    realRun = false;
    startRun();
  }
  async function stopRun() {
    running = false; stopPoll(); stopRunAnim();
    if (runSession) { try { await api().run_end(); } catch (e) {} runSession = false; }
    runPath = new Set(); runPathArr = []; runPorts = {}; runData = {}; runDataNodes = new Set(); runTimes = {};
    setRunUI();
    if (canvas) canvas.setDirty(true, true);
    setStatus("已停止运行");
  }
  // 性能监控开关：开启后运行时在每个节点叠加「自身耗时 / 帧内累计耗时」。可在运行中随时切换。
  function toggleProfile() {
    profileOn = !profileOn;
    const b = document.getElementById("profbtn");
    if (b) b.classList.toggle("on", profileOn);
    if (!profileOn) runTimes = {};
    if (runSession) { try { api().run_set_profile(profileOn); } catch (e) {} }
    if (canvas) canvas.setDirty(true, false);
    setStatus(profileOn ? "已开启性能监控：运行时每个节点显示「本节点ms · Σ累计ms」" : "已关闭性能监控");
  }

  // ============ 资源监控小窗：右上角常驻迷你曲线 + 数字，点开看详情/改采样配置 ============
  // 把经典版的“内存/CPU监控”融入编辑器：定时轮询后端 sys_stats（每秒级，开销极小），
  // 维护环形缓冲画曲线。轮询是唯一的常驻定时器，无 DOM 抖动，不影响试运行性能。
  const SYS_DOT = "#2a2f38";
  let sysCfg = { interval: 1000, minutes: 5 };       // 采样间隔(ms) / 保留时长(分钟)
  let sysHist = { cpu: [], mem: [], gcpu: [], gmem: [] }, sysLast = null, sysTimer = null, monOpen = false;
  function sysCap() { return Math.max(30, Math.round(sysCfg.minutes * 60000 / sysCfg.interval)); }
  function sysLoadCfg() {
    try {
      const v = JSON.parse(localStorage.getItem("flow.sysmon") || "{}");
      if (v.interval) sysCfg.interval = Math.max(200, Math.min(10000, v.interval));
      if (v.minutes) sysCfg.minutes = Math.max(1, Math.min(120, v.minutes));
    } catch (e) {}
  }
  function sysSaveCfg() { try { localStorage.setItem("flow.sysmon", JSON.stringify(sysCfg)); } catch (e) {} }
  function fmtMB(mb) { return mb >= 1024 ? (mb / 1024).toFixed(2) + " GB" : Math.round(mb) + " MB"; }
  // 在画布上画一条填充折线（data 为数值数组，maxv 为纵轴上限）。noClear=true 用于在同一图上叠第二条线。
  function drawSeries(cv, data, maxv, color, fill, noClear) {
    if (!cv) return;
    const ctx = cv.getContext("2d"), w = cv.width, h = cv.height;
    if (!noClear) ctx.clearRect(0, 0, w, h);
    if (!data.length) return;
    const n = data.length, dx = n > 1 ? w / (n - 1) : w, top = 2, bot = h - 2;
    const y = (v) => bot - Math.max(0, Math.min(1, v / maxv)) * (bot - top);
    ctx.beginPath();
    for (let i = 0; i < n; i++) { const px = i * dx, py = y(data[i]); i ? ctx.lineTo(px, py) : ctx.moveTo(px, py); }
    if (fill) {
      ctx.lineTo((n - 1) * dx, bot); ctx.lineTo(0, bot); ctx.closePath();
      ctx.fillStyle = fill; ctx.fill();
      ctx.beginPath();
      for (let i = 0; i < n; i++) { const px = i * dx, py = y(data[i]); i ? ctx.lineTo(px, py) : ctx.moveTo(px, py); }
    }
    ctx.strokeStyle = color; ctx.lineWidth = 1.5; ctx.lineJoin = "round"; ctx.stroke();
  }
  function memMax() {                              // 内存纵轴上限：随峰值动态抬升（本工具+游戏），至少 256MB，留 20% 余量
    let m = 256;
    for (const v of sysHist.mem) if (v > m) m = v;
    for (const v of sysHist.gmem) if (v > m) m = v;
    return Math.ceil(m * 1.2 / 64) * 64;
  }
  function drawSysMini() {
    const cv = document.getElementById("sysspark");
    if (cv) {                                      // 迷你窗只画 CPU 折线（最能反映卡顿），内存看数字
      const ctx = cv.getContext("2d");
      ctx.clearRect(0, 0, cv.width, cv.height);
      ctx.strokeStyle = SYS_DOT; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(0, cv.height - 1.5); ctx.lineTo(cv.width, cv.height - 1.5); ctx.stroke();
      drawSeries(cv, sysHist.cpu, 100, "#7fb0ee", "#7fb0ee22");
    }
    const t = document.getElementById("systext");
    if (t && sysLast) {
      const c = t.querySelector(".sm-cpu"), m = t.querySelector(".sm-mem");
      if (c) c.textContent = Math.round(sysLast.tool_cpu) + "%";
      if (m) m.textContent = fmtMB(sysLast.tool_mem);
    }
  }
  // 详细图表（缩放/平移/叠加游戏曲线 + 置顶/半透明）已移到独立的「资源监控」系统浮窗：web/sysmon.html。
  async function sysPoll() {
    let s = null;
    try { s = await api().sys_stats(); } catch (e) { s = null; }
    if (s && s.ok) {
      sysLast = s;
      const cap = sysCap();
      sysHist.cpu.push(s.tool_cpu); sysHist.mem.push(s.tool_mem);
      sysHist.gcpu.push(s.game_cpu || 0); sysHist.gmem.push(s.game_mem || 0);
      for (const k of ["cpu", "mem", "gcpu", "gmem"]) while (sysHist[k].length > cap) sysHist[k].shift();
      drawSysMini();
    }
    sysTimer = setTimeout(sysPoll, sysCfg.interval);
  }
  function startSysMon() {     // 编辑器内只保留右上角迷你窗（一眼看占用）；详细图表在独立的「资源监控」浮窗里看
    if (sysTimer) return;
    sysLoadCfg();
    sysPoll();
  }
  // 点迷你窗 → 开/关独立的「资源监控」系统浮窗（只它置顶、可拖到屏幕任意处、原生缩放；主编辑器不受影响）
  async function toggleSysMon() {
    try { const r = await api().toggle_monitor(); monOpen = !!(r && r.open); }
    catch (e) { showError("打开资源监控失败：" + (e && (e.stack || e.message) || e)); }
  }

  const self = {
    toggleRun, dryRun, stopRun, toggleProfile, toggleSimple, toggleSysMon,
    clearLog() { runLogs = []; renderLog(); },
    async save() {
      try {
        const p = await api().save(collect());
        if (p) await afterSaved(p);
        setStatus(p ? `已保存 ${p}` : "已取消保存");
      } catch (err) { showError("保存失败：" + (err.stack || err)); }
    },
    async saveAs() {
      try {
        const p = await api().save_as(collect());
        if (p) await afterSaved(p);
        setStatus(p ? `已保存 ${p}` : "已取消");
      } catch (err) { showError("另存为失败：" + (err.stack || err)); }
    },
    editFlowInfo,
    async open() {
      try {
        if (!(await guardUnsaved("打开其它流程"))) return;
        const flow = await api().open_dialog();
        if (flow) load(flow);
      } catch (err) { showError("打开失败：" + (err.stack || err)); }
    },
    async openBuiltin(path) {
      if (!path) return;
      try {
        if (!(await guardUnsaved("切换流程"))) { selectCurrentInList(); return; }  // 取消则下拉选回当前
        const flow = await api().open_path(path);
        if (flow) load(flow);   // load -> updateFlowMeta -> 下拉自动选中该项
      } catch (err) { showError("打开流程失败：" + (err.stack || err)); selectCurrentInList(); }
    },
    async autolayout() {
      try {
        const flow = await api().autolayout(collect());
        if (!flow) return;
        snapshotNow();                              // 先把“排版前”快照确保进撤销栈
        load(flow, { clean: false, keepHistory: true });   // 只动版面：保留“未保存”判定且可撤销
        setStatus("已自动排版（可 Ctrl+Z 撤销）");
      } catch (err) { showError("自动排版失败：" + (err.stack || err)); }
    },
    fit,
    help: toggleHelp,
  };

  // 在 ResizeObserver 回调里【同步】改 canvas 尺寸会再触发布局 -> 浏览器报 "ResizeObserver loop"。
  // 故把真正的尺寸调整推迟到下一帧（rAF），并合并连续触发，既消除告警又保持实时。
  let _resizePending = false;
  function doResize() {
    _resizePending = false;
    const w = document.getElementById("wrap");
    const c = document.getElementById("graph");
    if (!w || !c) return;
    if (c.width !== w.clientWidth || c.height !== w.clientHeight) {
      c.width = w.clientWidth; c.height = w.clientHeight;
    }
    if (canvas) { canvas.resize(); canvas.setDirty(true, true); }
  }
  function resize() {
    if (_resizePending) return;
    _resizePending = true;
    requestAnimationFrame(doResize);
  }

  // ---- 选中节点时显示中文说明（节点简介 + 各参数用法）----
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>]/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
  }

  function setupHelpPanel() {
    helpEl = document.createElement("div");
    helpEl.id = "helpbox";
    helpEl.style.cssText = "position:absolute;right:10px;bottom:62px;max-width:340px;max-height:50%;" +
      "overflow:auto;background:#23272fee;color:#cfd3da;border:1px solid #3a404a;border-radius:6px;" +
      "padding:8px 10px;font:12px/1.6 'Microsoft YaHei',sans-serif;display:none;z-index:50;";
    document.body.appendChild(helpEl);
    canvas.onNodeSelected = (n) => { selectedNode = n; showNodeHelp(n); };
    canvas.onNodeDeselected = () => { selectedNode = null; helpEl.style.display = "none"; };
  }

  // 选中节点的说明面板：只放“该节点专属”的内容（用途 + 需要解释的端口/参数），
  // 不重复通用的连线模型与操作说明——后者集中在顶部“帮助”里（见 self.help）。
  function portLine(p) {
    const tag = p.kind === "exec" ? "<span style='color:#ddd'>[执行]</span>"
                                  : "<span style='color:#7fbf7f'>[数据]</span>";
    return `<div style="margin-top:2px">${tag} <b style="color:#bcd">${esc(p.label || p.name)}</b>：${esc(p.help)}</div>`;
  }

  // 右下角说明各区段的“展开/折叠”记忆（节点切换/重绘时保持用户的展开状态）。
  let helpOpen = { params: true, ports: false, adv: false, pin: false };
  // 折叠区段：<details> 原生折叠；data-sec 用于记忆展开状态。
  function section(key, title, body, count) {
    const head = count != null ? `${title}（${count}）` : title;
    return `<details data-sec="${key}"${helpOpen[key] ? " open" : ""}>` +
           `<summary style="cursor:pointer;color:#8b909a;margin-top:6px;border-top:1px solid #3a404a;padding-top:4px;outline:none">${head}</summary>` +
           `<div style="padding-top:2px">${body}</div></details>`;
  }

  function showNodeHelp(node) {
    if (!helpEl) return;
    if (simpleMode) { helpEl.style.display = "none"; return; }   // 使用模式：画布只读，不显示可编辑的节点说明
    const d = defByType[node && node._typeId];
    if (!d) { helpEl.style.display = "none"; return; }
    const sub = "color:#7f8895;border-top:1px solid #3a404a;margin-top:6px;padding-top:4px";
    let html = `<div style="font-weight:bold;color:#e6e9ee;margin-bottom:2px">${esc(d.title)}</div>`;
    const doc = d.doc || d.help || "";
    if (doc) html += `<div style="color:#9aa3af;white-space:pre-line">${esc(doc)}</div>`;
    // 所属分组（带色块），并提供“分组…”入口
    const gidx = node ? nodeGroupIndex(node._id) : -1;
    if (gidx >= 0) {
      const g = groupDefs[gidx], col = groupColor(g, gidx);
      html += `<div style="margin-top:4px;color:#9aa3af">所属分组：` +
              `<span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:${col};vertical-align:middle"></span> ` +
              `<b style="color:#bcd">${esc(g.title || "分组")}</b> ` +
              `<a href="#" data-grp="1" style="color:#7fb0ee;text-decoration:none">[更改]</a></div>`;
    } else {
      html += `<div style="margin-top:4px;color:#7f8895">未分组 <a href="#" data-grp="1" style="color:#7fb0ee;text-decoration:none">[加入分组]</a></div>`;
    }

    const allPorts = (d.inputs || []).concat(d.outputs || []);
    // 主要参数说明（默认展开）；进阶参数/端口折到“进阶”区，避免误导用户以为都得用。
    const ps = (d.params || []).filter((p) => p.help && !p.advanced);
    if (ps.length)
      html += section("params", "参数说明",
        ps.map((p) => `<div style="margin-top:2px"><b style="color:#bcd">${esc(p.label)}</b>：${esc(p.help)}</div>`).join(""), ps.length);
    // 主要端口说明（默认折叠）：只列含义不直观、带说明的主端口
    const ports = allPorts.filter((p) => p.help && !p.advanced);
    if (ports.length)
      html += section("ports", "端口说明", ports.map(portLine).join(""), ports.length);
    // 进阶（一般用不到）：把次要端口/参数集中折叠，明确告诉用户平时不必接/不必改
    const advPorts = allPorts.filter((p) => p.advanced);
    const advParams = (d.params || []).filter((p) => p.advanced);
    if (advPorts.length || advParams.length) {
      let b = "<div style='color:#7f8895;margin-bottom:2px'>以下为次要/调试用，平时无需接线或修改。</div>";
      b += advPorts.map(portLine).join("");
      b += advParams.map((p) => `<div style="margin-top:2px"><b style="color:#9aa0aa">${esc(p.label)}</b>：${esc(p.help || "")}</div>`).join("");
      html += section("adv", "进阶（一般用不到）", b, advPorts.length + advParams.length);
    }

    // 显示到控制面板：勾选哪些参数置顶到顶部面板；已勾选的可单独设“面板显示名”。
    const pinnable = (d.params || []);
    if (pinnable.length) {
      let b = "";
      for (const p of pinnable) {
        const pinned = isPinned(node._id, p.key);
        b += `<div style="margin-top:3px"><label style="cursor:pointer;color:#aeb6c2">` +
             `<input type="checkbox" data-pin="${esc(p.key)}" ${pinned ? "checked" : ""}> ${esc(p.label)}</label>`;
        if (pinned) {
          const cur = (pinEntry(node._id, p.key) || [])[2] || "";
          b += ` <input type="text" data-pinlabel="${esc(p.key)}" value="${esc(cur)}" ` +
               `placeholder="${esc(defaultPinLabel(node, p.key))}" title="在控制面板上显示的名称（留空＝用默认）" ` +
               `style="width:110px;background:#15171c;color:#cfd3da;border:1px solid #444;border-radius:3px;font-size:12px;padding:1px 4px">`;
        }
        b += `</div>`;
      }
      html += section("pin", "显示到控制面板", b, null);
    }
    helpEl.innerHTML = html;
    helpEl.querySelectorAll("details[data-sec]").forEach((dt) => {
      dt.addEventListener("toggle", () => { helpOpen[dt.getAttribute("data-sec")] = dt.open; });
    });
    helpEl.querySelectorAll("[data-pin]").forEach((cb) => {
      cb.onchange = () => togglePin(node._id, cb.getAttribute("data-pin"));
    });
    helpEl.querySelectorAll("[data-pinlabel]").forEach((inp) => {
      inp.onchange = () => setPinLabel(node._id, inp.getAttribute("data-pinlabel"), inp.value);
      inp.onkeydown = (e) => e.stopPropagation();   // 输入框内按键不触发画布快捷键
    });
    const grpLink = helpEl.querySelector("[data-grp]");
    if (grpLink) grpLink.onclick = (e) => { e.preventDefault(); assignGroupDialog([node]); };
    // 已修改（未保存）的参数：列出 旧→新 并给“恢复”链接（每项 + 全部）
    const changed = (node.widgets || []).filter(paramChanged);
    if (changed.length) {
      const box = document.createElement("div");
      box.style.cssText = sub;
      let h2 = "<span style='color:#e6a23c'>已修改（未保存）</span> " +
               "<a href='#' data-revert='*' style='color:#7fb0ee;text-decoration:none'>[全部恢复]</a>";
      for (const w of changed)
        h2 += `<div style="margin-top:3px"><b style="color:#bcd">${esc(labelOf(node, w._key))}</b> ` +
              `<a href="#" data-revert="${esc(w._key)}" style="color:#7fb0ee;text-decoration:none">[恢复]</a>` +
              `<br><span style="color:#7f8895">旧：${esc(w._saved)} → 新：${esc(w.value)}</span></div>`;
      box.innerHTML = h2;
      helpEl.appendChild(box);
      box.querySelectorAll("[data-revert]").forEach((a) => {
        a.onclick = (e) => {
          e.preventDefault();
          const k = a.getAttribute("data-revert");
          if (k === "*") revertNode(node); else revertParam(node, k);
        };
      });
    }
    helpEl.style.display = "block";
  }

  // 流程统计：节点数 / 执行·数据连线数 / 分组数 / 面板项数 / 各类节点数（按分类）。
  function graphStats() {
    const ns = (graph && graph._nodes) || [];
    let execE = 0, dataE = 0;
    for (const k in (graph && graph.links || {})) {
      const l = graph.links[k]; if (!l) continue;
      const a = graph.getNodeById(l.origin_id);
      const t = a && a.outputs && a.outputs[l.origin_slot] && a.outputs[l.origin_slot].type;
      if (t === "exec") execE++; else dataE++;
    }
    const cats = {};
    for (const n of ns) { const d = defByType[n._typeId]; const c = (d && d.category) || "其它"; cats[c] = (cats[c] || 0) + 1; }
    return { nodes: ns.length, execE, dataE, groups: groupDefs.length, pins: panelPins.length, cats };
  }

  // 通用确认框（返回 Promise<bool>）：点窗口外/取消＝false，确定＝true。
  function confirmBox(title, msg, okLabel) {
    return new Promise((resolve) => {
      document.getElementById("confirmdlg")?.remove();
      const box = document.createElement("div");
      box.id = "confirmdlg"; box.className = "popdlg";
      box.style.cssText = "position:absolute;left:50%;top:46px;transform:translateX(-50%);width:min(420px,92vw);" +
        "background:#23272f;color:#cfd3da;border:1px solid #3a404a;border-radius:8px;padding:14px 16px;z-index:170;" +
        "box-shadow:0 8px 30px #000a;font:13px/1.6 'Microsoft YaHei',sans-serif;";
      box.innerHTML = `<b style='color:#e6c07b'>${esc(title)}</b>` +
        `<div style='margin-top:8px;color:#cdd3db;white-space:pre-wrap'>${esc(msg)}</div>` +
        "<div style='margin-top:12px;text-align:right'>" +
        `<button id='cf_ok' style='background:#3a2222;color:#ffb3b3;border:1px solid #a33;border-radius:4px;padding:4px 14px;cursor:pointer'>${esc(okLabel || "确定")}</button> ` +
        "<button id='cf_cancel' style='background:#2f343d;color:#cfd3da;border:1px solid #444;border-radius:4px;padding:4px 14px;cursor:pointer'>取消</button></div>";
      document.body.appendChild(box);
      const done = (v) => { box.remove(); resolve(v); };
      box.querySelector("#cf_ok").onclick = () => done(true);
      box.querySelector("#cf_cancel").onclick = () => done(false);
    });
  }
  // 删除“我的流程”：确认后删文件、刷新下拉、打开列表里的第一个流程（内置只读流程不可删）。
  async function deleteCurrentFlow() {
    if (!flowMeta.path || flowMeta.readonly) return;
    const nm = flowMeta.name;
    if (!(await confirmBox("删除流程", `确定删除「${nm}」吗？\n文件会从「我的流程」中移除，且不可恢复。`, "删除"))) return;
    try {
      const r = await api().delete_flow(flowMeta.path);
      if (!r || !r.ok) { showError("删除失败：" + ((r && r.reason) || "未知原因")); return; }
      document.getElementById("flowdlg")?.remove();
      const list = await api().list_builtin();
      fillFlowList(list);
      const first = (list && list[0] && list[0].path) || null;   // 优先打开列表第一个（通常是内置「统一生产」）
      if (first) { const f = await api().open_path(first); if (f) load(f); }
      setStatus("已删除流程：" + nm);
    } catch (e) { showError("删除失败：" + (e && (e.stack || e.message) || e)); }
  }

  // 流程信息：默认【只读】展示（名称/来源/说明/统计），点“编辑”才能改名称与说明。
  function editFlowInfo() {
    document.getElementById("flowdlg")?.remove();
    const box = document.createElement("div");
    box.id = "flowdlg"; box.className = "popdlg";
    box.style.cssText = "position:absolute;left:50%;top:46px;transform:translateX(-50%);width:min(480px,92vw);" +
      "max-height:80vh;overflow:auto;background:#23272f;color:#cfd3da;border:1px solid #3a404a;border-radius:8px;" +
      "padding:14px 16px;z-index:130;box-shadow:0 8px 30px #000a;font:13px/1.6 'Microsoft YaHei',sans-serif;";
    document.body.appendChild(box);
    const inputCss = "width:100%;margin:4px 0 10px;background:#15171c;color:#cfd3da;border:1px solid #444;" +
      "border-radius:4px;padding:6px;font:13px/1.5 \"Microsoft YaHei\",sans-serif;box-sizing:border-box";
    const btn = "background:#2f343d;color:#cfd3da;border:1px solid #444;border-radius:4px;padding:3px 12px;cursor:pointer";
    let editing = false;
    const renderView = () => {
      const s = graphStats();
      const src = flowMeta.readonly ? "<span style='color:#e6a23c'>内置流程（只读，保存会另存到「我的流程」）</span>"
                : (flowMeta.path ? "我的流程" : "未保存的新流程");
      const cats = Object.keys(s.cats).sort().map((c) => `${c} ${s.cats[c]}`).join(" · ") || "（空）";
      box.innerHTML =
        "<b style='color:#e6c07b;font-size:14px'>流程信息</b><span style='color:#6b727d;font-size:12px'>（点窗口外关闭）</span>" +
        `<div style='margin-top:8px'><span style='color:#9aa3af'>名称：</span><b>${esc(flowMeta.name || "未命名流程")}</b></div>` +
        `<div><span style='color:#9aa3af'>来源：</span>${src}</div>` +
        `<div style='margin-top:6px;color:#9aa3af'>说明：</div>` +
        `<div style='white-space:pre-wrap;color:#cdd3db;background:#1b1f27;border:1px solid #2c323c;border-radius:6px;padding:8px 10px;margin-top:2px'>${esc(flowMeta.desc || "（暂无说明，点“编辑”添加）")}</div>` +
        "<div style='border-top:1px solid #3a404a;margin-top:10px;padding-top:8px;color:#9aa3af'><b style='color:#e6c07b'>统计</b>" +
        `<div style='margin-top:4px;color:#cdd3db'>节点 ${s.nodes} · 连线 执行${s.execE}/数据${s.dataE} · 分组 ${s.groups} · 控制面板项 ${s.pins}</div>` +
        `<div style='margin-top:4px'><span style='color:#9aa3af'>节点构成：</span>${esc(cats)}</div></div>` +
        "<div style='margin-top:12px;text-align:right'>" +
        ((flowMeta.path && !flowMeta.readonly)   // 只有「我的流程」才可删；内置只读流程不显示删除
          ? `<button id='fi_del' style='${btn};background:#3a2222;color:#ffb3b3;border-color:#a33;float:left'>🗑 删除此流程</button>` : "") +
        `<button id='fi_edit' style='${btn}'>编辑名称/说明</button></div>`;
      box.querySelector("#fi_edit").onclick = () => { editing = true; renderEdit(); };
      const del = box.querySelector("#fi_del");
      if (del) del.onclick = () => deleteCurrentFlow();
    };
    const renderEdit = () => {
      box.innerHTML = "<b style='color:#e6c07b'>编辑流程信息</b>（仅展示，不影响运行）" +
        "<div style='margin-top:8px;color:#9aa3af'>名称</div>" +
        `<input id='flowname_in' style='${inputCss}'/>` +
        "<div style='color:#9aa3af'>说明</div>" +
        `<textarea id='flowdesc_in' style='${inputCss};height:120px'></textarea>` +
        "<div style='text-align:right'>" +
        `<button id='flowok' style='${btn}'>保存</button> <button id='flowback' style='${btn}'>取消</button></div>`;
      const nameIn = box.querySelector("#flowname_in"), descIn = box.querySelector("#flowdesc_in");
      nameIn.value = flowMeta.name || ""; descIn.value = flowMeta.desc || "";
      nameIn.onkeydown = (e) => e.stopPropagation(); descIn.onkeydown = (e) => e.stopPropagation();
      nameIn.focus();
      box.querySelector("#flowok").onclick = () => {
        flowMeta.name = nameIn.value.trim() || "未命名流程";
        flowMeta.desc = descIn.value.trim();
        updateFlowMeta(); setStatus(`流程：${flowMeta.name}`);
        scheduleSnap(); refreshDirty();   // 名称/说明计入“未保存”
        editing = false; renderView();
      };
      box.querySelector("#flowback").onclick = () => { editing = false; renderView(); };
    };
    renderView();
  }

  // 顶部“帮助”：集中放连线模型图例 + 常用操作（避免在每个节点面板里重复）
  let helpModal = null;
  function toggleHelp() {
    if (helpModal) { helpModal.remove(); helpModal = null; return; }
    helpModal = document.createElement("div");
    helpModal.className = "popdlg";
    helpModal.style.cssText = "position:absolute;left:50%;top:46px;transform:translateX(-50%);" +
      "width:min(640px,92vw);max-height:80%;overflow:auto;background:#23272f;color:#cfd3da;" +
      "border:1px solid #3a404a;border-radius:8px;padding:14px 18px;z-index:100;" +
      "box-shadow:0 8px 30px #000a;font:13px/1.7 'Microsoft YaHei',sans-serif;";
    // 一段线样色块 + 文字，用于颜色图例
    const wire = (color, label) =>
      `<span style="display:inline-block;width:24px;border-top:3px solid ${color};` +
      `vertical-align:middle;margin:0 4px 0 10px"></span>${label}`;
    helpModal.innerHTML =
      "<div style='display:flex;align-items:center;margin-bottom:6px'>" +
      "<b style='font-size:15px;color:#e6e9ee;flex:1'>编辑器帮助</b>" +
      "<span style='color:#6b727d;font-size:12px'>点窗口外关闭</span></div>" +
      "<div style='color:#9aa3af'><b style='color:#e6c07b'>连线模型（类虚幻蓝图）</b><br>" +
      "· <b style='color:#fff'>白线＝执行流</b>：决定先做什么、再做什么、走哪条路。入口是「每帧触发」；" +
      "某个出口不接任何节点，就表示那种情况下<b>本帧到此结束</b>（无需专门的“结束”节点）。<br>" +
      "· <b style='color:#7fbf7f'>彩线＝数据流</b>：传递“值”，下游用到时才向上游取，一个输出可同时连给多处；" +
      "不同颜色＝不同类型（数值/是否/图像/区域…）。<br>" +
      "· <b>判断与分支</b>：检测/比较类节点只输出一个“是/否”（彩线）；把它接到「分支」的“条件”，" +
      "再从「真」「假」两个出口分别往后连——判断本身和“分岔”是分开的两件事。</div>" +
      "<div style='border-top:1px solid #3a404a;margin-top:10px;padding-top:8px;color:#9aa3af'>" +
      "<b style='color:#e6c07b'>彩线颜色＝数据类型</b>（连什么类型就显什么色）<br>" +
      wire("#7AB0EE", "数值") + wire("#E0A85A", "是否(布尔)") + wire("#9AD08A", "文本") +
      wire("#d6c15a", "列表") + wire("#C792DF", "图像") + wire("#69b0a0", "区域/坐标") + wire("#cf8a6a", "颜色") + "<br>" +
      "<span style='color:#7f8895'>当前流程的数据多是数字与是/否，所以你主要看到蓝、黄两色；用到其它类型时会出现对应颜色。</span></div>" +
      "<div style='border-top:1px solid #3a404a;margin-top:10px;padding-top:8px;color:#9aa3af'>" +
      "<b style='color:#e6c07b'>常用操作</b><br>" +
      "· 右键空白处：添加节点　· 滚轮：缩放　· 拖动空白：平移<br>" +
      "· 拖动节点标题：移动　· Ctrl+拖动空白：框选多个　· 选中多个后可整体拖动<br>" +
      "· 单击参数输入框：直接编辑，<b>实时生效</b>（无需确认按钮）<br>" +
      "· 右键连线（线上任意处）：删除连线<br>" +
      "· Ctrl+Z 撤销　· Ctrl+Y 或 Ctrl+Shift+Z 重做<br>" +
      "· 顶部按钮：自动排版（重新理顺布局）/ 适应窗口 / 保存<br>" +
      "· 选中节点→右下角说明里勾选「显示到控制面板」，把常用开关/数值置顶到顶部面板，" +
      "普通使用时不必进节点图也能调（🎯 定位到节点）<br>" +
      "· <b>分组</b>：右键节点→「分组…」把它(或多选的若干节点)归入一个彩色分组；节点标题栏会染成分组色、" +
      "上方标出组名，框也会自动包住它们。一个节点只属于一个组。<br>" +
      "· 顶部「流程信息」查看名称/说明/统计，点其中「编辑」可改名称与说明<br>" +
      "· <b>使用模式</b>（顶部按钮切换）：把画布转为<b>只读</b>——仍可拖动查看、运行、用控制面板调参，但不能增删改节点/连线/参数；" +
      "其中的调参与拖动都是临时的，<b>不会改动已保存的流程</b>，回到编辑模式即还原（适合“只想用”的场景）</div>" +
      "<div style='border-top:1px solid #3a404a;margin-top:10px;padding-top:8px;color:#9aa3af'>" +
      "<b style='color:#e6c07b'>运行 / 试运行</b>　顶部 <b style='color:#8fe0a8'>▶运行</b> / 停止；试运行(干跑)在编辑模式下可见<br>" +
      "· <b style='color:#8fe0a8'>运行</b>：执行当前流程，<b>真正向游戏发按键/鼠标</b>（再点变「暂停」，运行中可按住 Shift/Ctrl/Alt 暂停）。<br>" +
      "· <b>试运行＝干跑</b>：只识别、<b>不发任何输入</b>，安全；走过的节点高亮、连线流动、出口显示数据值、底部出日志，用于上线前核对。<br>" +
      "· 每帧间隔＝流程「每帧触发」节点的「循环间隔(秒)」(面板可调)。<br>" +
      "· 走过的<b>分支</b>节点标「真/假」=走了哪条线；执行<b>走到头</b>的出口标「⏹ 结束」(出口空接=本帧到此)。<br>" +
      "· <b>断点</b>：右键节点→「设为断点 🔴」，运行命中它就自动暂停；「运行到此节点 ⏭」= 跑到它再停。<br>" +
      "· 底部日志每行带<b>时间</b>与等级(• 信息 / ▲ 提醒 / ✕ 错误)，可<b>拖上边沿调高度</b>、<b>选中复制</b>；上滚查看时不会被新日志拽回底部。<br>" +
      "· 右上角<b>资源监控</b>小窗显示本工具 CPU/内存曲线，点开可看系统占用与调采样间隔/保留时长。</div>" +
      "<div style='border-top:1px solid #3a404a;margin-top:10px;padding-top:8px;color:#9aa3af'>" +
      "<b style='color:#e6c07b'>在游戏画面上直接采集</b>（节点里相应参数下方的按钮）<br>" +
      "· <b>框选区域…</b>：按住左键拖出矩形，Enter 确认（区域参数）<br>" +
      "· <b>取点…/吸色…</b>：移动有放大镜，点一下取坐标/颜色（吸色会顺带回填配套坐标）<br>" +
      "· <b>截取模板…</b>：框选游戏画面裁出小图存为模板（图片参数）<br>" +
      "· <b>选择图片…</b>：从已有图片文件选模板　· <b>捕获按键…</b>：按一下记下按键<br>" +
      "<span style='color:#7f8895'>点按钮后编辑器会自动最小化让开、截到游戏画面，采完自动恢复；Esc 取消。</span></div>";
    document.body.appendChild(helpModal);
  }

  // ---- 撤销/重做（对整图做 JSON 快照；buildGraph/applySnapshot 期间抑制）----
  function snapshotNow() {
    if (suppressSnap || building || !graph) return;
    // 使用模式：不入撤销栈、不计“未保存”（运行/调参是临时操作）；但运行中仍把改动热更新给引擎、并刷新面板。
    if (simpleMode) {
      renderPanel();
      if (runSession) { try { api().run_update(collect()); } catch (e) {} }
      return;
    }
    const s = JSON.stringify(collect());
    if (undoStack.length && undoStack[undoStack.length - 1] === s) return;
    undoStack.push(s);
    if (undoStack.length > 100) undoStack.shift();
    redoStack = [];
    refreshDirty();
    // 选中的节点参数有改动时，同步刷新右下角“已修改”列表（橙点本就实时随重绘更新）
    if (selectedNode && helpEl && helpEl.style.display !== "none") showNodeHelp(selectedNode);
    renderPanel();   // 面板控件值与节点保持同步
    if (runSession) { try { api().run_update(collect()); } catch (e) {} }   // 试运行中：参数热更新到引擎
  }
  function scheduleSnap() { clearTimeout(snapTimer); snapTimer = setTimeout(snapshotNow, 250); }
  function applySnapshot(s) {
    suppressSnap = true;
    const data = JSON.parse(s);
    const cam = [canvas.ds.scale, canvas.ds.offset[0], canvas.ds.offset[1]];  // 保持视角不变
    // 折叠状态不进撤销：重建后沿用当前折叠状态，避免撤销把折叠的节点又展开
    const collapsed = new Set();
    for (const n of graph._nodes) if (n.flags && n.flags.collapsed) collapsed.add(n._id);
    buildGraph(data);
    for (const n of graph._nodes) if (collapsed.has(n._id)) { n.flags = n.flags || {}; n.flags.collapsed = true; }
    panelPins = Array.isArray(data.panel) ? data.panel.map((x) => x.slice(0, 3)) : [];   // 置顶项(含自定义名)随撤销/重做恢复
    groupDefs = Array.isArray(data.groups)
      ? data.groups.map((g) => ({ title: g.title || "分组", color: g.color || "", members: (g.members || []).slice() }))
      : [];   // 分组随撤销/重做恢复
    applyGroupColors();
    if (groupDlgRender && document.getElementById("grpdlg")) groupDlgRender();   // 分组弹窗开着则刷新
    attachBaselineRefs();   // 重建控件后重新挂基线引用，保证“已修改”橙点正确
    canvas.ds.scale = cam[0]; canvas.ds.offset = [cam[1], cam[2]];
    canvas.setDirty(true, true);
    renderPanel();
    suppressSnap = false;
  }
  function undo() {
    if (undoStack.length < 2) return;
    redoStack.push(undoStack.pop());
    applySnapshot(undoStack[undoStack.length - 1]);
    refreshDirty();
    setStatus("已撤销");
  }
  function redo() {
    if (!redoStack.length) return;
    const s = redoStack.pop();
    undoStack.push(s);
    applySnapshot(s);
    refreshDirty();
    setStatus("已重做");
  }

  // ---- 右键连线任意位置：找出离光标最近的连线（采样贝塞尔曲线）----
  function distToSeg(px, py, x1, y1, x2, y2) {
    const dx = x2 - x1, dy = y2 - y1, len2 = dx * dx + dy * dy;
    let t = len2 ? ((px - x1) * dx + (py - y1) * dy) / len2 : 0;
    t = Math.max(0, Math.min(1, t));
    return Math.hypot(px - (x1 + t * dx), py - (y1 + t * dy));
  }
  function linkNear(gx, gy) {
    if (!graph) return null;
    const thr = 14 / (canvas.ds.scale || 1);   // 命中阈值（图坐标）
    let best = null, bestD = thr;
    for (const k in graph.links) {
      const l = graph.links[k]; if (!l) continue;
      const a = graph.getNodeById(l.origin_id), b = graph.getNodeById(l.target_id);
      if (!a || !b) continue;
      let p0, p3;
      try { p0 = a.getConnectionPos(false, l.origin_slot); p3 = b.getConnectionPos(true, l.target_slot); }
      catch (e) { continue; }
      // 采样数随连线长度增加（含水平凸起），并用"点到线段"距离覆盖采样间隙——
      // 否则长的/竖向的连线采样点太稀，点在线上也会漏判。
      const span = Math.hypot(p3[0] - p0[0], p3[1] - p0[1]) + Math.abs(p3[0] - p0[0]) * 0.5;
      const steps = Math.max(24, Math.min(200, Math.round(span / 10)));
      let prev = canvas.computeConnectionPoint(p0, p3, 0);
      for (let i = 1; i <= steps; i++) {
        const cur = canvas.computeConnectionPoint(p0, p3, i / steps);
        const d = distToSeg(gx, gy, prev[0], prev[1], cur[0], cur[1]);
        if (d < bestD) { bestD = d; best = l; }
        prev = cur;
      }
    }
    return best;
  }
  function onRightDown(e) {
    if (e.button !== 2 || !graph || !canvas) return;
    if (simpleMode) return;   // 使用模式：不弹“删除连线”，右键交给只读菜单
    let off;
    try { off = canvas.convertEventToCanvasOffset(e); } catch (err) { return; }
    if (graph.getNodeOnPos(off[0], off[1], canvas.visible_nodes)) return;  // 节点上交给 LiteGraph
    const link = linkNear(off[0], off[1]);
    if (link) {
      e.preventDefault();
      e.stopImmediatePropagation();
      // 先关掉已打开的任何菜单，否则我们 stopImmediatePropagation 会让旧菜单的"点外部关闭"失效而叠加
      try { LiteGraph.closeAllContextMenus(window); } catch (err) {}
      canvas.showLinkMenu(link, e);
    }
  }

  // —— 拖动“分组标签页”整体移动组内节点（可拖区域只有那个小标签，避免误触画布/节点）——
  let _groupDrag = null;
  function _measCtx() { return (canvas && canvas.canvas) ? canvas.canvas.getContext("2d") : null; }
  function groupTabRect(g) {
    const ctx = _measCtx(); if (!ctx) return null;
    if (g.collapsed) {                            // 折叠态：箱体标题栏即拖动手柄（拖它移动整组）
      const sb = subgBox(g, ctx); if (!sb) return null;
      return [sb.x, sb.y, sb.w, SUBG.TITLE_H];
    }
    const box = groupBox(g, ctx); if (!box) return null;
    ctx.font = "bold 13px 'Microsoft YaHei',sans-serif";
    const tw = ctx.measureText(groupTabText(g)).width;
    return [box[0], box[1] - 18, tw + 18, 20];   // 与 drawGroups 标签页几何一致
  }
  // 命中测试：返回坐标落在哪个【折叠箱体】内的组下标（用于双击展开）；无则 -1。
  function foldedBoxAt(gx, gy) {
    const ctx = _measCtx(); if (!ctx) return -1;
    for (const { g, i } of collapsedGroupList()) {
      const b = subgBox(g, ctx); if (!b) continue;
      if (gx >= b.x && gx <= b.x + b.w && gy >= b.y && gy <= b.y + b.h) return i;
    }
    return -1;
  }
  // 折叠 / 展开某个分组（i=组下标）。折叠＝把该组收成一个紧凑子图节点；展开＝还原成员节点视图。
  function setGroupCollapsed(i, collapsed) {
    const g = groupDefs[i]; if (!g) return;
    g.collapsed = !!collapsed;
    refreshFold();
    if (canvas) canvas.setDirty(true, true);
    scheduleSnap(); refreshDirty();
    setStatus(g.collapsed ? `已折叠「${g.title || "分组"}」为子图（双击展开）` : `已展开「${g.title || "分组"}」`);
  }
  function onCanvasDblClick(e) {
    if (!graph || !canvas || simpleMode) return;
    let off; try { off = canvas.convertEventToCanvasOffset(e); } catch (err) { return; }
    if (graph.getNodeOnPos(off[0], off[1], canvas.visible_nodes)) return;   // 节点上交给 LiteGraph
    const gi = foldedBoxAt(off[0], off[1]);
    if (gi >= 0) { e.preventDefault(); e.stopImmediatePropagation(); setGroupCollapsed(gi, false); }
  }
  function onGroupDragDown(e) {
    if (e.button !== 0 || !graph || !canvas) return;
    let off; try { off = canvas.convertEventToCanvasOffset(e); } catch (err) { return; }
    if (graph.getNodeOnPos(off[0], off[1], canvas.visible_nodes)) return;   // 在节点上交给 LiteGraph
    for (let i = 0; i < groupDefs.length; i++) {
      const r = groupTabRect(groupDefs[i]); if (!r) continue;
      if (off[0] >= r[0] && off[0] <= r[0] + r[2] && off[1] >= r[1] && off[1] <= r[1] + r[3]) {
        e.preventDefault(); e.stopImmediatePropagation();
        const members = (groupDefs[i].members || []).map(nodeByOurId).filter(Boolean);
        _groupDrag = { last: off, members, moved: false };
        window.addEventListener("pointermove", onGroupDragMove, true);
        window.addEventListener("pointerup", onGroupDragUp, true);
        return;
      }
    }
  }
  function onGroupDragMove(e) {
    if (!_groupDrag) return;
    let off; try { off = canvas.convertEventToCanvasOffset(e); } catch (err) { return; }
    const dx = off[0] - _groupDrag.last[0], dy = off[1] - _groupDrag.last[1];
    if (dx || dy) {
      for (const n of _groupDrag.members) { n.pos[0] += dx; n.pos[1] += dy; }
      _groupDrag.last = off; _groupDrag.moved = true;
      if (canvas) canvas.setDirty(true, true);
    }
  }
  function onGroupDragUp() {
    window.removeEventListener("pointermove", onGroupDragMove, true);
    window.removeEventListener("pointerup", onGroupDragUp, true);
    if (_groupDrag && _groupDrag.moved) scheduleSnap();
    _groupDrag = null;
  }

  function start() {
    try {
      setupColors();
      installEditorTweaks();
      graph = new LGraph();
      canvas = new LGraphCanvas("#graph", graph);
      canvas.allow_searchbox = false;   // 关闭双击/Shift 弹出的搜索框（易误触；加节点统一走右键空白处"添加节点"）
      canvas.show_info = false;          // 隐藏左下角 T/I/N/V/FPS 调试信息（对普通用户无意义）
      canvas.render_connections_border = false;  // 连线不画深色描边——避免"一条线两种颜色(深/浅)"的观感
      canvas.render_canvas_border = false;  // 不画画布边框（背景里那条蓝色细线矩形）
      canvas.node_title_color = "#e3e7ee";  // 默认标题字调亮（原 #999 偏灰、看不清）
      canvas.onDrawBackground = drawGroups;   // 在节点后面画“分组框”（随成员自动包裹）/ 折叠态画“子图箱体”
      canvas.onDrawForeground = drawRunOverlay;   // 在所有节点之上画“试运行”高亮/数据/连线流动
      // 折叠子图：用自定义连线绘制——跳过组内连线、把跨边界连线改接到箱体端口；无折叠时走原版。
      const _origDrawConn = canvas.drawConnections;
      canvas.drawConnections = function (ctx) {
        if (!foldHidden.size) return _origDrawConn.call(this, ctx);
        drawFoldedConnections.call(this, ctx);
      };
      setupHelpPanel();
      setupLogResize();
      // 撤销触发点：连线变化 / 增删节点 / 移动节点（参数改动在 addParamWidget 的回调里）
      graph.onConnectionChange = scheduleSnap;
      graph.onNodeAdded = scheduleSnap;
      // 删除节点：除快照外，隐藏右下角说明面板（否则被删节点的说明会残留）
      graph.onNodeRemoved = () => {
        panelPins = panelPins.filter((p) => graph._nodes.some((n) => n._id === p[0]));   // 删节点连带移除其面板项
        const ids = new Set(graph._nodes.map((n) => n._id));
        breakpoints = new Set([...breakpoints].filter((id) => ids.has(id)));   // 删节点连带移除其断点
        if (runUntil && !ids.has(runUntil)) runUntil = null;
        // 清理悬空连线：删节点时，其“多条汇入 exec 输入”里 LiteGraph 没自动清的那些会变悬空，手动剔除
        for (const k in graph.links) {
          const l = graph.links[k];
          if (l && (!graph.getNodeById(l.origin_id) || !graph.getNodeById(l.target_id))) delete graph.links[k];
        }
        for (const g of groupDefs) g.members = (g.members || []).filter((m) => ids.has(m));
        groupDefs = groupDefs.filter((g) => g.members.length);   // 空组自动消失
        refreshFold();
        renderPanel();
        scheduleSnap(); selectedNode = null; if (helpEl) helpEl.style.display = "none";
      };
      canvas.onNodeMoved = scheduleSnap;
      // 右键任意位置点中连线 -> 删除连线菜单（捕获阶段，先于 LiteGraph 的右键菜单）
      canvas.canvas.addEventListener("pointerdown", onRightDown, true);
      canvas.canvas.addEventListener("pointerdown", onGroupDragDown, true);   // 左键拖“分组标签页”整体移动该组
      canvas.canvas.addEventListener("dblclick", onCanvasDblClick, true);     // 双击折叠箱体 = 展开该子图
      // 兜底：任何鼠标交互结束后尝试快照（snapshotNow 用 JSON 比对去重，无变化不入栈）
      canvas.canvas.addEventListener("pointerup", scheduleSnap);
      // Ctrl+Z 撤销 / Ctrl+Y 或 Ctrl+Shift+Z 重做（编辑输入框内已 stopPropagation，不会误触）
      document.addEventListener("keydown", (e) => {
        if (!(e.ctrlKey || e.metaKey)) return;
        const k = e.key.toLowerCase();
        if (k === "z" && !e.shiftKey) { e.preventDefault(); undo(); }
        else if (k === "y" || (k === "z" && e.shiftKey)) { e.preventDefault(); redo(); }
      });
      doResize();   // 首次同步定尺寸（之后的 resize 走 rAF 合并）
      window.addEventListener("resize", resize);
      // 用 ResizeObserver 让窗口拖拽改变大小时内容实时刷新（window resize 在部分情况下不够即时）
      try { new ResizeObserver(resize).observe(document.getElementById("wrap")); } catch (e) {}
      window.__bootReady = true;       // 供 Python 可选地 push 启动数据（__bootstrap__）
      setStatus("正在连接后端…");
      pullWhenReady(0);                // 主路径：轮询直到 api 就绪再拉取（pywebview 注入 api 有延迟）
    } catch (err) {
      showError("初始化画布失败：\n" + (err && (err.stack || err.message) || err));
    }
  }

  // pywebview 把 js_api 方法注入 window.pywebview.api 有延迟（且 api 对象可能先于方法出现），
  // 因此必须轮询到 get_defs 真的是函数再拉取，否则会 "a.get_defs is not a function"。
  async function pullWhenReady(tries) {
    if (booted) return;
    const a = window.pywebview && window.pywebview.api;
    if (!a || typeof a.get_defs !== "function") {
      if (tries > 200) {  // 约 40 秒仍不就绪才报错
        showError("无法连接后端：window.pywebview.api.get_defs 始终不可用。\n" +
                  "（pywebview/WebView2 注入失败？可设 AOE4_EDITOR_DEBUG=1 开开发者工具排查）");
        return;
      }
      setTimeout(() => pullWhenReady(tries + 1), 200);
      return;
    }
    try {
      const d = await a.get_defs();
      const b = await a.list_builtin();
      const f = await a.get_flow();
      boot({ defs: d, builtin: b, flow: f });
    } catch (err) {
      showError("拉取启动数据失败：\n" + (err && (err.stack || err.message) || err));
    }
  }

  window.addEventListener("DOMContentLoaded", start);
  return self;
})();
