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
  let selectedGroupId = null;   // 当前选中的【组】id（组像节点一样可选中；右下角显示组的详情/暴露参数）。与 selectedNode 互斥。
  let seq = 1;
  let booted = false;
  // 当前流程的元信息：名称/说明/文件路径/是否内置只读（保存时内置会被改为另存）
  let flowMeta = { name: "", desc: "", path: null, readonly: false };
  // 控制面板置顶项：有序的 [nodeId, paramKey, 自定义显示名?]（随流程保存）。
  let panelPins = [];
  // 参数暴露（封装：组≈函数，逐级暴露、不冒泡）。两级：
  //  · foldPins=[[nodeId,paramKey]]：节点把【自身】参数暴露给它【所在的直接组】（折叠该组后在箱体里可编辑）。
  //  · groupExpose=[[groupId,nodeId,paramKey]]：某组把【已暴露进它接口】的某参数，再【向上一级】暴露给它的父组。
  // 一个参数要出现在第 N 层组的折叠箱体里，必须从拥有它的节点起、沿途每一层组都勾选了向上暴露（见 interfaceParams）。
  let foldPins = [];
  let groupExpose = [];
  // 参数自定义显示名的【唯一权威存储】（键= nodeId|key）：控制面板置顶 与 “暴露给所在组”折叠箱体 共用同一个名字。
  // 与是否置顶无关——只要填了两处都用；随流程保存(labels 字段)、撤销/重做、取消勾选都不丢；清空文本即删除、回落默认名。
  let pinLabels = {};
  let _panelDrag = null;                        // 控制面板项拖动调序：正在拖的项 "nodeId|key"（仅编辑模式）
  // 可视化分组：[{title, color, collapsed?, members:[ourNodeId...]}]，框随成员节点自动包裹（仅展示，随流程保存）。
  // collapsed=true 时该组折叠成一个紧凑“子图节点”：隐藏成员、把跨边界连线汇成箱体输入/输出端口（见“可折叠子图”一节）。
  let groupDefs = [];
  let foldHidden = new Set();   // 当前被折叠组隐藏的成员 ourId（仅影响显示/命中；collect 仍输出完整扁平图供引擎用）
  // Alt 拖拽态：{kind:'node'|'group', detached:Set(ourId 被拖出的成员), keepIds:Set(不排除的组 id), targetGi:将落入的组下标|-1}。
  // 作用：拖拽期间把被拖成员从【源组/祖先】的包裹框里“摘出”(groupBox 跳过它们)，使父框不跟随、能拖出去；并高亮落点组。
  let _altDrag = null;
  let _lastMenuPos = null;     // 最近一次右键的【图坐标】（空白处“新建组”据此定位新组）
  let groupDlgRender = null;   // 分组弹窗打开时的重绘函数（撤销/重做后用于刷新弹窗）
  const GROUP_COLORS = ["#3a6ea5", "#5a9367", "#a5793a", "#8a5a9a", "#b05a5a", "#4a8a8a"];
  // —— 试运行（干跑）可视化状态 ——
  let running = false, runSession = false, pollTimer = null, realRun = false, _lastTick = 0;   // realRun: true=真跑(真发输入)
  let runPath = new Set(), runPathArr = [], runPorts = {}, runData = {}, runLogs = [];
  let runDataNodes = new Set();                 // 本帧产生过数据的节点（用于高亮/不被压暗）
  let profileOn = false, runTimes = {};         // 性能监控：开关 + 本帧各节点 [自身ms, 累计ms]
  let previewOn = false, runPreviews = {}, runPreviewLabels = {};   // 截图预览：开关 + 各感知节点截到的区域图(base64 PNG) + 一行标签(置信度/识别值)
  const _previewImgCache = {};                  // nodeId -> {b64, img}：解码后的 Image 缓存，避免每帧重建
  let breakpoints = new Set();                  // 试运行断点：命中(出现在执行路径)即暂停；会话级，不随流程保存
  let bpHitId = null;                           // 当前“因命中断点而暂停”停在的节点 ourId（用于醒目高亮+居中；继续/停止后清空）
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
      return [
        { content: "添加节点", has_submenu: true, callback: LGraphCanvas.onMenuAdd },
        null,
        { content: "新建组", callback: () => createGroupAt(_lastMenuPos) },   // 在右键处新建一个空组，拖节点进去即归入
        { content: "分组管理…", callback: () => assignGroupDialog(Object.values((canvas && canvas.selected_nodes) || {})) },   // 任意空白处都能管理分组（有选中节点则一并可指派）
      ];
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
          if (this.dragging_canvas) { cv.style.cursor = "grabbing"; return ret; }   // 正在平移整个画布 → 抓握
          if (_groupDrag) { cv.style.cursor = altKeyDown ? "copy" : "crosshair"; return ret; }   // 拖整组中：Alt=拖进/出组(copy)，否则移动(十字)
          if (this.node_dragged && !simpleMode) {     // 拖动节点中：按住 Alt 才是“拖去/拖出分组”，并实时摘出+高亮落点
            if (altKeyDown) {
              const dn = this.node_dragged;
              const sel = Object.values(this.selected_nodes || {});
              const nodes = (sel.length > 1 && sel.includes(dn)) ? sel : [dn];
              _altDrag = { kind: "node", detached: new Set(nodes.map((n) => n._id)), keepIds: new Set(), targetGi: -1 };
              _altDrag.targetGi = nodeDropGroupIndex(dn);   // groupBox 此刻已排除 detached → 不会误判回源组
              cv.style.cursor = "copy"; this.setDirty(true, true); return ret;   // 重绘背景层（分组框在背景）
            }
            if (_altDrag && _altDrag.kind === "node") { _altDrag = null; this.setDirty(true, true); }   // 松开 Alt：源组恢复跟随
          }
          if (this.node_dragged || this.resizing_node || this.connecting_node ||
              this.dragging_rectangle || this.selected_group) return ret;
          if (cv.style.cursor === "se-resize") return ret;            // 缩放角保持
          const x = e.canvasX, y = e.canvasY;
          const node = this.graph && this.graph.getNodeOnPos(x, y, this.visible_nodes);
          if (node) {
            if (simpleMode) { cv.style.cursor = "crosshair"; return ret; }   // 使用模式：可拖动查看位置(连线/改参仍只读)
            if (e.altKey) { cv.style.cursor = "copy"; return ret; }        // 按住 Alt：拖动将把它放进/移出分组
            if (node.getSlotInPosition && node.getSlotInPosition(x, y)) { cv.style.cursor = "pointer"; return ret; }   // 端口=可点(连线)
            if (_hitWidget(node, x - node.pos[0], y - node.pos[1])) { cv.style.cursor = "pointer"; return ret; }       // 控件=可点
            cv.style.cursor = "crosshair"; return ret;                // 标题/节点体：可拖动 → 十字
          }
          if (foldIconAt(x, y) >= 0) { cv.style.cursor = "pointer"; return ret; }                     // 折叠/展开按钮 → 小手
          if (overGroupHandle(x, y)) { cv.style.cursor = (!simpleMode && e.altKey) ? "copy" : "crosshair"; return ret; }   // 分组手柄：拖动整组(十字)；Alt=拖进/出组(copy，仅编辑模式)
          if (foldedBoxAt(x, y) >= 0) { cv.style.cursor = "pointer"; return ret; }                    // 折叠箱体：可双击展开 → 小手
          cv.style.cursor = "grab";                                   // 空白处：可平移 → 抓手
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

  // 「设置开关」节点的“目标开关”：用户不知道节点 id，所以给个下拉，列出图里所有开关——
  // 有面板显示名(如「出商人(市场)」)就显示名、否则显示 id；存的值这两种都能被引擎解析(按名或按id)。
  function switchLabel(n) { return pinLabels[n._id + "|on"] || n._id; }
  function switchTargetOptions() {
    const opts = [];
    for (const n of (graph && graph._nodes || [])) if (n._typeId === "data.switch") opts.push(switchLabel(n));
    return opts.length ? opts : ["（图中暂无开关）"];
  }

  function addParamWidget(node, p, def) {
    // 构造期（new base_class）createNode 尚未给 node.properties 赋初值，需自行兜底，
    // 否则末尾 node.properties[key]=... 会抛 TypeError，导致带参数的节点整体创建失败。
    if (!node.properties) node.properties = {};
    const cb = (v) => { if (!node.properties) node.properties = {}; node.properties[p.key] = v; scheduleSnap(); };
    let w;
    if ((def && def.type) === "control.set_switch" && p.key === "target")
      // 目标开关：图感知的下拉（值随当前图里的开关动态生成），免去手填节点 id
      w = node.addWidget("combo", p.label, p.default == null ? "" : String(p.default), cb, { values: switchTargetOptions });
    else if (p.ptype === "int")
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
      mkBtn("框选区域…", () => defer("框选区域…（拖动/移动/拖边角微调，Enter 确认 / Esc 取消）", () => api().pick_region(parseBox(w.value)), (box) => {
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
      mkBtn("捕获按键…", () => captureKey().then((k) => {   // 编辑器内部小窗捕获，不再弹独立窗口/不重复弹
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

  // 把浏览器 keydown 事件映射成引擎用的按键名（与 capture.py / pydirectinput 对齐）。
  // 返回 null=单独的修饰键(忽略)；"__cancel__"=Esc 取消。
  function jsKeyToName(e) {
    const k = e.key;
    if (k === "Escape") return "__cancel__";
    if (["Shift", "Control", "Alt", "Meta", "CapsLock", "AltGraph"].includes(k)) return null;
    const MAP = { " ": "space", "Enter": "enter", "Tab": "tab", "Backspace": "backspace",
      "Delete": "delete", "Insert": "insert", "Home": "home", "End": "end",
      "PageUp": "pageup", "PageDown": "pagedown",
      "ArrowUp": "up", "ArrowDown": "down", "ArrowLeft": "left", "ArrowRight": "right" };
    if (MAP[k]) return MAP[k];
    if (/^F\d{1,2}$/.test(k)) return k.toLowerCase();   // F1..F12
    if (k.length === 1) return k.toLowerCase();          // 字母/数字/符号
    return null;
  }
  // 在编辑器【内部】弹一个小窗捕获一次按键（取代原来的独立 Tk 窗口：不再弹多个、也不用最小化编辑器）。
  // 返回 Promise<按键名 | null(取消)>。多次点击只保留一个窗口（已开就忽略后续调用）。
  function captureKey() {
    if (document.getElementById("keycapdlg")) return Promise.resolve(null);   // 已有捕获窗 → 不再叠开
    return new Promise((resolve) => {
      const box = document.createElement("div");
      box.id = "keycapdlg"; box.className = "popdlg";
      box.style.cssText = "position:absolute;left:50%;top:46px;transform:translateX(-50%);min-width:260px;text-align:center;" +
        "background:#23272f;color:#cfd3da;border:1px solid #3a404a;border-radius:8px;padding:18px 22px;z-index:240;" +
        "box-shadow:0 8px 30px #000a;font:13px/1.6 'Microsoft YaHei',sans-serif;";
      box.innerHTML = "<b style='color:#e6c07b;font-size:15px'>请按下要捕获的按键…</b>" +
        "<div style='color:#7f8895;margin-top:8px'>（Esc 取消；单独按修饰键无效）</div>";
      document.body.appendChild(box);
      const finish = (k) => { window.removeEventListener("keydown", onKey, true); box.remove(); resolve(k); };
      // 用 window 捕获阶段：先于 document 上的快捷键监听(Ctrl+S/Delete…)，并 stopImmediatePropagation 拦下，
      // 这样捕获“S”时不会同时触发保存等快捷键。
      const onKey = (e) => {
        e.preventDefault(); e.stopImmediatePropagation();
        const name = jsKeyToName(e);
        if (name === "__cancel__") return finish(null);
        if (name === null) return;   // 单独修饰键：忽略，继续等
        finish(name);
      };
      window.addEventListener("keydown", onKey, true);
    });
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
  const GROUP_EMPTY_W = 240, GROUP_EMPTY_H = 130;   // 空组（无成员）的默认框尺寸——保证仍可见、可拖动、可作为拖放落点
  const GROUP_NEST_PAD = 12, GROUP_TAB_H = 20;      // 嵌套时父框比子组框再外扩一圈（含子组标题标签高度），使父框真正“包住”子组、层次可见
  function unionRect(a, b) {                          // 两个 [x,y,w,h] 的并集
    if (!a) return b; if (!b) return a;
    const x0 = Math.min(a[0], b[0]), y0 = Math.min(a[1], b[1]);
    const x1 = Math.max(a[0] + a[2], b[0] + b[2]), y1 = Math.max(a[1] + a[3], b[1] + b[3]);
    return [x0, y0, x1 - x0, y1 - y0];
  }
  function groupColor(g, i) { return g.color || GROUP_COLORS[i % GROUP_COLORS.length]; }
  // 组描述的单行短版（在名称旁直接显示，超长截断；换行/多空白压成单空格）。
  function groupDescShort(g, max) {
    const d = String(g && g.desc || "").trim().replace(/\s+/g, " ");
    const m = max || 22;
    return d.length > m ? d.slice(0, m) + "…" : d;
  }
  // 标签页左侧文字：⠿ 拖动手柄 + 路径名 +（有描述则）📝 描述短版。
  function groupTabLeft(g) {
    const d = groupDescShort(g);
    return "⠿ " + groupPathTitle(g) + (d ? "  📝 " + d : "");
  }
  // 标签页：左端 ⠿=可拖动整组；右端 ⊟=单击折叠成子图（折叠态箱体标题右端则是 ⊞=单击展开）。⊟ 占位保证标签留出按钮宽度。
  function groupTabText(g) { return groupTabLeft(g) + "  ⊟"; }
  // 画「组名(主) + 描述(更小、斜体、更淡)」——让描述和名称一眼可区分。nameText 已含前缀(⠿/◳)。
  function drawGroupNameWithDesc(ctx, x, y, nameText, g, color, nameFont) {
    ctx.font = nameFont; ctx.fillStyle = color; ctx.textAlign = "left";
    ctx.fillText(nameText, x, y);
    const d = groupDescShort(g);
    if (!d) return;
    const nameW = ctx.measureText(nameText).width;
    const saveFont = ctx.font, saveAlpha = ctx.globalAlpha;
    ctx.font = "italic 11px 'Microsoft YaHei',sans-serif";   // 描述：斜体小字
    ctx.globalAlpha = saveAlpha * 0.66;                       // 描述：更淡
    ctx.fillText("📝 " + d, x + nameW + 8, y);
    ctx.font = saveFont; ctx.globalAlpha = saveAlpha;
  }
  const GROUP_ICON_W = 24;   // 标签/标题栏最右侧用于“折叠/展开”单击的小图标命中宽度（图坐标）
  // 依据背景色亮度选黑/白文字，保证标题在分组色上清晰可读。
  function contrastText(hex) {
    const m = /^#?([0-9a-f]{6})$/i.exec(String(hex || ""));
    if (!m) return "#ffffff";
    const n = parseInt(m[1], 16), r = (n >> 16) & 255, g = (n >> 8) & 255, b = n & 255;
    return (0.299 * r + 0.587 * g + 0.114 * b) > 150 ? "#15181d" : "#ffffff";
  }
  // 分组包裹框：裹住该组【子树全体成员】（直接成员 + 所有后代组成员）。visibleOnly 时跳过被折叠隐藏的成员。
  function groupBox(g, ctx, visibleOnly) {
    let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9, any = false;
    for (const mid of groupAllMembers(g)) {
      if (visibleOnly && foldHidden.has(mid)) continue;
      if (_altDrag && !_altDrag.keepIds.has(g.id) && _altDrag.detached.has(mid)) continue;   // Alt 拖拽中：被拖出的成员不计入源组/祖先框（父框不跟随，可拖出）
      const n = nodeByOurId(mid);
      if (!n) continue;
      any = true;
      const top = n.pos[1] - (LiteGraph.NODE_TITLE_HEIGHT || 30);
      const bottom = n.pos[1] + n.size[1] + ((n.flags && n.flags.collapsed) ? 0 : cardHeightOf(n, ctx));
      x0 = Math.min(x0, n.pos[0]); y0 = Math.min(y0, top);
      x1 = Math.max(x1, n.pos[0] + n.size[0]); y1 = Math.max(y1, bottom);
    }
    if (!any) {   // 没有（可见）成员：用存下的“锚点框”，让空组依旧可见/可拖/可作为落点（支持空组）；从未定位过则无框
      if (g.pos && g.size) return [g.pos[0], g.pos[1], g.size[0], g.size[1]];
      return null;
    }
    return [x0 - GROUP_PAD, y0 - GROUP_TOP, (x1 - x0) + 2 * GROUP_PAD, (y1 - y0) + GROUP_TOP + GROUP_PAD];
  }
  // 维护每个【子树有节点】组的锚点框(pos/size)＝其当前包围盒（含后代组成员，故纯容器组也记锚点）。
  // 成员/子组被拖空/删空后据此把空组留在原地（仍可见、可拖回/可删）；groupBox 在子树非空时按成员算，不读 pos/size，无自反馈。
  // 每帧（drawGroups）调一次；Alt 拖拽中框会临时收缩，跳过以免把收缩尺寸写进锚点。
  function syncGroupAnchors(ctx) {
    if (_altDrag) return;
    for (const g of groupDefs) {
      if (!groupAllMembers(g).length) continue;   // 真·空组（整棵子树无节点）：锚点保持不动（留作空组兜底框）
      const bb = groupBox(g, ctx, true);
      if (bb) { g.pos = [bb[0], bb[1]]; g.size = [bb[2], bb[3]]; }
    }
  }

  // ============ 可折叠子图（把一个分组折叠成一个紧凑“子图节点”）============
  // 纯编辑器视图层：折叠只隐藏成员节点的“显示与命中”，collect() 输出的底层扁平图不变（引擎照常跑）。
  // 折叠后，把“跨越分组边界”的连线汇成箱体左/右侧的输入/输出端口，并列出该组被钉选的参数，
  // 看起来像一个独立节点；双击箱体即展开。组内连线在折叠态隐藏。
  const SUBG = { TITLE_H: 28, PORT_GAP: 22, PORT_R: 4.5, PAD: 12, MINW: 200, PARAM_ROW_H: 30, PARAM_MINW: 268, EXEC: "#e6a23c", DATA: "#59b6c7" };

  // ===== 容器树模型：组≈特殊节点。每个组有稳定 id + 单一 parent；members 仅记【直接子节点】。 =====
  // 节点只属于一个组（某组的直接成员），组只有一个父组。子树全体成员 = 直接成员 ∪ 各子组子树成员（递归）。
  let _gidSeq = 1;
  function newGroupId() { let id; do { id = "g" + (_gidSeq++); } while (groupDefs.some((g) => g.id === id)); return id; }
  function groupById(id) { return id ? groupDefs.find((g) => g.id === id) : null; }
  function childGroupsOf(g) { return groupDefs.filter((x) => x.parent === g.id); }
  function groupAncestors(g) {            // 自下而上祖先组
    const out = []; let p = groupById(g.parent);
    while (p && !out.includes(p)) { out.push(p); p = groupById(p.parent); }
    return out;
  }
  function isDescendantGroup(a, b) { return groupAncestors(a).includes(b); }   // a 在 b 的子树内
  function groupSubtreeIds(g) {   // g 及其所有后代组的 id 集合（Alt 拖组时，这些组的框保留成员=随组一起移动，不被“摘出”）
    const ids = new Set([g.id]);
    for (const x of groupDefs) if (isDescendantGroup(x, g)) ids.add(x.id);
    return ids;
  }
  function groupDepth(g) { return groupAncestors(g).length; }                  // 嵌套深度（顶层=0）
  function groupPathTitle(g) {        // 显示用「路径名」：祖先→自身的标题以 / 连接（如 A/B），直观看出嵌套层级
    const chain = groupAncestors(g).reverse(); chain.push(g);
    return chain.map((x) => x.title || "分组").join("/");
  }
  function groupAllMembers(g, _seen, _vg) {   // 子树全体节点 ourId（去重）
    const set = _seen || new Set(), vg = _vg || new Set();
    if (vg.has(g.id)) return _seen ? set : [...set];   // 防环（万一文件被手改出父子环）
    vg.add(g.id);
    for (const m of (g.members || [])) set.add(m);
    for (const c of childGroupsOf(g)) groupAllMembers(c, set, vg);
    return _seen ? set : [...set];
  }
  function collapsedGroupList() {         // [{g, i}]：collapsed 且子树有成员的组
    const out = [];
    groupDefs.forEach((g, i) => { if (g.collapsed) out.push({ g, i }); });   // 含空组：折叠的空组也画成一个箱体
    return out;
  }
  function topCollapsedGroups() {         // collapsed 且无 collapsed 祖先 → 画成可见箱体
    return collapsedGroupList().filter(({ g }) => !groupAncestors(g).some((a) => a.collapsed));
  }
  function isInsideCollapsed(g) { return groupAncestors(g).some((a) => a.collapsed); }   // 有 collapsed 祖先
  // 读入分组数组并规范成容器树：补 id；旧格式（members=全量、靠子集嵌套、无 parent）→ 推断 parent 并把 members 收为直接成员。
  function normalizeGroups(raw) {
    const gs = (raw || []).map((g) => ({
      id: g.id || null, title: g.title || "分组", color: g.color || "",
      collapsed: !!g.collapsed, parent: (g.parent !== undefined ? (g.parent || null) : undefined),
      members: (g.members || []).slice(),
      pos: Array.isArray(g.pos) ? g.pos.slice() : null, size: Array.isArray(g.size) ? g.size.slice() : null,   // 空组兜底定位（支持空组）
      desc: g.desc || "",
    }));
    const used = new Set(gs.filter((g) => g.id).map((g) => g.id));
    let seq = 1;
    for (const g of gs) if (!g.id) { let id; do { id = "g" + (seq++); } while (used.has(id)); g.id = id; used.add(id); }
    if (gs.some((g) => g.parent !== undefined)) {          // 新格式：信任 parent
      for (const g of gs) if (g.parent === undefined) g.parent = null;
      return gs;
    }
    // 旧格式：members 为子树全量、靠子集嵌套 → 推断 parent + 收为直接成员
    const orig = new Map(gs.map((g) => [g.id, g.members.slice()]));
    for (const g of gs) {                                  // parent = 严格包含自己的【最小】组
      let best = null, bestN = Infinity;
      for (const s of gs) {
        if (s === g) continue;
        const sm = orig.get(s.id), gm = orig.get(g.id);
        if (sm.length > gm.length && gm.every((m) => sm.includes(m)) && sm.length < bestN) { best = s; bestN = sm.length; }
      }
      g.parent = best ? best.id : null;
    }
    for (const g of gs) {                                  // members → 直接成员（减去各直接子组的子树成员）
      const childMembers = new Set();
      for (const c of gs) if (c.parent === g.id) for (const m of orig.get(c.id)) childMembers.add(m);
      g.members = orig.get(g.id).filter((m) => !childMembers.has(m));
    }
    return gs;
  }

  // 把若干节点设为某组的【直接成员】（单一归属：先从所有组移除，再加入目标；gi=null→移出所有组）。
  function setNodesDirectGroup(ourIds, gi) {
    const rm = new Set(ourIds);
    for (const g of groupDefs) g.members = (g.members || []).filter((m) => !rm.has(m));
    if (gi != null && groupDefs[gi]) {
      const g = groupDefs[gi]; g.members = g.members || [];
      for (const id of ourIds) if (!g.members.includes(id)) g.members.push(id);
    }
    refreshGroups();   // 不再自动删空组：支持空组（拖空/删空仍保留，可拖节点回去；删组走解散/删除菜单）
  }
  // 设某组的父组（parentId=null→顶层）。防环：目标不能是自己或自己的后代。
  function setGroupParent(g, parentId) {
    if (!g) return;
    if (parentId) { const p = groupById(parentId); if (!p || p === g || isDescendantGroup(p, g)) return; }
    g.parent = parentId || null;
    refreshGroups();   // 支持空组：不再因旧父组变空而删它
  }
  // 解散某组：子组与直接成员上提到它的父组（无父则变顶层/无组），再删除它本身。
  function dissolveGroup(g) {
    if (!g) return;
    const pid = g.parent || null, p = groupById(pid);
    for (const c of childGroupsOf(g)) c.parent = pid;          // 子组改挂到祖父
    if (p) { p.members = p.members || []; for (const m of (g.members || [])) if (!p.members.includes(m)) p.members.push(m); }
    const k = groupDefs.indexOf(g); if (k >= 0) groupDefs.splice(k, 1);
    refreshGroups();
  }
  // 新建一个【空组】(支持空组)：定位在 pos（图坐标；缺省=当前视口中心），默认尺寸 GROUP_EMPTY_W/H。返回新组。
  function createGroupAt(pos) {
    let x, y;
    if (pos && pos.length === 2) { x = pos[0]; y = pos[1]; }
    else if (canvas) {
      const s = canvas.ds.scale || 1, o = canvas.ds.offset;
      x = (canvas.canvas.width / 2) / s - o[0] - GROUP_EMPTY_W / 2;
      y = (canvas.canvas.height / 2) / s - o[1] - GROUP_EMPTY_H / 2;
    } else { x = 80; y = 80; }
    const g = { id: newGroupId(), title: "分组" + (groupDefs.length + 1), color: GROUP_COLORS[groupDefs.length % GROUP_COLORS.length], parent: null, members: [], pos: [x, y], size: [GROUP_EMPTY_W, GROUP_EMPTY_H] };
    groupDefs.push(g);
    refreshGroups();
    if (typeof selectGroup === "function") selectGroup(g.id);   // 顺手选中，便于改名/设置（selectGroup 见“组选中”一节）
    setStatus("已新建空组「" + g.title + "」——拖节点进框即归入（按住 Alt 拖动可放进/移出其它组）");
    return g;
  }
  // 展开态分组的可见包裹框：裹住【可见成员】并【把每个直接子组的画框（含其标题标签）整体并进来】，
  // 再向外扩一圈 GROUP_NEST_PAD——这样父框真正“包住”子组（子组≈一个节点），层次清晰；
  // 也修复“父组只剩子组、没有直接成员时父框与子组框重合而看不见”的问题。递归处理多层嵌套。
  function expandedGroupBox(g, ctx, _vg) {
    const vg = _vg || new Set(); if (vg.has(g.id)) return groupBox(g, ctx, true); vg.add(g.id);
    let bb = groupBox(g, ctx, true);   // 直接可见成员（已含成员留白）
    let hasChild = false;
    for (const c of childGroupsOf(g)) {
      // Alt 拖子组：被拖出的子组（在 keepIds 里、随组移动）不并入【非该子树】的父框——这样父框不跟随被拖走的子组，
      // 子组才拖得出去（与节点 Alt 拖出一致：groupBox 已对被拖成员做同样排除）。子树内部计算照常并入（整组一起动）。
      if (_altDrag && _altDrag.keepIds && _altDrag.keepIds.has(c.id) && !_altDrag.keepIds.has(g.id)) continue;
      let cb = null;
      if (c.collapsed) { const sb = subgBox(c, ctx); cb = sb && [sb.x, sb.y, sb.w, sb.h]; }
      else cb = expandedGroupBox(c, ctx, vg);
      if (!cb) continue;
      hasChild = true;
      bb = unionRect(bb, [cb[0], cb[1] - GROUP_TAB_H, cb[2], cb[3] + GROUP_TAB_H]);   // 含子组标题标签
    }
    if (!bb) return null;
    if (hasChild) bb = [bb[0] - GROUP_NEST_PAD, bb[1] - GROUP_NEST_PAD, bb[2] + 2 * GROUP_NEST_PAD, bb[3] + 2 * GROUP_NEST_PAD];
    return bb;
  }
  // 命中：包含坐标的【最内层】分组下标（拖组放进它用）；排除 exclude 组及其后代。无则 -1。
  function innermostGroupAt(gx, gy, exclude) {
    const ctx = _measCtx(); if (!ctx) return -1;
    let best = -1, bestN = Infinity;
    groupDefs.forEach((g, i) => {
      if (g === exclude || (exclude && isDescendantGroup(g, exclude))) return;
      let box;
      if (g.collapsed) { const b = subgBox(g, ctx); box = b && [b.x, b.y, b.w, b.h]; }
      else box = expandedGroupBox(g, ctx);
      if (!box) return;
      const n = groupAllMembers(g).length;
      if (gx >= box[0] && gx <= box[0] + box[2] && gy >= box[1] && gy <= box[1] + box[3] && n < bestN) { best = i; bestN = n; }
    });
    return best;
  }
  // 节点拖放的落点组：包含节点中心的【最内层、且展开可见】分组（折叠/被折叠隐藏的组不作落点）。
  function nodeDropGroupIndex(node) {
    const ctx = _measCtx(); if (!ctx || !node) return -1;
    const cx = node.pos[0] + node.size[0] / 2, cy = node.pos[1] + (node.size[1] || 0) / 2;
    let best = -1, bestN = Infinity;
    groupDefs.forEach((g, i) => {
      if (g.collapsed || isInsideCollapsed(g)) return;
      const box = expandedGroupBox(g, ctx); if (!box) return;
      const n = groupAllMembers(g).length;
      if (cx >= box[0] && cx <= box[0] + box[2] && cy >= box[1] && cy <= box[1] + box[3] && n < bestN) { best = i; bestN = n; }
    });
    return best;
  }
  function subgPortLabel(node, slot, isInput) {
    const title = (node.title || "").trim();
    const sn = slot.label || slot.name || "";
    if (slot.type === "exec") {
      // exec 端口：成员节点名最达意；槽位若具名（真/假/分支…）再补上，避免多个 exec 口同名
      const tag = (sn && sn !== "in" && sn !== "out") ? "·" + sn : "";
      return (title || (isInput ? "入口" : "出口")) + tag;
    }
    // 数据端口：用“节点名·槽位”——很多节点的输出都叫“数值/结果/数量”，只写槽位名根本看不出来自哪个内部节点
    const slotName = sn || (isInput ? "入" : "出");
    return title ? title + "·" + slotName : slotName;
  }
  // 某折叠组的边界端口：外部→组内=输入端口；组内→外部=输出端口。按 (成员,槽位) 去重，每个唯一槽位一个端口。
  // 注意：link.origin_id/target_id 是 LiteGraph 的【数字 id】，分组成员存的是【ourId(_id)】，必须经 getNodeById 转换后再比对/拼 key。
  function subgPorts(g) {
    const members = new Set(groupAllMembers(g));
    const ins = [], outs = [], seenI = new Set(), seenO = new Set();
    for (const k in (graph.links || {})) {
      const l = graph.links[k]; if (!l) continue;
      const a = graph.getNodeById(l.origin_id), b = graph.getNodeById(l.target_id);
      if (!a || !b) continue;
      const oIn = members.has(a._id), tIn = members.has(b._id);
      if (oIn === tIn) continue;   // 都在组内（内部线）或都在组外（无关）→ 非边界
      if (tIn) {
        if (!b.inputs || !b.inputs[l.target_slot]) continue;
        const key = b._id + "#i#" + l.target_slot; if (seenI.has(key)) continue; seenI.add(key);
        const slot = b.inputs[l.target_slot];
        ins.push({ key, label: subgPortLabel(b, slot, true), type: slot.type, _y: b.pos[1] });
      } else {
        if (!a.outputs || !a.outputs[l.origin_slot]) continue;
        const key = a._id + "#o#" + l.origin_slot; if (seenO.has(key)) continue; seenO.add(key);
        const slot = a.outputs[l.origin_slot];
        outs.push({ key, label: subgPortLabel(a, slot, false), type: slot.type, _y: a.pos[1] });
      }
    }
    ins.sort((p, q) => p._y - q._y); outs.sort((p, q) => p._y - q._y);   // 端口按成员节点上下顺序排，贴合原布局
    return { ins, outs };
  }
  // 组的【接口参数】＝出现在该组折叠箱体里的可编辑参数（逐级暴露、不冒泡）：
  //  · 直接成员节点中，被该节点暴露给本组的参数（isFoldPinned）；
  //  · 直接子组的接口参数中，被该子组【再向上暴露给本组】的（isGroupExposed(子组, nid, key)）。
  // 即一个深层参数只有沿途每层组都勾了“向上暴露”才会浮现到上层——和函数封装一致。
  function interfaceParams(g, _vg) {
    const vg = _vg || new Set(); if (!g || vg.has(g.id)) return []; vg.add(g.id);
    const out = [], seen = new Set();
    for (const nid of (g.members || [])) {
      const node = nodeByOurId(nid); if (!node) continue;
      const d = defByType[node._typeId]; if (!d) continue;
      for (const p of (d.params || [])) {
        if (!isFoldPinned(nid, p.key)) continue;
        const sk = nid + "|" + p.key; if (seen.has(sk)) continue; seen.add(sk);
        const w = (node.widgets || []).find((x) => x._key === p.key); if (!w) continue;
        // 折叠箱体里的显示名与控制面板【同源】：自定义名优先，否则用同一个“节点标题 · 参数标签”默认。
        out.push({ nid, key: p.key, node, label: customLabel(nid, p.key) || defaultPinLabel(node, p.key) });
      }
    }
    for (const c of childGroupsOf(g)) {
      for (const it of interfaceParams(c, vg)) {
        if (!isGroupExposed(c.id, it.nid, it.key)) continue;   // 仅子组选择向上暴露给本组的
        const sk = it.nid + "|" + it.key; if (seen.has(sk)) continue; seen.add(sk);
        out.push(it);
      }
    }
    return out;
  }
  // 折叠箱体里要显示的可编辑参数 = 该组的接口参数。
  function subgFoldParams(g) { return interfaceParams(g); }
  // 折叠箱体几何：锚定在成员包围盒左上角（成员仍保留 pos），尺寸按端口数 / 标题 / 折叠参数行自适应。
  // 折叠箱体标题文字：◳ 路径名 +（有描述则）📝 描述短版（与展开态标签的显示一致）。
  function subgTitleText(g) {
    const d = groupDescShort(g);
    return "◳ " + groupPathTitle(g) + (d ? "  📝 " + d : "");
  }
  function subgBox(g, ctx) {
    const bb = groupBox(g, ctx); if (!bb) return null;
    const ports = subgPorts(g), fparams = subgFoldParams(g);
    const rows = Math.max(ports.ins.length, ports.outs.length, 1);
    ctx.font = "bold 13px 'Microsoft YaHei',sans-serif";
    const titleW = ctx.measureText(subgTitleText(g)).width + GROUP_ICON_W + 14;   // 路径名(+描述) + 右端展开按钮留位
    ctx.font = "12px 'Microsoft YaHei',sans-serif";
    let li = 0, lo = 0;
    for (const p of ports.ins) li = Math.max(li, ctx.measureText(p.label).width);
    for (const p of ports.outs) lo = Math.max(lo, ctx.measureText(p.label).width);
    // 宽度：取“标题宽 / 左右端口标签不重叠 / 折叠参数最小宽”的最大值。左右标签各内缩 9px，中间再留 22px 空隙（共 40）。
    const w = Math.max(SUBG.MINW, titleW + 16, li + lo + 40, fparams.length ? SUBG.PARAM_MINW : 0);
    // paramsTop = 端口区下沿（折叠参数从这里起）。箱体高度、分隔线、DOM 参数浮层三处共用，确保对齐一致。
    const paramsTop = SUBG.TITLE_H + rows * SUBG.PORT_GAP + SUBG.PAD;
    const h = paramsTop + (fparams.length ? fparams.length * SUBG.PARAM_ROW_H + 8 : 0);
    return { x: bb[0], y: bb[1], w, h, ports, fparams, paramsTop };
  }
  function subgPortPos(box, side, idx) {
    const y = box.y + SUBG.TITLE_H + SUBG.PORT_GAP * (idx + 0.5);
    return side === "in" ? [box.x, y] : [box.x + box.w, y];
  }
  // 一次性算出本帧折叠所需：每个组的箱体 + 边界端口 key→屏幕坐标（供连线改接）。图很小，按需重算即可。
  function foldInfo() {
    const ctx = _measCtx(), boxes = [], portPos = new Map(), memberGroup = new Map();
    if (!ctx) return { boxes, portPos, memberGroup };
    for (const { g, i } of topCollapsedGroups()) {
      const box = subgBox(g, ctx); if (!box) continue;
      box.ports.ins.forEach((p, idx) => portPos.set(p.key, subgPortPos(box, "in", idx)));
      box.ports.outs.forEach((p, idx) => portPos.set(p.key, subgPortPos(box, "out", idx)));
      for (const m of groupAllMembers(g)) if (!memberGroup.has(m)) memberGroup.set(m, i);   // ourId → 所属【顶层】折叠箱体下标（嵌套时取最外层）
      boxes.push({ g, i, box });
    }
    return { boxes, portPos, memberGroup };
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
        const oH = foldHidden.has(start._id), tH = foldHidden.has(node._id);   // 用 ourId 判断隐藏，非数字 id
        // 两端都隐藏且属于【同一折叠组】→ 组内部线，折叠态不画；分属【不同折叠组】（组与组之间的线）则
        // 两端各改接到各自箱体端口，照常画出（修复：之前只要两端都隐藏就跳过，导致折叠组之间的连线消失）。
        if (oH && tH && fi.memberGroup.get(start._id) === fi.memberGroup.get(node._id)) continue;
        let a = oH ? fi.portPos.get(start._id + "#o#" + link.origin_slot)
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
  // 折叠/展开小按钮：在标签/标题右端画一个明显的圆角按钮（半透明白底+白边+图标），一眼看出可点。
  function drawFoldChip(ctx, rect, glyph) {
    const s = Math.min(rect[3] - 5, 17);
    const cx = rect[0] + rect[2] / 2, cy = rect[1] + rect[3] / 2;
    ctx.save();
    roundRect(ctx, cx - s / 2, cy - s / 2, s, s, 4);
    ctx.fillStyle = "rgba(255,255,255,0.22)"; ctx.fill();
    ctx.strokeStyle = "rgba(255,255,255,0.9)"; ctx.lineWidth = 1.3; ctx.stroke();
    ctx.fillStyle = "#ffffff"; ctx.font = "bold " + (s - 2) + "px 'Microsoft YaHei',sans-serif";
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.fillText(glyph, cx, cy + 0.5);
    ctx.restore();
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
    ctx.fillStyle = tcol; ctx.textBaseline = "middle"; ctx.textAlign = "left";
    drawGroupNameWithDesc(ctx, box.x + 10, box.y + SUBG.TITLE_H / 2, "◳ " + groupPathTitle(g), g, tcol, "bold 13px 'Microsoft YaHei',sans-serif");   // 名(主)+描述(小淡斜体)，宽度按 subgTitleText 量过
    drawFoldChip(ctx, [box.x + box.w - GROUP_ICON_W, box.y, GROUP_ICON_W, SUBG.TITLE_H], "⊞");  // 右端：单击展开按钮
    // 端口：exec=三角、data=圆点；标签在内侧
    const drawPort = (p, pos, side) => {
      const c = p.type === "exec" ? SUBG.EXEC : SUBG.DATA;
      ctx.fillStyle = c; ctx.strokeStyle = "#11141a"; ctx.lineWidth = 1;
      ctx.beginPath();
      if (p.type === "exec") {
        const d = SUBG.PORT_R + 1;   // 输入/输出三角都【朝右】（与执行流向一致），不再按左右边反向
        ctx.moveTo(pos[0] - d, pos[1] - d); ctx.lineTo(pos[0] + d, pos[1]); ctx.lineTo(pos[0] - d, pos[1] + d); ctx.closePath();
      } else { ctx.arc(pos[0], pos[1], SUBG.PORT_R, 0, Math.PI * 2); }
      ctx.fill(); ctx.stroke();
      ctx.fillStyle = "#c7ccd6"; ctx.font = "12px 'Microsoft YaHei',sans-serif"; ctx.textBaseline = "middle";
      if (side === "in") { ctx.textAlign = "left"; ctx.fillText(p.label, pos[0] + 9, pos[1]); }
      else { ctx.textAlign = "right"; ctx.fillText(p.label, pos[0] - 9, pos[1]); }
    };
    box.ports.ins.forEach((p, idx) => drawPort(p, subgPortPos(box, "in", idx), "in"));
    box.ports.outs.forEach((p, idx) => drawPort(p, subgPortPos(box, "out", idx), "out"));
    // 折叠参数区：实际的可编辑控件由 DOM 浮层（rebuildFoldWidgets）叠加在此区域；canvas 这里只画一条分隔线。
    if (box.fparams && box.fparams.length) {
      const sy = box.y + box.paramsTop - 5;   // 端口区与折叠参数区之间的分隔线（与 subgBox.paramsTop 对齐）
      ctx.strokeStyle = col + "55"; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(box.x + 8, sy); ctx.lineTo(box.x + box.w - 8, sy); ctx.stroke();
    }
    // 运行时若组内有节点正在本帧路径上，箱体描金光提示“此处有活动”
    if (runSession) {
      let active = false;
      for (const m of groupAllMembers(g)) { if (runPath.has(m) || runDataNodes.has(m)) { active = true; break; } }
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
    for (const { g } of topCollapsedGroups()) {
      let sumSelf = 0, maxCum = 0, any = false;
      for (const m of groupAllMembers(g)) {
        const tm = runTimes[m]; if (!tm) continue;
        any = true; sumSelf += tm[0]; if (tm[1] > maxCum) maxCum = tm[1];
      }
      if (!any) continue;
      const box = subgBox(g, mctx); if (!box) continue;
      drawTimePill(ctx, box.x, box.x + box.w, box.y - 3, sumSelf, maxCum);
    }
  }
  // ============ 折叠节点里的“可编辑参数”DOM 浮层 ============
  // 勾了“显示到折叠节点”的参数，用和节点上一样的【真·DOM 控件】呈现：放进 #foldwidgets 容器，
  // 按【图坐标】绝对定位，容器整体 transform 跟随画布平移/缩放（screen=(graph+offset)*scale，与 LiteGraph 一致），
  // 故缩放时控件也跟着缩放、始终贴在箱体内。
  let _foldWidgetSig = "";
  function foldWidgetSig() {
    const ctx = _measCtx(); if (!ctx) return "";
    const parts = [];
    for (const { g } of topCollapsedGroups()) {
      const box = subgBox(g, ctx); if (!box || !box.fparams.length) continue;
      parts.push(Math.round(box.x) + "," + Math.round(box.y) + "," + Math.round(box.w) + ":" +
                 box.fparams.map((f) => f.nid + "|" + f.key).join(","));
    }
    return parts.join(";");
  }
  function applyFoldWidgetTransform() {
    const host = document.getElementById("foldwidgets"); if (!host || !canvas) return;
    const s = canvas.ds.scale, o = canvas.ds.offset;
    host.style.transform = "translate(" + (o[0] * s) + "px," + (o[1] * s) + "px) scale(" + s + ")";
    host.style.display = host.childElementCount ? "block" : "none";
  }
  function rebuildFoldWidgets() {
    const host = document.getElementById("foldwidgets"); if (!host || !canvas) return;
    host.innerHTML = "";
    const ctx = _measCtx();
    if (ctx) for (const { g } of topCollapsedGroups()) {
      const box = subgBox(g, ctx); if (!box || !box.fparams.length) continue;
      const y0 = box.y + box.paramsTop;   // 与 subgBox.paramsTop / 分隔线保持一致
      box.fparams.forEach((f, k) => {
        const row = document.createElement("div");
        row.className = "fw-row";
        row.dataset.fwk = f.nid + "|" + f.key;   // 供 refreshFoldDots 找到对应 widget 判断“已修改”
        row.style.left = (box.x + SUBG.PAD) + "px";
        row.style.top = (y0 + k * SUBG.PARAM_ROW_H) + "px";
        row.style.width = (box.w - 2 * SUBG.PAD) + "px";
        const dot = document.createElement("span");   // 已修改（未保存）橙点——与节点上的标记对齐
        dot.className = "fw-dot";
        row.appendChild(dot);
        const lab = document.createElement("span");
        lab.className = "fw-label"; lab.textContent = f.label; lab.title = f.label;
        row.appendChild(lab);
        const ctrl = buildParamControl(f.node, f.key);
        if (ctrl) row.appendChild(ctrl);
        host.appendChild(row);
      });
    }
    _foldWidgetSig = foldWidgetSig();
    applyFoldWidgetTransform();
  }
  function syncFoldWidgets() {        // 每帧（onDrawBackground）调：几何/内容变了才重建，否则只更新 transform
    const host = document.getElementById("foldwidgets"); if (!host) return;
    if (foldWidgetSig() !== _foldWidgetSig) rebuildFoldWidgets();
    applyFoldWidgetTransform();
    refreshFoldDots();              // 折叠箱体里参数的“已修改”橙点（与节点上的橙点对齐）
  }
  function refreshFoldDots() {
    const host = document.getElementById("foldwidgets"); if (!host) return;
    host.querySelectorAll(".fw-row").forEach((row) => {
      const dot = row.querySelector(".fw-dot"); if (!dot) return;
      const s = row.dataset.fwk || "", ix = s.indexOf("|");
      const node = ix > 0 ? nodeByOurId(s.slice(0, ix)) : null;
      const w = node && (node.widgets || []).find((x) => x._key === s.slice(ix + 1));
      dot.style.display = (w && paramChanged(w)) ? "block" : "none";
    });
  }
  function syncFoldWidgetValues() {   // 值在别处被改（撤销/控制面板/采集）时，刷新折叠控件显示值
    const host = document.getElementById("foldwidgets"); if (!host) return;
    host.querySelectorAll("[data-fwk]").forEach((ctrl) => {
      const s = ctrl.dataset.fwk, ix = s.indexOf("|");
      const node = nodeByOurId(s.slice(0, ix)); if (!node) return;
      const w = (node.widgets || []).find((x) => x._key === s.slice(ix + 1)); if (!w) return;
      if (ctrl.type === "checkbox") ctrl.checked = !!w.value;
      else if ("value" in ctrl) ctrl.value = w.value == null ? "" : String(w.value);
    });
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
        const oH = foldHidden.has(a._id), tH = foldHidden.has(b._id);   // 用 ourId 判断隐藏
        if (oH && tH && fi && fi.memberGroup.get(a._id) === fi.memberGroup.get(b._id)) return;   // 同组内部汇入线：不画；跨折叠组则改接箱体端口照画
        let pa, pb;
        try {
          pa = oH ? fi.portPos.get(a._id + "#o#" + l.origin_slot) : a.getConnectionPos(false, l.origin_slot, [0, 0]);
          pb = tH ? fi.portPos.get(b._id + "#i#" + l.target_slot) : b.getConnectionPos(true, l.target_slot, [0, 0]);
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
  // Alt 拖拽时，给「将落入的目标组」描一圈金色光晕（松手前的落点提示）。
  function drawDropTargetHL(ctx, x, y, w, h) {
    ctx.save();
    roundRect(ctx, x - 3, y - 3, w + 6, h + 6, 11);
    ctx.strokeStyle = "#ffd23f"; ctx.lineWidth = 3;
    ctx.shadowColor = "#ffb300"; ctx.shadowBlur = 14;
    ctx.stroke();
    ctx.restore();
  }
  // 选中的组：描一圈青色虚线（区别于金色落点高亮），表示“此组已选中”（右下角显示其详情）。
  function drawGroupSelHL(ctx, x, y, w, h) {
    ctx.save();
    roundRect(ctx, x - 2, y - 2, w + 4, h + 4, 11);
    ctx.strokeStyle = "#8ad8ff"; ctx.lineWidth = 2; ctx.setLineDash([6, 4]);
    ctx.stroke();
    ctx.restore();
  }
  function drawGroups(ctx) {
    drawExtraExecLinks(ctx);     // 先补画 LiteGraph 漏掉的“汇入”执行连线（与分组无关，总要画）
    syncFoldWidgets();           // 折叠节点的可编辑参数 DOM 浮层：跟随平移/缩放，几何变则重建
    if (!groupDefs.length) return;
    ctx.save();
    syncGroupAnchors(ctx);   // 刷新各组锚点框，供空组兜底定位（必须在用 groupBox 取框之前）
    // 嵌套渲染顺序：按层级——外层（depth 小）先画、内层后画压在上面，保证嵌套子组/子箱体可见。
    const order = groupDefs.map((g, i) => i).sort((a, b) => groupDepth(groupDefs[a]) - groupDepth(groupDefs[b]));
    for (const i of order) {
      const g = groupDefs[i];
      if (g.collapsed) {         // 折叠态：画紧凑“子图节点”箱体（仅顶层折叠组；嵌套在折叠父组里的子组不单独画）
        if (isInsideCollapsed(g)) continue;
        const sbox = subgBox(g, ctx);
        if (sbox) {
          drawSubgBox(ctx, g, i, sbox);
          if (selectedGroupId === g.id) drawGroupSelHL(ctx, sbox.x, sbox.y, sbox.w, sbox.h);
          if (_altDrag && _altDrag.targetGi === i) drawDropTargetHL(ctx, sbox.x, sbox.y, sbox.w, sbox.h);
        }
        continue;
      }
      // 展开态：成员若已全部被折叠父组隐藏，则不画其包裹框（否则框会落在父箱体之上）。空组(无成员)不算“全隐藏”，照画。
      const _am = groupAllMembers(g);
      if (_am.length && _am.every((m) => foldHidden.has(m))) continue;
      const box = expandedGroupBox(g, ctx);  // 裹住可见成员 + 折叠子组箱体（拖折叠子组时父框跟随，不再浮出）
      if (!box) continue;
      const [x, y, w, h] = box, col = groupColor(g, i), name = groupTabText(g);
      roundRect(ctx, x, y, w, h, 10);
      ctx.fillStyle = col + "22"; ctx.fill();          // 半透明填充
      ctx.strokeStyle = col + "cc"; ctx.lineWidth = 2; ctx.stroke();
      if (selectedGroupId === g.id) drawGroupSelHL(ctx, x, y, w, h);   // 选中态高亮
      if (_altDrag && _altDrag.targetGi === i) drawDropTargetHL(ctx, x, y, w, h);   // Alt 拖拽落点提示

      // 组名做成“标签页”贴在框的左上沿之上——不会和框内第一个节点重叠
      ctx.font = "bold 13px 'Microsoft YaHei',sans-serif";
      ctx.textBaseline = "alphabetic";
      const tw = ctx.measureText(name).width;   // name 含占位的 ⊟，使标签留出按钮宽度
      roundRect(ctx, x, y - 18, tw + 18, 20, 5);
      ctx.fillStyle = col; ctx.fill();
      ctx.fillStyle = contrastText(col); ctx.textAlign = "left";   // 显式置左：折叠箱体端口标签会把 textAlign 设成 right 且不复位，否则本组名被右对齐而整体左移错位
      drawGroupNameWithDesc(ctx, x + 9, y - 4, "⠿ " + groupPathTitle(g), g, contrastText(col), "bold 13px 'Microsoft YaHei',sans-serif");   // 组名(主)+描述(小淡斜体)；⊟ 占位见 groupTabText
      const ir = groupIconRect(g); if (ir) drawFoldChip(ctx, ir, "⊟");   // 右端：单击折叠按钮
    }
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

  // 节点只属于一个组（某组的【直接成员】）。返回其直接所属分组下标（即最内层），无则 -1。
  function nodeGroupIndex(ourId) {
    return groupDefs.findIndex((g) => (g.members || []).includes(ourId));
  }
  function groupColorOf(ourId) {
    const i = nodeGroupIndex(ourId);
    return i >= 0 ? groupColor(groupDefs[i], i) : null;
  }
  // 分组变化后：把每个节点的标题栏染成所属分组色 + 重绘 + 计脏。
  function refreshFold() {   // 重算“被折叠隐藏的成员”集合（折叠组的子树全体）；任何改动分组的地方都应调用
    foldHidden = new Set();
    for (const g of groupDefs) if (g.collapsed) for (const m of groupAllMembers(g)) foldHidden.add(m);
  }
  function refreshGroups() {
    if (groupExpose.length) groupExpose = groupExpose.filter((e) => groupDefs.some((g) => g.id === e[0]));   // 组被解散/删除后清掉它的“向上暴露”项
    refreshFold();
    applyGroupColors();
    if (canvas) canvas.setDirty(true, true);
    if (groupDlgRender && document.getElementById("grpdlg")) groupDlgRender();   // 分组管理窗口开着则同步刷新
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
      // 容器树模型：一个节点只属于一个分组（直接成员）。这里用单选指定节点归属；嵌套请把一个组拖进另一个组。
      const curIdx = ids.map(nodeGroupIndex);
      const same = curIdx.every((x) => x === curIdx[0]) ? curIdx[0] : -2;   // 多选不一致 → 不预选
      const hasSel = ids.length > 0;
      let h = `<b style='color:#e6c07b'>分组管理</b>` + (hasSel ? `（已选 ${ids.length} 个节点）` : "") +
              "<div style='color:#7f8895;margin:2px 0 8px'>" +
              (hasSel ? "选一个组＝把选中节点放进去（一个节点只属于一个组）；" : "缩进表示嵌套层级；") +
              "嵌套可把一个组拖进另一个组，或用每行「归入」。</div>";
      if (hasSel) h += `<label style="display:block;cursor:pointer;padding:4px 0"><input type="radio" name="grp" value="-1" ${same === -1 ? "checked" : ""}> 不分组</label>`;
      // 按容器树 DFS 排序、按层级缩进，直观显示嵌套关系
      const ordered = [];
      const visit = (pid, depth) => { groupDefs.forEach((g, i) => { if ((g.parent || null) !== (pid || null)) return; ordered.push({ g, i, depth }); visit(g.id, depth + 1); }); };
      visit(null, 0);
      if (!ordered.length) h += "<div style='color:#6b727d;padding:4px 0'>（还没有分组——下面「新建空组」或选中节点后新建）</div>";
      ordered.forEach(({ g, i, depth }) => {
        const col = groupColor(g, i), nc = childGroupsOf(g).length;
        h += `<label style="display:flex;align-items:center;gap:6px;cursor:pointer;padding:4px 0;margin-left:${depth * 16}px">` +
             (depth ? `<span style="color:#5b626d;flex:none">└</span>` : "") +
             (hasSel ? `<input type="radio" name="grp" value="${i}" ${same === i ? "checked" : ""}>` : "") +
             `<span data-swatch="${i}" style="width:12px;height:12px;border-radius:3px;background:${col};flex:none"></span>` +
             `<span style="flex:1">${esc(g.title || "分组")}${g.desc ? " <span style='color:#7f8895' title='" + esc(g.desc) + "'>📝 " + esc(groupDescShort(g, 16)) + "</span>" : ""}</span>` +
             `<span style="color:#7f8895">${(g.members || []).length}个${nc ? "+" + nc + "组" : ""}</span>` +
             `<input type="color" data-color="${i}" value="${col}" title="自定义颜色" style="width:24px;height:20px;padding:0;border:1px solid #444;background:#15171c;cursor:pointer">` +
             `<button data-fold="${i}" title="折叠成一个紧凑节点 / 展开还原" style="background:${g.collapsed ? "#314a6b" : "#2f343d"};color:${g.collapsed ? "#cfe3ff" : "#cfd3da"};border:1px solid #444;border-radius:4px;padding:1px 7px;cursor:pointer">${g.collapsed ? "展开" : "折叠"}</button>` +
             `<button data-reparent="${i}" title="归入另一个组 / 移到顶层" style="background:#2f343d;color:#cfd3da;border:1px solid #444;border-radius:4px;padding:1px 7px;cursor:pointer">归入</button>` +
             `<button data-ren="${i}" style="background:#2f343d;color:#cfd3da;border:1px solid #444;border-radius:4px;padding:1px 7px;cursor:pointer">改名</button>` +
             `<button data-del="${i}" title="解散此组（子组与成员上提到父组）" style="background:#3a2222;color:#ffb3b3;border:1px solid #a33;border-radius:4px;padding:1px 7px;cursor:pointer">解散</button></label>`;
      });
      h += "<div style='margin-top:10px'>" +
        `<button id='grp_new' style='background:#2f343d;color:#cfd3da;border:1px solid #444;border-radius:4px;padding:3px 10px;cursor:pointer'>＋ ${hasSel ? "新建分组（含选中节点）" : "新建空组"}</button>` +
        "<span style='float:right;color:#6b727d;font-size:12px;padding-top:5px'>点窗口外关闭</span></div>";
      box.innerHTML = h;
      if (hasSel) box.querySelectorAll("input[name='grp']").forEach((r) =>
        r.onchange = () => { const v = +r.value; setNodesDirectGroup(ids, v < 0 ? null : v); render(); });
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
        dissolveGroup(groupDefs[+b.getAttribute("data-del")]); render();
      });
      box.querySelectorAll("[data-reparent]").forEach((b) => b.onclick = (ev) => {
        reparentGroupDialog(groupDefs[+b.getAttribute("data-reparent")], ev);   // 归入另一个组/顶层（菜单选）
      });
      const newBtn = box.querySelector("#grp_new");
      if (newBtn) newBtn.onclick = async () => {
        if (!hasSel) { createGroupAt(null); render(); return; }   // 无选中：新建一个空组（视口中心）
        const name = await askText("新建分组", "分组" + (groupDefs.length + 1));
        if (name == null) return;
        groupDefs.push({ id: newGroupId(), title: name.trim() || "分组", color: GROUP_COLORS[groupDefs.length % GROUP_COLORS.length], parent: null, members: [], pos: null, size: null, desc: "" });
        setNodesDirectGroup(ids, groupDefs.length - 1); render();   // 新组直接含选中节点；嵌套靠拖组
      };
    };
    groupDlgRender = render;   // 撤销/重做后若弹窗仍开着，据此刷新
    render();
  }

  // 在节点上画：①已改参数的橙色小点；②节点下方“附属卡片”（描述 📝 + 模板缩略图网格）。
  // 截图预览：把节点的 base64 PNG 解码成 Image 并缓存。返回【最近一张已解码完成】的图——
  // 新图在后台解码、好了再换上，期间继续显示旧图，避免每帧 img.complete=false 时漏画造成的闪烁。
  function getPreviewImg(id, b64) {
    let c = _previewImgCache[id];
    if (!c) c = _previewImgCache[id] = { b64: null, shown: null };
    if (c.b64 !== b64) {                 // 内容变了：后台解码新图，好了才替换（旧图先顶着）
      c.b64 = b64;
      const img = new Image();
      img.onload = () => { c.shown = img; if (canvas) canvas.setDirty(true, false); };
      img.src = "data:image/png;base64," + b64;
    }
    return c.shown;
  }
  function nodeDrawForeground(ctx) {
    if (this.flags && this.flags.collapsed) return;
    // 截图预览：在节点【上方】画“它实际截到的区域图”，用于核对截图范围是否对准了目标。
    if (previewOn && runPreviews[this._id]) {
      const img = getPreviewImg(this._id, runPreviews[this._id]);
      if (img && img.complete && img.naturalWidth) {
        const maxW = Math.max(72, this.size[0]);
        const sc = Math.min(maxW / img.naturalWidth, 96 / img.naturalHeight);
        const dw = Math.max(1, img.naturalWidth * sc), dh = Math.max(1, img.naturalHeight * sc);
        const th = LiteGraph.NODE_TITLE_HEIGHT || 20, y = -th - dh - 17;
        ctx.save();
        ctx.fillStyle = "#0b0d11"; ctx.fillRect(-2, y - 2, dw + 4, dh + 17);
        ctx.strokeStyle = "#5a93d4"; ctx.lineWidth = 1; ctx.strokeRect(-2, y - 2, dw + 4, dh + 4);
        try { ctx.imageSmoothingEnabled = false; ctx.drawImage(img, 0, y, dw, dh); } catch (e) {}
        // 标签：有置信度/识别值就显示它（清晰文字、不烤进图里→不会被裁切），否则显示“实时截图”
        const plabel = runPreviewLabels[this._id];
        ctx.fillStyle = "#8fb6e0"; ctx.font = "10px 'Microsoft YaHei',sans-serif";
        ctx.fillText("🖼 " + (plabel || "实时截图"), 0, y + dh + 11);
        ctx.restore();
      }
    }
    // ⓪ 搜索/面板「定位」高亮：被定位的节点画脉冲外框；若指定了参数，再在该参数控件行画高亮条。
    if (this === _flashNode && performance.now() < _flashUntil) {
      const a = 0.45 + 0.45 * Math.abs(Math.cos(performance.now() / 320));   // 脉冲透明度
      const th = LiteGraph.NODE_TITLE_HEIGHT || 20;
      ctx.save();
      ctx.strokeStyle = "rgba(255,210,80," + a.toFixed(2) + ")";
      ctx.lineWidth = 3;
      ctx.strokeRect(-4, -th - 4, this.size[0] + 8, this.size[1] + th + 8);
      if (_flashKey) {
        const w = (this.widgets || []).find((x) => x._key === _flashKey);
        if (w && w.last_y != null) {
          ctx.fillStyle = "rgba(255,210,80,0.18)";
          ctx.fillRect(2, w.last_y - 1, this.size[0] - 4, (LiteGraph.NODE_WIDGET_HEIGHT || 20) + 2);
        }
      }
      ctx.restore();
    }
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
    // —— 试运行调试：断点（“运行到此节点”与断点重复，已去掉）——
    options.push(null, {
      content: breakpoints.has(node._id) ? "取消断点 ⭕" : "设为断点 🔴",
      callback: () => toggleBreakpoint(node._id),
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
      const groups = (c.groups || []).map((g) => {
        const o = { id: g.id, title: g.title, color: g.color, collapsed: !!g.collapsed, parent: g.parent || null, members: g.members.slice().sort(), desc: g.desc || "" };
        if (!g.members.length && g.pos) o.pos = [Math.round(g.pos[0]), Math.round(g.pos[1])];   // 空组才把位置纳入签名（有成员时位置由成员推导，避免误判“未保存”）
        return o;
      }).sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
      const foldparams = (c.foldparams || []).slice().sort((a, b) => ((a + "") < (b + "") ? -1 : 1));
      const groupexpose = (c.groupexpose || []).map((e) => e.join("|")).sort();
      const labels = {}; Object.keys(c.labels || {}).sort().forEach((k) => { labels[k] = c.labels[k]; });   // 键排序，使签名稳定
      return JSON.stringify({ name: c.name, description: c.description, panel: c.panel, labels, groups, foldparams, groupexpose, nodes, edges });
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
      pushChangeSummary(d);   // 切到“有改动”时立刻推一份清单，关闭确认框据此展示详情
    }
  }
  // 把“本次改动清单”文本推给 Python（与编辑器内「修改变化详情」窗口同源），供退出确认展示详情。
  function pushChangeSummary(d) {
    try { api().set_change_summary(d ? summarizeChanges().slice(0, 40).join("\n") : ""); } catch (e) {}
  }
  function isDirty() { return savedSig !== null && curSig() !== savedSig; }

  // 列出“与上次保存相比改了什么”（给切换/退出时的确认框显示）。
  function paramLabelByType(type, key) {
    const d = defByType[type];
    const p = d && (d.params || []).find((q) => q.key === key);
    return (p && p.label) || key;
  }
  // 逐项返回 {text, restore?}：restore 存在 = 该条可【单条恢复】到上次保存的值。
  // 可单条恢复：参数值 / 节点描述 / 自定义显示名；结构性改动(增删节点·连线·分组等)只列出、不提供一键恢复。
  function diffChanges() {
    if (!savedBaseline) return [];
    const cur = collect(), out = [];
    const push = (text, restore) => out.push(restore ? { text, restore } : { text });
    const titleOf = (n) => (defByType[n.type] && defByType[n.type].title) || n.type;
    if (cur.name !== savedBaseline.name) push(`流程名称：${savedBaseline.name} → ${cur.name}`);
    if ((cur.description || "") !== (savedBaseline.description || "")) push("流程说明已修改");
    const baseN = {}; for (const n of savedBaseline.nodes) baseN[n.id] = n;
    const curN = {}; for (const n of cur.nodes) curN[n.id] = n;
    const nodeOf = (id) => curN[id] || baseN[id];
    // 节点·参数的“结构名”：节点标题·参数标签（如「开关(布尔)·开启」）——同类型节点多时仍可能不够具体。
    const structName = (id, key) => { const n = nodeOf(id); return (n ? titleOf(n) : id) + "·" + (n ? paramLabelByType(n.type, key) : key); };
    // 友好名：优先用户起的显示名（面板/折叠箱体同源的 labels）或面板置顶名，否则回退结构名——
    // 这样改动详情会写「出村民」而不是含糊的「开关(布尔)·开启」，一眼看出改的是哪个。
    const friendlyName = (id, key) => {
      const lk = id + "|" + key;
      const lab = (cur.labels && cur.labels[lk]) || (savedBaseline.labels && savedBaseline.labels[lk]);
      if (lab) return lab;
      const pf = (cur.panel || []).concat(savedBaseline.panel || []).find((p) => p[0] === id && p[1] === key);
      if (pf && pf[2]) return pf[2];
      return structName(id, key);
    };
    // 值的友好显示：布尔 → 开/关（switch 的 false→true 直接看不懂）。
    const fmtVal = (v) => (v === true || v === "true") ? "开" : (v === false || v === "false") ? "关" : String(v);
    for (const n of cur.nodes) if (!baseN[n.id]) push(`＋ 新增节点：${titleOf(n)}`, () => removeNodeById(n.id));
    for (const n of savedBaseline.nodes) if (!curN[n.id]) push(`－ 删除节点：${titleOf(n)}`, () => recreateNodeFrom(n));
    const moves = [];
    for (const n of cur.nodes) {
      const b = baseN[n.id]; if (!b) continue;
      for (const k in (n.params || {}))
        if (String(n.params[k]) !== String((b.params || {})[k])) {
          const bv = (b.params || {})[k];
          push(`◇ ${friendlyName(n.id, k)}：${fmtVal(bv)} → ${fmtVal(n.params[k])}`, () => restoreParam(n.id, k, bv));
        }
      if ((n.note || "") !== (b.note || "")) push(`◇ ${titleOf(n)}：描述已修改`, () => restoreNote(n.id, b.note || ""));
      const bp = b.pos || [0, 0], np = n.pos || [0, 0];
      if (Math.round(bp[0]) !== Math.round(np[0]) || Math.round(bp[1]) !== Math.round(np[1])) moves.push([n, bp]);
    }
    // 位置移动：少量逐个可恢复；大量(如自动排版动了全图)聚合一行，避免淹没其它改动(用 Ctrl+Z 撤销排版更快)。
    if (moves.length > 6) push(`◇ ${moves.length} 个节点位置移动（量大；撤销排版用 Ctrl+Z 更快）`);
    else for (const [n, bp] of moves) push(`◇ 位置移动：${titleOf(n)}`, () => restorePos(n.id, bp[0], bp[1]));
    const ek = (e) => e.src + "|" + e.src_port + "|" + e.dst + "|" + e.dst_port;
    const baseE = new Set(savedBaseline.edges.map(ek)), curE = new Set(cur.edges.map(ek));
    const edgeName = (e) => { const s = nodeOf(e.src), d = nodeOf(e.dst); return (s ? titleOf(s) : e.src) + "·" + e.src_port + " → " + (d ? titleOf(d) : e.dst) + "·" + e.dst_port; };
    for (const e of cur.edges) if (!baseE.has(ek(e))) push(`＋ 新增连线：${edgeName(e)}`, () => removeEdgeByPorts(e.src, e.src_port, e.dst, e.dst_port));
    for (const e of savedBaseline.edges) if (!curE.has(ek(e))) push(`－ 删除连线：${edgeName(e)}`, () => addEdgeByPorts(e.src, e.src_port, e.dst, e.dst_port));
    // —— 以下把“笼统一句话”改成逐项列出，便于核对本次到底改了什么 ——
    const pkName = structName;   // 结构名（节点·参数），用于“改名/暴露”等需要点出是哪个参数的地方
    const splitK = (k) => { const i = k.indexOf("|"); return [k.slice(0, i), k.slice(i + 1)]; };
    // 控制面板置顶项：逐项列出增 / 删 / 改名 / 调序
    const pKey = (p) => p[0] + "|" + p[1];
    const baseP = {}, curP = {};
    for (const p of (savedBaseline.panel || [])) baseP[pKey(p)] = p;
    for (const p of (cur.panel || [])) curP[pKey(p)] = p;
    for (const k in curP) if (!(k in baseP)) push(`＋ 面板置顶：${friendlyName(curP[k][0], curP[k][1])}`);
    for (const k in baseP) if (!(k in curP)) push(`－ 取消面板置顶：${friendlyName(baseP[k][0], baseP[k][1])}`);
    for (const k in curP) if ((k in baseP) && (curP[k][2] || "") !== (baseP[k][2] || "")) push(`◇ 面板显示名：${pkName(curP[k][0], curP[k][1])} → ${curP[k][2] || "(默认)"}`);
    {
      const co = (cur.panel || []).map(pKey).join(","), bo = (savedBaseline.panel || []).map(pKey).join(",");
      if (co !== bo && co.split(",").sort().join() === bo.split(",").sort().join()) push("◇ 面板项顺序已调整");
    }
    // 参数显示名（面板 / 折叠箱体共用）——可单条恢复
    const cl = cur.labels || {}, bl = savedBaseline.labels || {};
    for (const k in cl) if (cl[k] !== bl[k]) { const [id, key] = splitK(k); push(`◇ 显示名：${pkName(id, key)} → ${cl[k]}`, () => restoreLabel(id, key, bl[k] || "")); }
    for (const k in bl) if (!(k in cl)) { const [id, key] = splitK(k); push(`◇ 清除显示名：${pkName(id, key)}`, () => restoreLabel(id, key, bl[k] || "")); }
    // 暴露给所在组的参数
    const fKey = (p) => p[0] + "|" + p[1];
    const baseF = new Set((savedBaseline.foldparams || []).map(fKey)), curF = new Set((cur.foldparams || []).map(fKey));
    for (const k of curF) if (!baseF.has(k)) { const [id, key] = splitK(k); push(`＋ 暴露给组：${pkName(id, key)}`); }
    for (const k of baseF) if (!curF.has(k)) { const [id, key] = splitK(k); push(`－ 取消暴露：${pkName(id, key)}`); }
    const geSig = (a) => JSON.stringify([...(a || [])].map((x) => x.join("|")).sort());
    if (geSig(cur.groupexpose) !== geSig(savedBaseline.groupexpose)) push("◇ 组的“向上暴露”设置已修改");
    // 分组：逐个列出改了什么
    const gById = (gs) => { const m = {}; for (const g of (gs || [])) m[g.id] = g; return m; };
    const bg = gById(savedBaseline.groups), cg = gById(cur.groups);
    for (const id in cg) {
      const c = cg[id], b = bg[id];
      if (!b) { push(`＋ 新增分组：${c.title || "分组"}`); continue; }
      if ((c.title || "") !== (b.title || "")) push(`◇ 分组改名：${b.title} → ${c.title}`);
      if ((c.desc || "") !== (b.desc || "")) push(`◇ 分组「${c.title}」描述已修改`);
      const cm = (c.members || []).slice().sort().join(","), bm = (b.members || []).slice().sort().join(",");
      if (cm !== bm) push(`◇ 分组「${c.title}」成员变化`);
      if (!!c.collapsed !== !!b.collapsed) push(`◇ 分组「${c.title}」${c.collapsed ? "已折叠" : "已展开"}`);
    }
    for (const id in bg) if (!(id in cg)) push(`－ 删除分组：${bg[id].title || "分组"}`);
    return out;
  }
  function summarizeChanges() { return diffChanges().map((c) => c.text); }

  // 统一的「修改变化详情」弹窗：未保存提示 / 退出确认 / 主动保存预览 共用这一个窗口。
  // opts: { title, subtitle, buttons:[{act,label,style}], defaultAct }。返回 Promise<act>。
  const _BTN_SAVE = "background:#3a5a3a;color:#dfe;border:1px solid #5a6;border-radius:4px;padding:4px 14px;cursor:pointer";
  const _BTN_DISCARD = "background:#2f343d;color:#ffb3b3;border:1px solid #a33;border-radius:4px;padding:4px 14px;cursor:pointer";
  const _BTN_CANCEL = "background:#2f343d;color:#cfd3da;border:1px solid #444;border-radius:4px;padding:4px 14px;cursor:pointer";
  function showChangeDialog(opts) {
    return new Promise((resolve) => {
      const old = document.getElementById("unsaveddlg"); if (old) old.remove();
      const box = document.createElement("div");
      box.id = "unsaveddlg";
      box.style.cssText = "position:absolute;left:50%;top:46px;transform:translateX(-50%);width:min(560px,94vw);" +
        "max-height:80vh;overflow:auto;background:#23272f;color:#cfd3da;border:1px solid #3a404a;border-radius:8px;" +
        "padding:14px 16px;z-index:200;box-shadow:0 8px 30px #000a;font:13px/1.6 'Microsoft YaHei',sans-serif;";
      let h = "<b style='color:#e6c07b'>" + esc(opts.title || "修改详情") + "</b>";
      if (opts.subtitle) h += "<div style='color:#9aa3af;margin-top:4px'>" + esc(opts.subtitle) + "</div>";
      h += "<div id='cd_cnt' style='margin-top:8px;color:#8fb6e0'></div>";
      h += "<div id='cd_list' style='margin-top:4px;max-height:46vh;overflow:auto;background:#1b1f27;border:1px solid #2c323c;border-radius:6px;padding:8px 10px;color:#bcd'></div>";
      h += "<div style='margin-top:12px;text-align:right'>";
      h += (opts.buttons || []).map((b) => "<button data-act='" + esc(b.act) + "' style='" + b.style + "'>" + esc(b.label) + "</button>").join(" ");
      h += "</div>";
      box.innerHTML = h;
      document.body.appendChild(box);
      const listEl = box.querySelector("#cd_list"), cntEl = box.querySelector("#cd_cnt");
      const RB = "flex:none;background:#2f343d;color:#9fd0ff;border:1px solid #3f5f88;border-radius:4px;font-size:12px;height:20px;line-height:18px;cursor:pointer;padding:0 8px";
      function renderList() {
        const changes = diffChanges();
        cntEl.textContent = changes.length ? ("共 " + changes.length + " 处改动（可单条恢复）：") : "";
        if (!changes.length) { listEl.innerHTML = "<div style='color:#7f8895'>（无改动）</div>"; return; }
        listEl.innerHTML = changes.map((c, i) =>
          "<div style='display:flex;align-items:flex-start;gap:8px;margin:3px 0'>" +
          "<div style='flex:1;white-space:pre-wrap'>" + esc(c.text) + "</div>" +
          (c.restore ? "<button data-restore='" + i + "' style='" + RB + "' title='把这一条恢复到上次保存的值'>恢复</button>" : "") +
          "</div>").join("");
        listEl.querySelectorAll("[data-restore]").forEach((btn) => {
          btn.onclick = () => {
            const c = diffChanges()[+btn.getAttribute("data-restore")];
            _changeDlgRestoring = true;                              // 标记“本次改动来自弹窗内部”，别触发自动关闭
            try { if (c && c.restore) c.restore(); } catch (e) {} finally { _changeDlgRestoring = false; }
            renderList();                                            // 弹窗内的恢复仍就地刷新列表
          };
        });
      }
      renderList();
      const done = (v) => { _changeDlgClose = null; box.remove(); document.removeEventListener("keydown", onKey, true); resolve(v); };
      if (opts.autoCloseOnEdit) _changeDlgClose = done;             // 仅“查看修改”窗：图上任何其它改动即自动关闭(免去实时刷新)
      box.querySelectorAll("[data-act]").forEach((btn) => { btn.onclick = () => done(btn.getAttribute("data-act")); });
      const def = box.querySelector("[data-act='" + opts.defaultAct + "']");
      setTimeout(() => { if (def) def.focus(); }, 0);
      const onKey = (e) => {
        if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); done("cancel"); }
        else if (e.key === "Enter") { e.preventDefault(); e.stopPropagation(); if (document.activeElement) document.activeElement.click(); }
      };
      document.addEventListener("keydown", onKey, true);
    });
  }
  // 有未保存修改时弹确认框（列出修改内容），返回 Promise<"save"|"discard"|"cancel">。
  function confirmUnsaved(actionLabel) {
    if (!isDirty()) return Promise.resolve("discard");
    return showChangeDialog({
      title: "有未保存的修改",
      subtitle: (actionLabel || "继续操作") + "前要保存吗？",
      buttons: [
        { act: "save", label: "保存并继续", style: _BTN_SAVE },
        { act: "discard", label: "不保存", style: _BTN_DISCARD },
        { act: "cancel", label: "取消", style: _BTN_CANCEL },
      ],
      defaultAct: "cancel",   // 默认焦点在“取消”最安全：回车不会误保存/丢弃
    });
  }
  // 主动保存：先弹同一个详情窗口预览本次要保存的改动，返回 Promise<"save"|"cancel"|"nochange">。
  function confirmSave() {
    if (!isDirty()) return Promise.resolve("nochange");
    return showChangeDialog({
      title: "保存修改",
      subtitle: "确认要把以下改动保存到流程文件吗？",
      buttons: [
        { act: "save", label: "保存", style: _BTN_SAVE },
        { act: "cancel", label: "取消", style: _BTN_CANCEL },
      ],
      defaultAct: "save",   // 主动保存：默认焦点在“保存”，回车即存
    });
  }
  // 随时查看「与上次保存相比改了什么」：内容同保存预览，可逐条「恢复」。只读查看，不触发保存/丢弃。
  function viewChanges() {
    showChangeDialog({
      title: "当前修改（与上次保存相比）",
      subtitle: "点每条右侧「恢复」可单独还原该项；在图上做任何其它改动会自动关闭本窗（避免列表过时）。",
      buttons: [{ act: "cancel", label: "关闭", style: _BTN_CANCEL }],
      defaultAct: "cancel",
      autoCloseOnEdit: true,
    });
  }
  // 通用确认框（不带改动清单）：返回 Promise<bool>。danger=true 时“确定”按钮用红色。
  function confirmAction(title, msg, okLabel, danger) {
    return new Promise((resolve) => {
      const old = document.getElementById("confirmdlg"); if (old) old.remove();
      const box = document.createElement("div");
      box.id = "confirmdlg";
      box.style.cssText = "position:absolute;left:50%;top:46px;transform:translateX(-50%);width:min(440px,94vw);" +
        "background:#23272f;color:#cfd3da;border:1px solid #3a404a;border-radius:8px;padding:14px 16px;z-index:200;" +
        "box-shadow:0 8px 30px #000a;font:13px/1.6 'Microsoft YaHei',sans-serif;";
      box.innerHTML = "<b style='color:#e6c07b'>" + esc(title) + "</b>" +
        "<div style='margin-top:8px;color:#cfd3da;white-space:pre-wrap'>" + esc(msg) + "</div>" +
        "<div style='margin-top:12px;text-align:right'>" +
        "<button data-act='ok' style='" + (danger ? _BTN_DISCARD : _BTN_SAVE) + "'>" + esc(okLabel || "确定") + "</button> " +
        "<button data-act='cancel' style='" + _BTN_CANCEL + "'>取消</button></div>";
      document.body.appendChild(box);
      const done = (v) => { box.remove(); document.removeEventListener("keydown", onKey, true); resolve(v === "ok"); };
      box.querySelectorAll("[data-act]").forEach((b) => { b.onclick = () => done(b.getAttribute("data-act")); });
      const c = box.querySelector("[data-act='cancel']"); setTimeout(() => { if (c) c.focus(); }, 0);  // 默认焦点在取消
      const onKey = (e) => {
        if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); done("cancel"); }
        else if (e.key === "Enter") { e.preventDefault(); e.stopPropagation(); if (document.activeElement) document.activeElement.click(); }
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
    const foldparams = foldPins.filter(([nid]) => ids.has(nid)).map((p) => p.slice(0, 2));
    const gset = new Set(groupDefs.map((g) => g.id));
    const groupexpose = groupExpose.filter(([gid, nid]) => ids.has(nid) && gset.has(gid)).map((e) => e.slice(0, 3));
    // 自定义显示名（权威存储，含未置顶但已暴露给组的）：随流程保存，面板/折叠箱体共用。
    const labels = {};
    for (const k in pinLabels) {
      const nid = k.slice(0, k.indexOf("|"));
      if (pinLabels[k] && ids.has(nid)) labels[k] = pinLabels[k];
    }
    // 支持空组：保留所有组（含空组），随流程存盘；pos/size 用于空组兜底定位（有成员时由包围盒每帧刷新）。
    const groups = groupDefs
      .map((g) => ({ id: g.id, title: g.title, color: g.color, collapsed: !!g.collapsed, parent: g.parent || null, members: (g.members || []).filter((m) => ids.has(m)), pos: g.pos || null, size: g.size || null, desc: g.desc || "" }));
    return { name: flowMeta.name || "未命名流程", description: flowMeta.desc || "", panel, groups, foldparams, groupexpose, labels, nodes, edges };
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
      runSession = false; runPath = new Set(); runPathArr = []; runPorts = {}; runData = {}; runDataNodes = new Set(); runTimes = {}; runPreviews = {}; runPreviewLabels = {}; setRunUI();
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
    pinLabels = {};   // 自定义显示名权威存储：优先从 labels 字段载入（含未置顶的），旧流程再从 panel[2] 兼容补齐
    if (flow.labels && typeof flow.labels === "object") for (const k in flow.labels) if (flow.labels[k]) pinLabels[k] = String(flow.labels[k]);
    for (const p of panelPins) if (p[2] && !pinLabels[p[0] + "|" + p[1]]) pinLabels[p[0] + "|" + p[1]] = p[2];
    foldPins = Array.isArray(flow.foldparams) ? flow.foldparams.map((x) => x.slice(0, 2)) : [];   // 节点暴露给所在组的参数
    groupExpose = Array.isArray(flow.groupexpose) ? flow.groupexpose.map((x) => x.slice(0, 3)) : [];   // 组再向上一级暴露的参数
    groupDefs = Array.isArray(flow.groups) ? normalizeGroups(flow.groups) : [];   // 补 id/parent，旧格式自动迁移成容器树
    selectedGroupId = null;   // 新流程：清除组选中
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
    // 使用模式下【切换/打开流程】(clean)才刷新“退出时还原”的基线；【自动排版】(clean=false)不刷新——
    // 这样使用模式里的自动排版和拖动一样是临时的、退出使用模式即还原，符合“使用模式不改图/不保存”。
    if (simpleMode && clean) simpleEntrySig = JSON.stringify(collect());
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
    // 📁定位 / 🗑移除 仅对「我的流程」(真实独立用户文件) 有意义：内置打包后无独立文件、且只读不能删；未保存的也无文件。
    const userFlow = !!(flowMeta.path && !flowMeta.readonly);
    const lb = document.getElementById("locatebtn"); if (lb) lb.style.display = userFlow ? "" : "none";
    const rb = document.getElementById("removebtn"); if (rb) rb.style.display = userFlow ? "" : "none";
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
    if (i >= 0) panelPins.splice(i, 1);                       // 自定义名独立存于 pinLabels，取消置顶不丢
    else panelPins.push([nid, key, pinLabels[lk] || ""]);     // 置顶时镜像当前自定义名（保持 panel 字段兼容）
    renderPanel(); scheduleSnap(); refreshDirty();
    if (selectedNode) showNodeHelp(selectedNode);   // 同步说明里勾选框状态
  }
  // “显示到折叠节点”：与“显示到控制面板”并排的第二维勾选。勾上的参数会在其所属分组折叠后的箱体里
  // 以和节点上一样的可编辑控件出现（见 rebuildFoldWidgets）。
  function isFoldPinned(nid, key) { return foldPins.some((p) => p[0] === nid && p[1] === key); }
  function toggleFoldPin(nid, key) {
    const i = foldPins.findIndex((p) => p[0] === nid && p[1] === key);
    if (i >= 0) foldPins.splice(i, 1); else foldPins.push([nid, key]);
    if (i >= 0) groupExpose = groupExpose.filter((e) => !(e[1] === nid && e[2] === key));   // 取消节点暴露 → 连带清掉各组对它的“向上暴露”
    rebuildFoldWidgets();                       // 箱体行数/控件随之变
    if (canvas) canvas.setDirty(true, true);
    scheduleSnap(); refreshDirty();
    if (selectedNode) showNodeHelp(selectedNode);   // 同步说明里勾选框状态
    if (selectedGroupId) showGroupHelp(groupById(selectedGroupId));
  }
  // 组把“已暴露进它接口”的某参数再向上暴露给父组（逐级封装）。
  function isGroupExposed(gid, nid, key) { return groupExpose.some((e) => e[0] === gid && e[1] === nid && e[2] === key); }
  function toggleGroupExpose(gid, nid, key) {
    const i = groupExpose.findIndex((e) => e[0] === gid && e[1] === nid && e[2] === key);
    if (i >= 0) groupExpose.splice(i, 1); else groupExpose.push([gid, nid, key]);
    rebuildFoldWidgets();
    if (canvas) canvas.setDirty(true, true);
    scheduleSnap(); refreshDirty();
    if (selectedGroupId) showGroupHelp(groupById(selectedGroupId));
  }
  // 自定义显示名：以 (nodeId|key) 为键的【唯一权威存储】(pinLabels)，控制面板置顶与“暴露给所在组”折叠箱体
  // 【共用同一个名字】。与是否置顶无关——只要填了两处都用；随流程保存(labels 字段)、撤销/重做、取消勾选都不丢。
  function customLabel(nid, key) {
    const v = pinLabels[nid + "|" + key];
    return v ? String(v).trim() : "";
  }
  // 设置某参数的自定义显示名（空＝清除、回落默认名）。面板与折叠箱体即时刷新。
  function setPinLabel(nid, key, name) {
    const lk = nid + "|" + key, v = String(name || "").trim();
    if (v) pinLabels[lk] = v; else delete pinLabels[lk];
    const e = pinEntry(nid, key); if (e) e[2] = v;   // 已置顶项的内联名同步，保持 panel 字段向后兼容
    renderPanel(); rebuildFoldWidgets();
    if (canvas) canvas.setDirty(true, true);
    scheduleSnap(); refreshDirty();
  }
  // —— 单条「恢复到上次保存」：供「保存预览 / 查看修改」窗口里每条改动旁的“恢复”按钮调用 ——
  function restoreParam(id, key, val) {   // 把某节点的某参数恢复为基线值（回写 widget + properties + 折叠控件）
    const n = nodeByOurId(id); if (!n) return;
    const w = (n.widgets || []).find((x) => x._key === key);
    if (w) w.value = (w.type === "combo") ? String(val) : val;
    if (!n.properties) n.properties = {};
    n.properties[key] = w ? w.value : val;
    if (canvas) canvas.setDirty(true, true);
    scheduleSnap(); refreshDirty(); syncFoldWidgetValues();
  }
  function restoreNote(id, note) {
    const n = nodeByOurId(id); if (!n) return;
    n._note = note;
    if (canvas) canvas.setDirty(true, true);
    scheduleSnap(); refreshDirty();
  }
  function restoreLabel(id, key, name) { setPinLabel(id, key, name); }   // 空=清除显示名（回落默认）
  // 运行中节点自动改写了别的参数（如「设置开关」把某开关设为开/关）：回写到编辑器里的对应控件，
  // 让界面也显示成新值、并记为一处可保存/可恢复的改动。按 (节点|参数) 去重，避免每帧重复套用。
  let _appliedPW = {};
  function applyRunParamWrites(pw) {
    let any = false, lastMsg = "";
    for (const nid in pw) {
      const kv = pw[nid] || {};
      for (const key in kv) {
        const sig = nid + "|" + key, val = kv[key];
        if (_appliedPW[sig] === val) continue;     // 已套用且未变 → 跳过(免每帧重复)
        const n = nodeByOurId(nid); if (!n) continue;
        _appliedPW[sig] = val;
        // 直接回写控件值 + properties（不经 restoreParam，避免其在运行中的副作用把这次回写吞掉）
        const w = (n.widgets || []).find((x) => x._key === key);
        if (w) w.value = (w.type === "combo") ? String(val) : (w.type === "toggle" ? !!val : val);
        if (!n.properties) n.properties = {};
        n.properties[key] = w ? w.value : val;
        try { flashLocate(n, key); } catch (e) {}
        const lab = (typeof panelLabel === "function" && panelLabel(n, key)) || n.title || key;
        lastMsg = "⚙ 「设置开关」已把「" + lab + "」设为「" + (val ? "开" : "关") + "」";
        any = true;
      }
    }
    if (any) {
      if (canvas) canvas.setDirty(true, true);       // 强制【整屏】重绘（节点本体+背景），保证开关勾选立刻刷新
      renderPanel();                                  // 同步刷新控制面板控件——否则面板勾选不跟着变
      scheduleSnap();                                 // 记为可保存/可恢复的改动 + 运行中热更新引擎
      try { setStatus(lastMsg); } catch (e) {}        // 状态栏给出可见确认（即使节点在折叠组里看不到，也知道生效了）
    }
  }
  // —— 结构性改动的单条恢复（位置移动 / 节点增删 / 连线增删）——
  function _afterEdit() { if (canvas) canvas.setDirty(true, true); scheduleSnap(); refreshDirty(); }
  function restorePos(id, x, y) { const n = nodeByOurId(id); if (n) n.pos = [x, y]; _afterEdit(); }
  function removeNodeById(id) { const n = nodeByOurId(id); if (n) { try { graph.remove(n); } catch (e) {} } _afterEdit(); }
  function recreateNodeFrom(b) {   // b = 基线节点数据 {id,type,pos,params,note}（连线作为单独条目各自恢复）
    if (nodeByOurId(b.id)) return;
    const key = typeKeyByType[b.type]; if (!key) return;
    const n = LiteGraph.createNode(key); if (!n) return;
    n._id = b.id; n._typeId = b.type; n._note = b.note || "";
    n.pos = [b.pos ? b.pos[0] : 0, b.pos ? b.pos[1] : 0];
    for (const w of (n.widgets || [])) if (b.params && w._key in b.params) { w.value = (w.type === "combo") ? String(b.params[w._key]) : b.params[w._key]; n.properties[w._key] = w.value; }
    try { graph.add(n); } catch (e) {}
    _afterEdit();
  }
  function addEdgeByPorts(src, sp, dst, dp) {
    const a = nodeByOurId(src), b = nodeByOurId(dst); if (a && b) { const so = a.findOutputSlot(sp), si = b.findInputSlot(dp); if (so >= 0 && si >= 0) { try { a.connect(so, b, si); } catch (e) {} } }
    _afterEdit();
  }
  function removeEdgeByPorts(src, sp, dst, dp) {
    const a = nodeByOurId(src), b = nodeByOurId(dst); if (a && b) { const so = a.findOutputSlot(sp); if (so >= 0) { try { a.disconnectOutput(so, b); } catch (e) {} } }
    _afterEdit();
  }

  // 默认显示名：「节点标题 · 参数标签」——自带节点上下文，多个节点置顶/暴露同名参数（区域/阈值/间隔(秒)…）也不混淆。
  // 控制面板与折叠箱体【共用这同一个默认】；填了自定义名则两处都用自定义名（随流程保存）。
  function defaultPinLabel(node, key) {
    const d = defByType[node._typeId];
    const p = d && (d.params || []).find((q) => q.key === key);
    const plabel = (p && p.label) || key;
    return ((d && d.title) || node._typeId) + " · " + plabel;
  }
  function panelLabel(node, key) {
    return customLabel(node._id, key) || defaultPinLabel(node, key);   // 自定义名优先（与折叠箱体同源）
  }
  // 定位高亮：被定位的节点（和可选的某个参数）在图里画脉冲外框一会儿，醒目又不需手动找。
  let _flashNode = null, _flashKey = null, _flashUntil = 0, _flashTimer = null;
  function flashLocate(node, key) {
    _flashNode = node; _flashKey = key || null; _flashUntil = performance.now() + 1800;
    if (_flashTimer) clearInterval(_flashTimer);
    _flashTimer = setInterval(() => {                       // 脉冲期间持续重绘；到点收尾
      if (canvas) canvas.setDirty(true, true);
      if (performance.now() >= _flashUntil) { clearInterval(_flashTimer); _flashTimer = null; _flashNode = null; _flashKey = null; if (canvas) canvas.setDirty(true, true); }
    }, 33);
  }
  // 在节点图里定位到某节点：选中并居中、并高亮（可选高亮某个参数行）。
  function locateNode(node, key) {
    if (!node || !canvas) return;
    try { canvas.centerOnNode(node); } catch (e) {}
    try { canvas.selectNode(node, false); } catch (e) {}
    selectedNode = node; showNodeHelp(node);   // 顺带展开右下角说明（在那里可取消显示）
    flashLocate(node, key);
    canvas.setDirty(true, true);
  }

  // ==================== 图内文字搜索（Ctrl+F）====================
  // 在节点图里按文字找：节点的 类型/参数值/参数名/自定义显示名/说明/内部id，以及分组名/描述；
  // 点结果即调用 locateNode 选中并居中。图一大时不必肉眼翻找。
  function searchableFields(n) {
    const out = [];   // 每项 [字段名, 文本, 可选参数key(用于定位时高亮该参数行)]
    const def = defByType[n._typeId] || {};
    out.push(["类型", def.title || n._typeId]);
    const doc = def.doc || def.help || "";
    if (doc) out.push(["节点说明", doc]);
    if ((n._note || "").trim()) out.push(["描述", n._note]);
    const pmeta = {}; for (const p of (def.params || [])) pmeta[p.key] = p;
    for (const key in (n.properties || {})) {
      const v = n.properties[key];
      const p = pmeta[key];
      out.push(["参数·" + ((p && p.label) || key), String(v == null ? "" : v), key]);
      if (p && p.help) out.push(["参数说明·" + (p.label || key), p.help, key]);
      const cl = customLabel(n._id, key);
      if (cl) out.push(["显示名", cl, key]);
    }
    for (const io of [].concat(def.inputs || [], def.outputs || [])) {   // 端口名/端口说明也一并搜
      if (io && (io.label || io.name)) out.push(["端口", io.label || io.name]);
      if (io && io.help) out.push(["端口说明", io.help]);
    }
    out.push(["id", n._id]);
    return out;
  }
  function searchGraph(q) {
    q = (q || "").trim().toLowerCase();
    if (!q) return [];
    const res = [];
    for (const n of (graph && graph._nodes) || []) {
      for (const [fl, tx, key] of searchableFields(n)) {
        if (String(tx).toLowerCase().includes(q)) { res.push({ node: n, field: fl, text: String(tx), key: key || null }); break; }
      }
      if (res.length >= 300) break;
    }
    for (const g of groupDefs || []) {
      const tx = ((g.title || "") + " " + (g.desc || "")).toLowerCase();
      if (tx.includes(q)) res.push({ group: g, field: "分组", text: g.title || "分组" });
    }
    return res;
  }
  function locateResult(r) {
    if (r.node) locateNode(r.node, r.key);
    else if (r.group) { const m = nodeByOurId((r.group.members || [])[0]); if (m) locateNode(m); }
  }
  // 返回【已转义的 HTML】：以匹配处为中心截一段，并把段内所有匹配处套上 .gs-hit 高亮底色。
  function _searchHilite(text, q) {
    text = String(text); q = (q || "").trim();
    const low = text.toLowerCase(), ql = q.toLowerCase();
    const first = ql ? low.indexOf(ql) : -1;
    let s = 0, e = text.length, pre = "", suf = "";
    if (first >= 0) {
      s = Math.max(0, first - 20); e = Math.min(text.length, first + ql.length + 40);
    } else {
      e = Math.min(text.length, 80);
    }
    pre = s > 0 ? "…" : ""; suf = e < text.length ? "…" : "";
    const seg = text.slice(s, e), segLow = seg.toLowerCase();
    if (!ql) return pre + esc(seg) + suf;
    let html = "", i = 0;
    for (;;) {
      const j = segLow.indexOf(ql, i);
      if (j < 0) { html += esc(seg.slice(i)); break; }
      html += esc(seg.slice(i, j)) + "<span class='gs-hit'>" + esc(seg.slice(j, j + ql.length)) + "</span>";
      i = j + ql.length;
    }
    return pre + html + suf;
  }
  let _searchSel = 0;
  function openSearch() {
    const existing = document.getElementById("graphsearch");
    if (existing) { const i = existing.querySelector("input"); if (i) { i.focus(); i.select(); } return; }
    const box = document.createElement("div");
    box.id = "graphsearch";
    // 放左上角（不放正中）：定位会把目标节点居中显示，搜索框若在正中会正好挡住它。
    box.style.cssText = "position:absolute;left:10px;top:8px;width:min(440px,46vw);" +
      "background:#23272f;color:#cfd3da;border:1px solid #3a404a;border-radius:8px;padding:10px 12px;z-index:210;" +
      "box-shadow:0 8px 30px #000a;font:13px/1.5 'Microsoft YaHei',sans-serif;";
    box.innerHTML =
      "<style>#graphsearch .gs-hit{background:#6a5300;color:#ffe08a;border-radius:2px;padding:0 1px}" +
      "#graphsearch .gs-row.sel{background:#2d3848;border-color:#3f5f88}</style>" +
      "<div style='display:flex;align-items:center;gap:8px'>" +
      "<span style='color:#e6c07b;white-space:nowrap'>🔍 图内搜索</span>" +
      "<input type='text' placeholder='类型/参数/显示名/说明/id 或分组名…' " +
      "style='flex:1;background:#15171c;color:#dfe;border:1px solid #444;border-radius:5px;height:26px;padding:0 8px;font-size:13px'>" +
      "<span id='gs_cnt' style='color:#8fb6e0;white-space:nowrap'></span>" +
      "<span id='gs_x' style='color:#9aa3af;cursor:pointer;padding:0 2px' title='关闭(Esc)'>✕</span></div>" +
      "<div id='gs_res' style='margin-top:8px;max-height:50vh;overflow:auto'></div>";
    document.getElementById("wrap").appendChild(box);
    const input = box.querySelector("input");
    const resBox = box.querySelector("#gs_res");
    const cnt = box.querySelector("#gs_cnt");
    let results = [];
    const close = () => { box.remove(); document.removeEventListener("keydown", onDocKey, true); };
    const scrollSel = () => { const el = resBox.querySelector(".gs-row[data-i='" + _searchSel + "']"); if (el) el.scrollIntoView({ block: "nearest" }); };
    function render() {
      results = searchGraph(input.value);
      if (_searchSel >= results.length) _searchSel = Math.max(0, results.length - 1);
      cnt.textContent = input.value.trim() ? ("找到 " + results.length) : "";
      if (!input.value.trim()) { resBox.innerHTML = "<div style='color:#7f8895;padding:6px 2px'>输入文字开始搜索（实时）。↑↓ 选择 · Enter 定位 · Esc 关闭。</div>"; return; }
      if (!results.length) { resBox.innerHTML = "<div style='color:#7f8895;padding:6px 2px'>没有匹配。</div>"; return; }
      resBox.innerHTML = results.map((r, i) => {
        const name = r.node ? ((defByType[r.node._typeId] && defByType[r.node._typeId].title) || r.node._typeId) : ("分组「" + (r.group.title || "") + "」");
        const idtag = r.node ? ("<span style='color:#6c727c'> · " + esc(r.node._id) + "</span>") : "";
        return "<div class='gs-row" + (i === _searchSel ? " sel" : "") + "' data-i='" + i + "' style='padding:5px 8px;border:1px solid #2c323c;border-radius:6px;margin:3px 0;cursor:pointer'>" +
          "<div style='color:#cdd6e2'>" + esc(name) + idtag + "</div>" +
          "<div style='color:#8b929e;font-size:12px'>" + esc(r.field) + "：" + _searchHilite(r.text, input.value) + "</div></div>";
      }).join("");
      resBox.querySelectorAll(".gs-row").forEach((row) => {
        row.onclick = () => { _searchSel = +row.getAttribute("data-i"); locateResult(results[_searchSel]); render(); };
      });
    }
    input.addEventListener("input", () => { _searchSel = 0; render(); });
    input.addEventListener("keydown", (e) => {
      e.stopPropagation();   // 别触发画布快捷键（Ctrl+A/Delete…）
      if (e.key === "Escape") { e.preventDefault(); close(); }
      else if (e.key === "ArrowDown") { e.preventDefault(); if (results.length) { _searchSel = (_searchSel + 1) % results.length; render(); scrollSel(); } }
      else if (e.key === "ArrowUp") { e.preventDefault(); if (results.length) { _searchSel = (_searchSel - 1 + results.length) % results.length; render(); scrollSel(); } }
      else if (e.key === "Enter") { e.preventDefault(); if (results[_searchSel]) locateResult(results[_searchSel]); }
    });
    box.querySelector("#gs_x").onclick = close;
    const onDocKey = (e) => { if (e.key === "Escape") { close(); } };
    document.addEventListener("keydown", onDocKey, true);
    render();
    setTimeout(() => input.focus(), 0);
  }

  function setPanelValue(node, key, val) {
    const w = (node.widgets || []).find((x) => x._key === key);
    if (!w) return;
    w.value = val;
    if (node.properties) node.properties[key] = val;
    if (canvas) canvas.setDirty(true, true);
    scheduleSnap(); refreshDirty();
    syncFoldWidgetValues();   // 折叠箱体里若也显示了同一参数，同步其控件显示值
  }
  // 按参数类型构建一个可编辑控件（控制面板与折叠箱体共用）：改值统一走 setPanelValue（回写 widget + 实时生效）。
  function buildParamControl(node, key) {
    const w = (node.widgets || []).find((x) => x._key === key);
    if (!w) return null;
    const def = defByType[node._typeId];
    const pspec = def && (def.params || []).find((q) => q.key === key);
    const pt = pspec && pspec.ptype;
    let ctrl;
    if (pt === "key" || pt === "region" || pt === "point" || pt === "color") {
      ctrl = captureControl(node, key, pt);   // 采集型：按钮 + 当前值
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
    ctrl.dataset.fwk = node._id + "|" + key;   // 供 syncFoldWidgetValues 找到并更新
    return ctrl;
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
      mkb("捕获", () => { captureKey().then((k) => { if (k) apply(k); }); });   // 编辑器内部小窗捕获
      mkb("特殊键", () => specialKeyMenu((name) => apply(name)));
    } else if (pt === "region") {
      mkb("框选", () => { setStatus("框选区域…（拖动/移动/拖边角微调，Enter 确认 / Esc 取消）"); Promise.resolve(api().pick_region(parseBox(node.properties && node.properties[key]))).then((b) => { if (b) apply(b.join(",")); }).catch((e) => showError("采集失败：" + e)); });
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
      const ctrl = buildParamControl(node, key);
      if (ctrl) item.appendChild(ctrl);
      // 🎯 定位：选中并居中到该节点（取代“移除”按钮，避免误点删除；移除在右下角说明里取消勾选）
      const loc = document.createElement("span");
      loc.className = "ploc"; loc.textContent = "🎯";
      loc.title = "定位到此节点（在右下角说明里可取消显示 / 改显示名）";
      loc.onclick = () => locateNode(node, key);   // 同时高亮该参数行
      item.appendChild(loc);
      el.appendChild(item);
    }
  }

  // 让下拉显示“当前打开的流程”：按路径精确匹配，匹配不到再按文件名，仍不到则置空(-1)。
  function selectCurrentInList() {
    const sel = document.getElementById("builtin");
    if (!sel) return;
    const key = String(flowMeta.path || "").replace(/\\/g, "/");
    let idx = -1;
    // 下拉项的值是【相对路径】"flows/xxx" / "user_flows/xxx"；而 flowMeta.path 可能是相对(下拉打开的)
    // 也可能是绝对(刚“另存为”的)。按【相对尾路径】匹配：v===key 或 key 以 "/"+v 结尾——
    // 这样既区分“同名不同目录”(内置 vs 我的流程)，又兼容绝对路径(另存为后下拉能正确选中，不再空白)。
    if (key) for (let i = 0; i < sel.options.length; i++) {
      const v = String(sel.options[i].value || "").replace(/\\/g, "/");
      if (v && (v === key || key.endsWith("/" + v))) { idx = i; break; }
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
    // 使用模式【允许拖动节点/组的位置查看（不落盘，退出使用模式即还原）】，但仍只读其它改图操作：
    // 改控件/连线/克隆/增删 由前面的“三道闸”+菜单/键盘 simpleMode 守卫各自拦下。故这里不再开 read_only
    // （read_only 会连拖动也禁掉）；平移/缩放本就不受影响。
    if (canvas) { canvas.read_only = false; canvas.resize(); canvas.setDirty(true, true); }   // 两种模式都显示画布：切换后重算尺寸
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
      startRunStateSync(); // 常驻同步运行态：覆盖层启停 ↔ 主界面按钮保持一致
      try { api().set_run_payload(collect()); } catch (e) {}    // 开机即登记当前图，覆盖层可立刻「启动」
      try { if (!overlayOpen) toggleOverlay(); } catch (e) {}   // 启动即默认打开游戏内覆盖层（可在工具栏 🎮 关掉）
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
  // anchorRight=true 时 x 为药丸【右边界】（用于把输入值贴在输入端口左侧）；dim=true 时略淡（区分“输入侧回显”与权威的输出值）。
  function drawValPill(ctx, x, y, v, anchorRight, dim) {
    const text = fmtRunVal(v);
    let bg = "#27496b", fg = "#dff0ff";                 // 默认（数值/其它）
    if (v === true) { bg = "#1f8a47"; fg = "#eafff0"; }
    else if (v === false) { bg = "#a83a3a"; fg = "#ffe6e6"; }
    else if (v && typeof v === "object" && v._list) { bg = "#5a4a85"; fg = "#efe8ff"; }
    else if (typeof v === "string") { bg = "#3f6a4a"; fg = "#e7ffe7"; }
    ctx.save();
    ctx.font = "bold 12px Consolas, monospace";
    const tw = ctx.measureText(text).width, bw = tw + 12;
    const bx = anchorRight ? (x - bw) : x;              // 右对齐：右边界落在 x
    if (dim) ctx.globalAlpha = 0.82;
    roundRect(ctx, bx, y - 9, bw, 18, 5);
    ctx.shadowColor = "#000a"; ctx.shadowBlur = 5; ctx.fillStyle = bg; ctx.fill();
    ctx.shadowBlur = 0; ctx.strokeStyle = "#0007"; ctx.lineWidth = 1; ctx.stroke();
    ctx.fillStyle = fg; ctx.textBaseline = "middle"; ctx.textAlign = "left";
    ctx.fillText(text, bx + 6, y + 0.5);
    ctx.restore();
  }
  // 性能监控药丸：拆成两个独立小药丸——自身ms 贴节点左沿（向右生长）、Σ累计ms 贴右沿（向左生长）。
  // 各自只有一端固定、另一端随位数伸缩，所以数字变长变短时不会互相挤动、看起来不再左右晃。
  // lx/rx = 节点左/右边界，by = 底边（节点顶部上方）。自身耗时越大数字越偏红（>8ms≈一次截图量级）。
  function drawTimePill(ctx, lx, rx, by, selfMs, cumMs) {
    const fmt = (v) => (v >= 100 ? Math.round(v) + "" : v.toFixed(1));
    const pad = 6, h = 16, y = by - h, cy = y + h / 2 + 0.5;
    ctx.save();
    ctx.font = "bold 11px Consolas, monospace";
    ctx.textBaseline = "middle"; ctx.textAlign = "left";
    const pill = (px, text, fill) => {       // px=左上角X；底/边框统一，只换文字与字色
      const w = ctx.measureText(text).width + pad * 2;
      roundRect(ctx, px, y, w, h, 5);
      ctx.fillStyle = "rgba(20,26,36,0.92)"; ctx.shadowColor = "#000a"; ctx.shadowBlur = 5; ctx.fill();
      ctx.shadowBlur = 0; ctx.strokeStyle = "#0008"; ctx.lineWidth = 1; ctx.stroke();
      ctx.fillStyle = fill; ctx.fillText(text, px + pad, cy);
      return w;
    };
    const t1 = fmt(selfMs) + "ms", hot = Math.min(1, selfMs / 16);
    pill(lx, t1, `rgb(${160 + hot * 95 | 0},${200 - hot * 120 | 0},${255 - hot * 200 | 0})`);  // 自身：左沿向右
    const t2 = "Σ" + fmt(cumMs) + "ms";
    pill(rx - (ctx.measureText(t2).width + pad * 2), t2, "#ffd23f");                            // 累计：右沿向左
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
  // 命中断点暂停时：在停下的节点（或其所属折叠箱体）上画醒目的红色脉冲环 + “⏸ 已暂停”标牌，
  // 让“停在哪一步”一眼可见——之前暂停后画面几乎无变化，用户找不到停在哪。
  function drawBpHitMarker(ctx) {
    if (!bpHitId || !graph) return;
    let rect = null;                       // [x, y, w, h]（图坐标）
    const n = nodeByOurId(bpHitId);
    if (n && !foldHidden.has(bpHitId)) {
      rect = nodeFullRect(n, ctx);
    } else {                               // 命中的是被折叠隐藏的成员：高亮它所属的【顶层折叠箱体】
      for (const { g } of topCollapsedGroups()) {
        if (!groupAllMembers(g).includes(bpHitId)) continue;
        const b = subgBox(g, ctx); if (b) rect = [b.x, b.y, b.w, b.h];
        break;
      }
    }
    if (!rect) return;
    const [x, y, w, h] = rect;
    const pulse = 0.5 + 0.5 * Math.sin(runPhase * 2.2);   // runPhase 在暂停时仍以 20fps 推进 → 环会呼吸
    ctx.save();
    ctx.strokeStyle = "#ff5b5b"; ctx.lineWidth = 3 + 2 * pulse;
    ctx.shadowColor = "#ff2d2d"; ctx.shadowBlur = 14 + 16 * pulse;
    roundRect(ctx, x - 3, y - 3, w + 6, h + 6, 10); ctx.stroke();
    ctx.restore();
    const label = "⏸ 已暂停（命中断点）";    // 正上方居中的醒目标牌
    ctx.save();
    ctx.font = "bold 12px 'Microsoft YaHei',sans-serif"; ctx.textBaseline = "middle";
    // 居中 + 抬高：让开节点左右上角的耗时药丸（自身在左角、Σ累计在右角，都贴节点顶边）。
    const tw = ctx.measureText(label).width, px = x + (w - (tw + 14)) / 2, py = y - 48;
    roundRect(ctx, px, py, tw + 14, 20, 6); ctx.fillStyle = "#ff5b5b"; ctx.fill();
    ctx.fillStyle = "#fff"; ctx.textAlign = "left"; ctx.fillText(label, px + 7, py + 11);
    ctx.restore();
  }
  // 悬停/选中某节点时：点亮它的所有连线（按类型上色、加粗、发光 + 方向箭头），其余连线压暗——
  // 密集线团里一眼看清“这个节点连了哪些、流向哪”。静止时零额外动画、不打扰；试运行时让位给运行可视化。
  function _linkColor(A, l) {
    const os = A.outputs && A.outputs[l.origin_slot];
    return (os && LGraphCanvas.link_type_colors[os.type]) || "#9aa3af";
  }
  function _bezAt(p0, c0, c1, p1, t) {
    const u = 1 - t, a = u * u * u, b = 3 * u * u * t, c = 3 * u * t * t, d = t * t * t;
    return [a * p0[0] + b * c0[0] + c * c1[0] + d * p1[0], a * p0[1] + b * c0[1] + c * c1[1] + d * p1[1]];
  }
  function _drawDirArrow(ctx, pa, pb, color) {
    const cc = linkCtrlPts(pa, pb);
    const m = _bezAt(pa, cc[0], cc[1], pb, 0.5);
    const a0 = _bezAt(pa, cc[0], cc[1], pb, 0.44), a1 = _bezAt(pa, cc[0], cc[1], pb, 0.56);
    ctx.save(); ctx.translate(m[0], m[1]); ctx.rotate(Math.atan2(a1[1] - a0[1], a1[0] - a0[0]));
    ctx.fillStyle = color; ctx.beginPath(); ctx.moveTo(7, 0); ctx.lineTo(-5, -5); ctx.lineTo(-5, 5); ctx.closePath(); ctx.fill();
    ctx.restore();
  }
  function drawLinkFocus(ctx) {
    if (!graph || runSession || foldHidden.size) return;   // 试运行交给运行可视化；折叠态连线走箱体端口，先不掺和
    const ids = new Set();
    if (canvas && canvas.node_over) ids.add(canvas.node_over._id);   // 悬停
    for (const s of Object.values((canvas && canvas.selected_nodes) || {})) ids.add(s._id);   // 选中(可多选)，与悬停并集
    if (!ids.size) return;
    const links = graph.links || {}, hot = [], cold = [];
    for (const k in links) {
      const l = links[k]; if (!l) continue;
      const A = graph.getNodeById(l.origin_id), B = graph.getNodeById(l.target_id);
      if (!A || !B || foldHidden.has(A._id) || foldHidden.has(B._id)) continue;
      const pa = A.getConnectionPos(false, l.origin_slot, _t0), pb = B.getConnectionPos(true, l.target_slot, _t1);
      const rec = { pa: [pa[0], pa[1]], pb: [pb[0], pb[1]], col: _linkColor(A, l) };
      (ids.has(A._id) || ids.has(B._id) ? hot : cold).push(rec);
    }
    if (!hot.length) return;          // 焦点节点没连线就别打扰（也别凭空压暗整图）
    ctx.save();
    // ① 压暗其余连线：与底层连线【等宽】的半透明深色覆盖（融入深色背景）——不加宽、不发光、平头端，
    //    所以不会在连线四周留出深色描边(halo)，看起来就是“整条线变淡/半透明”。
    ctx.lineCap = "butt"; ctx.strokeStyle = "rgba(24,27,33,0.6)"; ctx.lineWidth = (canvas.connections_width || 3);
    for (const r of cold) {
      const cc = linkCtrlPts(r.pa, r.pb);
      ctx.beginPath(); ctx.moveTo(r.pa[0], r.pa[1]); ctx.bezierCurveTo(cc[0][0], cc[0][1], cc[1][0], cc[1][1], r.pb[0], r.pb[1]); ctx.stroke();
    }
    ctx.lineCap = "round";
    for (const r of hot) {            // ② 点亮焦点连线（类型色、加粗、发光）+ 方向箭头
      const cc = linkCtrlPts(r.pa, r.pb);
      ctx.strokeStyle = r.col; ctx.lineWidth = 3.5; ctx.shadowColor = r.col; ctx.shadowBlur = 8;
      ctx.beginPath(); ctx.moveTo(r.pa[0], r.pa[1]); ctx.bezierCurveTo(cc[0][0], cc[0][1], cc[1][0], cc[1][1], r.pb[0], r.pb[1]); ctx.stroke();
      ctx.shadowBlur = 0; _drawDirArrow(ctx, r.pa, r.pb, r.col);
    }
    ctx.restore();
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
    // ⑤ 数据输出值（彩色标签）——贴在输出端口右侧
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
    // ⑤b 数据输入值（回显）——贴在【输入端口左侧】，让长连线也能就近看到“喂进来的是多少”。略淡，区别于权威的输出值。
    for (const n of nodes) {
      if (foldHidden.has(n._id)) continue;
      const def = defByType[n._typeId]; if (!def) continue;
      (def.inputs || []).forEach((p, i) => {
        if (p.kind === "exec") return;
        const slot = n.inputs && n.inputs[i];
        if (!slot || slot.link == null) return;                       // 该输入没接线 → 无值可显
        const link = graph.links[slot.link]; if (!link) return;
        const A = graph.getNodeById(link.origin_id);
        if (!A || foldHidden.has(A._id)) return;
        const outSlot = A.outputs && A.outputs[link.origin_slot]; if (!outSlot) return;
        const key = A._id + RUNSEP + outSlot.name;
        if (!(key in runData)) return;                                // 上游本帧没产出这个值 → 不显
        const bp = n.getConnectionPos(true, i, _t0);
        drawValPill(ctx, bp[0] - 12, bp[1], runData[key], true, true);   // 右对齐贴端口左侧、略淡
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
        drawTimePill(ctx, n.pos[0], n.pos[0] + n.size[0], n.pos[1] - th - 3, tm[0], tm[1]);
      }
      drawFoldedTimePills(ctx);   // 折叠组：在子图箱体上汇总「组内自身耗时 · 组终累计」
    }
    drawBpHitMarker(ctx);          // ⑨ 命中断点暂停：醒目红环 + “已暂停”标牌（画在最上层）
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
      runLogs = []; _lastRunStatus = ""; _lastTick = 0; _appliedPW = {}; renderLog();
      if (runSession) {
        startRunAnim();                                  // 启动脉冲/流动动画
        try { api().run_set_breakpoints([...breakpoints], runUntil); } catch (e) {}   // 把断点同步给引擎
        try { api().run_set_profile(profileOn); } catch (e) {}   // 把「性能监控」开关同步给引擎
        try { api().run_set_preview(previewOn); } catch (e) {}   // 把「截图预览」开关同步给引擎
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
    if (execOuts.length >= 2) {            // 停在判断分支 = 这轮被它拦下、没执行后续操作
      // 门控类例行短路（开关关 / 队列正在造 / 资源·人口不足）每帧都会发生，是稳态常态、不该刷屏。
      // 约定：门控节点 id 以 "pre_" 开头、或描述以「门控」开头 → 不记日志（不在游戏/遮挡/被修饰键暂停等非门控原因仍照常记）。
      const note = (term._note || "").trim();
      if ((term._id || "").startsWith("pre_") || note.startsWith("门控")) return null;
      return { level: "INFO", msg: "本轮未操作 · " + branchStopReason(term) };
    }
    return { level: "INFO", msg: "本轮已完成一轮" };
  }
  // 处理一次轮询结果：刷新高亮状态 + 追加增量日志 + 命中断点则暂停（断点判定已在引擎侧精确完成）。
  function applyPoll(r) {
    if (!r) return;
    // 覆盖层（或别处）把引擎暂停了：编辑器同步成暂停态——停止轮询、按钮回到「继续」，画面定格在当帧供查看。
    if (r.paused && running && !r.bp_hit) {
      running = false; stopPoll(); setRunUI();
      setStatus("⏸ 已暂停 · 第 " + _lastTick + " 帧（可在覆盖层或此处点「继续」）");
      return;
    }
    const t = r.trace;
    if (t && t.tick === _lastTick && !(r.logs && r.logs.length) && !r.bp_hit) return;  // 帧未推进、无新日志/断点 → 跳过，免去高频轮询下的无谓重绘与集合重建
    if (t) {
      runPathArr = t.path || [];
      runPath = new Set(runPathArr);
      runPorts = t.ports || {};
      runData = t.data || runData;       // 无人观看时引擎可能略过 data；保留上次，避免标签闪烁
      runDataNodes = new Set(Object.keys(runData).map((k) => k.split(RUNSEP)[0]));
      runTimes = t.times || (profileOn ? runTimes : {});   // 仅在“性能监控”开启时引擎才附带耗时
      if (t.previews) runPreviews = t.previews;             // 截图预览：各感知节点截到的区域图(base64)
      else if (!previewOn) runPreviews = {};
      runPreviewLabels = t.preview_labels || (previewOn ? runPreviewLabels : {});   // 预览标签(置信度/识别值)，编辑器以清晰文字显示
      if (t.param_writes) applyRunParamWrites(t.param_writes);   // 把运行时自动改写(如设开关)落到编辑器控件+面板(试运行同样反映，便于调试；记为可恢复的改动)
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
      bpHitId = r.bp_hit;                 // 记下“停在哪”：drawRunOverlay 据此画醒目暂停高亮
      if (n && canvas) { try { canvas.centerOnNode(n); } catch (e) {} if (canvas) canvas.setDirty(true, true); }   // 居中到命中节点 + 整屏重绘（平移后背景/分组框也要刷新）
      setStatus("⏸ 命中断点：" + (n ? n.title : r.bp_hit) + " · 第 " + _lastTick + " 帧 ·（▶继续 / 取消该断点）");
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
    _runActionAt = Date.now();
    bpHitId = null;                                  // 继续/开始跑：清掉“停在断点”的高亮
    if (!(await ensureRunSession())) return;
    try { await api().run_resume(); } catch (e) {}   // 让后台引擎线程开始/继续跑
    running = true; setRunUI();
    startPoll();
  }
  function pauseRun() { _runActionAt = Date.now(); running = false; stopPoll(); try { api().run_pause(); } catch (e) {} setRunUI(); }

  // ——— 运行态常驻同步：后端引擎是“运行/暂停”的唯一真相，主界面据此对齐按钮与轮询。———
  // 这样【覆盖层】发起的 启动/暂停/继续 会实时反映到主界面（反之亦然）；也能“认领”从覆盖层冷启动的会话并显示其轨迹。
  let _runActionAt = 0;                 // 本界面刚发起过运行操作的时刻：短暂内不被同步覆盖，避免与后端结算赛跑
  function reconcileRunState(s) {
    if (!s || Date.now() - _runActionAt < 800) return;   // 本地动作 800ms 内让位，等后端结算稳定
    if (s.alive) {
      if (!runSession) {                // 引擎在跑但本界面还没认领（多半来自覆盖层「启动」）→ 认领该会话
        runSession = true; realRun = !!s.real;
        runLogs = []; _lastRunStatus = ""; _lastTick = 0; _appliedPW = {}; renderLog();
        startRunAnim();
        try { api().run_set_breakpoints([...breakpoints], runUntil); } catch (e) {}
        try { api().run_set_profile(profileOn); } catch (e) {}
        try { api().run_set_preview(previewOn); } catch (e) {}
      }
      if (s.paused) { if (running) { running = false; stopPoll(); } setRunUI(); }
      else if (!running) { running = true; setRunUI(); startPoll(); }   // 引擎在跑且未暂停 → 开始/恢复轮询轨迹
    } else if (runSession) {            // 引擎已停（外部 stop / 自然结束）→ 收尾，按钮回到「运行」
      running = false; stopPoll(); stopRunAnim(); runSession = false; bpHitId = null;
      runPath = new Set(); runPathArr = []; runPorts = {}; runData = {}; runDataNodes = new Set(); runTimes = {}; runPreviews = {}; runPreviewLabels = {};
      setRunUI(); if (canvas) canvas.setDirty(true, true);
    }
  }
  function startRunStateSync() {
    setInterval(async () => {
      let s = null; try { s = await api().run_state(); } catch (e) {}
      if (s) reconcileRunState(s);
    }, 400);
  }
  function toggleBreakpoint(id) {
    if (breakpoints.has(id)) breakpoints.delete(id); else breakpoints.add(id);
    if (bpHitId === id && !breakpoints.has(id)) bpHitId = null;   // 取消了正停在的那个断点：撤掉暂停高亮
    if (canvas) canvas.setDirty(true, true);
    if (runSession) { try { api().run_set_breakpoints([...breakpoints], runUntil); } catch (e) {} }
    setStatus(breakpoints.has(id) ? "已设断点 🔴（运行命中即暂停）" : "已取消断点");
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
    _runActionAt = Date.now();
    running = false; stopPoll(); stopRunAnim(); bpHitId = null;
    if (runSession) { try { await api().run_end(); } catch (e) {} runSession = false; }
    runPath = new Set(); runPathArr = []; runPorts = {}; runData = {}; runDataNodes = new Set(); runTimes = {}; runPreviews = {}; runPreviewLabels = {};
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
  // 截图预览开关：开启后运行/试运行时，感知节点(模板匹配/识别数字·文本/遮挡/建筑计数)在节点上显示“它截到的区域图”。
  function togglePreview() {
    previewOn = !previewOn;
    const b = document.getElementById("prevbtn");
    if (b) b.classList.toggle("on", previewOn);
    if (!previewOn) runPreviews = {};
    if (runSession) { try { api().run_set_preview(previewOn); } catch (e) {} }
    if (canvas) canvas.setDirty(true, true);
    setStatus(previewOn ? "已开启截图预览：运行/试运行时感知节点上显示“它截到的区域图”（用于核对截图范围）" : "已关闭截图预览");
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
  // 开/关游戏内覆盖层（透明毛玻璃窄条，悬在游戏上方）
  let overlayOpen = false;
  async function toggleOverlay() {
    try {
      const r = await api().toggle_overlay();
      overlayOpen = !!(r && r.open);
      const b = document.getElementById("overlaybtn");
      if (b) b.classList.toggle("on", overlayOpen);   // 与 ⏱耗时/🖼预览 一致：开启时蓝底高亮
      if (r && r.open === false && r.reason) showError("打开覆盖层失败：" + r.reason);
    } catch (e) { showError("打开覆盖层失败：" + (e && (e.stack || e.message) || e)); }
  }

  const self = {
    toggleRun, dryRun, stopRun, toggleProfile, togglePreview, toggleSimple, toggleSysMon, toggleOverlay,
    clearLog() { runLogs = []; renderLog(); },
    async save() {
      try {
        // 主动保存也弹「修改变化详情」预览（内置只读流程除外——它必走另存对话框）。
        if (!flowMeta.readonly) {
          const act = await confirmSave();
          if (act === "nochange") { setStatus("没有改动，无需保存"); return; }
          if (act !== "save") { setStatus("已取消保存"); return; }
        }
        const p = await api().save(collect());
        if (p) await afterSaved(p);
        setStatus(p ? `已保存 ${p}` : "已取消保存");
      } catch (err) { showError("保存失败：" + (err.stack || err)); }
    },
    async revealCurrent() {   // 在系统文件浏览器中定位当前流程文件（仅我的流程；未保存的则提示先另存）
      try {
        if (!flowMeta.path) { setStatus("当前流程尚未保存到文件——先「另存为」再定位"); return; }
        const r = await api().reveal_path(flowMeta.path);
        if (!(r && r.ok)) setStatus("定位失败：" + ((r && r.reason) || "未知"));
      } catch (e) { showError("定位失败：" + (e.stack || e)); }
    },
    async removeCurrent() {   // 从「我的流程」中删除当前流程文件（内置只读流程拒绝）
      try {
        if (!flowMeta.path || flowMeta.readonly) { setStatus("只能移除「我的流程」（内置流程为只读）"); return; }
        const name = flowMeta.name || (flowMeta.path.split(/[\\/]/).pop());
        if (!(await confirmAction("移除流程", "确定从「我的流程」中删除：\n" + name + "\n\n（删除的是磁盘上的流程文件，不可撤销）", "删除", true))) return;
        const r = await api().delete_flow(flowMeta.path);
        if (!(r && r.ok)) { setStatus("移除失败：" + ((r && r.reason) || "未知")); return; }
        setStatus("已移除「" + name + "」");
        try { fillFlowList(await api().list_builtin()); } catch (e) {}   // 刷新下拉
        // 当前流程已删 → 打开内置「统一生产」兜底（删的若不是当前流程则保持不变）
        try { const f = await api().open_path("flows/combined.flow.json"); if (f) load(f); } catch (e) {}
      } catch (e) { showError("移除失败：" + (e.stack || e)); }
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
    search: openSearch,
    viewChanges,
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
  // 把区域参数当前值 "l,t,r,b" 解析成 [l,t,r,b]（4 个有限数）；非法/空返回 null（框选时用于预显示当前框）。
  function parseBox(v) {
    const a = String(v == null ? "" : v).split(",").map((x) => parseInt(x, 10));
    return (a.length === 4 && a.every((n) => Number.isFinite(n))) ? a : null;
  }

  function setupHelpPanel() {
    helpEl = document.createElement("div");
    helpEl.id = "helpbox";
    helpEl.style.cssText = "position:absolute;right:10px;bottom:62px;max-width:340px;max-height:50%;" +
      "overflow:auto;background:#23272fee;color:#cfd3da;border:1px solid #3a404a;border-radius:6px;" +
      "padding:8px 10px;font:12px/1.6 'Microsoft YaHei',sans-serif;display:none;z-index:50;";
    document.body.appendChild(helpEl);
    canvas.onNodeSelected = (n) => { selectedNode = n; selectedGroupId = null; showNodeHelp(n); };   // 选节点即取消组选中
    canvas.onNodeDeselected = () => { selectedNode = null; if (!selectedGroupId) helpEl.style.display = "none"; };
  }

  // 选中节点的说明面板：只放“该节点专属”的内容（用途 + 需要解释的端口/参数），
  // 不重复通用的连线模型与操作说明——后者集中在顶部“帮助”里（见 self.help）。
  function portLine(p) {
    const tag = p.kind === "exec" ? "<span style='color:#ddd'>[执行]</span>"
                                  : "<span style='color:#7fbf7f'>[数据]</span>";
    return `<div style="margin-top:2px">${tag} <b style="color:#bcd">${esc(p.label || p.name)}</b>：${esc(p.help)}</div>`;
  }

  // 右下角说明各区段的“展开/折叠”记忆（节点切换/重绘时保持用户的展开状态）。
  let helpOpen = { params: true, ports: false, adv: false, pin: false, gexpose: true };
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

    // 显示到 控制面板 / 折叠节点：两列勾选——左=置顶到顶部面板；右=折叠该参数所属分组后在折叠箱体里显示可编辑控件。
    const pinnable = (d.params || []);
    if (pinnable.length) {
      let b = "<div style='color:#7f8895;margin-bottom:4px;font-size:12px'>左：置顶到顶部控制面板 ｜ 右：把该参数暴露给所在组（折叠该组后在组里可直接编辑；要再往上层显示需在组里继续勾选）。勾选任一后可填“显示名”，面板与折叠箱体共用同一个名字。</div>";
      for (const p of pinnable) {
        const pinned = isPinned(node._id, p.key), fpinned = isFoldPinned(node._id, p.key);
        b += `<div style="margin-top:3px;display:flex;align-items:center;gap:10px;flex-wrap:wrap">` +
             `<label style="cursor:pointer;color:#aeb6c2;flex:1 1 120px;min-width:120px">` +
             `<input type="checkbox" data-pin="${esc(p.key)}" ${pinned ? "checked" : ""}> ${esc(p.label)}</label>` +
             `<label style="cursor:pointer;color:#8fb6e0;white-space:nowrap" title="把该参数暴露给它所在的分组：折叠该组后在组里显示这个可编辑控件（需先把节点加入分组）。逐级封装：要再往上一层显示，在那个组的详情里继续勾选。">` +
             `<input type="checkbox" data-foldpin="${esc(p.key)}" ${fpinned ? "checked" : ""}> 暴露给所在组</label>`;
        if (pinned || fpinned) {   // 置顶或暴露给组任一勾选都显示“显示名”输入——两处共用同一个名字
          const cur = customLabel(node._id, p.key);
          b += `<input type="text" data-pinlabel="${esc(p.key)}" value="${esc(cur)}" ` +
               `placeholder="${esc(defaultPinLabel(node, p.key))}" title="显示名：控制面板与“暴露给所在组”折叠箱体共用同一个名字（留空＝用默认）" ` +
               `style="flex-basis:100%;background:#15171c;color:#cfd3da;border:1px solid #444;border-radius:3px;font-size:12px;padding:1px 4px">`;
        }
        b += `</div>`;
      }
      html += section("pin", "显示到 控制面板 / 暴露给所在组", b, null);
    }
    helpEl.innerHTML = html;
    helpEl.querySelectorAll("details[data-sec]").forEach((dt) => {
      dt.addEventListener("toggle", () => { helpOpen[dt.getAttribute("data-sec")] = dt.open; });
    });
    helpEl.querySelectorAll("[data-pin]").forEach((cb) => {
      cb.onchange = () => togglePin(node._id, cb.getAttribute("data-pin"));
    });
    helpEl.querySelectorAll("[data-foldpin]").forEach((cb) => {
      cb.onchange = () => toggleFoldPin(node._id, cb.getAttribute("data-foldpin"));
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

  // ====== 组的“选中”：组像节点一样可被选中（仅点标题处；折叠后整箱体即标题＝点哪都选）。======
  // 选中后右下角显示组的详情面板（改名/折叠/颜色/暴露参数/删除）。与节点选中互斥。
  function selectGroup(id) {
    selectedGroupId = id || null;
    if (selectedGroupId) {
      selectedNode = null;
      try { if (canvas && canvas.deselectAllNodes) canvas.deselectAllNodes(); } catch (e) {}   // 触发 onNodeDeselected→暂隐 help，下面再显示组面板
      showGroupHelp(groupById(selectedGroupId));
    } else if (helpEl) { helpEl.style.display = "none"; }
    if (canvas) canvas.setDirty(true, true);
  }
  // 选中组的详情面板（组≈节点/函数）：改名 / 折叠·展开 / 颜色 / 描述 / 克隆 /
  //   勾选“暴露给父组的参数”（只列【已暴露进本组接口】的参数，逐级向上、不冒泡）/ 解散·删除。
  function showGroupHelp(g) {
    if (!helpEl) return;
    if (simpleMode || !g) { helpEl.style.display = "none"; return; }
    const gi = groupDefs.indexOf(g); if (gi < 0) { helpEl.style.display = "none"; return; }
    const col = groupColor(g, gi);
    const direct = (g.members || []).map(nodeByOurId).filter(Boolean);
    const childN = childGroupsOf(g).length;
    const par = groupById(g.parent);
    let html = `<div style="display:flex;align-items:center;gap:6px;margin-bottom:2px">` +
      `<span style="width:12px;height:12px;border-radius:3px;background:${col};flex:none"></span>` +
      `<b style="color:#e6e9ee;flex:1">${esc(groupPathTitle(g))}</b>` +
      `<a href="#" data-grename="1" style="color:#7fb0ee;text-decoration:none;white-space:nowrap">[改名]</a></div>`;
    html += `<div style="color:#7f8895">直接成员 ${direct.length} 个` +
      `${childN ? "，子组 " + childN + " 个" : ""}` +
      `${par ? "，父组：" + esc(par.title || "分组") : "，顶层组"}</div>`;
    if (g.desc) html += `<div style="margin-top:3px;color:#9aa3af;white-space:pre-line">${esc(g.desc)}</div>`;
    html += `<div style="margin-top:6px;display:flex;align-items:center;gap:8px;flex-wrap:wrap">` +
      `<button data-gfold="1" style="background:${g.collapsed ? "#314a6b" : "#2f343d"};color:${g.collapsed ? "#cfe3ff" : "#cfd3da"};border:1px solid #444;border-radius:4px;padding:2px 9px;cursor:pointer">${g.collapsed ? "展开" : "折叠"}</button>` +
      `<button data-gclone="1" style="background:#2f343d;color:#cfd3da;border:1px solid #444;border-radius:4px;padding:2px 9px;cursor:pointer">克隆</button>` +
      `<button data-gdesc="1" style="background:#2f343d;color:#cfd3da;border:1px solid #444;border-radius:4px;padding:2px 9px;cursor:pointer">${g.desc ? "编辑描述" : "添加描述"}</button>` +
      `<label style="color:#9aa3af;display:flex;align-items:center;gap:4px">颜色 <input type="color" data-gcolor="1" value="${col}" style="width:26px;height:20px;padding:0;border:1px solid #444;background:#15171c;cursor:pointer"></label>` +
      `</div>`;
    // 暴露给父组的参数：只列【已暴露进本组接口】的参数（interfaceParams），勾选=再向上暴露一级（groupExpose）。
    const iface = interfaceParams(g);
    let pb = "";
    for (const it of iface) {
      const ex = isGroupExposed(g.id, it.nid, it.key);
      pb += `<label style="display:block;cursor:pointer;color:#aeb6c2;padding:1px 0">` +
        `<input type="checkbox" data-gexnid="${esc(it.nid)}" data-gexkey="${esc(it.key)}" ${ex ? "checked" : ""}> ` +
        `${esc((it.node.title || "节点") + "·" + it.label)}</label>`;
    }
    if (iface.length) html += section("gexpose", "暴露给父组的参数（再往上一级显示）", pb, iface.length);
    else html += `<div style="margin-top:6px;color:#7f8895">还没有参数暴露进本组——在子节点说明里勾「暴露给所在组」，或在子组里勾「暴露给父组」。</div>`;
    html += `<div style="margin-top:8px;border-top:1px solid #3a404a;padding-top:6px">` +
      `<a href="#" data-gdissolve="1" style="color:#7fb0ee;text-decoration:none">解散组（成员/子组上提到父层）</a><br>` +
      `<a href="#" data-gdelall="1" style="color:#ff9b9b;text-decoration:none">删除组及全部内容（连同内部节点）</a></div>`;
    helpEl.innerHTML = html;
    helpEl.querySelectorAll("details[data-sec]").forEach((dt) =>
      dt.addEventListener("toggle", () => { helpOpen[dt.getAttribute("data-sec")] = dt.open; }));
    const ren = helpEl.querySelector("[data-grename]");
    if (ren) ren.onclick = async (e) => { e.preventDefault(); const nm = await askText("分组改名", g.title || ""); if (nm != null) { g.title = nm.trim() || "分组"; refreshGroups(); showGroupHelp(g); } };
    const fold = helpEl.querySelector("[data-gfold]");
    if (fold) fold.onclick = () => { setGroupCollapsed(gi, !g.collapsed); showGroupHelp(g); };
    const clone = helpEl.querySelector("[data-gclone]");
    if (clone) clone.onclick = () => { const ng = cloneGroup(g); if (ng) selectGroup(ng.id); };
    const desc = helpEl.querySelector("[data-gdesc]");
    if (desc) desc.onclick = () => editGroupNote(g);
    const colInp = helpEl.querySelector("[data-gcolor]");
    if (colInp) colInp.oninput = () => { g.color = colInp.value; refreshGroups(); };
    helpEl.querySelectorAll("[data-gexnid]").forEach((cb) =>
      cb.onchange = () => { toggleGroupExpose(g.id, cb.getAttribute("data-gexnid"), cb.getAttribute("data-gexkey")); });
    const dis = helpEl.querySelector("[data-gdissolve]");
    if (dis) dis.onclick = (e) => { e.preventDefault(); dissolveGroup(g); selectGroup(null); setStatus("已解散组"); };
    const delAll = helpEl.querySelector("[data-gdelall]");
    if (delAll) delAll.onclick = (e) => {
      e.preventDefault();
      const cnt = groupAllMembers(g).length;   // 不弹确认：删除可 Ctrl+Z 整体恢复（节点+组都进撤销快照）
      deleteGroupAll(g); selectGroup(null);
      setStatus(`已删除组及全部 ${cnt} 个节点——可 Ctrl+Z 撤销`);
    };
    helpEl.style.display = "block";
  }
  // 删除组及其全部内容：删掉子树全体成员节点 + 该组与所有后代组本身。
  function deleteGroupAll(g) {
    if (!g) return;
    const memberIds = groupAllMembers(g), subtree = groupSubtreeIds(g);
    for (const id of memberIds) { const n = nodeByOurId(id); if (n) { try { graph.remove(n); } catch (e) {} } }   // onNodeRemoved 会清成员/折叠/面板项
    groupDefs = groupDefs.filter((x) => !subtree.has(x.id));
    refreshGroups();
  }
  // 克隆一个组（组≈节点：可整体复制成“蓝图”）：深拷贝子树全体节点 + 内部连线 + 子组结构 +
  // 暴露设置（foldPins/groupExpose）+ 描述，全部换新 id、整体偏移，挂到与原组同一父层。返回新组。
  function cloneGroup(g) {
    if (!g || !graph || simpleMode) return null;
    const OFF = 40;
    const subtreeIds = [...groupSubtreeIds(g)], memberIds = groupAllMembers(g);
    const memberSet = new Set(memberIds);
    // 新 id 映射（节点/组都换新，避免与现有冲突）
    const nodeIdMap = {}, nexist = new Set((graph._nodes || []).map((n) => n._id));
    for (const oid of memberIds) { let id, k = 0; do { id = oid + "_c" + (++k); } while (nexist.has(id)); nexist.add(id); nodeIdMap[oid] = id; }
    const grpIdMap = {}, gexist = new Set(groupDefs.map((x) => x.id));
    let gseq = 1;
    for (const gid of subtreeIds) { let id; do { id = "g" + (gseq++); } while (gexist.has(id)); gexist.add(id); grpIdMap[gid] = id; }
    // 1) 克隆节点
    const created = {};
    for (const oid of memberIds) {
      const src = nodeByOurId(oid); if (!src) continue;
      const key = typeKeyByType[src._typeId]; if (!key) continue;
      const n = LiteGraph.createNode(key); if (!n) continue;
      n._id = nodeIdMap[oid]; n._typeId = src._typeId; n._note = src._note || "";
      n.pos = [src.pos[0] + OFF, src.pos[1] + OFF];
      for (const w of (n.widgets || [])) {
        const sw = (src.widgets || []).find((x) => x._key === w._key);
        if (sw) { w.value = sw.value; n.properties[w._key] = sw.value; }
      }
      graph.add(n); created[n._id] = n;
    }
    // 2) 克隆【内部】连线（两端都在子树内；克隆与原节点同类型 → 槽位下标一致，可直接连）
    for (const l of Object.values(graph.links || {})) {
      if (!l) continue;
      const a = graph.getNodeById(l.origin_id), b = graph.getNodeById(l.target_id);
      if (!a || !b || !memberSet.has(a._id) || !memberSet.has(b._id)) continue;
      const na = created[nodeIdMap[a._id]], nb = created[nodeIdMap[b._id]];
      if (na && nb) { try { na.connect(l.origin_slot, nb, l.target_slot); } catch (e) {} }
    }
    // 3) 克隆组（子树）：根克隆挂到原组同父层，其余按映射重挂
    for (const gid of subtreeIds) {
      const src = groupById(gid);
      groupDefs.push({
        id: grpIdMap[gid], title: src.title, color: src.color, collapsed: !!src.collapsed,
        parent: (src === g) ? (g.parent || null) : (grpIdMap[src.parent] || null),
        members: (src.members || []).map((m) => nodeIdMap[m]).filter(Boolean),
        desc: src.desc || "", pos: src.pos ? [src.pos[0] + OFF, src.pos[1] + OFF] : null, size: src.size ? src.size.slice() : null,
      });
    }
    // 4) 克隆暴露设置
    for (const [nid, key] of foldPins.slice()) if (nodeIdMap[nid]) foldPins.push([nodeIdMap[nid], key]);
    for (const [gid, nid, key] of groupExpose.slice()) if (grpIdMap[gid] && nodeIdMap[nid]) groupExpose.push([grpIdMap[gid], nodeIdMap[nid], key]);
    refreshGroups(); scheduleSnap();
    setStatus("已克隆组「" + (g.title || "分组") + "」");
    return groupById(grpIdMap[g.id]);
  }

  // ===== 复制 / 粘贴 / 再制（Ctrl+C / Ctrl+V / Ctrl+D）=====
  let _clip = null, _clipPastes = 0;   // 会话级剪贴板（脱离实时引用的复制规格）+ 连续粘贴次数（每次再加偏移免叠在一起）
  // 把一批节点抽成“复制规格”（脱离实时引用：记类型/参数/位置/说明/直接所属组 + 内部连线 + 暴露参数）。
  function buildCopySpec(srcNodes) {
    const idSet = new Set(srcNodes.map((n) => n._id));
    const nodes = srcNodes.map((n) => ({
      oid: n._id, typeId: n._typeId, pos: [n.pos[0], n.pos[1]], note: n._note || "",
      params: Object.fromEntries((n.widgets || []).filter((w) => w._key).map((w) => [w._key, w.value])),
      gid: (groupDefs[nodeGroupIndex(n._id)] || {}).id || null,
    }));
    const edges = [];
    for (const l of Object.values(graph.links || {})) {
      if (!l) continue;
      const a = graph.getNodeById(l.origin_id), b = graph.getNodeById(l.target_id);
      if (!a || !b || !idSet.has(a._id) || !idSet.has(b._id)) continue;   // 仅复制【选区内部】连线
      edges.push([a._id, l.origin_slot, b._id, l.target_slot]);
    }
    const foldpins = foldPins.filter(([nid]) => idSet.has(nid)).map((p) => p.slice());
    return { nodes, edges, foldpins };
  }
  // 把“复制规格”实例化进图（换新 ourId、整体偏移 dx/dy）。keepGroups=true → 克隆并入与源相同的组（再制用）。返回新节点。
  function materializeSpec(spec, dx, dy, keepGroups) {
    if (!graph || simpleMode || !spec || !spec.nodes.length) return [];
    const idMap = {}, exist = new Set((graph._nodes || []).map((n) => n._id));
    for (const nd of spec.nodes) { let id, k = 0; do { id = nd.oid + "_c" + (++k); } while (exist.has(id)); exist.add(id); idMap[nd.oid] = id; }
    const created = {};
    for (const nd of spec.nodes) {
      const key = typeKeyByType[nd.typeId]; if (!key) continue;
      const nn = LiteGraph.createNode(key); if (!nn) continue;
      nn._id = idMap[nd.oid]; nn._typeId = nd.typeId; nn._note = nd.note || "";
      nn.pos = [nd.pos[0] + dx, nd.pos[1] + dy];
      for (const w of (nn.widgets || [])) {
        if (nd.params && Object.prototype.hasOwnProperty.call(nd.params, w._key)) { w.value = nd.params[w._key]; nn.properties[w._key] = nd.params[w._key]; }
      }
      graph.add(nn); created[nn._id] = nn;
    }
    for (const [so, ss, dt, dslot] of spec.edges) {   // 同类型克隆 → 槽位下标一致，可直接连
      const na = created[idMap[so]], nb = created[idMap[dt]];
      if (na && nb) { try { na.connect(ss, nb, dslot); } catch (e) {} }
    }
    if (keepGroups) for (const nd of spec.nodes) {
      const g = nd.gid ? groupById(nd.gid) : null;
      if (g && idMap[nd.oid]) { g.members = g.members || []; if (!g.members.includes(idMap[nd.oid])) g.members.push(idMap[nd.oid]); }
    }
    for (const [nid, pkey] of spec.foldpins) if (idMap[nid]) foldPins.push([idMap[nid], pkey]);
    refreshGroups(); scheduleSnap();
    return Object.values(created);
  }
  function selectNewNodes(nodes) {   // 复制/粘贴/再制后选中新节点（单个则顺带展开右下角说明）
    if (!canvas || !nodes.length) return;
    try { canvas.deselectAllNodes(); } catch (e) {}
    if (selectedGroupId) selectGroup(null);
    for (const n of nodes) { try { canvas.selectNode(n, true); } catch (e) {} }
    selectedNode = nodes.length === 1 ? nodes[0] : null;
    if (selectedNode) showNodeHelp(selectedNode);
    canvas.setDirty(true, true);
  }
  function selectedNodeList() { return Object.values((canvas && canvas.selected_nodes) || {}); }
  function copySelection() {
    const sel = selectedNodeList(); if (!sel.length) return false;
    _clip = buildCopySpec(sel); _clipPastes = 0;
    setStatus(`已复制 ${sel.length} 个节点（Ctrl+V 粘贴）`);
    return true;
  }
  function pasteClipboard() {
    if (!_clip || !_clip.nodes.length) { setStatus("剪贴板为空——先选中节点按 Ctrl+C"); return; }
    _clipPastes++;
    const made = materializeSpec(_clip, 36 * _clipPastes, 36 * _clipPastes, false);   // 粘贴=自由节点（不自动并入原组）
    selectNewNodes(made);
    setStatus(`已粘贴 ${made.length} 个节点`);
  }
  function duplicateSelection() {   // Ctrl+D：选中组→克隆组；选中节点→连内部线整体再制（保留分组归属）
    if (selectedGroupId) { const g = groupById(selectedGroupId); if (g) { const ng = cloneGroup(g); if (ng) selectGroup(ng.id); } return; }
    const sel = selectedNodeList(); if (!sel.length) { setStatus("没有选中节点——先点选要再制的节点"); return; }
    selectNewNodes(materializeSpec(buildCopySpec(sel), 32, 32, true));
    setStatus(`已再制 ${sel.length} 个节点`);
  }
  function selectAllNodes() {   // Ctrl+A：全选所有节点（取代 LiteGraph 自带的无提示全选）
    if (!graph || !canvas) return;
    try { canvas.deselectAllNodes(); for (const n of (graph._nodes || [])) canvas.selectNode(n, true); } catch (e) {}
    if (selectedGroupId) selectGroup(null);
    canvas.setDirty(true, true);
    setStatus(`已全选 ${(graph._nodes || []).length} 个节点`);
  }
  // 编辑组的描述（仅展示、不参与运行）。
  function editGroupNote(g) {
    if (!g) return;
    document.getElementById("notedlg")?.remove();
    const box = document.createElement("div");
    box.id = "notedlg"; box.className = "popdlg";
    box.style.cssText = "position:absolute;left:50%;top:46px;transform:translateX(-50%);width:min(420px,90vw);" +
      "background:#23272f;color:#cfd3da;border:1px solid #3a404a;border-radius:8px;padding:14px 16px;z-index:130;" +
      "box-shadow:0 8px 30px #000a;font:13px/1.6 'Microsoft YaHei',sans-serif;";
    box.innerHTML = "<b style='color:#e6c07b'>分组描述</b>（说明这个分组/封装块的作用，仅展示）<br>" +
      "<textarea id='notetext' style='width:100%;height:84px;margin-top:8px;background:#15171c;color:#cfd3da;" +
      "border:1px solid #444;border-radius:4px;padding:6px;font:13px/1.5 \"Microsoft YaHei\",sans-serif;box-sizing:border-box'></textarea>" +
      "<div style='margin-top:8px;text-align:right'>" +
      "<button id='noteok' style='background:#2f343d;color:#cfd3da;border:1px solid #444;border-radius:4px;padding:3px 12px;cursor:pointer'>确定</button> " +
      "<button id='notecancel' style='background:#2f343d;color:#cfd3da;border:1px solid #444;border-radius:4px;padding:3px 12px;cursor:pointer'>取消</button></div>";
    document.body.appendChild(box);
    const ta = box.querySelector("#notetext"); ta.value = g.desc || ""; ta.focus();
    box.querySelector("#noteok").onclick = () => { g.desc = ta.value.trim(); box.remove(); scheduleSnap(); refreshDirty(); if (selectedGroupId === g.id) showGroupHelp(g); };
    box.querySelector("#notecancel").onclick = () => box.remove();
  }
  // 组的右键菜单（在组标题/折叠箱体上右键弹出）：与节点行为对齐——折叠 / 改名 / 描述 / 克隆 / 再分组 / 分组管理 / 解散 / 删除。
  // 给 LiteGraph 弹出菜单补「点菜单外任意处即关闭」：原生菜单只在点中项/点菜单背景时关，
  // 从弹窗里弹出的菜单点别处不会消失（用户反馈：分组管理里「归入」菜单点别处不关）。
  function closeMenuOnOutside(menu) {
    if (!menu || !menu.root) return;
    const onDown = (ev) => {
      if (menu.root && menu.root.contains(ev.target)) return;   // 点在菜单内：交给菜单自身处理
      try { menu.close(); } catch (e) {}
      document.removeEventListener("pointerdown", onDown, true);
    };
    setTimeout(() => document.addEventListener("pointerdown", onDown, true), 0);   // 延后挂载，避免捕获到“打开它”的这一次点击
  }
  function showGroupMenu(g, e) {
    const gi = groupDefs.indexOf(g); if (gi < 0) return;
    const items = [
      { content: g.collapsed ? "展开" : "折叠", callback: () => setGroupCollapsed(gi, !g.collapsed) },
      { content: "改名…", callback: async () => { const nm = await askText("分组改名", g.title || ""); if (nm != null) { g.title = nm.trim() || "分组"; refreshGroups(); if (selectedGroupId === g.id) showGroupHelp(g); } } },
      { content: g.desc ? "编辑描述…" : "添加描述…", callback: () => editGroupNote(g) },
      { content: "克隆组", callback: () => { const ng = cloneGroup(g); if (ng) selectGroup(ng.id); } },
      { content: "再分组（归入组…）", callback: () => reparentGroupDialog(g, e) },
      null,
      { content: "分组管理…", callback: () => assignGroupDialog([]) },
      { content: "解散组（成员/子组上提到父层）", callback: () => { dissolveGroup(g); if (selectedGroupId === g.id) selectGroup(null); setStatus("已解散组"); } },
      { content: "删除组及全部内容（连同内部节点）", callback: () => { const cnt = groupAllMembers(g).length; deleteGroupAll(g); if (selectedGroupId === g.id) selectGroup(null); setStatus(`已删除组及全部 ${cnt} 个节点——可 Ctrl+Z 撤销`); } },
    ];
    closeMenuOnOutside(new LiteGraph.ContextMenu(items, { event: e, title: "分组：" + (g.title || "分组") }));
  }
  // 「再分组」：把某组归入另一个组（=设父组），下拉选目标（含“顶层”），防环。
  function reparentGroupDialog(g, e) {
    if (!g) return;
    const opts = [{ content: "▲ 顶层（不归任何组）", callback: () => { setGroupParent(g, null); setStatus("已移到顶层"); } }];
    for (const t of groupDefs) {
      if (t === g || isDescendantGroup(t, g)) continue;   // 不能归入自己或自己的后代
      opts.push({ content: groupPathTitle(t), callback: () => { setGroupParent(g, t.id); setStatus(`已把「${g.title || "分组"}」归入「${t.title || "分组"}」`); } });
    }
    closeMenuOnOutside(new LiteGraph.ContextMenu(opts, { event: e, title: "归入组：" + (g.title || "分组") }));
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
      "· <b>快捷键</b>：<b>Ctrl+S</b> 保存（弹改动详情预览）　<b>Ctrl+Shift+S</b> 另存为　<b>Ctrl+Z</b> 撤销　<b>Ctrl+Y</b> / <b>Ctrl+Shift+Z</b> 重做　<b>Ctrl+C</b> 复制　<b>Ctrl+V</b> 粘贴　" +
      "<b>Ctrl+D</b> 再制（选中组则克隆整组）　<b>Ctrl+A</b> 全选节点　<b>Ctrl+F</b> 图内搜索（找节点/分组并定位）　<b>Delete</b> 删除（选中组＝解散该组）<br>" +
      "<span style='color:#7f8895'>　复制/再制会连同选区内部连线、参数、说明、暴露设置一起带走；再制保留原分组归属，粘贴为自由节点。</span><br>" +
      "· 顶部按钮：自动排版（重新理顺布局）/ 适应窗口 / 保存（都会先弹「修改变化详情」窗口预览要保存的改动）/ <b>📋 查看修改</b>（随时查看当前所有改动，可逐条恢复）/ <b>📁 定位</b>（在文件浏览器中显示当前流程文件）<br>" +
      "· 选中节点→右下角说明里勾选「显示到控制面板」，把常用开关/数值置顶到顶部面板，" +
      "普通使用时不必进节点图也能调（🎯 定位到节点）<br>" +
      "· <b>分组＝把若干节点封装成一个“函数块”</b>：右键节点→「分组…」把它(或多选)归入一个彩色分组；" +
      "节点标题栏染成组色、框自动包住它们；一个节点只属于一个组。<br>" +
      "　- <b>嵌套</b>：按住 <b>Alt</b> 拖节点/子组到另一个组框里＝放进去，拖到空白＝移出（子组当作一个节点看待）。<br>" +
      "　- <b>选中组</b>：点组标题栏即选中（右下角出详情：改名/颜色/描述/克隆/再分组/解散/删除；描述会显示在组名旁）。<br>" +
      "　- <b>折叠 ⊟ / 展开 ⊞</b>：把一个组收成紧凑“子图节点”——隐藏内部、跨边界连线汇成箱体端口；单击标题右端图标或双击切换。<br>" +
      "　- <b>逐级暴露参数</b>：子节点说明里勾「暴露给所在组」→该参数出现在本组折叠箱里；选中组再勾「暴露给父组」才会往上一层显示（不冒泡，像函数封装）。<br>" +
      "　- 顶部/组标题右键「<b>分组管理</b>」窗口：按嵌套缩进总览所有组，可改名/折叠/归入/解散、新建空组。<br>" +
      "· <b>🖼 预览</b>：开启后运行/试运行时，感知节点(模板匹配/识别数字·文本/遮挡/建筑计数)上方显示「它实际截到的区域图」——一眼核对截图范围对没对准（如黄金/食物数字框歪了立刻看出来）<br>" +
      "· 顶部「流程信息」查看名称/说明/统计，点其中「编辑」可改名称与说明<br>" +
      "· <b>使用模式</b>（顶部按钮切换）：把画布转为<b>只读</b>——仍可拖动查看、运行、用控制面板调参，但不能增删改节点/连线/参数；" +
      "其中的调参与拖动都是临时的，<b>不会改动已保存的流程</b>，回到编辑模式即还原（适合“只想用”的场景）</div>" +
      "<div style='border-top:1px solid #3a404a;margin-top:10px;padding-top:8px;color:#9aa3af'>" +
      "<b style='color:#e6c07b'>运行 / 试运行</b>　顶部 <b style='color:#8fe0a8'>▶运行</b> / 停止；试运行(干跑)在编辑模式下可见<br>" +
      "· <b style='color:#8fe0a8'>运行</b>：执行当前流程，<b>真正向游戏发按键/鼠标</b>（再点变「暂停」，运行中可按住 Shift/Ctrl/Alt 暂停）。<br>" +
      "· <b>试运行＝干跑</b>：只识别、<b>不发任何输入</b>，安全；走过的节点高亮、连线流动、<b>端口旁显示当前数据值（输出在右、输入回显在左）</b>、底部出日志，用于上线前核对。<br>" +
      "· 每帧间隔＝流程「每帧触发」节点的「循环间隔(秒)」(面板可调)。<br>" +
      "· 走过的<b>分支</b>节点标「真/假」=走了哪条线；执行<b>走到头</b>的出口标「⏹ 结束」(出口空接=本帧到此)。<br>" +
      "· <b>断点</b>：右键节点→「设为断点 🔴」（标题左上角红点）。运行命中它就<b>自动暂停</b>：视图会<b>居中到该节点</b>并套上<b>红色脉冲环 +「⏸ 已暂停」标牌</b>，一眼看出停在哪一步；点 <b>▶继续</b> 往下跑，或取消该断点。（断点仅作用于节点，是会话级、不随流程保存）<br>" +
      "· 底部日志每行带<b>时间</b>与等级(• 信息 / ▲ 提醒 / ✕ 错误)，可<b>拖上边沿调高度</b>、<b>选中复制</b>；上滚查看时不会被新日志拽回底部。<br>" +
      "· 右上角<b>资源监控</b>小窗显示本工具 CPU/内存曲线，点开可看系统占用与调采样间隔/保留时长。</div>" +
      "<div style='border-top:1px solid #3a404a;margin-top:10px;padding-top:8px;color:#9aa3af'>" +
      "<b style='color:#e6c07b'>在游戏画面上直接采集</b>（节点里相应参数下方的按钮）<br>" +
      "· <b>框选区域…</b>：会<b>预显示该参数当前的框</b>，可<b>框内拖动=移动</b>、<b>拖边/角手柄=改大小</b>、空白拖动=重画，Enter 确认（区域参数）<br>" +
      "· <b>取点…/吸色…</b>：移动有放大镜，点一下取坐标/颜色（吸色会顺带回填配套坐标）<br>" +
      "· <b>截取模板…</b>：框选游戏画面裁出小图存为模板（图片参数）<br>" +
      "· <b>选择图片…</b>：从已有图片文件选模板<br>" +
      "· <b>捕获按键…</b>：在编辑器内弹小窗，按一下记下按键（Esc 取消；不会最小化窗口、也不会弹多个）<br>" +
      "<span style='color:#7f8895'>截图类（框选/取点/吸色/截模板）点按钮后编辑器会自动最小化让开、截到游戏画面，采完自动恢复；Esc 取消。</span></div>";
    document.body.appendChild(helpModal);
  }

  // ---- 撤销/重做（对整图做 JSON 快照；buildGraph/applySnapshot 期间抑制）----
  function snapshotNow() {
    if (suppressSnap || building || !graph) return;
    try { api().set_run_payload(collect()); } catch (e) {}   // 当前图实时登记到后端：覆盖层「启动」直接跑当前图（含未保存改动）
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
    pushChangeSummary(isDirty());   // 编辑过程中保持“改动清单”最新（debounce 后调），让退出确认详情不过时
    // 选中的节点参数有改动时，同步刷新右下角“已修改”列表（橙点本就实时随重绘更新）
    if (selectedNode && helpEl && helpEl.style.display !== "none") showNodeHelp(selectedNode);
    renderPanel();   // 面板控件值与节点保持同步
    if (runSession) { try { api().run_update(collect()); } catch (e) {} }   // 试运行中：参数热更新到引擎
  }
  // “查看修改”窗：图上任何【外部】改动即自动关闭它（弹窗内的「恢复」例外，靠 _changeDlgRestoring 区分）。
  let _changeDlgClose = null, _changeDlgRestoring = false;
  function maybeCloseChangeDlgOnEdit() { if (_changeDlgClose && !_changeDlgRestoring) _changeDlgClose("cancel"); }
  function scheduleSnap() { maybeCloseChangeDlgOnEdit(); clearTimeout(snapTimer); snapTimer = setTimeout(snapshotNow, 250); }
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
    pinLabels = {};   // 自定义显示名权威存储随撤销/重做恢复（含未置顶但暴露给组的）
    if (data.labels && typeof data.labels === "object") for (const k in data.labels) if (data.labels[k]) pinLabels[k] = String(data.labels[k]);
    for (const p of panelPins) if (p[2] && !pinLabels[p[0] + "|" + p[1]]) pinLabels[p[0] + "|" + p[1]] = p[2];
    foldPins = Array.isArray(data.foldparams) ? data.foldparams.map((x) => x.slice(0, 2)) : [];   // 暴露给所在组的参数随撤销/重做恢复
    groupExpose = Array.isArray(data.groupexpose) ? data.groupexpose.map((x) => x.slice(0, 3)) : [];   // 组向上暴露随撤销/重做恢复
    groupDefs = Array.isArray(data.groups) ? normalizeGroups(data.groups) : [];   // 分组（含 id/parent/折叠态）随撤销/重做恢复
    refreshFold();
    applyGroupColors();
    if (selectedGroupId && groupById(selectedGroupId)) showGroupHelp(groupById(selectedGroupId));   // 撤销/重做后组面板开着则刷新
    else if (selectedGroupId) { selectedGroupId = null; if (helpEl) helpEl.style.display = "none"; }
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
    _lastMenuPos = [off[0], off[1]];   // 记下右键位置，供画布菜单“新建组”定位
    if (graph.getNodeOnPos(off[0], off[1], canvas.visible_nodes)) return;  // 节点上交给 LiteGraph
    // 命中组标题/折叠箱体 → 组专属右键菜单（最内层优先）
    const gorder = groupDefs.map((g, i) => i).sort((a, b) => groupAllMembers(groupDefs[a]).length - groupAllMembers(groupDefs[b]).length);
    for (const i of gorder) {
      const r = groupTabRect(groupDefs[i]); if (!r) continue;
      if (off[0] >= r[0] && off[0] <= r[0] + r[2] && off[1] >= r[1] && off[1] <= r[1] + r[3]) {
        e.preventDefault(); e.stopImmediatePropagation();
        try { LiteGraph.closeAllContextMenus(window); } catch (err) {}
        showGroupMenu(groupDefs[i], e);
        return;
      }
    }
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
  let altKeyDown = false;   // 按住 Alt 才把「拖动」当作「拖进/拖出分组」；否则只是普通移动（见 onNodeMoved / onGroupDragUp）
  function _measCtx() { return (canvas && canvas.canvas) ? canvas.canvas.getContext("2d") : null; }
  function groupTabRect(g) {
    const ctx = _measCtx(); if (!ctx) return null;
    if (g.collapsed) {                            // 折叠态：整个箱体都是拖动手柄（像普通节点，空白处即可拖动整组）
      if (isInsideCollapsed(g)) return null;      // 嵌套在折叠父组里：不画箱体也就没有手柄
      const sb = subgBox(g, ctx); if (!sb) return null;
      return [sb.x, sb.y, sb.w, sb.h];            // 折叠参数的 DOM 控件在更高层(pointer-events:auto)，点控件不会触发拖动
    }
    const _mm = g.members || [];
    if (_mm.length && _mm.every((m) => foldHidden.has(m))) return null;   // 成员整体被折叠父组隐藏：无可见框（空组照常有框）
    const box = expandedGroupBox(g, ctx); if (!box) return null;
    ctx.font = "bold 13px 'Microsoft YaHei',sans-serif";
    const tw = ctx.measureText(groupTabText(g)).width;
    return [box[0], box[1] - 18, tw + 18, 20];   // 与 drawGroups 标签页几何一致
  }
  // 命中测试：返回坐标落在哪个【折叠箱体】内的组下标（用于双击展开）；无则 -1。
  function foldedBoxAt(gx, gy) {
    const ctx = _measCtx(); if (!ctx) return -1;
    for (const { g, i } of topCollapsedGroups()) {
      const b = subgBox(g, ctx); if (!b) continue;
      if (gx >= b.x && gx <= b.x + b.w && gy >= b.y && gy <= b.y + b.h) return i;
    }
    return -1;
  }
  // 标签页/箱体标题栏【最右侧】的折叠/展开图标命中区（⊟ 折叠 / ⊞ 展开）——单击即切换，不必进任何弹窗。
  function groupIconRect(g) {
    const r = groupTabRect(g); if (!r) return null;
    const iw = Math.min(GROUP_ICON_W, r[2]);
    return [r[0] + r[2] - iw, r[1], iw, r[3]];
  }
  // 命中测试：坐标是否落在某个分组的“折叠/展开图标”上；返回组下标，无则 -1。
  function foldIconAt(gx, gy) {
    for (let i = 0; i < groupDefs.length; i++) {
      const r = groupIconRect(groupDefs[i]); if (!r) continue;
      if (gx >= r[0] && gx <= r[0] + r[2] && gy >= r[1] && gy <= r[1] + r[3]) return i;
    }
    return -1;
  }
  // 命中测试：坐标是否落在任一分组的标签页/折叠箱体标题（可拖动整组的“手柄”）上。
  function overGroupHandle(gx, gy) {
    for (let i = 0; i < groupDefs.length; i++) {
      const r = groupTabRect(groupDefs[i]); if (!r) continue;
      if (gx >= r[0] && gx <= r[0] + r[2] && gy >= r[1] && gy <= r[1] + r[3]) return true;
    }
    return false;
  }
  // 折叠 / 展开某个分组（i=组下标）。折叠＝把该组收成一个紧凑子图节点；展开＝还原成员节点视图。
  function setGroupCollapsed(i, collapsed) {
    const g = groupDefs[i]; if (!g) return;
    g.collapsed = !!collapsed;
    refreshFold();
    if (canvas) canvas.setDirty(true, true);
    scheduleSnap(); refreshDirty();
    setStatus(g.collapsed ? `已折叠「${g.title || "分组"}」（单击 ⊞ 或双击展开）` : `已展开「${g.title || "分组"}」`);
  }
  function onCanvasDblClick(e) {
    if (!graph || !canvas) return;     // 折叠/展开是纯视图操作，使用模式（只读）下也允许
    let off; try { off = canvas.convertEventToCanvasOffset(e); } catch (err) { return; }
    if (graph.getNodeOnPos(off[0], off[1], canvas.visible_nodes)) return;   // 节点上交给 LiteGraph
    const gi = foldedBoxAt(off[0], off[1]);                                  // 双击折叠箱体 → 展开
    if (gi >= 0) { e.preventDefault(); e.stopImmediatePropagation(); setGroupCollapsed(gi, false); return; }
    for (let i = 0; i < groupDefs.length; i++) {                             // 双击展开态分组标签 → 折叠
      if (groupDefs[i].collapsed) continue;
      const r = groupTabRect(groupDefs[i]); if (!r) continue;
      if (off[0] >= r[0] && off[0] <= r[0] + r[2] && off[1] >= r[1] && off[1] <= r[1] + r[3]) {
        e.preventDefault(); e.stopImmediatePropagation(); setGroupCollapsed(i, true); return;
      }
    }
  }
  function onGroupDragDown(e) {
    if (e.button !== 0 || !graph || !canvas) return;
    let off; try { off = canvas.convertEventToCanvasOffset(e); } catch (err) { return; }
    if (graph.getNodeOnPos(off[0], off[1], canvas.visible_nodes)) { if (selectedGroupId) selectGroup(null); return; }   // 点节点→取消组选中，交给 LiteGraph
    const ii = foldIconAt(off[0], off[1]);                                   // 先：单击右端 ⊟/⊞ 图标 → 折叠/展开（两种模式都可用）
    if (ii >= 0) { e.preventDefault(); e.stopImmediatePropagation(); setGroupCollapsed(ii, !groupDefs[ii].collapsed); return; }
    // 使用模式：允许拖动整组移动位置(查看用，不落盘)；改父组归属(Alt)仍在 onGroupDragMove/Up 里被 simpleMode 拦下。
    // 命中手柄时按【最内层】优先（子树成员少的先判定），这样嵌套时能抓到里层子组而不是外层父组。
    const order = groupDefs.map((g, i) => i).sort((a, b) => groupAllMembers(groupDefs[a]).length - groupAllMembers(groupDefs[b]).length);
    for (const i of order) {
      const r = groupTabRect(groupDefs[i]); if (!r) continue;
      if (off[0] >= r[0] && off[0] <= r[0] + r[2] && off[1] >= r[1] && off[1] <= r[1] + r[3]) {
        e.preventDefault(); e.stopImmediatePropagation();
        selectGroup(groupDefs[i].id);   // 一按下手柄就选中该组（与节点一致：拖动即选中，右下角随即出详情）
        const members = groupAllMembers(groupDefs[i]).map(nodeByOurId).filter(Boolean);   // 拖整组＝移动其【子树全体】节点
        _groupDrag = { gi: i, last: off, members, moved: false };
        window.addEventListener("pointermove", onGroupDragMove, true);
        window.addEventListener("pointerup", onGroupDragUp, true);
        return;
      }
    }
    if (selectedGroupId) selectGroup(null);   // 点在空白处（非节点/非组手柄）：取消组选中
  }
  function onGroupDragMove(e) {
    if (!_groupDrag) return;
    let off; try { off = canvas.convertEventToCanvasOffset(e); } catch (err) { return; }
    const dx = off[0] - _groupDrag.last[0], dy = off[1] - _groupDrag.last[1];
    if (dx || dy) {
      for (const n of _groupDrag.members) { n.pos[0] += dx; n.pos[1] += dy; }
      // 空组（子树无任何节点）没有成员可移动——直接挪它的锚点框 pos，让空组能被拖动
      const dg = groupDefs[_groupDrag.gi];
      if (dg) { const sub = groupSubtreeIds(dg); for (const g2 of groupDefs) if (sub.has(g2.id) && g2.pos && !groupAllMembers(g2).length) { g2.pos = [g2.pos[0] + dx, g2.pos[1] + dy]; } }
      _groupDrag.last = off; _groupDrag.moved = true;
      if (canvas) canvas.setDirty(true, true);
    }
    // Alt 拖组：把整组从【祖先】框里摘出（自身/子组保留＝随组移动），并高亮将落入的目标组。（改归属＝改图，使用模式不允许）
    if (e.altKey && !simpleMode) {
      const g = groupDefs[_groupDrag.gi];
      if (g) {
        _altDrag = { kind: "group", detached: new Set(groupAllMembers(g)), keepIds: groupSubtreeIds(g), targetGi: -1 };
        _altDrag.targetGi = innermostGroupAt(off[0], off[1], g);
        if (canvas) canvas.setDirty(true, true);
      }
    } else if (_altDrag && _altDrag.kind === "group") { _altDrag = null; if (canvas) canvas.setDirty(true, true); }
  }
  function onGroupDragUp() {
    window.removeEventListener("pointermove", onGroupDragMove, true);
    window.removeEventListener("pointerup", onGroupDragUp, true);
    const gd = _groupDrag; _groupDrag = null;
    const ad = _altDrag; const wasAlt = !!_altDrag; _altDrag = null;   // 先捕获再清空 Alt 拖拽态（恢复源组框跟随）
    if (canvas) canvas.setDirty(true, true);
    if (!gd) return;
    if (!gd.moved) { const cg = groupDefs[gd.gi]; selectGroup(cg && cg.id); return; }   // 只点不拖（点标题/折叠箱体）＝选中该组
    const g = groupDefs[gd.gi];
    if (g && !simpleMode && (altKeyDown || wasAlt)) {
      // 【按住 Alt】放下才改父组：落在某个组内 → 设为其子组（最内层那个）；落在空白 → 设为顶层。否则只是移动。
      // 用拖拽时实时算好的落点：那时祖先框已排除本组成员，不会因清空 _altDrag 后“回弹”把子组又判回原父组（=子组拖不进别的组）。
      const ti = (ad && ad.kind === "group") ? ad.targetGi : innermostGroupAt(gd.last[0], gd.last[1], g);
      const tTitle = ti >= 0 ? (groupDefs[ti].title || "分组") : null;
      setGroupParent(g, ti >= 0 ? groupDefs[ti].id : null);
      setStatus(tTitle ? `已把「${g.title || "分组"}」嵌入「${tTitle}」` : `「${g.title || "分组"}」移到顶层`);
    } else { scheduleSnap(); }
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
      canvas.render_connection_arrows = true;    // 每条连线中段画一个方向箭头，一眼看出数据/执行的流向
      canvas.render_curved_connections = true;   // 箭头朝向按【曲线切线】算（连线本就是样条）；否则会退化成纯上/下的竖直朝向 → 方向看着不对
      canvas.render_canvas_border = false;  // 不画画布边框（背景里那条蓝色细线矩形）
      canvas.node_title_color = "#e3e7ee";  // 默认标题字调亮（原 #999 偏灰、看不清）
      canvas.onDrawBackground = drawGroups;   // 在节点后面画“分组框”（随成员自动包裹）/ 折叠态画“子图箱体”
      canvas.onDrawForeground = (ctx) => { drawRunOverlay(ctx); };   // 节点之上只放“试运行”高亮/数据/流动；连线(含悬停聚焦)一律画在节点【后面】
      // 悬停的节点变化时强制重绘前景，让“聚焦关联连线”实时跟手（选中变化 LiteGraph 本就会重绘）
      let _lastHoverId = null;
      canvas.canvas.addEventListener("mousemove", () => {
        const id = (canvas && canvas.node_over) ? canvas.node_over._id : null;
        if (id !== _lastHoverId) { _lastHoverId = id; if (!runSession && canvas) canvas.setDirty(true, true); }
      });
      // 折叠子图：用自定义连线绘制——跳过组内连线、把跨边界连线改接到箱体端口；无折叠时走原版。
      const _origDrawConn = canvas.drawConnections;
      canvas.drawConnections = function (ctx) {
        if (!foldHidden.size) _origDrawConn.call(this, ctx);
        else drawFoldedConnections.call(this, ctx);
        drawLinkFocus(ctx);   // 悬停/选中的“聚焦连线(点亮+压暗其余)”跟基础连线一起画在【节点后面】，绝不再盖住节点
      };
      setupHelpPanel();
      setupLogResize();
      // 撤销触发点：连线变化 / 增删节点 / 移动节点（参数改动在 addParamWidget 的回调里）
      graph.onConnectionChange = scheduleSnap;
      graph.onNodeAdded = scheduleSnap;
      // 删除节点：除快照外，隐藏右下角说明面板（否则被删节点的说明会残留）
      graph.onNodeRemoved = () => {
        panelPins = panelPins.filter((p) => graph._nodes.some((n) => n._id === p[0]));   // 删节点连带移除其面板项
        foldPins = foldPins.filter((p) => graph._nodes.some((n) => n._id === p[0]));     // 删节点连带移除其“暴露给所在组”
        groupExpose = groupExpose.filter((e) => graph._nodes.some((n) => n._id === e[1]));   // 删节点连带移除组对它的“向上暴露”
        const ids = new Set(graph._nodes.map((n) => n._id));
        breakpoints = new Set([...breakpoints].filter((id) => ids.has(id)));   // 删节点连带移除其断点
        if (runUntil && !ids.has(runUntil)) runUntil = null;
        // 清理悬空连线：删节点时，其“多条汇入 exec 输入”里 LiteGraph 没自动清的那些会变悬空，手动剔除
        for (const k in graph.links) {
          const l = graph.links[k];
          if (l && (!graph.getNodeById(l.origin_id) || !graph.getNodeById(l.target_id))) delete graph.links[k];
        }
        for (const g of groupDefs) g.members = (g.members || []).filter((m) => ids.has(m));
        // 支持空组：删节点后即使组变空也保留（用户可再拖节点回去；要删组走解散/删除菜单）
        refreshFold();
        renderPanel();
        scheduleSnap(); selectedNode = null; if (helpEl) helpEl.style.display = "none";
      };
      canvas.onNodeMoved = (node) => {
        // 仅【按住 Alt】拖放才改归属：拖到某展开组框内 → 成为其直接成员；拖到空白→移出。否则只是移动位置。
        // 与“拖组进组”一致：落点决定归属。多选则随主拖动节点一起归到同一组。
        const ad = _altDrag; const wasAlt = !!_altDrag; _altDrag = null;   // 先捕获再清空 Alt 拖拽态
        if (node && !simpleMode && (altKeyDown || wasAlt)) {
          const sel = Object.values((canvas && canvas.selected_nodes) || {});
          const moved = (sel.length > 1 && sel.includes(node)) ? sel : [node];
          // 用拖拽过程中【实时算好】的落点（那时 groupBox 已排除被拖成员，源/祖先框不会回弹把它“吸回”）；
          // 不要在清空 _altDrag 后再 nodeDropGroupIndex——那样源组框已恢复跟随、会把刚拖出的节点又判回源组（=拖不出去）。
          const ti = (ad && ad.kind === "node") ? ad.targetGi : nodeDropGroupIndex(node);
          if (!moved.every((nd) => nodeGroupIndex(nd._id) === ti)) {
            setNodesDirectGroup(moved.map((n) => n._id), ti >= 0 ? ti : null);   // 内含 refreshGroups（已计快照）
            return;
          }
        }
        scheduleSnap();
      };
      // 右键任意位置点中连线 -> 删除连线菜单（捕获阶段，先于 LiteGraph 的右键菜单）
      canvas.canvas.addEventListener("pointerdown", onRightDown, true);
      canvas.canvas.addEventListener("pointerdown", onGroupDragDown, true);   // 左键拖“分组标签页”整体移动该组
      canvas.canvas.addEventListener("dblclick", onCanvasDblClick, true);     // 双击折叠箱体 = 展开该子图
      // 兜底：任何鼠标交互结束后尝试快照（snapshotNow 用 JSON 比对去重，无变化不入栈）
      canvas.canvas.addEventListener("pointerup", scheduleSnap);
      // 跟踪 Alt 键：按住 Alt 拖动才算“拖进/拖出分组”，否则只是移动位置（光标也会变）
      window.addEventListener("keydown", (e) => { if (e.key === "Alt") { altKeyDown = true; if (canvas) canvas.setDirty(true, true); } }, true);
      window.addEventListener("keyup", (e) => { if (e.key === "Alt") { altKeyDown = false; if (canvas) canvas.setDirty(true, true); } }, true);
      window.addEventListener("blur", () => { altKeyDown = false; });
      // 通用快捷键：Ctrl+Z 撤销 / Ctrl+Y·Ctrl+Shift+Z 重做 / Ctrl+C·V·D 复制·粘贴·再制 / Ctrl+A 全选节点。
      // 必须用【捕获阶段】（文档级，早于 LiteGraph 画布的捕获监听）：否则 LiteGraph 的 processKey 在 Ctrl+C/Ctrl+A
      // 上会 stopImmediatePropagation 抢走事件（用它自带剪贴板），导致我们的复制根本没执行、随后粘贴提示“剪贴板为空”。
      document.addEventListener("keydown", (e) => {
        if (!(e.ctrlKey || e.metaKey)) return;
        const t = e.target;
        const inField = t && (/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) || t.isContentEditable);   // 文本框/下拉里：让系统/控件自己处理
        if (inField) return;
        const k = e.key.toLowerCase();
        const take = () => { e.preventDefault(); e.stopImmediatePropagation(); };   // 接管本键，阻止 LiteGraph 自带处理重复触发
        if (k === "z" && !e.shiftKey) { take(); undo(); return; }
        if (k === "y" || (k === "z" && e.shiftKey)) { take(); redo(); return; }
        if (k === "s") { take(); if (e.shiftKey) self.saveAs(); else self.save(); return; }   // Ctrl+S 保存 / Ctrl+Shift+S 另存为
        if (k === "f") { take(); openSearch(); return; }   // Ctrl+F 图内搜索（只读安全，使用模式也可用）
        if (simpleMode) return;   // 以下均为“改图”操作：只读（使用）模式不接管
        if (k === "c") {   // 选中了文字（如日志）→ 让系统复制文字；否则复制选中的节点
          const hasTextSel = !!(window.getSelection && String(window.getSelection() || ""));
          if (hasTextSel) return;
          take();
          if (!copySelection()) setStatus("没有选中节点——先点选要复制的节点再 Ctrl+C");
          return;
        }
        if (k === "v") { take(); pasteClipboard(); return; }
        if (k === "d") { take(); duplicateSelection(); return; }
        if (k === "a") { take(); selectAllNodes(); return; }
      }, true);
      // Delete/Backspace：删选中节点；选中的是【组】则【整组删除】（组当作一个节点看待；可 Ctrl+Z 恢复）。
      document.addEventListener("keydown", (e) => {
        if (e.key !== "Delete" && e.key !== "Backspace") return;
        if (simpleMode) return;
        const t = e.target;
        if (t && (/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) || t.isContentEditable)) return;   // 正在输入框里：不拦截
        if (selectedGroupId) {
          const g = groupById(selectedGroupId); e.preventDefault(); e.stopImmediatePropagation();   // 抢在 LiteGraph 删节点之前
          if (g) { const cnt = groupAllMembers(g).length; deleteGroupAll(g); selectGroup(null); setStatus(`已删除组及全部 ${cnt} 个节点（Delete）——可 Ctrl+Z 撤销`); }
          return;
        }
        const sel = Object.values((canvas && canvas.selected_nodes) || {});
        if (sel.length) { e.preventDefault(); e.stopImmediatePropagation(); for (const n of sel.slice()) { try { graph.remove(n); } catch (err) {} } setStatus(`已删除 ${sel.length} 个节点——可 Ctrl+Z 撤销`); }
      }, true);
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
