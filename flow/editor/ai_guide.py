"""生成「给 AI 看的流程图(.flow.json)编写指南」并复制到剪贴板。

目的：让没接触过本程序的 AI，仅凭这段文字 ＋ 流程文件里每个节点自带的 note，
即可直接编辑 JSON 搭出合理流程。节点目录从注册表【实时生成】，永远与代码一致。
"""
from __future__ import annotations

import json


def _fmt_default(v) -> str:
    """参数默认值按 JSON 字面量显示（true/false/null/[..]/"str"/数字），与写进 .flow.json 的一致。"""
    try:
        return json.dumps(v, ensure_ascii=False)
    except Exception:
        return str(v)


def _ports(ports, kind) -> list[str]:
    out = []
    for p in ports:
        if p["kind"] != kind:
            continue
        star = "*" if p.get("advanced") else ""
        if kind == "exec":
            out.append(p["name"] + star)
        else:
            lbl = (p.get("label") or "").strip()
            out.append(f'{p["name"]}:{p["dtype"]}{star}' + (f"({lbl})" if lbl else ""))
    return out


def _params(params) -> list[str]:
    out = []
    for s in params:
        star = "*" if s.get("advanced") else ""
        seg = f'{s["key"]}={_fmt_default(s.get("default"))}{star}'
        if s.get("choices"):
            seg += "∈" + "/".join(map(str, s["choices"]))
        lbl = (s.get("label") or "").strip()
        out.append(seg + (f"({lbl})" if lbl else ""))
    return out


def _catalog(defs) -> str:
    by_cat: dict[str, list] = {}
    for d in defs:
        by_cat.setdefault(d.get("category") or "其他", []).append(d)
    lines: list[str] = []
    for cat in sorted(by_cat):
        lines.append(f"\n### [{cat}]")
        for d in sorted(by_cat[cat], key=lambda x: x["type"]):
            head = (d.get("help") or "").strip().splitlines()
            lines.append(f'- **{d["type"]}** «{d.get("title", "")}»' + (f" — {head[0]}" if head else ""))
            ei, eo = _ports(d["inputs"], "exec"), _ports(d["outputs"], "exec")
            di, do = _ports(d["inputs"], "data"), _ports(d["outputs"], "data")
            if ei or eo:
                lines.append(f'    exec  in:{",".join(ei) or "—"}  out:{",".join(eo) or "—"}')
            if di or do:
                lines.append(f'    data  in:{", ".join(di) or "—"}  out:{", ".join(do) or "—"}')
            pr = _params(d["params"])
            if pr:
                lines.append("    参数: " + "  ".join(pr))
    return "\n".join(lines)


