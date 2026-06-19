"""
采集覆盖层：在游戏画面上直接框选区域 / 取点 / 吸色 / 截模板 / 捕获按键。

这些是节点参数旁「按钮」点一下就用的便利工具，复用自经典 GUI（gui_app.py）里
已验证好用的全屏 Tkinter 覆盖层 + 放大镜逻辑，抽成独立、自包含的函数。

设计要点（适配 pywebview）：
- pywebview 的 js_api 方法在「每次调用新起的 worker 线程」里执行（见 webhost.Api 注释）。
- Tcl/Tk 支持「每线程一个解释器」，所以每个函数自己 new 一个 tk.Tk() root、跑完
  mainloop 后 destroy——只要这个 root 只被它所在的线程碰，就安全。绝不复用全局 root。
- 截图是「冻结的一张全屏图」铺在覆盖层上：调用方（webhost）应在调用前把编辑器窗口
  最小化、让游戏露出来，再抓屏；覆盖层永远 topmost，盖住一切。
- 返回值都是 JSON 可序列化的朴素结构（list/dict/str/None），方便直接回传前端。

坐标说明：用 PIL.ImageGrab.grab()（主显示器），与经典工具一致，返回的就是绝对屏幕像素，
与游戏/节点里用的区域坐标同一套坐标系。
"""
from __future__ import annotations

import os
import time
from typing import Optional

# 功能键 keysym -> 我们用的按键名（与经典 GUI 对齐；普通字符键直接用小写字符）。
_KEY_NAME = {
    "space": "space", "return": "enter", "escape": "esc",
    "backspace": "backspace", "delete": "delete", "insert": "insert",
    "home": "home", "end": "end", "page_up": "pageup", "page_down": "pagedown",
    "left": "left", "right": "right", "up": "up", "down": "down",
    "tab": "tab",
    **{f"f{i}": f"f{i}" for i in range(1, 13)},
}
_MODIFIER_KEYSYMS = {"control_l", "control_r", "alt_l", "alt_r", "shift_l", "shift_r",
                     "super_l", "super_r", "win_l", "win_r", "caps_lock"}

_HINT_BG = "#1e1e1e"
_HINT_FG = "#cca700"
_HINT_FONT = ("Microsoft YaHei UI", 11, "bold")


def _grab_screen():
    """抓取主显示器全屏，返回 (PIL.Image, w, h)。失败抛异常由调用方处理。"""
    from PIL import ImageGrab
    shot = ImageGrab.grab()
    w, h = shot.size
    return shot, w, h


def _make_overlay(hint: str):
    """创建一个全屏、置顶、半透明的 Tk 覆盖层并铺上当前屏幕截图。

    返回 (root, canvas, screenshot, w, h)。调用方绑定自己的事件后调用 root.mainloop()。
    """
    import tkinter as tk
    from PIL import ImageTk

    shot, w, h = _grab_screen()

    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    try:
        root.attributes("-alpha", 0.9)
    except Exception:
        pass

    canvas = tk.Canvas(root, cursor="crosshair", highlightthickness=0)
    canvas.pack(fill=tk.BOTH, expand=True)

    photo = ImageTk.PhotoImage(shot, master=root)
    canvas.create_image(0, 0, anchor=tk.NW, image=photo)
    root._photo = photo  # 防 GC

    bar = tk.Frame(canvas, bg=_HINT_BG)
    canvas.create_window(w // 2, 6, anchor=tk.N, window=bar)
    tk.Label(bar, text=hint, bg=_HINT_BG, fg=_HINT_FG, font=_HINT_FONT,
             padx=14, pady=6).pack()

    root.geometry(f"{w}x{h}+0+0")
    root.focus_force()
    return root, canvas, shot, w, h


def _draw_zoom(canvas, shot, sw, sh, px, py, root):
    """在光标右下方画一个放大镜（20x20 像素放大到 100x100）+ 中心十字。返回新的 PhotoImage。"""
    from PIL import Image as PILImage, ImageTk
    canvas.delete("zoom")
    src, dst = 20, 100
    x1 = max(0, min(px - src // 2, sw - src))
    y1 = max(0, min(py - src // 2, sh - src))
    crop = shot.crop((x1, y1, x1 + src, y1 + src)).resize((dst, dst), PILImage.NEAREST)
    img = ImageTk.PhotoImage(crop, master=root)
    zx = px + 20 if px + 20 + dst + 10 < sw else px - 20 - dst
    zy = py + 20 if py + 20 + dst + 10 < sh else py - 20 - dst
    canvas.create_image(zx, zy, anchor="nw", image=img, tags="zoom")
    canvas.create_rectangle(zx, zy, zx + dst, zy + dst, outline=_HINT_FG, width=2, tags="zoom")
    cx, cy = zx + dst // 2, zy + dst // 2
    canvas.create_line(cx - 6, cy, cx + 6, cy, fill="red", width=1, tags="zoom")
    canvas.create_line(cx, cy - 6, cx, cy + 6, fill="red", width=1, tags="zoom")
    return img


# ==================== 取点 / 吸色 ====================
def _pick_pixel(want_color: bool):
    """点屏取一个像素：want_color=True 同时返回颜色。左键确认，Esc 取消。"""
    root, canvas, shot, sw, sh = _make_overlay(
        ("点击取色（坐标+颜色） | Esc 取消" if want_color else "点击取点（坐标） | Esc 取消"))
    state = {"result": None, "_zoom": None}
    live = canvas.create_text(sw // 2, sh - 24, text="", fill=_HINT_FG,
                              font=("Consolas", 11), anchor="s")

    def on_motion(e):
        px, py = e.x, e.y
        if not (0 <= px < sw and 0 <= py < sh):
            return
        r, g, b = shot.getpixel((px, py))[:3]
        canvas.itemconfigure(live, text=f"({px}, {py})  RGB=({r}, {g}, {b})  #{r:02x}{g:02x}{b:02x}")
        state["_zoom"] = _draw_zoom(canvas, shot, sw, sh, px, py, root)

    def on_click(e):
        px, py = e.x, e.y
        if not (0 <= px < sw and 0 <= py < sh):
            return
        r, g, b = shot.getpixel((px, py))[:3]
        state["result"] = {"point": [px, py], "color": [r, g, b]} if want_color else {"point": [px, py]}
        root.quit()

    def on_esc(_e):
        root.quit()

    canvas.bind("<Motion>", on_motion)
    canvas.bind("<ButtonPress-1>", on_click)
    root.bind("<Escape>", on_esc)
    root.mainloop()
    root.destroy()
    return state["result"]


def pick_point():
    """取一个坐标点，返回 [x, y]（取消返回 None）。"""
    res = _pick_pixel(want_color=False)
    return res["point"] if res else None


def pick_color():
    """吸色：返回 {'point': [x,y], 'color': [r,g,b]}（取消返回 None）。"""
    return _pick_pixel(want_color=True)


# ==================== 框选区域 / 截模板 ====================
def _drag_rect(on_confirm_label: str):
    """拖拽框选一个矩形：拖动画框，松手定框，Enter 确认，重新拖动可重画，Esc 取消。

    确认时返回 [left, top, right, bottom]（已规整为左上<右下），取消返回 None。
    """
    import tkinter as tk
    root, canvas, shot, sw, sh = _make_overlay(
        f"按住左键拖动框选 | Enter {on_confirm_label} | 重新拖动可重画 | Esc 取消")
    state = {"box": None, "start": None, "rid": None, "tid": None, "confirmed": False}

    def redraw():
        for k in ("rid", "tid"):
            if state[k] is not None:
                canvas.delete(state[k])
                state[k] = None
        b = state["box"]
        if not b:
            return
        x1, y1, x2, y2 = b
        state["rid"] = canvas.create_rectangle(x1, y1, x2, y2, outline="#00e0ff",
                                                width=2, fill="#00e0ff", stipple="gray12")
        state["tid"] = canvas.create_text(x1, y1 - 8, anchor="sw", fill="#00e0ff",
                                          font=("Consolas", 11),
                                          text=f"({x1},{y1},{x2},{y2})  {x2-x1}x{y2-y1}")

    def on_press(e):
        state["start"] = (e.x, e.y)
        state["box"] = [e.x, e.y, e.x, e.y]
        redraw()

    def on_drag(e):
        if not state["start"]:
            return
        x0, y0 = state["start"]
        state["box"] = [min(x0, e.x), min(y0, e.y), max(x0, e.x), max(y0, e.y)]
        redraw()

    def on_release(_e):
        state["start"] = None

    def on_enter(_e):
        b = state["box"]
        if b and b[2] > b[0] and b[3] > b[1]:
            state["confirmed"] = True
            root.quit()

    def on_esc(_e):
        root.quit()

    canvas.bind("<ButtonPress-1>", on_press)
    canvas.bind("<B1-Motion>", on_drag)
    canvas.bind("<ButtonRelease-1>", on_release)
    root.bind("<Return>", on_enter)
    root.bind("<Escape>", on_esc)
    root.mainloop()
    box = list(state["box"]) if state["confirmed"] else None
    # 截模板需要原图，附在 root 上由调用方取用后再 destroy。
    root._shot = shot
    return root, box


def pick_region():
    """框选区域，返回 [left, top, right, bottom]（取消返回 None）。"""
    root, box = _drag_rect("确认")
    root.destroy()
    return box


def capture_template(save_dir: str):
    """框选并把该区域裁出存为 PNG 模板，返回保存路径（取消返回 None）。"""
    root, box = _drag_rect("截图保存")
    shot = getattr(root, "_shot", None)
    if box is None or shot is None:
        root.destroy()
        return None
    crop = shot.crop(tuple(box))
    root.destroy()
    os.makedirs(save_dir, exist_ok=True)
    name = f"cap_{time.strftime('%Y%m%d_%H%M%S')}.png"
    path = os.path.join(save_dir, name)
    crop.save(path)
    # 返回相对路径（与内置模板写法一致：templates/xxx.png），便于跨机移动工程。
    try:
        return os.path.relpath(path).replace("\\", "/")
    except ValueError:
        return path


# ==================== 捕获按键 ====================
def capture_key():
    """弹一个小提示，捕获一次按键，返回按键名（如 'q' / 'f5' / 'space'）；Esc 取消返回 None。"""
    import tkinter as tk
    root = tk.Tk()
    root.title("捕获按键")
    root.attributes("-topmost", True)
    root.resizable(False, False)
    root.configure(bg=_HINT_BG)
    tk.Label(root, text="请按下要捕获的按键……", bg=_HINT_BG, fg=_HINT_FG,
             font=_HINT_FONT, padx=30, pady=24).pack()
    tk.Label(root, text="（Esc 取消）", bg=_HINT_BG, fg="#888",
             font=("Microsoft YaHei UI", 9)).pack(pady=(0, 12))
    state = {"key": None}

    def on_key(e):
        ks = e.keysym
        low = ks.lower()
        if low == "escape":
            root.quit(); return "break"
        if low in _MODIFIER_KEYSYMS:
            return "break"  # 忽略单独的修饰键
        state["key"] = low if len(ks) == 1 else _KEY_NAME.get(low, low)
        root.quit()
        return "break"

    root.bind("<KeyPress>", on_key)
    # 居中
    root.update_idletasks()
    w, h = root.winfo_width(), root.winfo_height()
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"+{(sw - w) // 2}+{(sh - h) // 3}")
    root.focus_force()
    root.mainloop()
    root.destroy()
    return state["key"]