def build_ai_guide(defs, version: int = 1) -> str:
    """defs = node_defs() 的返回值（节点目录）。返回一份 Markdown 指南字符串。"""
    return f"""# AoE4 自动生产助手 · 流程图(.flow.json)编写指南（写给 AI）

你将通过【直接编辑一个 .flow.json 文件】来搭建/修改"流程"。一个流程＝一张节点图：程序每隔几秒触发一次，从入口节点沿"执行线"走一遍——识别屏幕(截图/OCR/模板匹配)→判断→向游戏发按键/鼠标，实现自动化(典型用途：自动出兵/出农)。

## 1) 执行模型
- 入口必须是 `event.*` 节点(如 `event.on_tick`，按 `interval` 秒周期触发)。
- 从入口沿【执行线 exec_edges】逐节点走；某 exec 出口【不接任何线＝本帧到此结束】。
- 控制节点决定走向：`control.if` 有 `true`/`false` 两个 exec 出口，按其 `cond` 数据输入二选一走。
- 动作节点(`action.press_key` 等)只有被执行线走到时才发输入。

## 2) 两类连线（端口一律按【名字】引用，不是序号）
- 执行线 `exec_edges`：`["源id","源exec出口","目标id","目标exec入口"]`，控制先后顺序。一个 exec 入口【可被多条线汇入】(fan-in＝多个分支殊途同归)。
- 数据线 `data_edges`：`["源id","源数据出口","目标id","目标数据入口"]`，传值(数字/布尔/字符串/区域…)。【惰性求值】：节点被执行时才沿数据线回拉输入值；数据节点没有 exec 口、不在执行线上。
- exec 口只接 exec 口、data 口只接 data 口(见下方目录里每个端口的种类)。

## 3) .flow.json 结构
```json
{{
  "version": {version},
  "name": "流程名",
  "description": "可选，整体说明",
  "nodes": [
    {{"id": "唯一英文id", "type": "节点type", "pos": [0, 0],
     "params": {{"参数键": 值}}, "note": "该节点中文说明(可选但强烈建议)"}}
  ],
  "exec_edges": [["a", "out", "b", "in"]],
  "data_edges": [["sensor", "value", "cmp", "a"]],
  "panel": [["nodeId", "paramKey", "面板显示名"]],
  "overlaypanel": [["nodeId", "paramKey"]],
  "groups": [{{"id": "g1", "title": "组名", "color": "#3a6ea5",
              "members": ["id1", "id2"], "parent": null, "desc": "", "collapsed": false}}],
  "labels": {{"nodeId|paramKey": "显示名"}}
}}
```
- `pos` 只影响编辑器排版，随便给(如 `[0,0]`)，编辑器有"自动排版"会重排。
- `params` 只写你要改的键，其余取节点默认值。
- `panel`／`overlaypanel`＝把常用旋钮(开关/数值/按键)置顶到控制面板／游戏内覆盖层，给非技术用户用。
- `groups` 纯视觉分组(≈把流程分段)，不影响执行；`parent` 可做二级嵌套。

## 4) 节点目录（实时取自注册表，与当前代码一致）
图例：`端口名:类型`＝数据端口(类型 number/bool/string/region/image/list/point/color/any)；`*`＝高级(编辑器默认折叠)；`参数键=默认值`。
{_catalog(defs)}

## 5) 推荐套路（出兵类，可按需裁剪，非强制）
入口 → 守卫(没按修饰键? 在游戏中? 没被遮挡?) → 识别(人口/资源/建筑数) → 门控(开关开? 队列没在造? 资源够买1个? 有人口空位?——任一不满足就跳过本段/结束本帧，避免空操作骚扰玩家键鼠) → 操作区(取锁`control.lock_acquire`→屏蔽输入`control.input_block_begin`→存编组→选建筑`action.press_key`→产能`game.produce_count`→排队`action.press_key`→收尾恢复/解屏蔽/解锁)。
- 启停一段：`data.switch`(on) --value--> `control.if`(cond)，false 出口跳过该段。
- 产量：`game.produce_count` ＝ min(计划数, 人口空位÷人口成本`pop_cost`, 各资源÷各自单价)。多段(如村民/商人/渔船)串联时，把上一段的 `slots_left`/`xxx_left` 接到下一段的 `available_slots`/`xxx`，共享同一池子、逐段扣减，避免同帧重复占用。
- 没识别到建筑→`logic.not`→`control.set_switch` 自动关掉该段开关并在日志提示。
- 人口成本 `pop_cost` 默认 1；占 2 人口的单位填 2、不占人口填 0。

## 6) 要点 & 坑
- `id` 用英文、全图唯一。新增一段最省事：复制一段同类已有节点，改 `id`/模板/按键/成本即可。
- 区域 `[left,top,right,bottom]`(像素)、模板图路径、按键，都依赖【用户的分辨率与游戏内键位】，属占位值，必须让用户在编辑器里用"框选/吸色/截取"工具核对、用"🖼预览/试运行"验证。
- 想读懂某个【具体流程】：直接看它 `nodes[]` 里每个节点的 `note`(逐节点中文说明)即可，无需额外文档。
- 改完务必让用户"试运行"(只识别、不真正发输入)核对分支与取值。
"""


def copy_to_clipboard(text: str) -> bool:
    """把文本写入 Windows 剪贴板(CF_UNICODETEXT)。纯 ctypes，无第三方依赖。失败返回 False。"""
    try:
        import ctypes
        from ctypes import wintypes
        CF_UNICODETEXT, GMEM_MOVEABLE = 13, 0x0002
        u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
        # 64 位下句柄/指针是 8 字节，必须显式声明 restype，否则被默认 c_int 截断 → 崩溃
        k32.GlobalAlloc.restype = wintypes.HGLOBAL
        k32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        k32.GlobalLock.restype = wintypes.LPVOID
        k32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        k32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        u32.SetClipboardData.restype = wintypes.HANDLE
        u32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
        if not u32.OpenClipboard(None):
            return False
        try:
            u32.EmptyClipboard()
            buf = ctypes.create_unicode_buffer(text)        # 含结尾 \0，大小 = (len+1)*2
            size = ctypes.sizeof(buf)
            h = k32.GlobalAlloc(GMEM_MOVEABLE, size)
            if not h:
                return False
            lock = k32.GlobalLock(h)
            ctypes.memmove(lock, buf, size)
            k32.GlobalUnlock(h)
            if not u32.SetClipboardData(CF_UNICODETEXT, h):  # 成功后内存归系统所有，不再释放
                return False
            return True
        finally:
            u32.CloseClipboard()
    except Exception:
        return False
