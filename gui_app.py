"""
AOE4 自动生产村民工具 - 图形界面
使用 tkinter 构建，提供启动/停止/暂停控制、日志输出、状态栏、配置显示、帮助和快捷键
"""
import sys
import os
import re
import json
import threading
import time
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox

# 确保项目根目录在 sys.path 中
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# PyInstaller 打包后，配置文件应保存到 exe 所在目录
if getattr(sys, 'frozen', False):
    _APP_DIR = os.path.dirname(sys.executable)
else:
    _APP_DIR = BASE_DIR

# 配置文件路径（保存到 exe 同目录）
SHORTCUT_FILE = os.path.join(_APP_DIR, "shortcuts.json")
CONFIG_OVERRIDE_FILE = os.path.join(_APP_DIR, "config_override.json")


# ==================== LOD 时间序列 ====================

class _LODSeries:
    """带LOD（Level of Detail）层级的时间序列

    Level 0: 原始数据（self.raw list），索引访问O(1)
    Level K (K≥1): 每 GROUP^K 个原始点的 (min, max) 对

    绘图时根据可见范围选择合适的LOD层级，避免遍历大量原始数据。
    内存开销约为原始数据的 5/3 倍（1 + 1/2 + 1/8 + ... ≈ 5/3）。
    """

    GROUP = 4  # 每组4个原始点聚合为1个(min,max)

    def __init__(self):
        self.raw = []             # 原始数据 list（替代deque，O(1)索引）
        self.levels = []          # levels[k] = [(min, max), ...]
        self._pending = []        # 未满 GROUP 的原始点（尾部余数）
        # _lod_pending[k] 存储第k层未满GROUP的尾部项（来自levels[k]的引用切片）
        # 仅在 append 时维护，rebuild 时重建
        self._lod_pending = []    # 每层未满 GROUP 的 (min, max) 列表

    def __len__(self):
        return len(self.raw)

    def __bool__(self):
        return bool(self.raw)

    def append(self, val):
        """追加一个值，增量维护LOD层级"""
        self.raw.append(val)
        self._pending.append(val)
        if len(self._pending) >= self.GROUP:
            lo = min(self._pending)
            hi = max(self._pending)
            self._pending = []
            self._push_lod(0, (lo, hi))

    def _push_lod(self, level, val_tuple):
        """向指定LOD层级推送一个(min,max)对"""
        while level >= len(self.levels):
            self.levels.append([])
            self._lod_pending.append([])
        self.levels[level].append(val_tuple)
        self._lod_pending[level].append(val_tuple)
        if len(self._lod_pending[level]) >= self.GROUP:
            lo = min(v[0] for v in self._lod_pending[level])
            hi = max(v[1] for v in self._lod_pending[level])
            self._lod_pending[level] = []
            self._push_lod(level + 1, (lo, hi))

    def trim(self, maxlen):
        """淘汰超出maxlen的旧数据并重建LOD"""
        if maxlen <= 0 or len(self.raw) <= maxlen:
            return
        self.raw = self.raw[len(self.raw) - maxlen:]
        self.rebuild()

    def rebuild(self):
        """从raw重建所有LOD层级（trim后或设置变更后调用）"""
        self.levels = []
        self._lod_pending = []
        self._pending = []

        src = self.raw
        level = 0
        while len(src) >= self.GROUP:
            dst = []
            full_count = len(src) - len(src) % self.GROUP
            for i in range(0, full_count, self.GROUP):
                group = src[i:i + self.GROUP]
                if level == 0:
                    dst.append((min(group), max(group)))
                else:
                    dst.append((min(v[0] for v in group), max(v[1] for v in group)))
            self.levels.append(dst)
            self._lod_pending.append([])  # 余数由下方回填逻辑处理
            src = dst
            level += 1

        # 回填 _lod_pending：每层的余数就是该层最后几个未被上层聚合的项
        for k in range(len(self.levels)):
            r = len(self.levels[k]) % self.GROUP
            if r > 0:
                self._lod_pending[k] = list(self.levels[k][-r:])

        # raw 的余数放入 _pending
        r = len(self.raw) % self.GROUP
        self._pending = list(self.raw[-r:]) if r else []

    def get_draw_data(self, offset, count, chart_width):
        """获取绘图数据，自动选择合适的LOD层级

        :param offset: 可见范围在raw中的起始索引
        :param count: 可见范围的原始数据点数
        :param chart_width: 图表像素宽度
        :return: (values, raw_indices) 两个list，等长
                 values[i] 是值，raw_indices[i] 是对应的raw索引（用于X轴定位）
        """
        if count <= 0 or not self.raw:
            return [], []

        max_points = chart_width * 2  # 每像素最多2个采样点，保证视觉精度

        # 如果原始数据量本身就不大，直接用原始数据，无需LOD
        if count <= max_points:
            end = min(offset + count, len(self.raw))
            values = self.raw[offset:end]
            indices = list(range(offset, end))
            return values, indices

        # 选择LOD层级：可见范围对应的LOD点数×2 ≤ max_points
        chosen_level = -1  # -1 表示用原始数据
        for k in range(len(self.levels)):
            group_size = self.GROUP ** (k + 1)
            # 该层级覆盖offset..offset+count范围的LOD点数
            lod_start = offset // group_size
            lod_end = (offset + count + group_size - 1) // group_size
            lod_count = lod_end - lod_start
            if lod_count * 2 <= max_points:
                chosen_level = k
                break

        if chosen_level == -1:
            # 所有LOD层级仍不够稀疏，退回原始数据
            end = min(offset + count, len(self.raw))
            values = self.raw[offset:end]
            indices = list(range(offset, end))
            return values, indices

        # 用LOD层级：每个(min,max)对展开为2个值
        group_size = self.GROUP ** (chosen_level + 1)
        lod = self.levels[chosen_level]
        lod_start = offset // group_size
        lod_end = (offset + count + group_size - 1) // group_size
        lod_start = max(0, lod_start)
        lod_end = min(lod_end, len(lod))

        values = []
        indices = []
        for i in range(lod_start, lod_end):
            lo, hi = lod[i]
            raw_idx_start = i * group_size
            raw_idx_end = raw_idx_start + group_size - 1
            # 先小后大，保持视觉连续性
            if lo <= hi:
                values.extend([lo, hi])
            else:
                values.extend([hi, lo])
            indices.extend([raw_idx_start, raw_idx_end])

        # 补充LOD层级未覆盖的尾部数据（_lod_pending + _pending）
        # 计算LOD层级实际覆盖的raw索引范围
        covered_end = len(lod) * group_size  # LOD层级覆盖到此处
        raw_end = min(offset + count, len(self.raw))
        if covered_end < raw_end:
            # 从中间LOD层级补充：逐层展开 lod_pending[k] (k < chosen_level)
            # 从raw读取LOD未覆盖的尾部（长度≤GROUP^(chosen_level+1)，性能影响有限）
            tail_start = max(covered_end, offset)
            for ri in range(tail_start, raw_end):
                values.append(self.raw[ri])
                indices.append(ri)

        return values, indices

    def peak(self, offset=0, count=None):
        """获取指定范围的数据范围（min, max），使用LOD加速"""
        if not self.raw:
            return 0.0, 0.0
        if count is None:
            count = len(self.raw) - offset
        end = min(offset + count, len(self.raw))
        if offset >= end:
            return 0.0, 0.0

        lo = self.raw[offset]
        hi = self.raw[offset]

        # 使用LOD Level 0加速：对齐到GROUP边界的部分用LOD，余数遍历raw
        gs = self.GROUP
        # 头部：offset到下一个GROUP边界
        first_boundary = ((offset // gs) + 1) * gs
        head_end = min(first_boundary, end)
        for i in range(offset, head_end):
            v = self.raw[i]
            if v < lo:
                lo = v
            elif v > hi:
                hi = v

        # 中间：用Level 0的(min, max)对
        if self.levels and head_end < end:
            lod0 = self.levels[0]
            lod_idx_start = head_end // gs
            lod_idx_end = min(end // gs, len(lod0))
            for li in range(lod_idx_start, lod_idx_end):
                mn, mx = lod0[li]
                if mn < lo:
                    lo = mn
                if mx > hi:
                    hi = mx

            # 尾部：Level 0未覆盖的余数
            tail_start = lod_idx_end * gs
            for i in range(tail_start, end):
                v = self.raw[i]
                if v < lo:
                    lo = v
                elif v > hi:
                    hi = v

        return lo, hi


# ==================== 快捷键管理 ====================

# 功能名 → 默认无快捷键
SHORTCUT_ACTIONS = {
    "start": "启动",
    "stop": "停止",
    "pause": "暂停/继续",
    "reset_tc": "清零TC",
    "clear_log": "清除日志",
}

# tkinter key 名称 → 用户显示名称
_KEY_DISPLAY = {
    "space": "Space", "return": "Enter", "escape": "Esc",
    "backspace": "Backspace", "delete": "Del", "insert": "Ins",
    "home": "Home", "end": "End", "page_up": "PgUp", "page_down": "PgDn",
    "left": "←", "right": "→", "up": "↑", "down": "↓",
    "tab": "Tab", "caps_lock": "CapsLock",
    "f1": "F1", "f2": "F2", "f3": "F3", "f4": "F4",
    "f5": "F5", "f6": "F6", "f7": "F7", "f8": "F8",
    "f9": "F9", "f10": "F10", "f11": "F11", "f12": "F12",
}

# 反向映射：用户输入 → tkinter keysym
_INPUT_TO_KEYSYM = {}
for _k, _v in _KEY_DISPLAY.items():
    _INPUT_TO_KEYSYM[_v.lower()] = _k
    _INPUT_TO_KEYSYM[_k] = _k
# 额外常见映射
_INPUT_TO_KEYSYM.update({
    "enter": "return", "esc": "escape", "del": "delete",
    "ins": "insert", "space": "space", "tab": "tab",
})


# ==================== 配置项定义 ====================
# 按分类和有用程度排序：核心参数 > 操作时序 > OCR > 按键延迟 > 模板阈值 > 调试

CONFIG_CATEGORIES = [
    ("核心参数", [
        ("ENABLE_MAX_VILLAGERS", "村民上限检测", "bool", "是否启用村民上限（⚠统计不含移动/建造中村民，仅供参考，功能不稳定）"),
        ("MAX_VILLAGERS", "村民上限", "int", "超过此数量停止生产（⚠统计不准，仅供参考）"),
        ("MIN_FOOD", "最低食物", "int", "低于此值不生产村民"),
        ("VILLAGERS_PER_TC", "每TC排队", "int", "每个TC同时排队的村民数"),
        ("FOOD_PER_VILLAGER", "每村民食物", "int", "单个村民需要的食物，用于计算食物不足时应生产几个村民"),
        ("HDR_ENABLED", "游戏HDR", "bool", "游戏是否开启HDR，影响检测颜色值（开关仅控制使用SDR/HDR哪个颜色）"),
    ]),
    ("按键设置", [
        ("TC_SELECT_KEY", "选所有TC按键", "str", "选中所有城镇中心的快捷键（需与游戏内设置一致）"),
        ("VILLAGER_QUEUE_KEY", "生产村民按键", "str", "生产村民的快捷键（需与游戏内设置一致）"),
        ("ENABLE_SHIFT_QUEUE", "Shift批量排队", "bool", "启用后Shift+生产键每次排5个，关闭则逐个排队"),
    ]),
    ("操作时序", [
        ("CHECK_INTERVAL", "检测间隔(秒)", "float", "主检测循环间隔，越小响应越快但CPU越高"),
        ("VILLAGER_CHECK_INTERVAL", "村民检查间隔(秒)", "float", "村民数量OCR间隔，村民变化慢无需频繁检查"),
        ("POST_OPERATION_DELAY", "操作后延迟(秒)", "float", "操作后等待UI更新的时间，过短可能导致重复生产"),
        ("ENABLE_INPUT_BLOCK", "输入屏蔽", "bool", "操作期间屏蔽物理输入(需管理员)"),
        ("BLOCK_INPUT_DURATION", "屏蔽等待(秒)", "float", "操作后额外等待时长，0最快"),
    ]),
    ("OCR设置", [
        ("USE_GPU", "GPU加速", "bool", "使用GPU加速OCR（⚠不建议开启，小图片OCR时CPU更快；CPU版exe无法使用GPU）"),
        ("OCR_IMAGE_SCALE", "图片缩放", "float", "OCR图片缩放比例，越小越快但可能不准"),
    ]),
    ("按键延迟", [
        ("TC_SELECT_DELAY", "TC选中延迟(秒)", "float", "按选中TC键后等待UI刷新的时间"),
        ("TC_RETRY_DELAY", "TC重试延迟(秒)", "float", "可能UI仍未刷新导致检测失败后重试的等待时间"),
        ("TC_MAX_RETRY", "TC最大重试", "int", "TC检测失败最大重试次数"),
        ("TC_DETECTION_FAILED_COOLDOWN", "TC失败冷却(秒)", "float", "TC检测失败后冷却时间"),
        ("COOLDOWN_CHECK_INTERVAL", "冷却检测间隔(秒)", "float", "冷却期间的检测间隔"),
        ("QUEUE_DELAY", "排队延迟(秒)", "float", "每次生产键之间的延迟，0最快"),
    ]),
    ("模板匹配阈值", [
        ("VILLAGER_MATCH_THRESHOLD", "村民匹配阈值", "float", "村民图标匹配阈值(0-1)"),
        ("BLOCKED_MATCH_THRESHOLD", "遮挡阈值", "float", "完全遮挡匹配阈值(0-1)"),
        ("BLOCKED_TRANSITION_THRESHOLD", "渐变下限阈值", "float", "低于此值认为未遮挡(0-1)"),
        ("TC_MATCH_THRESHOLD", "TC匹配阈值", "float", "TC图标匹配阈值(0-1)"),
    ]),
    ("游戏状态检测点", [
        ("GAME_DETECT_PIXEL_SDR", "SDR检测坐标", "tuple", "SDR模式下检测像素坐标(x,y)"),
        ("GAME_DETECT_COLOR_SDR", "SDR检测颜色", "tuple", "SDR模式下检测点RGB颜色(r,g,b)"),
        ("GAME_DETECT_PIXEL_HDR", "HDR检测坐标", "tuple", "HDR模式下检测像素坐标(x,y)"),
        ("GAME_DETECT_COLOR_HDR", "HDR检测颜色", "tuple", "HDR模式下检测点RGB颜色(r,g,b)"),
    ]),
    ("截图区域坐标", [
        ("VILLAGER_QUEUE_REGION", "生产队列区域", "tuple", "村民生产队列检测区域(x1,y1,x2,y2)"),
        ("BLOCKED_DETECT_REGION", "遮挡检测区域", "tuple", "UI遮挡检测区域(x1,y1,x2,y2)"),
        ("POPULATION_REGION", "人口显示区域", "tuple", "人口OCR识别区域(x1,y1,x2,y2)"),
        ("TC_ICON_REGION", "TC图标区域", "tuple", "TC图标检测区域(x1,y1,x2,y2)"),
        ("SINGLE_TC_REGION", "单TC预检区域", "tuple", "单TC预检测区域(x1,y1,x2,y2)"),
        ("VILLAGER_COUNT_REGION", "村民计数区域", "tuple", "村民总数OCR区域(x1,y1,x2,y2)"),
        ("FOOD_REGION", "食物显示区域", "tuple", "食物数量OCR区域(x1,y1,x2,y2)"),
    ]),
    ("调试开关", [
        ("DEBUG_MODE", "全局调试", "bool", "所有模块的详细日志（TC/村民/食物/遮挡/生产检测）"),
        ("DEBUG_PERFORMANCE", "性能分析", "bool", "显示各模块详细耗时（独立开关）"),
        ("DEBUG_SAVE_SCREENSHOTS", "保存截图", "bool", "保存调试截图（需配合全局调试，关闭可提升5-10ms）"),
    ]),
]


# 保存 config 模块的原始默认值（在任何覆盖之前）
_CONFIG_ORIGINAL_DEFAULTS = {}
try:
    import config as _cfg_for_defaults
    for _cat_name, _items in CONFIG_CATEGORIES:
        for _key, _, _, _ in _items:
            if hasattr(_cfg_for_defaults, _key):
                _CONFIG_ORIGINAL_DEFAULTS[_key] = getattr(_cfg_for_defaults, _key)
except ImportError:
    pass


def _load_shortcuts():
    """从文件加载快捷键配置"""
    if not os.path.exists(SHORTCUT_FILE):
        return {}
    try:
        with open(SHORTCUT_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _save_shortcuts(shortcuts):
    """保存快捷键配置到文件"""
    try:
        with open(SHORTCUT_FILE, 'w', encoding='utf-8') as f:
            json.dump(shortcuts, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _load_config_override():
    """从文件加载配置覆盖"""
    if not os.path.exists(CONFIG_OVERRIDE_FILE):
        return {}
    try:
        with open(CONFIG_OVERRIDE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _save_config_override(override):
    """保存配置覆盖到文件"""
    try:
        with open(CONFIG_OVERRIDE_FILE, 'w', encoding='utf-8') as f:
            json.dump(override, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _apply_config_override():
    """将配置覆盖应用到 config 模块"""
    override = _load_config_override()
    if not override:
        return
    try:
        import config
        for key, value in override.items():
            if hasattr(config, key):
                # list值转为tuple（JSON中tuple存为list）
                if isinstance(value, list):
                    value = tuple(value)
                setattr(config, key, value)
    except ImportError:
        pass


def _display_shortcut(shortcut_str):
    """将快捷键字符串转为显示格式，如 'Control+s' → 'Ctrl+S'"""
    parts = shortcut_str.split('+')
    display_parts = []
    for p in parts:
        p_lower = p.lower()
        if p_lower == 'control':
            display_parts.append('Ctrl')
        elif p_lower == 'alt':
            display_parts.append('Alt')
        elif p_lower == 'shift':
            display_parts.append('Shift')
        else:
            display_parts.append(_KEY_DISPLAY.get(p_lower, p.upper()))
    return '+'.join(display_parts)


def _parse_tuple(text):
    """解析元组字符串，如 '(2526, 1405)' 或 '2526, 1405' → (2526, 1405)"""
    text = text.strip()
    if not text:
        return None
    # 去除首尾括号
    if text.startswith('(') and text.endswith(')'):
        text = text[1:-1]
    elif text.startswith('[') and text.endswith(']'):
        text = text[1:-1]
    try:
        parts = [p.strip() for p in text.split(',')]
        values = [int(p) if '.' not in p else float(p) for p in parts if p]
        return tuple(values)
    except (ValueError, TypeError):
        return None


# ==================== 日志颜色分类规则 ====================

_STATUS_RULES = [
    (r'生产\s*\d+', 'success'),
    (r'程序已就绪', 'success'),
    (r'恢复.*检测到村民', 'success'),
    (r'提前结束冷却', 'success'),
    (r'已继续', 'success'),
    (r'不在游戏窗口', 'warning'),
    (r'不在游戏中', 'warning'),
    (r'检测到修饰键', 'warning'),
    (r'遮挡', 'warning'),
    (r'渐变', 'warning'),
    (r'生产中.*跳过', 'warning'),
    (r'人口识别失败', 'warning'),
    (r'食物识别失败', 'warning'),
    (r'操作进行中', 'warning'),
    (r'暂停中', 'warning'),
    (r'\[错误\]', 'error'),
    (r'异常', 'error'),
    (r'失败', 'error'),
    (r'TC检测失败', 'error'),
    (r'跳过', 'dim'),
    (r'食物不足', 'dim'),
    (r'人口已满', 'dim'),
    (r'村民已达上限', 'dim'),
    (r'冷却', 'dim'),
    (r'\[操作\]', 'highlight'),
    (r'\[缓存\]', 'highlight'),
    (r'GPU', 'highlight'),
    (r'OCR', 'highlight'),
    (r'预热', 'highlight'),
    (r'初始化', 'highlight'),
    (r'TC已清零', 'highlight'),
]


def _classify_log_tag(text):
    for pattern, tag in _STATUS_RULES:
        if re.search(pattern, text):
            return tag
    return 'info'


class TextRedirector:
    """将 print 输出重定向到 GUI 文本框，并分类着色"""
    def __init__(self, text_widget, status_callback=None):
        self.text_widget = text_widget
        self.status_callback = status_callback
        self._buffer = ""

    def write(self, message):
        if not message:
            return
        self._buffer += message
        if '\n' in self._buffer:
            lines = self._buffer.split('\n')
            for line in lines[:-1]:
                self._append_line(line)
            self._buffer = lines[-1]
        elif len(self._buffer) > 512:
            self._append_line(self._buffer)
            self._buffer = ""

    def _append_line(self, line):
        try:
            clean_line = re.sub(r'\033\[[0-9;]*m', '', line)
            stripped = clean_line.strip()
            if not stripped:
                return
            tag = _classify_log_tag(stripped)
            self.text_widget.configure(state='normal')
            self.text_widget.insert(tk.END, clean_line + '\n', tag)
            self.text_widget.configure(state='disabled')
            self.text_widget.see(tk.END)
            if self.status_callback and not stripped.startswith('='):
                display = re.sub(r'\s*x\d+$', '', stripped)
                self.status_callback(display, tag)
        except tk.TclError:
            pass

    def flush(self):
        if self._buffer:
            self._append_line(self._buffer)
            self._buffer = ""


class AOE4App:
    """AOE4 自动生产村民工具 GUI"""

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("AOE4 自动生产村民工具")
        self.root.geometry("760x640")
        self.root.resizable(True, True)
        self.root.minsize(560, 460)

        # 设置窗口图标（延迟加载，避免启动卡顿）
        self.root.after(100, self._load_icon)

        self.running = False
        self.paused = False  # 暂停生产标志
        self.worker_thread = None
        self._redirector = None
        self._original_stdout = sys.stdout
        self._original_stderr = sys.stderr

        # TC 缓存清零标志（工作线程检查）
        self._tc_reset_requested = False

        # 内存/CPU监控数据（使用_LODSeries，O(1)追加和索引访问，LOD加速缩小视图绘制）
        self._mem_monitor_win = None
        self._mem_data = {"tool": _LODSeries(), "game": _LODSeries(), "system_avail": _LODSeries()}
        self._mem_after_id = None
        self._mem_draw_after_id = None  # 绘制定时器（动态按需绘制）
        self._mem_dirty = False  # 数据脏标记：有新数据或画布尺寸变化时置True
        self._mem_sample_interval = 1000  # 采样间隔（毫秒）
        self._mem_max_minutes = 100  # 最大记录时长（分钟）
        self._mem_zoom = 1.0  # 横向缩放倍率
        self._mem_maxlen = 6000  # 数据容量，打开窗口时按设置计算
        self._mem_peak = {"left": 0.0, "right": 0.0}  # 缓存Y轴峰值，避免每次遍历全量数据

        self._cpu_monitor_win = None
        self._cpu_data = {"tool": _LODSeries(), "game": _LODSeries(), "system_avail": _LODSeries()}
        self._cpu_after_id = None
        self._cpu_draw_after_id = None
        self._cpu_dirty = False  # 数据脏标记：有新数据或画布尺寸变化时置True
        self._cpu_sample_interval = 1000
        self._cpu_max_minutes = 100
        self._cpu_zoom = 1.0
        self._cpu_maxlen = 6000
        self._cpu_tool_proc = None  # 缓存工具进程的psutil.Process实例
        self._cpu_game_procs = []  # 缓存游戏进程的psutil.Process实例
        self._cpu_game_warmup = set()  # 正在预热的游戏进程PID（首次采样间隔内跳过）

        # 快捷键配置
        self._shortcuts = _load_shortcuts()

        # 状态栏颜色映射
        self._tag_colors = {
            'info': '#d4d4d4',
            'success': '#6a9955',
            'warning': '#cca700',
            'error': '#f44747',
            'highlight': '#569cd6',
            'dim': '#808080',
        }

        self._build_ui()
        self._register_shortcuts()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # 启动时应用配置覆盖
        _apply_config_override()

    def _load_icon(self):
        """延迟加载窗口图标"""
        try:
            icon_path = os.path.join(BASE_DIR, "templates", "cunmin.png")
            if os.path.exists(icon_path):
                from PIL import Image, ImageTk
                icon_img = Image.open(icon_path)
                icon_photo = ImageTk.PhotoImage(icon_img)
                self.root.iconphoto(False, icon_photo)
        except Exception:
            pass

    @staticmethod
    def _get_version():
        """版本号：统一从根目录 version.py 读取（单一来源，界面/打包一致）。"""
        try:
            from version import __version__
            return __version__
        except Exception:
            return "3.2.0"

    def _build_ui(self):
        """构建界面"""
        # 获取版本号
        self._version = self._get_version()

        # 顶部标题区
        header = ttk.Frame(self.root, padding=(10, 8))
        header.pack(fill=tk.X)

        title_label = ttk.Label(
            header,
            text=f"AOE4 自动生产村民工具  v{self._version}",
            font=("Microsoft YaHei UI", 16, "bold")
        )
        title_label.pack(side=tk.LEFT)

        self.status_var = tk.StringVar(value="未启动")
        self.status_label = ttk.Label(
            header,
            textvariable=self.status_var,
            font=("Microsoft YaHei UI", 10),
            foreground="gray"
        )
        self.status_label.pack(side=tk.RIGHT, padx=(10, 0))

        ttk.Separator(self.root, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=10)

        # 当前状态栏
        status_frame = tk.Frame(self.root, bg="#1e1e1e", height=32)
        status_frame.pack(fill=tk.X, padx=10, pady=(6, 0))
        status_frame.pack_propagate(False)

        self.last_status_var = tk.StringVar(value="等待启动...")
        self.last_status_label = tk.Label(
            status_frame,
            textvariable=self.last_status_var,
            font=("Consolas", 11),
            bg="#1e1e1e",
            fg="#888888",
            anchor=tk.W,
            padx=8,
            pady=4
        )
        self.last_status_label.pack(fill=tk.BOTH, expand=True)

        # 配置信息区域
        config_frame = ttk.LabelFrame(self.root, text="运行配置", padding=(10, 4))
        config_frame.pack(fill=tk.X, padx=10, pady=(4, 2))
        self._load_config_display(config_frame)

        # 控制按钮区域 - 第一行
        btn_frame1 = ttk.Frame(self.root, padding=(10, 3))
        btn_frame1.pack(fill=tk.X)

        self.start_btn = ttk.Button(
            btn_frame1, text="▶ 启动", width=14, command=self._start
        )
        self.start_btn.pack(side=tk.LEFT, padx=(0, 6))

        self.stop_btn = ttk.Button(
            btn_frame1, text="■ 停止", width=14, command=self._stop, state=tk.DISABLED
        )
        self.stop_btn.pack(side=tk.LEFT, padx=(0, 6))

        self.pause_btn = ttk.Button(
            btn_frame1, text="⏸ 暂停", width=14, command=self._toggle_pause, state=tk.DISABLED
        )
        self.pause_btn.pack(side=tk.LEFT, padx=(0, 6))

        self.reset_tc_btn = ttk.Button(
            btn_frame1, text="↺ 清零TC", width=12, command=self._reset_tc, state=tk.DISABLED
        )
        self.reset_tc_btn.pack(side=tk.LEFT)

        # 控制按钮区域 - 第二行
        btn_frame2 = ttk.Frame(self.root, padding=(10, 0, 10, 3))
        btn_frame2.pack(fill=tk.X)

        self.clear_btn = ttk.Button(
            btn_frame2, text="清除日志", width=10, command=self._clear_log
        )
        self.clear_btn.pack(side=tk.LEFT, padx=(0, 6))

        self.shortcut_btn = ttk.Button(
            btn_frame2, text="快捷键", width=10, command=self._show_shortcut_dialog
        )
        self.shortcut_btn.pack(side=tk.LEFT, padx=(0, 6))

        self.config_btn = ttk.Button(
            btn_frame2, text="⚙ 配置", width=10, command=self._show_config_dialog
        )
        self.config_btn.pack(side=tk.LEFT, padx=(0, 6))

        self.mem_btn = ttk.Button(
            btn_frame2, text="内存监控", width=9, command=self._show_memory_monitor
        )
        self.mem_btn.pack(side=tk.LEFT, padx=(0, 4))

        self.cpu_btn = ttk.Button(
            btn_frame2, text="CPU监控", width=9, command=self._show_cpu_monitor
        )
        self.cpu_btn.pack(side=tk.LEFT, padx=(0, 4))

        self.help_btn = ttk.Button(
            btn_frame2, text="? 帮助", width=10, command=self._show_help
        )
        self.help_btn.pack(side=tk.LEFT)

        # 日志区域
        log_frame = ttk.LabelFrame(self.root, text="运行日志", padding=(5, 5))
        log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(2, 10))

        self.log_text = scrolledtext.ScrolledText(
            log_frame,
            wrap=tk.WORD,
            state='disabled',
            font=("Consolas", 9),
            bg="#1e1e1e",
            fg="#d4d4d4",
            insertbackground="white",
            selectbackground="#264f78"
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)

        # 配置日志标签颜色
        self.log_text.tag_configure("info", foreground="#d4d4d4")
        self.log_text.tag_configure("success", foreground="#6a9955")
        self.log_text.tag_configure("warning", foreground="#cca700")
        self.log_text.tag_configure("error", foreground="#f44747")
        self.log_text.tag_configure("highlight", foreground="#569cd6")
        self.log_text.tag_configure("dim", foreground="#808080")

        # 更新按钮文本（含快捷键显示）
        self._update_button_labels()

    def _update_button_labels(self):
        """更新按钮文本，如果设置了快捷键则显示"""
        action_labels = {
            "start": ("▶ 启动", "▶ 启动"),
            "stop": ("■ 停止", "■ 停止"),
            "pause": ("⏸ 暂停", "⏸ 暂停"),
            "reset_tc": ("↺ 清零TC", "↺ 清零TC"),
            "clear_log": ("清除日志", "清除日志"),
        }
        btn_map = {
            "start": self.start_btn,
            "stop": self.stop_btn,
            "pause": self.pause_btn,
            "reset_tc": self.reset_tc_btn,
            "clear_log": self.clear_btn,
        }
        for action, btn in btn_map.items():
            base_text = action_labels.get(action, ("", ""))[0]
            sc = self._shortcuts.get(action, "")
            if sc:
                display = _display_shortcut(sc)
                btn.configure(text=f"{base_text} ({display})")
            else:
                btn.configure(text=base_text)

    def _register_shortcuts(self):
        """注册全局快捷键"""
        for action, shortcut_str in self._shortcuts.items():
            if not shortcut_str:
                continue
            callback_map = {
                "start": self._start,
                "stop": self._stop,
                "pause": self._toggle_pause,
                "reset_tc": self._reset_tc,
                "clear_log": self._clear_log,
            }
            callback = callback_map.get(action)
            if callback:
                try:
                    self.root.bind(f"<{shortcut_str}>", lambda e, cb=callback: cb())
                except Exception:
                    pass

    # ==================== 功能操作 ====================

    def _start(self):
        if self.running:
            return
        self.running = True
        self.paused = False
        self.start_btn.configure(state=tk.DISABLED)
        self.stop_btn.configure(state=tk.NORMAL)
        self.pause_btn.configure(state=tk.NORMAL, text="⏸ 暂停")
        self.reset_tc_btn.configure(state=tk.NORMAL)
        self.status_var.set("启动中...")
        self._set_status_color("#cca700")
        self._update_last_status("正在初始化模块...", 'warning')

        self._redirector = TextRedirector(self.log_text, status_callback=self._update_last_status)
        sys.stdout = self._redirector
        sys.stderr = self._redirector

        self.worker_thread = threading.Thread(target=self._worker, daemon=True)
        self.worker_thread.start()
        self._check_status()

    def _stop(self):
        if not self.running:
            return
        self.running = False
        self.paused = False
        self.start_btn.configure(state=tk.NORMAL)
        self.stop_btn.configure(state=tk.DISABLED)
        self.pause_btn.configure(state=tk.DISABLED, text="⏸ 暂停")
        self.reset_tc_btn.configure(state=tk.DISABLED)
        self.status_var.set("已停止")
        self._set_status_color("#f44747")
        self._update_last_status("已停止", 'error')

        sys.stdout = self._original_stdout
        sys.stderr = self._original_stderr
        print("程序已停止", file=self._original_stdout)

    def _toggle_pause(self):
        """暂停/继续 切换"""
        if not self.running:
            return
        self.paused = not self.paused
        base_text = "▶ 继续" if self.paused else "⏸ 暂停"
        sc = self._shortcuts.get("pause", "")
        if sc:
            self.pause_btn.configure(text=f"{base_text} ({_display_shortcut(sc)})")
        else:
            self.pause_btn.configure(text=base_text)
        if self.paused:
            self.status_var.set("暂停中")
            self._set_status_color("#cca700")
            self._update_last_status("已暂停生产村民", 'warning')
            self._safe_print("已暂停生产村民（暂停中不会自动生产）", "warning")
        else:
            self.status_var.set("运行中")
            self._set_status_color("#6a9955")
            self._update_last_status("已继续生产", 'success')
            self._safe_print("已继续生产村民", "success")

    def _reset_tc(self):
        """清零TC缓存数量"""
        if not self.running:
            return
        self._tc_reset_requested = True
        self._safe_print("TC已清零（下一轮检测将重新识别TC数量）", "highlight")
        self._update_last_status("TC已清零", 'highlight')

    def _clear_log(self):
        self.log_text.configure(state='normal')
        self.log_text.delete('1.0', tk.END)
        self.log_text.configure(state='disabled')
        self._update_last_status("日志已清除", 'dim')

    # ==================== 监控窗口通用绘制 ====================

    @staticmethod
    def _nice_step(v_max, target_ticks=5):
        """计算好看的Y轴刻度步长"""
        for nice in [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000, 50000]:
            if nice >= v_max / target_ticks:
                return nice
        return int(v_max / target_ticks)

    def _perf_draw_chart(self, canvas, data_dict, left_keys, right_keys, y_axes,
                         colors, unit_left="", unit_right="", interval_ms=1000,
                         left_peak=None, right_peak=None, visible_offset=0):
        """通用性能曲线图绘制（LOD加速 + PIL渲染，高性能）

        :param canvas: tk.Canvas
        :param data_dict: {"key": _LODSeries/list, ...}
        :param left_keys: 左Y轴对应的数据键列表
        :param right_keys: 右Y轴对应的数据键列表
        :param y_axes: {"key": y_max, ...} 各曲线的Y轴最大值
        :param colors: {"key": "#color", ...}
        :param unit_left: 左Y轴单位文字
        :param unit_right: 右Y轴单位文字
        :param interval_ms: 采样间隔（毫秒），用于X轴时间标签
        :param left_peak: 左Y轴数据的已知峰值（避免遍历全量数据）
        :param right_peak: 右Y轴数据的已知峰值
        :param visible_offset: 只绘制 data[visible_offset:] 的部分
        """
        if canvas is None:
            return

        w = canvas.winfo_width()
        h = canvas.winfo_height()
        if w < 50 or h < 50:
            return

        margin_l, margin_r, margin_t, margin_b = 55, 55, 10, 22  # 左/右/上/下边距
        chart_w = w - margin_l - margin_r
        chart_h = h - margin_t - margin_b
        if chart_w < 10 or chart_h < 10:
            return

        # 取任意一个key确定数据长度（只计算可见部分）
        any_series = next((v for v in data_dict.values() if v), None)
        if not any_series:
            return
        total_len = len(any_series)
        if visible_offset > 0:
            n = total_len - visible_offset
        else:
            n = total_len
        if n < 2:
            return

        # 左Y轴范围（优先使用缓存的peak值，避免遍历全量数据）
        if left_peak is not None and left_peak > 0:
            left_max = left_peak * 1.2
        else:
            left_vals = []
            for k in left_keys:
                s = data_dict.get(k)
                if s and isinstance(s, _LODSeries):
                    lo, hi = s.peak(visible_offset, n)
                    left_vals.extend([lo, hi])
                elif s:
                    left_vals.extend(s[visible_offset:visible_offset + n] if hasattr(s, '__getitem__') else [])
            left_max = max(left_vals) * 1.2 if left_vals else 100
        if left_max < 10:
            left_max = 10
        left_step = self._nice_step(left_max)
        left_max = ((int(left_max / left_step) + 1) * left_step)

        # 右Y轴范围
        if right_keys:
            if right_peak is not None and right_peak > 0:
                right_max = right_peak * 1.15
            else:
                right_vals = []
                for k in right_keys:
                    s = data_dict.get(k)
                    if s and isinstance(s, _LODSeries):
                        lo, hi = s.peak(visible_offset, n)
                        right_vals.extend([lo, hi])
                    elif s:
                        right_vals.extend(s[visible_offset:visible_offset + n] if hasattr(s, '__getitem__') else [])
                right_max = max(right_vals) * 1.15 if right_vals else 100
            if right_max < 10:
                right_max = 10
            right_step = self._nice_step(right_max)
            right_max = ((int(right_max / right_step) + 1) * right_step)
        else:
            right_max = 100

        # 更新 y_axes
        for k in left_keys:
            y_axes[k] = left_max
        for k in right_keys:
            y_axes[k] = right_max

        # ---- 使用 PIL 渲染 ----
        from PIL import Image, ImageDraw, ImageTk

        img = Image.new('RGB', (w, h), (30, 30, 30))  # 深灰背景 #1e1e1e，与Canvas默认bg一致
        draw = ImageDraw.Draw(img)

        # 字体
        font = self._get_pil_font(10)

        # 绘制水平网格线 + 左Y轴刻度
        left_ticks = int(left_max / left_step)
        grid_color = (51, 51, 51)
        left_color = self._hex_to_rgb(colors.get(left_keys[0], "#888888"))  # 灰色fallback
        for i in range(left_ticks + 1):
            y_val = i * left_step
            y_pos = margin_t + chart_h - (y_val / left_max) * chart_h
            iy = int(y_pos)
            draw.line([(margin_l, iy), (margin_l + chart_w, iy)], fill=grid_color, width=1)
            draw.text((margin_l - 5, iy - 5), f"{y_val:.0f}",
                      fill=left_color, font=font)

        # 右Y轴刻度
        if right_keys:
            right_color = self._hex_to_rgb(colors.get(right_keys[0], "#888888"))
            right_ticks = int(right_max / right_step)
            for i in range(right_ticks + 1):
                y_val = i * right_step
                y_pos = margin_t + chart_h - (y_val / right_max) * chart_h
                iy = int(y_pos)
                draw.text((margin_l + chart_w + 4, iy - 5), f"{y_val:.0f}",
                          fill=right_color, font=font)

        # Y轴单位
        if unit_left:
            draw.text((margin_l - 5, margin_t - 12), unit_left,
                      fill=left_color, font=font)
        if unit_right and right_keys:
            right_color = self._hex_to_rgb(colors.get(right_keys[0], "#888888"))
            draw.text((margin_l + chart_w + 4, margin_t - 12), unit_right,
                      fill=right_color, font=font)

        # 绘制数据曲线（两层降采样：LOD层级聚合 + 像素级min/max保留）
        off = visible_offset
        x_scale = chart_w / (n - 1) if n > 1 else 1.0
        chart_bottom = margin_t + chart_h
        for key in list(data_dict.keys()):
            series = data_dict[key]
            data_len = len(series) - off
            if data_len < 2:
                continue
            color_rgb = self._hex_to_rgb(colors.get(key, "#888888"))
            y_max = y_axes.get(key, left_max)
            y_scale = chart_h / y_max

            # 使用LOD获取绘图数据
            if isinstance(series, _LODSeries):
                values, raw_indices = series.get_draw_data(off, data_len, chart_w)
            else:
                values = list(series[off:off + data_len])
                raw_indices = list(range(off, off + data_len))

            nv = len(values)
            if nv < 2:
                continue

            # 像素级降采样：每step个点保留min/max，确保视觉上不丢失峰值
            # 先记录min后max（X坐标递增），使draw.line能正确绘制连续折线
            max_points = chart_w * 2
            if nv > max_points:
                step = nv / max_points
                coords = []
                for si in range(max_points):
                    i_start = int(si * step)
                    i_end = min(int((si + 1) * step), nv)
                    if i_end <= i_start:
                        continue
                    min_v = values[i_start]
                    max_v = min_v
                    min_ri = raw_indices[i_start]
                    max_ri = min_ri
                    for j in range(i_start + 1, i_end):
                        v = values[j]
                        if v < min_v:
                            min_v = v
                            min_ri = raw_indices[j]
                        elif v > max_v:
                            max_v = v
                            max_ri = raw_indices[j]
                    if min_ri <= max_ri:
                        first_ri, first_v = min_ri, min_v
                        second_ri, second_v = max_ri, max_v
                    else:
                        first_ri, first_v = max_ri, max_v
                        second_ri, second_v = min_ri, min_v
                    coords.append((margin_l + (first_ri - off) * x_scale,
                                   chart_bottom - first_v * y_scale))
                    if min_v != max_v:
                        coords.append((margin_l + (second_ri - off) * x_scale,
                                       chart_bottom - second_v * y_scale))
            else:
                coords = []
                # 缓存频繁访问的变量到局部，减少属性查找开销
                _ml = margin_l
                _off = off
                _xs = x_scale
                _cb = chart_bottom
                _ys = y_scale
                for i in range(nv):
                    coords.append((_ml + (raw_indices[i] - _off) * _xs,
                                   _cb - values[i] * _ys))

            if len(coords) >= 2:
                # draw.line绘制连续折线，比逐段draw.line性能更高
                draw.line(coords, fill=color_rgb, width=2)

        # X轴时间标签
        def _fmt_time(num_points):
            total_sec = int(num_points * interval_ms / 1000)
            m, s = divmod(total_sec, 60)
            h, m = divmod(m, 60)
            if h > 0:
                return f"-{h}:{m:02d}:{s:02d}"
            return f"-{m}:{s:02d}" if m > 0 else f"-{s}s"

        label_color = (136, 136, 136)
        # X轴三个时间标签：起点(最旧)、50%处、终点(现在)
        for frac, label in [(0, _fmt_time(n - 1)), (0.5, _fmt_time(int((n - 1) * 0.5))), (1.0, "现在")]:
            x = margin_l + frac * chart_w
            draw.text((int(x) - 5, margin_t + chart_h + 4), label,
                      fill=label_color, font=font)

        # 渲染到canvas：PIL渲染到图片后通过PhotoImage显示到Canvas
        # 复用PhotoImage和canvas项：paste+itemconfig替代delete+create，
        # 避免重复创建PhotoImage的开销，且保证多Toplevel窗口下Tk正常刷新
        try:
            canvas_id = id(canvas)
            # 懒初始化：监控窗口未打开时无需这些字典
            # 三个字典以canvas_id为键，必须同步维护
            if not hasattr(self, '_chart_photos'):
                self._chart_photos = {}          # {canvas_id: ImageTk.PhotoImage}
            if not hasattr(self, '_chart_canvas_items'):
                self._chart_canvas_items = {}    # {canvas_id: canvas item id}
            if not hasattr(self, '_chart_photo_sizes'):
                self._chart_photo_sizes = {}     # {canvas_id: (w, h)}

            photo = self._chart_photos.get(canvas_id)
            canvas_item = self._chart_canvas_items.get(canvas_id)
            prev_size = self._chart_photo_sizes.get(canvas_id)

            # 尺寸不变时复用PhotoImage（paste不改变尺寸，尺寸变化必须重建）
            if photo is not None and prev_size == (w, h):
                try:
                    photo.paste(img)
                    # paste后需要通知canvas重绘该项，否则Tk可能不刷新显示
                    if canvas_item is not None:
                        canvas.itemconfig(canvas_item, image=photo)
                except Exception:
                    photo = None
            else:
                photo = None

            if photo is None:
                # 首次创建或尺寸变化后重建
                photo = ImageTk.PhotoImage(img)
                canvas.delete("all")
                canvas_item = canvas.create_image(0, 0, anchor=tk.NW, image=photo)
                self._chart_photos[canvas_id] = photo
                self._chart_canvas_items[canvas_id] = canvas_item
                self._chart_photo_sizes[canvas_id] = (w, h)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Chart render error: {e}")

    @staticmethod
    def _hex_to_rgb(hex_color):
        """将 #RRGGBB 转为 (R, G, B) 元组（仅支持6位十六进制格式）"""
        h = hex_color.lstrip('#')
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))

    @staticmethod
    def _get_pil_font(size=10):
        """获取PIL字体，优先使用等宽字体（仅Windows）"""
        from PIL import ImageFont
        # Consolas(两种大小写) > Courier New > Lucida Console > Arial
        font_names = ["consola.ttf", "Consolas.ttf", "cour.ttf", "lucon.ttf", "arial.ttf"]
        for name in font_names:
            try:
                return ImageFont.truetype(name, size)
            except Exception:
                continue
        # 尝试 Windows 字体目录（完整路径，大小写变体不再需要）
        import os
        windir = os.environ.get('WINDIR', r'C:\Windows')
        for name in ["consola.ttf", "cour.ttf", "lucon.ttf", "arial.ttf"]:
            path = os.path.join(windir, 'Fonts', name)
            if os.path.exists(path):
                try:
                    return ImageFont.truetype(path, size)
                except Exception:
                    continue
        return ImageFont.load_default()

    # ==================== 内存监控窗口 ====================

    def _show_memory_monitor(self):
        """显示内存监控窗口（含实时曲线图、控件）"""
        if self._mem_monitor_win is not None:
            try:
                self._mem_monitor_win.lift()
                return
            except tk.TclError:
                self._mem_monitor_win = None

        win = tk.Toplevel(self.root)
        win.title("内存监控")
        win.geometry("700x480")
        win.resizable(True, True)
        win.minsize(520, 360)
        self._mem_monitor_win = win

        def _on_win_close():
            """关闭内存监控窗口：置空引用、取消定时器、清理PIL缓存、销毁窗口"""
            self._mem_monitor_win = None
            self._mem_dirty = False
            # 取消采样定时器（after_cancel在回调已执行/已取消时可能抛TclError）
            if self._mem_after_id is not None:
                try:
                    self.root.after_cancel(self._mem_after_id)
                except Exception:
                    pass
                self._mem_after_id = None
            if self._mem_draw_after_id is not None:
                try:
                    self.root.after_cancel(self._mem_draw_after_id)
                except Exception:
                    pass
                self._mem_draw_after_id = None
            # 清理PIL图片缓存（避免PhotoImage引用泄漏导致字典持续增长）
            cid = id(self._mem_canvas) if self._mem_canvas else None
            self._mem_canvas = None
            if cid and hasattr(self, '_chart_photos'):
                self._chart_photos.pop(cid, None)
                self._chart_canvas_items.pop(cid, None)
                self._chart_photo_sizes.pop(cid, None)
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", _on_win_close)

        # ---- 数值显示区 ----
        info_frame = ttk.Frame(win, padding=(10, 6))
        info_frame.pack(fill=tk.X)

        self._mem_tool_var = tk.StringVar(value="工具: -- MB")
        self._mem_game_var = tk.StringVar(value="游戏: -- MB")
        self._mem_sys_var = tk.StringVar(value="系统可用: -- MB")

        ttk.Label(info_frame, textvariable=self._mem_tool_var, font=("Consolas", 10),
                  foreground="#569cd6").pack(side=tk.LEFT, padx=(0, 16))
        ttk.Label(info_frame, textvariable=self._mem_game_var, font=("Consolas", 10),
                  foreground="#6a9955").pack(side=tk.LEFT, padx=(0, 16))
        # 系统可用放最右侧，对应右Y轴
        ttk.Label(info_frame, textvariable=self._mem_sys_var, font=("Consolas", 10),
                  foreground="#cca700").pack(side=tk.RIGHT)

        # ---- 控件区 ----
        ctrl_frame = ttk.Frame(win, padding=(10, 0, 10, 4))
        ctrl_frame.pack(fill=tk.X)

        ttk.Label(ctrl_frame, text="刷新率:", font=("Microsoft YaHei UI", 8)).pack(side=tk.LEFT)
        self._mem_interval_var = tk.IntVar(value=self._mem_sample_interval)
        self._mem_interval_spin = ttk.Spinbox(ctrl_frame, from_=10, to=10000, increment=100,
                                               textvariable=self._mem_interval_var, width=5,
                                               command=self._mem_apply_interval)
        self._mem_interval_spin.pack(side=tk.LEFT, padx=(2, 2))
        self._mem_interval_spin.bind("<Return>", lambda e: self._mem_apply_interval())
        self._mem_interval_spin.bind("<FocusOut>", lambda e: self._mem_apply_interval())
        ttk.Label(ctrl_frame, text="ms", font=("Microsoft YaHei UI", 8)).pack(side=tk.LEFT, padx=(0, 12))

        ttk.Label(ctrl_frame, text="记录时长:", font=("Microsoft YaHei UI", 8)).pack(side=tk.LEFT)
        self._mem_max_min_var = tk.IntVar(value=self._mem_max_minutes)
        self._mem_max_min_spin = ttk.Spinbox(ctrl_frame, from_=1, to=60000, increment=1,
                                              textvariable=self._mem_max_min_var, width=4,
                                              command=self._mem_apply_max_minutes)
        self._mem_max_min_spin.pack(side=tk.LEFT, padx=(2, 2))
        self._mem_max_min_spin.bind("<Return>", lambda e: self._mem_apply_max_minutes())
        self._mem_max_min_spin.bind("<FocusOut>", lambda e: self._mem_apply_max_minutes())
        ttk.Label(ctrl_frame, text="分钟", font=("Microsoft YaHei UI", 8)).pack(side=tk.LEFT, padx=(0, 12))

        ttk.Label(ctrl_frame, text="滚轮缩放横向", font=("Microsoft YaHei UI", 8),
                  foreground="gray").pack(side=tk.RIGHT)

        # ---- 图表画布 ----
        chart_frame = ttk.Frame(win, padding=(10, 0, 10, 2))
        chart_frame.pack(fill=tk.BOTH, expand=True)

        self._mem_canvas = tk.Canvas(chart_frame, bg="#1e1e1e", highlightthickness=0)
        self._mem_canvas.pack(fill=tk.BOTH, expand=True)

        # 滚轮缩放
        def _mem_wheel(event):
            if event.delta > 0 or event.num == 4:
                self._mem_zoom = min(self._mem_zoom * 1.2, 30.0)
            elif event.delta < 0 or event.num == 5:
                self._mem_zoom = max(self._mem_zoom / 1.2, 0.05)
            self._mem_invalidate()

        self._mem_canvas.bind("<MouseWheel>", _mem_wheel)
        self._mem_canvas.bind("<Button-4>", _mem_wheel)
        self._mem_canvas.bind("<Button-5>", _mem_wheel)
        # 画布尺寸变化时标记脏并请求绘制（窗口resize/移动）
        self._mem_canvas.bind("<Configure>", lambda e: self._mem_invalidate())

        # ---- 图例 ----
        legend_frame = ttk.Frame(win, padding=(10, 0, 10, 8))
        legend_frame.pack(fill=tk.X)
        for color, label in [("#569cd6", "工具内存 (左轴)"), ("#6a9955", "游戏内存 (左轴)"),
                              ("#cca700", "系统可用内存 (右轴)")]:
            c = tk.Canvas(legend_frame, width=16, height=10, bg="#2d2d2d", highlightthickness=0)
            c.create_line(2, 5, 14, 5, fill=color, width=2)
            c.pack(side=tk.LEFT, padx=(0, 2))
            ttk.Label(legend_frame, text=label, font=("Microsoft YaHei UI", 8)).pack(side=tk.LEFT, padx=(0, 12))

        # 清空历史数据并启动采样
        maxlen = int(self._mem_max_minutes * 60 * 1000 / self._mem_sample_interval)
        self._mem_data = {"tool": _LODSeries(), "game": _LODSeries(), "system_avail": _LODSeries()}
        self._mem_maxlen = maxlen
        self._mem_zoom = 1.0
        self._mem_peak = {"left": 0.0, "right": 0.0}
        # 启动采样（绘制由脏标记按需触发）
        self._mem_sample()

    def _mem_apply_interval(self):
        """应用内存监控刷新率设置（自动校验修正：10ms~10000ms）"""
        try:
            val = int(self._mem_interval_spin.get())
        except (ValueError, tk.TclError):
            return
        # 校验修正
        val = max(10, min(10000, val))
        self._mem_interval_spin.delete(0, tk.END)
        self._mem_interval_spin.insert(0, str(val))
        self._mem_sample_interval = val
        # 重新计算容量
        maxlen = int(self._mem_max_minutes * 60 * 1000 / val)
        self._mem_maxlen = maxlen
        for key in self._mem_data:
            self._mem_data[key].trim(maxlen)
        # 取消当前定时器并按新间隔重新安排
        if self._mem_after_id is not None:
            try:
                self.root.after_cancel(self._mem_after_id)
            except Exception:
                pass
            self._mem_after_id = None
        if self._mem_draw_after_id is not None:
            try:
                self.root.after_cancel(self._mem_draw_after_id)
            except Exception:
                pass
            self._mem_draw_after_id = None
        self._mem_after_id = self.root.after(val, self._mem_sample)

    def _mem_apply_max_minutes(self):
        """应用内存监控最大记录时长设置（自动校验修正：1分钟~60000分钟/1000小时）"""
        try:
            val = int(self._mem_max_min_spin.get())
        except (ValueError, tk.TclError):
            return
        # 校验修正
        val = max(1, min(60000, val))
        self._mem_max_min_spin.delete(0, tk.END)
        self._mem_max_min_spin.insert(0, str(val))
        self._mem_max_minutes = val
        # 重新计算容量
        maxlen = int(val * 60 * 1000 / self._mem_sample_interval)
        self._mem_maxlen = maxlen
        for key in self._mem_data:
            self._mem_data[key].trim(maxlen)
        # 重建后峰值可能失效，重置并触发重绘
        self._mem_peak = {"left": 0.0, "right": 0.0}
        self._mem_invalidate()

    def _mem_sample(self):
        """定时采样内存数据"""
        if self._mem_monitor_win is None:
            return

        try:
            import psutil
            tool_mem = psutil.Process(os.getpid()).memory_info().rss / 1024 / 1024

            # 查找游戏进程内存
            game_mem = 0.0
            for proc in psutil.process_iter(['name', 'memory_info']):
                try:
                    name = (proc.info['name'] or '').lower()
                    if name in ('ageofempires4.exe', 'age4_x64.exe', 'reliccardinal.exe'):
                        game_mem += proc.info['memory_info'].rss / 1024 / 1024
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    pass

            sys_avail = psutil.virtual_memory().available / 1024 / 1024
        except Exception:
            tool_mem = 0.0
            game_mem = 0.0
            sys_avail = 0.0

        # 追加数据（_LODSeries自动维护LOD层级，O(1)追加）
        self._mem_data["tool"].append(tool_mem)
        self._mem_data["game"].append(game_mem)
        self._mem_data["system_avail"].append(sys_avail)

        # 定期淘汰旧数据（每1000次采样检查一次，避免频繁trim）
        if len(self._mem_data["tool"]) > self._mem_maxlen + 500:
            for key in self._mem_data:
                self._mem_data[key].trim(self._mem_maxlen)

        # 更新峰值缓存
        left_peak = max(tool_mem, game_mem, self._mem_peak["left"])
        right_peak = max(sys_avail, self._mem_peak["right"])
        self._mem_peak["left"] = left_peak
        self._mem_peak["right"] = right_peak

        # 更新数值
        self._mem_tool_var.set(f"工具: {tool_mem:.1f} MB")
        self._mem_game_var.set(f"游戏: {game_mem:.1f} MB" if game_mem > 0 else "游戏: 未运行")
        self._mem_sys_var.set(f"系统可用: {sys_avail:.0f} MB")

        # 标记数据脏，触发按需绘制
        self._mem_dirty = True
        self._mem_request_draw()

        # 安排下一次采样
        try:
            self._mem_after_id = self.root.after(self._mem_sample_interval, self._mem_sample)
        except tk.TclError:
            pass

    def _mem_invalidate(self):
        """标记脏并请求绘制（窗口resize/缩放等外部事件触发）"""
        self._mem_dirty = True
        self._mem_request_draw()

    def _mem_request_draw(self):
        """请求绘制：如尚未安排则调度一次，避免重复调度"""
        if self._mem_draw_after_id is not None:
            return  # 已有待执行的绘制，不重复调度（resize等事件可能高频触发）
        # 绘制间隔 = max(20ms, 采样间隔)
        # 20ms保证最高50帧且双窗口同时打开时不压垮事件循环；
        # 采样间隔保证不超采样频率，且resize后不会等太久才更新
        draw_interval = max(20, self._mem_sample_interval)
        try:
            self._mem_draw_after_id = self.root.after(draw_interval, self._mem_do_draw)
        except tk.TclError:
            pass

    def _mem_do_draw(self):
        """执行绘制：数据脏时绘制，绘制后若又有新数据则再排一帧"""
        self._mem_draw_after_id = None
        if self._mem_monitor_win is None:
            return
        if self._mem_dirty:
            self._mem_dirty = False
            try:
                self._mem_draw_chart()
            except Exception:
                # 绘制出错时恢复dirty标记，确保下次采样到来能重试
                self._mem_dirty = True
            # 调度间隔期间_sample可能已将dirty重新标记为True，若有则再排一帧
            if self._mem_dirty:
                self._mem_request_draw()

    def _mem_draw_chart(self):
        """绘制内存曲线图（双Y轴 + 横向缩放）"""
        if self._mem_canvas is None:
            return

        # 应用缩放：zoom越大显示越少的数据点（放大）
        all_data = self._mem_data
        n = max(len(v) for v in all_data.values()) if all_data else 0
        if n < 2:
            return

        # 缩放后显示的采样点数
        visible = max(int(n / self._mem_zoom), 10)
        visible = min(visible, n)

        # 计算偏移量，传给绘图函数（LOD自动选择合适的层级）
        offset = n - visible

        colors = {"tool": "#569cd6", "game": "#6a9955", "system_avail": "#cca700"}
        y_axes = {}
        self._perf_draw_chart(
            self._mem_canvas, all_data,
            left_keys=["tool", "game"], right_keys=["system_avail"],
            y_axes=y_axes, colors=colors,
            unit_left="MB", unit_right="MB",
            interval_ms=self._mem_sample_interval,
            left_peak=self._mem_peak["left"],
            right_peak=self._mem_peak["right"],
            visible_offset=offset
        )

    # ==================== CPU监控窗口 ====================

    def _show_cpu_monitor(self):
        """显示CPU监控窗口（含实时曲线图、控件）"""
        if self._cpu_monitor_win is not None:
            try:
                self._cpu_monitor_win.lift()
                return
            except tk.TclError:
                self._cpu_monitor_win = None

        win = tk.Toplevel(self.root)
        win.title("CPU监控")
        win.geometry("700x480")
        win.resizable(True, True)
        win.minsize(520, 360)
        self._cpu_monitor_win = win

        def _on_win_close():
            """关闭CPU监控窗口：置空引用、取消定时器、清理PIL缓存、销毁窗口"""
            self._cpu_monitor_win = None
            self._cpu_dirty = False
            # 取消采样定时器（after_cancel在回调已执行/已取消时可能抛TclError）
            if self._cpu_after_id is not None:
                try:
                    self.root.after_cancel(self._cpu_after_id)
                except Exception:
                    pass
                self._cpu_after_id = None
            if self._cpu_draw_after_id is not None:
                try:
                    self.root.after_cancel(self._cpu_draw_after_id)
                except Exception:
                    pass
                self._cpu_draw_after_id = None
            # 清理PIL图片缓存（避免PhotoImage引用泄漏导致字典持续增长）
            cid = id(self._cpu_canvas) if self._cpu_canvas else None
            self._cpu_canvas = None
            if cid and hasattr(self, '_chart_photos'):
                self._chart_photos.pop(cid, None)
                self._chart_canvas_items.pop(cid, None)
                self._chart_photo_sizes.pop(cid, None)
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", _on_win_close)

        # ---- 数值显示区 ----
        info_frame = ttk.Frame(win, padding=(10, 6))
        info_frame.pack(fill=tk.X)

        self._cpu_tool_var = tk.StringVar(value="工具: --%")
        self._cpu_game_var = tk.StringVar(value="游戏: --%")
        self._cpu_sys_var = tk.StringVar(value="系统空闲: --%")

        ttk.Label(info_frame, textvariable=self._cpu_tool_var, font=("Consolas", 10),
                  foreground="#569cd6").pack(side=tk.LEFT, padx=(0, 16))
        ttk.Label(info_frame, textvariable=self._cpu_game_var, font=("Consolas", 10),
                  foreground="#6a9955").pack(side=tk.LEFT, padx=(0, 16))
        ttk.Label(info_frame, textvariable=self._cpu_sys_var, font=("Consolas", 10),
                  foreground="#cca700").pack(side=tk.RIGHT)

        # ---- 控件区 ----
        ctrl_frame = ttk.Frame(win, padding=(10, 0, 10, 4))
        ctrl_frame.pack(fill=tk.X)

        ttk.Label(ctrl_frame, text="刷新率:", font=("Microsoft YaHei UI", 8)).pack(side=tk.LEFT)
        self._cpu_interval_var = tk.IntVar(value=self._cpu_sample_interval)
        self._cpu_interval_spin = ttk.Spinbox(ctrl_frame, from_=10, to=10000, increment=100,
                                               textvariable=self._cpu_interval_var, width=5,
                                               command=self._cpu_apply_interval)
        self._cpu_interval_spin.pack(side=tk.LEFT, padx=(2, 2))
        self._cpu_interval_spin.bind("<Return>", lambda e: self._cpu_apply_interval())
        self._cpu_interval_spin.bind("<FocusOut>", lambda e: self._cpu_apply_interval())
        ttk.Label(ctrl_frame, text="ms", font=("Microsoft YaHei UI", 8)).pack(side=tk.LEFT, padx=(0, 12))

        ttk.Label(ctrl_frame, text="记录时长:", font=("Microsoft YaHei UI", 8)).pack(side=tk.LEFT)
        self._cpu_max_min_var = tk.IntVar(value=self._cpu_max_minutes)
        self._cpu_max_min_spin = ttk.Spinbox(ctrl_frame, from_=1, to=60000, increment=1,
                                              textvariable=self._cpu_max_min_var, width=4,
                                              command=self._cpu_apply_max_minutes)
        self._cpu_max_min_spin.pack(side=tk.LEFT, padx=(2, 2))
        self._cpu_max_min_spin.bind("<Return>", lambda e: self._cpu_apply_max_minutes())
        self._cpu_max_min_spin.bind("<FocusOut>", lambda e: self._cpu_apply_max_minutes())
        ttk.Label(ctrl_frame, text="分钟", font=("Microsoft YaHei UI", 8)).pack(side=tk.LEFT, padx=(0, 12))

        ttk.Label(ctrl_frame, text="滚轮缩放横向", font=("Microsoft YaHei UI", 8),
                  foreground="gray").pack(side=tk.RIGHT)

        # ---- 图表画布 ----
        chart_frame = ttk.Frame(win, padding=(10, 0, 10, 2))
        chart_frame.pack(fill=tk.BOTH, expand=True)

        self._cpu_canvas = tk.Canvas(chart_frame, bg="#1e1e1e", highlightthickness=0)
        self._cpu_canvas.pack(fill=tk.BOTH, expand=True)

        # 滚轮缩放
        def _cpu_wheel(event):
            if event.delta > 0 or event.num == 4:
                self._cpu_zoom = min(self._cpu_zoom * 1.2, 30.0)
            elif event.delta < 0 or event.num == 5:
                self._cpu_zoom = max(self._cpu_zoom / 1.2, 0.05)
            self._cpu_invalidate()

        self._cpu_canvas.bind("<MouseWheel>", _cpu_wheel)
        self._cpu_canvas.bind("<Button-4>", _cpu_wheel)
        self._cpu_canvas.bind("<Button-5>", _cpu_wheel)
        # 画布尺寸变化时标记脏并请求绘制（窗口resize/移动）
        self._cpu_canvas.bind("<Configure>", lambda e: self._cpu_invalidate())

        # ---- 图例 ----
        legend_frame = ttk.Frame(win, padding=(10, 0, 10, 8))
        legend_frame.pack(fill=tk.X)
        for color, label in [("#569cd6", "工具CPU (左轴)"), ("#6a9955", "游戏CPU (左轴)"),
                              ("#cca700", "系统空闲CPU (左轴)")]:
            c = tk.Canvas(legend_frame, width=16, height=10, bg="#2d2d2d", highlightthickness=0)
            c.create_line(2, 5, 14, 5, fill=color, width=2)
            c.pack(side=tk.LEFT, padx=(0, 2))
            ttk.Label(legend_frame, text=label, font=("Microsoft YaHei UI", 8)).pack(side=tk.LEFT, padx=(0, 12))

        # 清空历史数据并启动采样
        maxlen = int(self._cpu_max_minutes * 60 * 1000 / self._cpu_sample_interval)
        self._cpu_data = {"tool": _LODSeries(), "game": _LODSeries(), "system_avail": _LODSeries()}
        self._cpu_maxlen = maxlen
        self._cpu_zoom = 1.0
        self._cpu_tool_proc = None
        self._cpu_game_procs = []
        self._cpu_game_warmup = set()
        # 预热 psutil 的 cpu_percent（首次调用返回0，需先初始化内部缓存）
        try:
            import psutil
            psutil.cpu_percent(interval=None)
            self._cpu_tool_proc = psutil.Process(os.getpid())
            self._cpu_tool_proc.cpu_percent(interval=None)
        except Exception:
            pass
        # 启动采样（绘制由脏标记按需触发）
        self._cpu_sample()

    def _cpu_apply_interval(self):
        """应用CPU监控刷新率设置（自动校验修正：10ms~10000ms）"""
        try:
            val = int(self._cpu_interval_spin.get())
        except (ValueError, tk.TclError):
            return
        # 校验修正
        val = max(10, min(10000, val))
        self._cpu_interval_spin.delete(0, tk.END)
        self._cpu_interval_spin.insert(0, str(val))
        self._cpu_sample_interval = val
        # 重新计算容量
        maxlen = int(self._cpu_max_minutes * 60 * 1000 / val)
        self._cpu_maxlen = maxlen
        for key in self._cpu_data:
            self._cpu_data[key].trim(maxlen)
        self._cpu_invalidate()

        # 取消当前定时器并按新间隔重新安排
        if self._cpu_after_id is not None:
            try:
                self.root.after_cancel(self._cpu_after_id)
            except Exception:
                pass
            self._cpu_after_id = None
        if self._cpu_draw_after_id is not None:
            try:
                self.root.after_cancel(self._cpu_draw_after_id)
            except Exception:
                pass
            self._cpu_draw_after_id = None
        self._cpu_after_id = self.root.after(val, self._cpu_sample)

    def _cpu_apply_max_minutes(self):
        """应用CPU监控最大记录时长设置（自动校验修正：1分钟~60000分钟/1000小时）"""
        try:
            val = int(self._cpu_max_min_spin.get())
        except (ValueError, tk.TclError):
            return
        # 校验修正
        val = max(1, min(60000, val))
        self._cpu_max_min_spin.delete(0, tk.END)
        self._cpu_max_min_spin.insert(0, str(val))
        self._cpu_max_minutes = val
        # 重新计算容量
        maxlen = int(val * 60 * 1000 / self._cpu_sample_interval)
        self._cpu_maxlen = maxlen
        for key in self._cpu_data:
            self._cpu_data[key].trim(maxlen)
        self._cpu_invalidate()



    def _cpu_sample(self):
        """定时采样CPU数据并更新图表"""
        if self._cpu_monitor_win is None:
            return

        try:
            import psutil

            # psutil.Process.cpu_percent() 返回值是相对所有CPU核心的总百分比，
            # 多核机器上单进程可超过100%。除以核心数得到占总CPU容量的百分比，
            # 与Windows任务管理器显示一致。
            cpu_count = psutil.cpu_count() or 1

            # 工具进程CPU：复用缓存的Process实例
            if self._cpu_tool_proc is None:
                self._cpu_tool_proc = psutil.Process(os.getpid())
                self._cpu_tool_proc.cpu_percent(interval=None)  # 首次初始化
                tool_cpu = 0.0
            else:
                tool_cpu = self._cpu_tool_proc.cpu_percent(interval=None) / cpu_count

            # 刷新游戏进程缓存：移除已死的，添加新发现的
            alive_procs = []
            new_pids = set()
            game_names = ('ageofempires4.exe', 'age4_x64.exe', 'reliccardinal.exe')
            known_pids = set()
            for proc in self._cpu_game_procs:
                try:
                    if proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE:
                        alive_procs.append(proc)
                        known_pids.add(proc.pid)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            # 清理预热表中已不存在的PID
            self._cpu_game_warmup &= known_pids
            # 扫描新出现的游戏进程
            for proc in psutil.process_iter(['name']):
                try:
                    name = (proc.info['name'] or '').lower()
                    if name in game_names and proc.pid not in known_pids:
                        proc.cpu_percent(interval=None)  # 首次初始化
                        alive_procs.append(proc)
                        known_pids.add(proc.pid)
                        new_pids.add(proc.pid)
                        self._cpu_game_warmup.add(proc.pid)
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    pass
            self._cpu_game_procs = alive_procs

            # 获取游戏CPU（预热中的进程跳过，因为间隔不足会返回异常值）
            game_cpu = 0.0
            warmed_up = set()
            for proc in self._cpu_game_procs:
                try:
                    if proc.pid in self._cpu_game_warmup:
                        # 预热中：调用一次使内部计时器推进，但不计入结果
                        proc.cpu_percent(interval=None)
                        warmed_up.add(proc.pid)
                    else:
                        game_cpu += proc.cpu_percent(interval=None) / cpu_count
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    pass
            # 经过一次采样间隔后，预热完成的进程下次可正常采样
            self._cpu_game_warmup -= warmed_up

            # psutil.cpu_percent() 返回系统总占用率，可用率 = 100 - 占用率
            sys_cpu = 100.0 - psutil.cpu_percent(interval=None)
        except Exception:
            tool_cpu = 0.0
            game_cpu = 0.0
            sys_cpu = 0.0


        # 追加数据（_LODSeries自动维护LOD层级，O(1)追加）
        self._cpu_data["tool"].append(tool_cpu)
        self._cpu_data["game"].append(game_cpu)
        self._cpu_data["system_avail"].append(sys_cpu)

        # 定期淘汰旧数据
        if len(self._cpu_data["tool"]) > self._cpu_maxlen + 500:
            for key in self._cpu_data:
                self._cpu_data[key].trim(self._cpu_maxlen)

        # 更新数值
        self._cpu_tool_var.set(f"工具: {tool_cpu:.1f}%")
        self._cpu_game_var.set(f"游戏: {game_cpu:.1f}%" if game_cpu > 0 else "游戏: 未运行")
        self._cpu_sys_var.set(f"系统空闲: {sys_cpu:.1f}%")

        # 标记数据脏，触发按需绘制
        self._cpu_dirty = True
        self._cpu_request_draw()

        # 安排下一次采样
        try:
            self._cpu_after_id = self.root.after(self._cpu_sample_interval, self._cpu_sample)
        except tk.TclError:
            pass

    def _cpu_invalidate(self):
        """标记脏并请求绘制（窗口resize/缩放等外部事件触发）"""
        self._cpu_dirty = True
        self._cpu_request_draw()

    def _cpu_request_draw(self):
        """请求绘制：如尚未安排则调度一次，避免重复调度"""
        if self._cpu_draw_after_id is not None:
            return  # 已有待执行的绘制，不重复调度（resize等事件可能高频触发）
        # 绘制间隔 = max(20ms, 采样间隔)
        # 20ms保证最高50帧且双窗口同时打开时不压垮事件循环；
        # 采样间隔保证不超采样频率，且resize后不会等太久才更新
        draw_interval = max(20, self._cpu_sample_interval)
        try:
            self._cpu_draw_after_id = self.root.after(draw_interval, self._cpu_do_draw)
        except tk.TclError:
            pass

    def _cpu_do_draw(self):
        """执行绘制：数据脏时绘制，绘制后若又有新数据则再排一帧"""
        self._cpu_draw_after_id = None
        if self._cpu_monitor_win is None:
            return
        if self._cpu_dirty:
            self._cpu_dirty = False
            try:
                self._cpu_draw_chart()
            except Exception:
                # 绘制出错时恢复dirty标记，确保下次采样到来能重试
                self._cpu_dirty = True
            if self._cpu_dirty:
                self._cpu_request_draw()

    def _cpu_draw_chart(self):
        """绘制CPU曲线图（单Y轴，0-100% + 横向缩放）"""
        if self._cpu_canvas is None:
            return

        all_data = self._cpu_data
        n = max(len(v) for v in all_data.values()) if all_data else 0
        if n < 2:
            return

        # 缩放
        visible = max(int(n / self._cpu_zoom), 10)
        visible = min(visible, n)
        offset = n - visible

        colors = {"tool": "#569cd6", "game": "#6a9955", "system_avail": "#cca700"}
        y_axes = {}

        # CPU所有曲线共用左Y轴，固定0-100%
        for key in all_data:
            y_axes[key] = 100

        self._perf_draw_chart(
            self._cpu_canvas, all_data,
            left_keys=["tool", "game", "system_avail"], right_keys=[],
            y_axes=y_axes, colors=colors,
            unit_left="%", unit_right="",
            interval_ms=self._cpu_sample_interval,
            visible_offset=offset
        )

    def _show_help(self):
        help_win = tk.Toplevel(self.root)
        help_win.title("帮助 - AOE4 自动生产村民工具")
        help_win.geometry("680x560")
        help_win.resizable(True, True)

        text = scrolledtext.ScrolledText(
            help_win, wrap=tk.WORD, font=("Microsoft YaHei UI", 10),
            padx=12, pady=10
        )
        text.pack(fill=tk.BOTH, expand=True)

        help_content = """\
═══════════════════════════════════════
     AOE4 自动生产村民工具 - 使用帮助
═══════════════════════════════════════

【工具原理】
本工具通过 OCR 识别帝国时代4游戏画面中的人口、食物、村民数量等信息，自动判断是否需要生产村民。当检测到没有村民在生产时，自动选中城镇中心（TC）并排队生产村民。

【功能按钮说明】

▶ 启动
  开始运行自动生产村民的主循环。首次启动会初始化OCR模型（约需几秒到十几秒）。

■ 停止
  完全停止主循环。停止后可以释放OCR模型占用的内存。如果运行时间较长导致内存占用增加，可以停止后重新启动来清理内存。

⏸ 暂停 / ▶ 继续
  暂停或继续自动生产村民。暂停期间程序仍会检测游戏状态但不会执行生产操作。适用于同一局游戏中暂时不想自动生产的场景（如村民数量过多时）。

↺ 清零TC
  清零TC（城镇中心）数量缓存。适用于以下场景：
  • 开始新一局游戏时
  • 当前游戏中的TC被摧毁时
  
  清零后，程序会在下一轮检测时重新识别TC数量。如果之前的缓存值大于实际TC数，可能导致生产过多的村民。

清除日志
  清除日志区域的所有内容。

【配置设置】
点击"⚙ 配置"按钮可打开配置窗口，调整所有工具参数：
  • 核心参数：村民上限（⚠仅供参考，功能不稳定）、最低食物、每TC排队数等
  • 按键设置：选所有TC按键、生产村民按键、Shift批量排队
  • 操作时序：检测间隔、操作延迟、输入屏蔽等
  • OCR设置：GPU加速（⚠不建议开启，CPU更快）、图片缩放比例
  • 按键延迟：TC选中/重试延迟、最大重试次数等
  • 模板匹配阈值：各识别模块的匹配阈值
  • 调试开关：全局调试、性能分析等

【关于村民上限】
村民上限检测默认关闭，因为OCR只能统计空闲村民，正在移动、建造、战斗中的村民无法统计，因此检测值偏低，功能不稳定。如需启用，在配置中打开"村民上限检测"开关，但请注意该数值仅供参考。

【按键设置说明】
  • 选所有TC按键：默认H键，需与游戏内"选择所有城镇中心"的快捷键一致（注意是选择所有TC，不是选择单个TC）
  • 生产村民按键：默认Q键，需与游戏内"生产村民"的快捷键一致
  • Shift批量排队：默认开启，使用Shift+生产键每次排5个村民，关闭则逐个排队

配置修改后实时生效（无需重启）。
点击"保存"将配置持久化到 config_override.json 文件（下次启动自动加载）。
如果所有配置都恢复默认值并保存，则自动删除配置文件。
点击"恢复默认"可将所有参数还原为初始值。

【提示】
• 自动生成村民时会屏蔽输入（键盘鼠标）以防止误操作，操作完成后会自动恢复输入，执行期间会通过Ctrl+0临时保存当前选中的单位，执行完成后自动恢复
• 修饰键暂停：在游戏中按住 Shift、Ctrl 或 Alt 键时会临时暂停自动生产，松开后自动恢复，目的同样是防止按键冲突
• 内存优化：长时间运行后如果内存占用较高，可以点击"停止"再"启动"来释放并重新加载OCR模型
• 开局建议：游牧开局时（没有TC），程序会自动跳过，等建造完TC并手动生产第一个村民后即可自动恢复正常工作
• 多TC支持：生产总数 = 每tc生产数量 * tc数量。由于是同时选中所有tc并生成村民，所有有可能分配不均匀需要手动调整。

【自定义模板图片】
在配置窗口中点击"模板图片"可查看所有模板。如需替换某个模板：
  1. 在 exe 同目录下创建 user_templates 文件夹
  2. 放入与内置模板同名的图片文件（如 cunmin.png、tc_single.png）
  3. 重启程序即可自动使用替换图片
替换优先级：user_templates 中的同名文件 > 内置模板

【注意事项】
• 本程序需要管理员权限运行（用于输入屏蔽功能）
• 如果OCR识别不准确，可在配置窗口中调整 OCR图片缩放 参数

【快捷键】
点击"快捷键"按钮可以为各功能设置自定义快捷键：
  • 点击输入框后直接按下快捷键即可自动捕获
  • 支持单键（F9、Space）和组合键（Ctrl+S、Alt+P）
  • 快捷键配置保存在程序同目录的 shortcuts.json 文件
  • 删除该文件即可恢复默认（无快捷键）
  • 要导入/导出配置，直接替换 shortcuts.json 文件即可
"""

        text.insert(tk.END, help_content)
        text.configure(state='disabled')

        ttk.Button(help_win, text="关闭", command=help_win.destroy).pack(pady=8)

    # ==================== 快捷键设置窗口 ====================

    def _show_shortcut_dialog(self):
        sc_win = tk.Toplevel(self.root)
        sc_win.title("快捷键设置")
        sc_win.geometry("520x340")
        sc_win.resizable(False, False)
        sc_win.grab_set()

        ttk.Label(
            sc_win,
            text="点击输入框后按键自动捕获，也可手动输入（如 Ctrl+S, Alt+P, F5, Ctrl+Space）",
            font=("Microsoft YaHei UI", 9),
            wraplength=480
        ).pack(padx=15, pady=(12, 6))

        # 输入框
        entries = {}
        capture_labels = {}
        frame = ttk.Frame(sc_win, padding=(15, 5))
        frame.pack(fill=tk.BOTH, expand=True)

        for i, (action, label) in enumerate(SHORTCUT_ACTIONS.items()):
            ttk.Label(frame, text=f"{label}:", font=("Microsoft YaHei UI", 10)).grid(
                row=i, column=0, sticky=tk.E, padx=(0, 8), pady=4
            )
            var = tk.StringVar(value=_display_shortcut(self._shortcuts.get(action, "")))
            entry = ttk.Entry(frame, textvariable=var, width=20)
            entry.grid(row=i, column=1, pady=4)
            entries[action] = var

            # 捕获状态提示
            cap_label = ttk.Label(frame, text="点击捕获", foreground="gray",
                                  font=("Microsoft YaHei UI", 8))
            cap_label.grid(row=i, column=2, padx=(8, 0), pady=4)
            capture_labels[action] = cap_label

            # 清除按钮
            def _clear_entry(a=action, v=var, cl=cap_label):
                v.set("")
                cl.configure(text="点击捕获", foreground="gray")
                if hasattr(sc_win, '_captured_binds') and a in sc_win._captured_binds:
                    del sc_win._captured_binds[a]
                _update_save_btn()

            ttk.Button(frame, text="清除", width=5, command=_clear_entry).grid(
                row=i, column=3, padx=(4, 0), pady=4
            )

            # 点击/聚焦输入框开始捕获
            def _on_entry_focus(event, a=action, v=var, e=entry, cl=cap_label):
                # 还原上一个正在捕获的输入框
                prev_capturing = getattr(sc_win, '_capturing', None)
                if prev_capturing and prev_capturing != a:
                    prev_bind = sc_win._captured_binds.get(prev_capturing, "")
                    if prev_bind:
                        entries[prev_capturing].set(_display_shortcut(prev_bind))
                        capture_labels[prev_capturing].configure(text="已设置", foreground="#6a9955")
                    else:
                        entries[prev_capturing].set("")
                        capture_labels[prev_capturing].configure(text="点击捕获", foreground="gray")
                v.set("请按键...")
                cl.configure(text="等待按键", foreground="#cca700")
                sc_win._capturing = a
                # 标记：跳过因 focus_set 触发的 FocusOut
                sc_win._ignore_focusout = a
                # 设置焦点到窗口以确保接收按键
                sc_win.focus_set()

            # 失去焦点时解析手动输入
            def _on_entry_focusout(event, a=action, v=var, e=entry, cl=cap_label):
                # 跳过因 focus_set 触发的 FocusOut
                if getattr(sc_win, '_ignore_focusout', None) == a:
                    sc_win._ignore_focusout = None
                    return
                # 如果正在捕获，取消捕获
                if getattr(sc_win, '_capturing', None) == a:
                    sc_win._capturing = None
                text = v.get().strip()
                if text and text != "请按键...":
                    bind_str, display_str = _parse_manual_input(text)
                    if bind_str:
                        v.set(display_str)
                        sc_win._captured_binds[a] = bind_str
                        cl.configure(text="手动输入", foreground="#569cd6")
                    else:
                        # 无效输入，恢复之前的值
                        prev_bind = sc_win._captured_binds.get(a, "")
                        if prev_bind:
                            v.set(_display_shortcut(prev_bind))
                            cl.configure(text="已设置", foreground="#6a9955")
                        else:
                            v.set("")
                            cl.configure(text="点击捕获", foreground="gray")
                elif text == "请按键...":
                    # 未捕获到按键，恢复之前的值
                    prev_bind = sc_win._captured_binds.get(a, "")
                    if prev_bind:
                        v.set(_display_shortcut(prev_bind))
                        cl.configure(text="已设置", foreground="#6a9955")
                    else:
                        v.set("")
                        cl.configure(text="点击捕获", foreground="gray")
                _update_save_btn()

            entry.bind("<FocusIn>", _on_entry_focus)
            entry.bind("<FocusOut>", _on_entry_focusout)

        def _parse_manual_input(text):
            """解析手动输入的快捷键字符串为 bind 字符串和显示字符串
            例如 'Ctrl+S' -> ('Control+s', 'Ctrl+S')
                 'Control+Space' -> ('Control+space', 'Ctrl+Space')
            """
            text = text.strip()
            if not text:
                return None, None
            parts = [p.strip() for p in text.split('+')]
            bind_parts = []
            display_parts = []
            key_part = None
            for p in parts:
                p_lower = p.lower()
                if p_lower in ('ctrl', 'control'):
                    bind_parts.append('Control')
                    display_parts.append('Ctrl')
                elif p_lower == 'alt':
                    bind_parts.append('Alt')
                    display_parts.append('Alt')
                elif p_lower == 'shift':
                    bind_parts.append('Shift')
                    display_parts.append('Shift')
                else:
                    # 查找 keysym 映射
                    keysym = _INPUT_TO_KEYSYM.get(p_lower, p)
                    key_part = keysym
                    display_parts.append(_KEY_DISPLAY.get(keysym.lower(), p.upper()))
            if not key_part:
                return None, None
            bind_parts.append(key_part)
            bind_str = '+'.join(bind_parts)
            display_str = '+'.join(display_parts)
            return bind_str, display_str

        # 全局按键捕获
        def _on_key_press(event):
            capturing = getattr(sc_win, '_capturing', None)
            if not capturing:
                return

            parts = []
            if event.state & 0x4:
                parts.append("Control")
            if event.state & 0x20000:
                parts.append("Alt")
            if event.state & 0x1:
                parts.append("Shift")

            keysym = event.keysym
            if keysym.lower() in ('control_l', 'control_r', 'alt_l', 'alt_r',
                                   'shift_l', 'shift_r'):
                return "break"

            display_name = _KEY_DISPLAY.get(keysym.lower(), keysym.upper())
            parts.append(display_name)
            display_str = '+'.join(parts)

            bind_parts = []
            if event.state & 0x4:
                bind_parts.append("Control")
            if event.state & 0x20000:
                bind_parts.append("Alt")
            if event.state & 0x1:
                bind_parts.append("Shift")
            bind_parts.append(keysym)
            bind_str = '+'.join(bind_parts)

            entries[capturing].set(display_str)
            sc_win._captured_binds[capturing] = bind_str
            capture_labels[capturing].configure(text="已设置", foreground="#6a9955")
            sc_win._capturing = None
            _update_save_btn()
            return "break"  # 阻止默认输入

        sc_win.bind("<KeyPress>", _on_key_press)
        sc_win._capturing = None
        sc_win._captured_binds = {}

        # 初始化已有快捷键的绑定字符串
        for action, sc in self._shortcuts.items():
            if sc:
                sc_win._captured_binds[action] = sc

        # 保存按钮（有改动时才可点击）
        btn_frame = ttk.Frame(sc_win, padding=(15, 8))
        btn_frame.pack(fill=tk.X)

        save_btn = ttk.Button(btn_frame, text="保存", width=10, state=tk.DISABLED)

        def _has_changes():
            """检查快捷键是否有改动"""
            new_binds = {a: b for a, b in sc_win._captured_binds.items() if b}
            old_binds = {a: b for a, b in self._shortcuts.items() if b}
            return new_binds != old_binds

        def _update_save_btn():
            save_btn.configure(state=tk.NORMAL if _has_changes() else tk.DISABLED)

        def _save():
            new_shortcuts = {a: b for a, b in sc_win._captured_binds.items() if b}
            # 先解绑所有旧快捷键
            for action, shortcut_str in self._shortcuts.items():
                if shortcut_str:
                    try:
                        self.root.unbind(f"<{shortcut_str}>")
                    except Exception:
                        pass
            self._shortcuts = new_shortcuts
            if new_shortcuts:
                _save_shortcuts(new_shortcuts)
            else:
                # 无快捷键时删除文件
                if os.path.exists(SHORTCUT_FILE):
                    os.remove(SHORTCUT_FILE)
            # 重新注册新快捷键
            self._register_shortcuts()
            self._update_button_labels()
            sc_win.destroy()

        def _reset():
            for var in entries.values():
                var.set("")
            for cl in capture_labels.values():
                cl.configure(text="")
            sc_win._captured_binds.clear()
            _update_save_btn()

        save_btn.configure(command=_save)
        ttk.Button(btn_frame, text="全部清除", command=_reset, width=10).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(btn_frame, text="关闭", command=sc_win.destroy, width=10).pack(side=tk.RIGHT)
        save_btn.pack(side=tk.RIGHT, padx=(0, 8))

        _update_save_btn()

    # ==================== 配置设置窗口 ====================

    def _show_config_dialog(self):
        """显示配置设置窗口（实时生效，手动保存到文件）"""
        import config as config_module
        _apply_config_override()

        cfg_win = tk.Toplevel(self.root)
        cfg_win.title("配置设置")
        cfg_win.geometry("660x600")
        cfg_win.resizable(True, True)
        cfg_win.grab_set()

        ttk.Label(
            cfg_win,
            text="参数修改后实时生效。点击'保存'持久化到 config_override.json，'恢复默认'可还原。",
            font=("Microsoft YaHei UI", 9),
            wraplength=600
        ).pack(padx=15, pady=(10, 4))

        # 按钮区域（先 pack 到底部，确保可见）
        btn_frame = ttk.Frame(cfg_win, padding=(15, 8))
        btn_frame.pack(fill=tk.X, side=tk.BOTTOM)

        # 可滚动的配置区域
        canvas = tk.Canvas(cfg_win, highlightthickness=0)
        scrollbar = ttk.Scrollbar(cfg_win, orient=tk.VERTICAL, command=canvas.yview)
        scroll_frame = ttk.Frame(canvas)
        scroll_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scroll_frame, anchor=tk.NW)
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(10, 0), pady=5)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y, pady=5)

        # 鼠标滚轮支持（绑定到 canvas 和其子组件，而非全局）
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind("<MouseWheel>", _on_mousewheel)
        scroll_frame.bind("<MouseWheel>", _on_mousewheel)
        # 鼠标进入配置区域时绑定滚轮，离开时解绑
        def _on_enter(event):
            canvas.bind_all("<MouseWheel>", _on_mousewheel)
        def _on_leave(event):
            canvas.unbind_all("<MouseWheel>")
        canvas.bind("<Enter>", _on_enter)
        canvas.bind("<Leave>", _on_leave)

        # 需要重启才能生效的配置项
        # 目前所有 GUI 暴露的配置项都可以实时生效
        _RESTART_REQUIRED = set()

        # 检测GPU是否可用（CPU版exe中torch是CPU-only版本，无法使用GPU）
        _gpu_available = False
        try:
            import torch
            if torch.cuda.is_available():
                _gpu_available = True
        except Exception:
            pass
        if not _gpu_available and getattr(config_module, 'USE_GPU', False):
            # GPU不可用但配置中开启了GPU加速，自动关闭
            setattr(config_module, 'USE_GPU', False)
            config_current_vals['USE_GPU'] = False

        # 默认值（从原始 config 获取，在覆盖之前已保存）
        _DEFAULTS = dict(_CONFIG_ORIGINAL_DEFAULTS)

        # 存储所有变量
        config_vars = {}
        # 当前实时值（与 config 模块同步）
        config_current_vals = {}
        for key, _, _, _ in [(k, *rest) for cat in CONFIG_CATEGORIES for k, *rest in cat[1]]:
            if hasattr(config_module, key):
                config_current_vals[key] = getattr(config_module, key)

        row = 0

        # 存储HDR颜色项的描述标签引用，用于动态更新"当前使用"标记
        _hdr_desc_labels = {}

        # 分类提示信息
        _CAT_HINTS = {
            "游戏状态检测点": "💡 可使用下方「吸色工具」更方便地设置颜色和坐标",
            "截图区域坐标": "💡 可使用下方「区域编辑」更方便地设置截图区域",
        }

        for cat_name, items in CONFIG_CATEGORIES:
            # 分类标题
            cat_label = ttk.Label(
                scroll_frame, text=f"━━ {cat_name} ━━",
                font=("Microsoft YaHei UI", 10, "bold"),
                foreground="#569cd6"
            )
            cat_label.grid(row=row, column=0, columnspan=5, sticky=tk.W, padx=(5, 0), pady=(10, 3))
            row += 1

            # 分类提示
            hint_text = _CAT_HINTS.get(cat_name)
            if hint_text:
                hint_lbl = ttk.Label(
                    scroll_frame, text=hint_text,
                    font=("Microsoft YaHei UI", 8), foreground="#cca700"
                )
                hint_lbl.grid(row=row, column=0, columnspan=5, sticky=tk.W, padx=(20, 0), pady=(0, 2))
                row += 1

            for key, label, vtype, desc in items:
                current_val = getattr(config_module, key, None)
                if current_val is None:
                    continue

                # GPU不可用时跳过USE_GPU选项（CPU版exe无法使用GPU加速）
                if key == "USE_GPU" and not _gpu_available:
                    continue

                # 标签
                ttk.Label(scroll_frame, text=f"{label}:", font=("Microsoft YaHei UI", 9)).grid(
                    row=row, column=0, sticky=tk.E, padx=(10, 6), pady=2
                )

                # 描述 + 是否需要重启
                desc_text = desc
                if key in _RESTART_REQUIRED:
                    desc_text += " [需重启]"
                desc_lbl = ttk.Label(scroll_frame, text=desc_text, foreground="gray",
                                     font=("Microsoft YaHei UI", 8), wraplength=480)
                desc_lbl.grid(row=row, column=3, sticky=tk.W, padx=(10, 0), pady=2)
                # 保存HDR/SDR像素和颜色项的描述标签，用于动态更新"当前使用"标记
                if key in ("GAME_DETECT_PIXEL_SDR", "GAME_DETECT_COLOR_SDR",
                           "GAME_DETECT_PIXEL_HDR", "GAME_DETECT_COLOR_HDR"):
                    _hdr_desc_labels[key] = desc_lbl

                # 修改状态标记
                changed_label = ttk.Label(scroll_frame, text="", font=("Microsoft YaHei UI", 8), foreground="#6a9955")
                changed_label.grid(row=row, column=4, padx=(4, 0), pady=2)

                if vtype == "bool":
                    chk_var = tk.BooleanVar(value=current_val)
                    chk = ttk.Checkbutton(scroll_frame, variable=chk_var, text="开启")
                    chk.grid(row=row, column=1, sticky=tk.W, pady=2)
                    config_vars[key] = ("bool", chk_var, changed_label)

                    def _on_bool_change(k=key, v=chk_var, cl=changed_label, vt=vtype):
                        try:
                            new_val = v.get()
                            setattr(config_module, k, new_val)
                            config_current_vals[k] = new_val
                            # HDR联动：更新颜色描述标记
                            if k == "HDR_ENABLED":
                                _update_hdr_desc()
                            _update_changed_mark(k, new_val, cl)
                            _update_save_btn()
                            self._refresh_config_display()
                        except Exception:
                            pass

                    chk.configure(command=_on_bool_change)
                else:
                    var = tk.StringVar(value=str(current_val))
                    entry_width = 22 if vtype == "tuple" else (6 if vtype == "str" else 14)
                    entry = ttk.Entry(scroll_frame, textvariable=var, width=entry_width)
                    entry.grid(row=row, column=1, sticky=tk.W, pady=2)
                    config_vars[key] = (vtype, var, changed_label)

                    # 按键类配置项添加捕获按钮
                    if key in ("TC_SELECT_KEY", "VILLAGER_QUEUE_KEY"):
                        cap_btn = ttk.Button(scroll_frame, text="捕获", width=4)
                        _cap_bind = [None]  # 用列表存储bind id，便于闭包内修改
                        _orig_val = [str(current_val)]  # 保存原始值，用于取消时恢复

                        def _on_capture_click(e=entry, v=var, b=cap_btn, k=key, cb=_cap_bind, ov=_orig_val):
                            ov[0] = v.get()  # 保存当前值
                            v.set("请按键...")
                            b.configure(text="...", state=tk.DISABLED)
                            e.configure(state='readonly')

                            def _cancel_capture(ev=e, vv=v, bb=b, cbb=cb):
                                """取消捕获，恢复原始值"""
                                vv.set(ov[0])
                                bb.configure(text="捕获", state=tk.NORMAL)
                                ev.configure(state='normal')
                                if cbb[0] is not None:
                                    cfg_win.unbind("<KeyPress>", cbb[0])
                                    cbb[0] = None

                            def _on_key_press(event, ev=e, vv=v, bb=b, kk=k, cbb=cb):
                                keysym = event.keysym
                                # Escape 取消捕获
                                if keysym == 'Escape':
                                    _cancel_capture()
                                    return "break"
                                # 忽略单独的修饰键
                                if keysym.lower() in ('control_l', 'control_r', 'alt_l', 'alt_r',
                                                       'shift_l', 'shift_r'):
                                    return "break"
                                # 映射为单个按键字符
                                if len(keysym) == 1:
                                    key_char = keysym.lower()
                                else:
                                    # 功能键等使用小写keysym
                                    key_char = _KEY_DISPLAY.get(keysym.lower(), keysym).lower()
                                vv.set(key_char)
                                bb.configure(text="捕获", state=tk.NORMAL)
                                ev.configure(state='normal')
                                if cbb[0] is not None:
                                    cfg_win.unbind("<KeyPress>", cbb[0])
                                    cbb[0] = None
                                # 触发值变更
                                setattr(config_module, kk, key_char)
                                config_current_vals[kk] = key_char
                                _update_changed_mark(kk, key_char, changed_label)
                                _update_save_btn()
                                self._refresh_config_display()
                                return "break"

                            cb[0] = cfg_win.bind("<KeyPress>", _on_key_press)

                        cap_btn.configure(command=_on_capture_click)
                        cap_btn.grid(row=row, column=2, padx=(4, 0), pady=2)

                    def _on_value_change(k=key, v=var, cl=changed_label, vt=vtype):
                        try:
                            raw = v.get().strip()
                            if vt == "int":
                                new_val = int(raw)
                            elif vt == "float":
                                new_val = float(raw)
                            elif vt == "tuple":
                                new_val = _parse_tuple(raw)
                                if new_val is None:
                                    return
                            else:
                                new_val = raw
                            setattr(config_module, k, new_val)
                            config_current_vals[k] = new_val
                            _update_changed_mark(k, new_val, cl)
                            _update_save_btn()
                            self._refresh_config_display()
                        except (ValueError, tk.TclError):
                            pass

                    var.trace_add("write", lambda *a, cb=_on_value_change: cb())

                row += 1

        def _update_hdr_desc():
            """根据HDR开关状态更新SDR/HDR坐标和颜色项描述中的'当前使用'标记"""
            hdr_on = config_current_vals.get("HDR_ENABLED", False)
            sdr_items = {
                "GAME_DETECT_PIXEL_SDR": "SDR模式下检测像素坐标(x,y)",
                "GAME_DETECT_COLOR_SDR": "SDR模式下检测点RGB颜色(r,g,b)",
            }
            hdr_items = {
                "GAME_DETECT_PIXEL_HDR": "HDR模式下检测像素坐标(x,y)",
                "GAME_DETECT_COLOR_HDR": "HDR模式下检测点RGB颜色(r,g,b)",
            }
            for key, base_desc in sdr_items.items():
                lbl = _hdr_desc_labels.get(key)
                if lbl:
                    lbl.configure(
                        text=f"{base_desc} ← 当前使用" if not hdr_on else base_desc,
                        foreground="#cca700" if not hdr_on else "gray"
                    )
            for key, base_desc in hdr_items.items():
                lbl = _hdr_desc_labels.get(key)
                if lbl:
                    lbl.configure(
                        text=f"{base_desc} ← 当前使用" if hdr_on else base_desc,
                        foreground="#cca700" if hdr_on else "gray"
                    )

        # 初始化HDR颜色描述标记
        _update_hdr_desc()

        def _update_changed_mark(key, current_val, label_widget):
            """更新是否已修改标记"""
            default_val = _DEFAULTS.get(key)
            if default_val is not None and current_val != default_val:
                label_widget.configure(text="*", foreground="#6a9955")
            else:
                label_widget.configure(text="", foreground="gray")

        # 初始化修改标记
        for key, (vtype, var, cl) in config_vars.items():
            try:
                if vtype == "bool":
                    val = var.get()
                else:
                    raw = var.get().strip()
                    if vtype == "int":
                        val = int(raw)
                    elif vtype == "float":
                        val = float(raw)
                    elif vtype == "tuple":
                        val = _parse_tuple(raw)
                        if val is None:
                            continue
                    else:
                        val = raw
                _update_changed_mark(key, val, cl)
            except (ValueError, tk.TclError):
                pass

        # 按钮（btn_frame 已在上方创建并 pack）

        save_btn = ttk.Button(btn_frame, text="保存", width=10, state=tk.DISABLED)

        def _build_override():
            """从当前 config 模块值构建 override（只包含与默认不同的项）"""
            override = {}
            for key, (vtype, var, _) in config_vars.items():
                try:
                    if vtype == "bool":
                        val = var.get()
                    else:
                        raw = var.get().strip()
                        if vtype == "int":
                            val = int(raw)
                        elif vtype == "float":
                            val = float(raw)
                        elif vtype == "tuple":
                            val = _parse_tuple(raw)
                            if val is None:
                                continue
                        else:
                            val = raw
                    # 只保存与默认值不同的项
                    if val != _DEFAULTS.get(key):
                        # tuple转为list以便JSON序列化
                        override[key] = list(val) if isinstance(val, tuple) else val
                except (ValueError, tk.TclError):
                    pass
            return override

        def _has_changes():
            """检查是否有需要保存的改动"""
            override = _build_override()
            # 与当前文件比较
            existing = _load_config_override()
            return override != existing

        def _update_save_btn():
            save_btn.configure(state=tk.NORMAL if _has_changes() else tk.DISABLED)

        def _save_config():
            override = _build_override()
            if override:
                _save_config_override(override)
            else:
                # 全部恢复默认，删除文件
                if os.path.exists(CONFIG_OVERRIDE_FILE):
                    os.remove(CONFIG_OVERRIDE_FILE)
            _update_save_btn()

        def _reset_to_defaults():
            """恢复所有配置为默认值"""
            for key, (vtype, var, cl) in config_vars.items():
                default = _DEFAULTS.get(key)
                if default is None:
                    continue
                try:
                    if vtype == "bool":
                        var.set(default)
                    elif vtype == "tuple":
                        var.set(str(default))
                    else:
                        var.set(str(default))
                    setattr(config_module, key, default)
                    config_current_vals[key] = default
                    _update_changed_mark(key, default, cl)
                except Exception:
                    pass
            # 联动更新HDR颜色描述标记
            _update_hdr_desc()
            self._refresh_config_display()
            _update_save_btn()

        def _on_close():
            try:
                canvas.unbind_all("<MouseWheel>")
            except Exception:
                pass
            cfg_win.destroy()

        save_btn.configure(command=_save_config)
        ttk.Button(btn_frame, text="恢复默认", command=_reset_to_defaults, width=10).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(btn_frame, text="区域编辑", command=lambda: self._show_region_editor(config_module, config_vars, cfg_win), width=10).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(btn_frame, text="吸色工具", command=lambda: self._show_color_picker(config_module, config_vars, cfg_win), width=10).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(btn_frame, text="模板图片", command=lambda: self._show_template_viewer(cfg_win), width=10).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(btn_frame, text="关闭", command=_on_close, width=10).pack(side=tk.RIGHT)
        save_btn.pack(side=tk.RIGHT, padx=(0, 8))

        cfg_win.protocol("WM_DELETE_WINDOW", _on_close)

        _update_save_btn()

    # ==================== 区域编辑器 ====================

    # 区域配置定义：(key, 中文名, 类型)
    # 类型: "rect"=矩形区域(x1,y1,x2,y2), "pixel"=像素点(x,y)
    _REGION_DEFS = [
        ("GAME_DETECT_PIXEL_SDR", "SDR检测点", "pixel"),
        ("GAME_DETECT_PIXEL_HDR", "HDR检测点", "pixel"),
        ("VILLAGER_QUEUE_REGION", "生产队列", "rect"),
        ("BLOCKED_DETECT_REGION", "遮挡检测", "rect"),
        ("POPULATION_REGION", "人口显示", "rect"),
        ("TC_ICON_REGION", "TC图标", "rect"),
        ("SINGLE_TC_REGION", "单TC预检", "rect"),
        ("VILLAGER_COUNT_REGION", "村民计数", "rect"),
        ("FOOD_REGION", "食物显示", "rect"),
    ]

    # 区域颜色映射（每个区域不同颜色）
    _REGION_COLORS = [
        "#ff4444",  # 红
        "#ff8800",  # 橙
        "#44ff44",  # 绿
        "#4444ff",  # 蓝
        "#ff44ff",  # 紫
        "#44ffff",  # 青
        "#ffff44",  # 黄
        "#ff8844",  # 深橙
        "#88ff44",  # 黄绿
    ]

    def _show_region_editor(self, config_module, config_vars, cfg_win):
        """显示区域编辑器：全屏覆盖层上显示所有区域，可拖拽调整"""
        try:
            from PIL import ImageGrab
        except ImportError:
            messagebox.showerror("错误", "需要 Pillow 库", parent=cfg_win)
            return

        # 截取全屏作为背景
        screenshot = ImageGrab.grab()
        screen_w, screen_h = screenshot.size

        # 创建全屏覆盖窗口
        overlay = tk.Toplevel(cfg_win)
        overlay.overrideredirect(True)
        overlay.attributes("-topmost", True)
        # 尝试设置半透明（Windows）
        try:
            overlay.attributes("-alpha", 0.85)
        except Exception:
            pass

        canvas = tk.Canvas(overlay, cursor="crosshair", highlightthickness=0)
        canvas.pack(fill=tk.BOTH, expand=True)

        # 提示栏
        hint_frame = tk.Frame(overlay, bg="#1e1e1e")
        hint_frame.place(x=0, y=0, relwidth=1.0)
        tk.Label(hint_frame,
                 text="拖动区域边框调整位置和大小 | Enter=保存 | Esc=取消",
                 bg="#1e1e1e", fg="#cca700", font=("Microsoft YaHei UI", 11, "bold")
                 ).pack(pady=6)

        # 绘制背景截图
        from PIL import ImageTk
        bg_photo = ImageTk.PhotoImage(screenshot, master=overlay)
        canvas.create_image(0, 0, anchor=tk.NW, image=bg_photo)
        # 保持引用防止GC
        overlay._bg_photo = bg_photo

        # 区域数据
        regions_data = []  # [{key, name, type, coords, canvas_ids, color}]

        for idx, (key, name, rtype) in enumerate(self._REGION_DEFS):
            val = getattr(config_module, key, None)
            if val is None:
                continue
            color = self._REGION_COLORS[idx % len(self._REGION_COLORS)]
            coords = list(val)  # 可变的副本
            region = {
                "key": key, "name": name, "type": rtype,
                "coords": coords, "canvas_ids": [], "color": color
            }
            regions_data.append(region)

        # 绘制所有区域
        def _draw_regions():
            for r in regions_data:
                # 清除旧的画布元素
                for cid in r["canvas_ids"]:
                    canvas.delete(cid)
                r["canvas_ids"].clear()

                c = r["coords"]
                color = r["color"]

                if r["type"] == "rect":
                    x1, y1, x2, y2 = c
                    # 填充半透明矩形（用stipple模拟）
                    rid = canvas.create_rectangle(
                        x1, y1, x2, y2,
                        outline=color, width=2, fill=color,
                        stipple="gray12"
                    )
                    r["canvas_ids"].append(rid)
                    # 标签
                    tid = canvas.create_text(
                        x1, y1 - 8, anchor=tk.SW,
                        text=f'{r["name"]} ({x1},{y1},{x2},{y2})',
                        fill=color, font=("Microsoft YaHei UI", 9, "bold")
                    )
                    r["canvas_ids"].append(tid)
                    # 4个角的拖拽手柄
                    handle_size = 6
                    for hx, hy in [(x1, y1), (x2, y1), (x1, y2), (x2, y2)]:
                        hid = canvas.create_rectangle(
                            hx - handle_size, hy - handle_size,
                            hx + handle_size, hy + handle_size,
                            fill=color, outline="white", width=1
                        )
                        r["canvas_ids"].append(hid)
                else:  # pixel
                    px, py = c
                    # 十字标记
                    size = 12
                    cid1 = canvas.create_line(px - size, py, px + size, py, fill=color, width=2)
                    cid2 = canvas.create_line(px, py - size, px, py + size, fill=color, width=2)
                    r["canvas_ids"].extend([cid1, cid2])
                    # 标签
                    tid = canvas.create_text(
                        px + 8, py - 8, anchor=tk.SW,
                        text=f'{r["name"]} ({px},{py})',
                        fill=color, font=("Microsoft YaHei UI", 9, "bold")
                    )
                    r["canvas_ids"].append(tid)
                    # 中心圆点
                    dot = canvas.create_oval(
                        px - 4, py - 4, px + 4, py + 4,
                        fill=color, outline="white", width=1
                    )
                    r["canvas_ids"].append(dot)

        _draw_regions()

        # 拖拽逻辑
        _drag_state = {
            "region": None,     # 当前拖拽的区域
            "handle": None,     # "move" / "tl" / "tr" / "bl" / "br" / "pixel"
            "start_x": 0, "start_y": 0,
            "orig_coords": None,
        }

        def _find_region(x, y, margin=8):
            """查找点击位置对应的区域和手柄"""
            for r in regions_data:
                c = r["coords"]
                if r["type"] == "rect":
                    x1, y1, x2, y2 = c
                    # 检查4个角
                    for label, hx, hy in [("tl", x1, y1), ("tr", x2, y1),
                                           ("bl", x1, y2), ("br", x2, y2)]:
                        if abs(x - hx) <= margin and abs(y - hy) <= margin:
                            return r, label
                    # 检查内部（移动）
                    if x1 <= x <= x2 and y1 <= y <= y2:
                        return r, "move"
                else:  # pixel
                    px, py = c
                    if abs(x - px) <= margin and abs(y - py) <= margin:
                        return r, "pixel"
            return None, None

        def _on_press(event):
            r, handle = _find_region(event.x, event.y)
            if r:
                _drag_state["region"] = r
                _drag_state["handle"] = handle
                _drag_state["start_x"] = event.x
                _drag_state["start_y"] = event.y
                _drag_state["orig_coords"] = list(r["coords"])

        def _on_drag(event):
            r = _drag_state.get("region")
            if not r:
                return
            handle = _drag_state["handle"]
            dx = event.x - _drag_state["start_x"]
            dy = event.y - _drag_state["start_y"]
            oc = _drag_state["orig_coords"]

            if r["type"] == "rect":
                x1, y1, x2, y2 = oc
                if handle == "move":
                    r["coords"] = [x1 + dx, y1 + dy, x2 + dx, y2 + dy]
                elif handle == "tl":
                    r["coords"] = [x1 + dx, y1 + dy, x2, y2]
                elif handle == "tr":
                    r["coords"] = [x1, y1 + dy, x2 + dx, y2]
                elif handle == "bl":
                    r["coords"] = [x1 + dx, y1, x2, y2 + dy]
                elif handle == "br":
                    r["coords"] = [x1, y1, x2 + dx, y2 + dy]
            else:  # pixel
                r["coords"] = [oc[0] + dx, oc[1] + dy]

            _draw_regions()

        def _on_release(event):
            _drag_state["region"] = None
            _drag_state["handle"] = None

        canvas.bind("<ButtonPress-1>", _on_press)
        canvas.bind("<B1-Motion>", _on_drag)
        canvas.bind("<ButtonRelease-1>", _on_release)

        # 键盘：Enter保存，Esc取消
        def _on_key(event):
            if event.keysym == "Return":
                # 保存到config和GUI变量
                for r in regions_data:
                    new_val = tuple(r["coords"])
                    setattr(config_module, r["key"], new_val)
                    if r["key"] in config_vars:
                        vtype, var, cl = config_vars[r["key"]]
                        var.set(str(new_val))
                overlay.destroy()
                self._safe_print("区域编辑：已保存所有区域坐标", "success")
            elif event.keysym == "Escape":
                overlay.destroy()
                self._safe_print("区域编辑：已取消", "warning")

        overlay.bind("<Key>", _on_key)
        overlay.focus_set()

        # 全屏显示
        overlay.geometry(f"{screen_w}x{screen_h}+0+0")

    # ==================== 吸色工具 ====================

    def _show_color_picker(self, config_module, config_vars, cfg_win):
        """显示吸色工具：全屏截图上点击取色，分别填充到SDR/HDR的坐标和颜色"""
        try:
            from PIL import ImageGrab
        except ImportError:
            messagebox.showerror("错误", "需要 Pillow 库", parent=cfg_win)
            return

        # 创建选择窗口
        pick_win = tk.Toplevel(cfg_win)
        pick_win.title("吸色工具")
        pick_win.geometry("380x290")
        pick_win.resizable(False, False)
        pick_win.grab_set()

        ttk.Label(pick_win, text="吸色工具：截取全屏后点击取色",
                  font=("Microsoft YaHei UI", 10, "bold")).pack(pady=(12, 4))
        ttk.Label(pick_win, text="点击屏幕上任意位置获取该点的坐标和颜色",
                  font=("Microsoft YaHei UI", 9), foreground="gray").pack(pady=(0, 10))

        # 目标选择
        target_var = tk.StringVar(value="SDR")
        target_frame = ttk.LabelFrame(pick_win, text="填充目标", padding=8)
        target_frame.pack(padx=20, pady=4, fill=tk.X)
        ttk.Radiobutton(target_frame, text="SDR（填充 SDR坐标 + SDR颜色）",
                        variable=target_var, value="SDR").pack(anchor=tk.W)
        ttk.Radiobutton(target_frame, text="HDR（填充 HDR坐标 + HDR颜色）",
                        variable=target_var, value="HDR").pack(anchor=tk.W)

        # 预览区域
        preview_frame = ttk.Frame(pick_win, padding=8)
        preview_frame.pack(padx=20, fill=tk.X)

        ttk.Label(preview_frame, text="上次取色：").grid(row=0, column=0, sticky=tk.W)
        color_preview = tk.Canvas(preview_frame, width=30, height=20, bg="#000000",
                                   highlightthickness=1, highlightbackground="gray")
        color_preview.grid(row=0, column=1, padx=6)
        color_text = ttk.Label(preview_frame, text="未取色", font=("Consolas", 10))
        color_text.grid(row=0, column=2, sticky=tk.W)

        # 开始按钮
        def _start_pick():
            # 截取全屏
            from PIL import Image as PILImage  # PILImage.NEAREST 用于放大镜缩放
            screenshot = ImageGrab.grab()

            # 创建全屏覆盖
            overlay = tk.Toplevel(pick_win)
            overlay.overrideredirect(True)
            overlay.attributes("-topmost", True)
            try:
                overlay.attributes("-alpha", 0.85)
            except Exception:
                pass

            screen_w, screen_h = screenshot.size

            canvas = tk.Canvas(overlay, cursor="crosshair", highlightthickness=0)
            canvas.pack(fill=tk.BOTH, expand=True)

            # 提示栏
            hint_frame = tk.Frame(overlay, bg="#1e1e1e")
            hint_frame.place(x=0, y=0, relwidth=1.0)
            tk.Label(hint_frame,
                     text="点击屏幕取色 | Esc=取消",
                     bg="#1e1e1e", fg="#cca700", font=("Microsoft YaHei UI", 11, "bold")
                     ).pack(pady=6)

            # 显示截图
            from PIL import ImageTk
            bg_photo = ImageTk.PhotoImage(screenshot, master=overlay)
            canvas.create_image(0, 0, anchor=tk.NW, image=bg_photo)
            overlay._bg_photo = bg_photo

            # 实时跟踪光标
            _live_text = canvas.create_text(
                screen_w // 2, screen_h - 30,
                text="", fill="#cca700", font=("Consolas", 10),
                anchor=tk.S
            )
            # 放大镜：在光标附近显示放大的像素
            _zoom_img = None

            def _on_motion(event):
                """鼠标移动时实时显示坐标、颜色和放大镜"""
                nonlocal _zoom_img
                px, py = event.x, event.y
                if 0 <= px < screen_w and 0 <= py < screen_h:
                    pixel = screenshot.getpixel((px, py))
                    r, g, b = pixel[:3]
                    canvas.itemconfigure(
                        _live_text,
                        text=f"坐标: ({px}, {py})  颜色: ({r}, {g}, {b})  #{r:02x}{g:02x}{b:02x}"
                    )
                    # 清除旧的放大镜元素
                    canvas.delete("zoom_lens")
                    if _zoom_img:
                        del _zoom_img
                    # 截取光标周围 20x20 像素，放大到 100x100
                    zoom_src = 20
                    zoom_dst = 100
                    x1 = max(0, px - zoom_src // 2)
                    y1 = max(0, py - zoom_src // 2)
                    x2 = min(screen_w, x1 + zoom_src)
                    y2 = min(screen_h, y1 + zoom_src)
                    crop = screenshot.crop((x1, y1, x2, y2)).resize(
                        (zoom_dst, zoom_dst), PILImage.NEAREST
                    )
                    _zoom_img = ImageTk.PhotoImage(crop, master=overlay)
                    # 放在光标右下方
                    zx = min(px + 20, screen_w - zoom_dst - 10)
                    zy = min(py + 20, screen_h - zoom_dst - 10)
                    canvas.create_image(zx, zy, anchor=tk.NW, image=_zoom_img, tags="zoom_lens")
                    # 放大镜边框
                    canvas.create_rectangle(zx, zy, zx + zoom_dst, zy + zoom_dst,
                                            outline="#cca700", width=2, tags="zoom_lens")
                    # 中心十字
                    cx, cy = zx + zoom_dst // 2, zy + zoom_dst // 2
                    canvas.create_line(cx - 6, cy, cx + 6, cy, fill="red", width=1, tags="zoom_lens")
                    canvas.create_line(cx, cy - 6, cx, cy + 6, fill="red", width=1, tags="zoom_lens")

            def _on_click(event):
                px, py = event.x, event.y
                if 0 <= px < screen_w and 0 <= py < screen_h:
                    pixel = screenshot.getpixel((px, py))
                    r, g, b = pixel[:3]
                    target = target_var.get()

                    # 填充到配置
                    pixel_key = f"GAME_DETECT_PIXEL_{target}"
                    color_key = f"GAME_DETECT_COLOR_{target}"
                    new_pixel = (px, py)
                    new_color = (r, g, b)

                    setattr(config_module, pixel_key, new_pixel)
                    setattr(config_module, color_key, new_color)

                    # 更新GUI变量
                    for k, val in [(pixel_key, new_pixel), (color_key, new_color)]:
                        if k in config_vars:
                            vtype, var, cl = config_vars[k]
                            var.set(str(val))

                    overlay.destroy()
                    color_preview.configure(bg=f"#{r:02x}{g:02x}{b:02x}")
                    color_text.configure(text=f"({px},{py}) RGB=({r},{g},{b})")
                    self._safe_print(
                        f"吸色工具：{target} 坐标=({px},{py}) 颜色=({r},{g},{b})",
                        "success"
                    )

            def _on_key(event):
                if event.keysym == "Escape":
                    overlay.destroy()

            canvas.bind("<Motion>", _on_motion)
            canvas.bind("<ButtonPress-1>", _on_click)
            overlay.bind("<Key>", _on_key)
            overlay.focus_set()
            overlay.geometry(f"{screen_w}x{screen_h}+0+0")

        ttk.Button(pick_win, text="开始取色", command=_start_pick).pack(pady=10)
        ttk.Button(pick_win, text="关闭", command=pick_win.destroy).pack()

    # ==================== 模板图片查看器 ====================

    def _show_template_viewer(self, parent):
        """显示模板图片查看器：列出所有模板，显示内置/用户替换图片"""
        try:
            import config
            from PIL import Image, ImageTk
        except ImportError:
            messagebox.showerror("错误", "需要 Pillow 库", parent=parent)
            return

        tmpl_win = tk.Toplevel(parent)
        tmpl_win.title("模板图片")
        tmpl_win.geometry("720x520")
        tmpl_win.resizable(True, True)
        tmpl_win.grab_set()

        # 顶部说明
        info_frame = ttk.Frame(tmpl_win, padding=(10, 8))
        info_frame.pack(fill=tk.X)

        ttk.Label(
            info_frame,
            text="模板图片管理：可在 exe 同目录的 user_templates/ 文件夹中放置同名图片替换内置模板",
            font=("Microsoft YaHei UI", 9),
            wraplength=680
        ).pack(anchor=tk.W)

        user_dir = getattr(config, 'USER_TEMPLATES_DIR', '')
        if user_dir:
            dir_text = f"用户模板目录: {user_dir}"
            dir_label = ttk.Label(info_frame, text=dir_text, font=("Consolas", 8), foreground="gray")
            dir_label.pack(anchor=tk.W, pady=(2, 0))

            def _open_user_dir():
                os.makedirs(user_dir, exist_ok=True)
                os.startfile(user_dir)

            ttk.Button(info_frame, text="打开目录", command=_open_user_dir, width=10).pack(anchor=tk.W, pady=(4, 0))

        # 可滚动的模板列表
        canvas = tk.Canvas(tmpl_win, highlightthickness=0)
        scrollbar = ttk.Scrollbar(tmpl_win, orient=tk.VERTICAL, command=canvas.yview)
        scroll_frame = ttk.Frame(canvas)
        scroll_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scroll_frame, anchor=tk.NW)
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(10, 0), pady=5)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y, pady=5)

        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind("<MouseWheel>", _on_mousewheel)

        # 获取模板信息
        template_info = config.get_template_info()

        # 表头
        header_frame = ttk.Frame(scroll_frame, padding=(5, 2))
        header_frame.pack(fill=tk.X)
        ttk.Label(header_frame, text="模板名称", font=("Microsoft YaHei UI", 9, "bold"), width=28).grid(row=0, column=0, padx=2)
        ttk.Label(header_frame, text="内置", font=("Microsoft YaHei UI", 9, "bold"), width=8).grid(row=0, column=1, padx=2)
        ttk.Label(header_frame, text="替换", font=("Microsoft YaHei UI", 9, "bold"), width=8).grid(row=0, column=2, padx=2)
        ttk.Label(header_frame, text="状态", font=("Microsoft YaHei UI", 9, "bold"), width=12).grid(row=0, column=3, padx=2)
        ttk.Label(header_frame, text="预览", font=("Microsoft YaHei UI", 9, "bold"), width=16).grid(row=0, column=4, padx=2)

        ttk.Separator(scroll_frame, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=2)

        # 图片引用缓存（防止GC）
        _photo_refs = []

        for i, (display_name, internal_path, user_path, has_user) in enumerate(template_info):
            row_frame = ttk.Frame(scroll_frame, padding=(5, 3))
            row_frame.pack(fill=tk.X)

            # 模板名称
            ttk.Label(row_frame, text=display_name, font=("Microsoft YaHei UI", 9), width=28).grid(row=0, column=0, sticky=tk.W, padx=2)

            # 内置图存在标记
            internal_exists = os.path.exists(internal_path)
            ttk.Label(row_frame, text="✓" if internal_exists else "✗",
                      foreground="#6a9955" if internal_exists else "#f44747",
                      font=("Microsoft YaHei UI", 10), width=8).grid(row=0, column=1, padx=2)

            # 替换图存在标记
            ttk.Label(row_frame, text="✓" if has_user else "-",
                      foreground="#569cd6" if has_user else "gray",
                      font=("Microsoft YaHei UI", 10), width=8).grid(row=0, column=2, padx=2)

            # 使用状态
            if has_user:
                status_text = "▶ 使用替换"
                status_color = "#569cd6"
            else:
                status_text = "使用内置"
                status_color = "#6a9955"
            ttk.Label(row_frame, text=status_text, foreground=status_color,
                      font=("Microsoft YaHei UI", 9), width=12).grid(row=0, column=3, padx=2)

            # 预览缩略图
            preview_frame = ttk.Frame(row_frame, width=60, height=30)
            preview_frame.grid(row=0, column=4, padx=2)
            preview_frame.grid_propagate(False)

            # 优先显示用户替换图
            preview_path = user_path if has_user else internal_path
            if preview_path and os.path.exists(preview_path):
                try:
                    img = Image.open(preview_path)
                    # 缩放到适合预览的大小
                    img.thumbnail((56, 26), Image.LANCZOS)
                    photo = ImageTk.PhotoImage(img, master=tmpl_win)
                    _photo_refs.append(photo)
                    lbl = ttk.Label(preview_frame, image=photo)
                    lbl.image = photo  # 防止GC
                    lbl.pack(padx=2, pady=2)
                except Exception:
                    ttk.Label(preview_frame, text="加载失败", font=("Microsoft YaHei UI", 7), foreground="gray").pack()

        # 底部按钮
        btn_frame = ttk.Frame(tmpl_win, padding=(10, 8))
        btn_frame.pack(fill=tk.X)
        ttk.Button(btn_frame, text="关闭", command=tmpl_win.destroy, width=10).pack(side=tk.RIGHT)

    def _update_last_status(self, text, tag='info'):
        try:
            display = text if len(text) <= 80 else text[:77] + "..."
            self.last_status_var.set(display)
            color = self._tag_colors.get(tag, '#888888')
            self.last_status_label.configure(fg=color)
        except tk.TclError:
            pass

    def _load_config_display(self, parent):
        """加载并显示当前配置摘要"""
        self._config_frame = parent
        self._config_labels = {}
        self._refresh_config_display()

    def _refresh_config_display(self):
        """刷新配置显示区域

        核心参数始终显示；HDR/村民上限/GPU加速/调试开关等仅在启用时显示
        """
        # 清除旧内容
        for w in self._config_frame.winfo_children():
            w.destroy()
        self._config_labels.clear()

        try:
            import config
            _apply_config_override()

            # 核心参数（始终显示）
            summary = [
                ("每TC排队", getattr(config, 'VILLAGERS_PER_TC', '?')),
                ("最低食物", getattr(config, 'MIN_FOOD', '?')),
                ("选所有TC键", getattr(config, 'TC_SELECT_KEY', '?').upper()),
                ("出农键", getattr(config, 'VILLAGER_QUEUE_KEY', '?').upper()),
                ("Shift排队", "开" if getattr(config, 'ENABLE_SHIFT_QUEUE', True) else "关"),
            ]

            # 仅启用时显示的参数
            if getattr(config, 'ENABLE_MAX_VILLAGERS', False):
                summary.append(("村民上限⚠", f"{getattr(config, 'MAX_VILLAGERS', '?')}（仅供参考）"))
            if getattr(config, 'HDR_ENABLED', False):
                summary.append(("游戏HDR", "已开启"))
            if getattr(config, 'USE_GPU', False):
                summary.append(("GPU加速", "已开启"))
            if getattr(config, 'DEBUG_MODE', False):
                summary.append(("调试模式", "已开启"))
            if getattr(config, 'DEBUG_PERFORMANCE', False):
                summary.append(("性能分析", "已开启"))
            if getattr(config, 'DEBUG_SAVE_SCREENSHOTS', False):
                summary.append(("保存截图", "已开启"))

        except Exception:
            summary = [("配置加载", "失败")]

        for i, (key, value) in enumerate(summary):
            col = i % 3
            row = i // 3
            ttk.Label(self._config_frame, text=f"{key}:", font=("Microsoft YaHei UI", 9)).grid(
                row=row, column=col * 2, sticky=tk.E, padx=(0, 2), pady=1
            )
            lbl = ttk.Label(self._config_frame, text=str(value), font=("Microsoft YaHei UI", 9, "bold"))
            lbl.grid(row=row, column=col * 2 + 1, sticky=tk.W, padx=(0, 20), pady=1)
            self._config_labels[key] = lbl

    def _update_status(self, text, color):
        try:
            self.status_var.set(text)
            self._set_status_color(color)
        except tk.TclError:
            pass

    def _set_status_color(self, color):
        try:
            self.status_label.configure(foreground=color)
        except tk.TclError:
            pass

    def _safe_print(self, message, tag="info"):
        try:
            self.log_text.configure(state='normal')
            self.log_text.insert(tk.END, message + '\n', tag)
            self.log_text.configure(state='disabled')
            self.log_text.see(tk.END)
        except tk.TclError:
            pass

    def _check_status(self):
        if self.running and self.worker_thread and not self.worker_thread.is_alive():
            self._stop()
            self._safe_print("\n[错误] 工作线程异常退出\n", "error")
            return
        if self.running:
            self.root.after(1000, self._check_status)

    # ==================== 工作线程 ====================

    def _worker(self):
        try:
            import main as main_module
            main_module._gui_running = True
            self._run_main_loop(main_module)
        except Exception as e:
            if self.running:
                self._safe_print(f"\n[错误] 程序异常: {e}\n", "error")
                self.root.after(0, self._stop)

    def _run_main_loop(self, main_module):
        try:
            import config
            from game_detector import GameDetector
            from villager_training_detector import VillagerTrainingDetector
            from population_reader import PopulationReader
            from villager_trainer import VillagerTrainer
            from tc_counter import TCCounter
            from tc_selector import TCSelector
            from villager_counter import VillagerCounter
            from food_reader import FoodReader
            from lock import acquire_lock, release_lock, cleanup_lock
            from input_blocker import input_blocked
            from logger import perf_stats
            import concurrent.futures
            import ctypes
            import pydirectinput
            pydirectinput.FAILSAFE = False
            pydirectinput.PAUSE = 0.0
            from contextlib import nullcontext

            user32 = ctypes.windll.user32
            VK_SHIFT = 0x10
            VK_CONTROL = 0x11
            VK_MENU = 0x12

            def is_modifier_key_pressed():
                return (
                    (user32.GetAsyncKeyState(VK_SHIFT) & 0x8000) != 0 or
                    (user32.GetAsyncKeyState(VK_CONTROL) & 0x8000) != 0 or
                    (user32.GetAsyncKeyState(VK_MENU) & 0x8000) != 0
                )

            def release_stuck_modifiers():
                """强制释放所有可能粘滞的修饰键（Shift/Ctrl/Alt）

                解决 BlockInput 不屏蔽 GetAsyncKeyState 导致的修饰键粘滞问题：
                用户在操作期间按下的修饰键，pydirectinput 的 keyDown/keyUp
                与物理按键冲突，导致操作系统认为修饰键仍被按住
                """
                for key_name in ('shift', 'ctrl', 'alt'):
                    pydirectinput.keyUp(key_name)
                time.sleep(0.01)

            class LogMerger:
                def __init__(self):
                    self.last_message = None
                    self.repeat_count = 0
                    self.printed_first = False

                def log(self, message):
                    if message == self.last_message:
                        self.repeat_count += 1
                    else:
                        self._flush()
                        self.last_message = message
                        self.repeat_count = 1
                        self.printed_first = False
                        print(message, end='', flush=True)
                        self.printed_first = True

                def _flush(self):
                    if self.last_message and self.printed_first:
                        if self.repeat_count > 1:
                            print(f" x{self.repeat_count}")
                        else:
                            print()

                def force_print(self, message):
                    self._flush()
                    print(message)
                    self.last_message = None
                    self.repeat_count = 0
                    self.printed_first = False

            # === 初始化 ===
            print("\n" + "=" * 50, flush=True)
            print(f"  AOE4 自动生产村民工具 v{self._version} (GUI模式)", flush=True)
            print("=" * 50, flush=True)

            # 1. 检测运行环境
            print("  [>] 检测运行环境...", flush=True)
            gpu_available = False
            gpu_name = ""
            try:
                import torch
                if torch.cuda.is_available():
                    gpu_available = True
                    gpu_name = torch.cuda.get_device_name(0)
            except ImportError:
                pass
            except Exception:
                pass
            ocr_mode = "GPU加速" if (config.USE_GPU and gpu_available) else "CPU模式"
            if gpu_available:
                print(f"       GPU: {gpu_name} (可用) | OCR模式: {ocr_mode}", flush=True)
            else:
                print(f"       OCR模式: CPU模式", flush=True)

            # 2. 清理残留锁文件
            print("  [>] 清理残留文件...", flush=True)
            cleanup_lock()

            # 3. 初始化各模块
            print("  [>] 初始化检测器...", flush=True)
            game_detector = GameDetector()
            training_detector = VillagerTrainingDetector()
            population_reader = PopulationReader()
            tc_selector = TCSelector()
            tc_counter = TCCounter()
            villager_counter = VillagerCounter()
            food_reader = FoodReader()
            cooldown_detector = VillagerTrainingDetector()
            villager_trainer = VillagerTrainer()
            logger = LogMerger()
            executor = concurrent.futures.ThreadPoolExecutor(max_workers=3)

            # 4. 预热OCR
            print("\n  [>] 预热OCR模型...", flush=True)
            warmup_start = time.time()
            population_reader.do()
            warmup_time = time.time() - warmup_start
            print(f"       耗时 {warmup_time:.2f}秒", flush=True)

            # 就绪
            max_villagers_str = f"村民上限: {config.MAX_VILLAGERS}(已启用，仅供参考)" if config.ENABLE_MAX_VILLAGERS else "村民上限: 未启用"
            print(f"\n  {max_villagers_str}  |  最低食物: {config.MIN_FOOD}  |  每TC排队: {config.VILLAGERS_PER_TC}", flush=True)
            print(f"  选所有TC键: {config.TC_SELECT_KEY.upper()}  |  出农键: {config.VILLAGER_QUEUE_KEY.upper()}  |  Shift排队: {'开' if config.ENABLE_SHIFT_QUEUE else '关'}", flush=True)
            print("  程序已就绪，等待进入游戏...", flush=True)
            print("=" * 50 + "\n", flush=True)

            self.root.after(0, lambda: self._update_status("运行中", "#6a9955"))

            # 主循环
            last_trigger_time = None
            last_villager_check_time = 0
            cached_tc_count = 0
            modifier_stuck_count = 0  # 修饰键连续检测计数，用于检测粘滞

            while self.running and getattr(main_module, '_gui_running', True):
                # 检查TC清零请求
                if self._tc_reset_requested:
                    cached_tc_count = 0
                    self._tc_reset_requested = False

                # 检查暂停状态
                if self.paused:
                    time.sleep(0.5)
                    continue

                perf_stats.maybe_report()
                loop_start = time.time()

                # [1] 修饰键检测
                if is_modifier_key_pressed():
                    modifier_stuck_count += 1
                    # 连续检测到修饰键超过阈值，可能是粘滞（pydirectinput keyDown/keyUp冲突）
                    if modifier_stuck_count >= 50:
                        print(f"[修复] 修饰键连续检测{modifier_stuck_count}次，可能粘滞，强制释放", flush=True)
                        release_stuck_modifiers()
                        modifier_stuck_count = 0
                        time.sleep(0.05)
                        if not is_modifier_key_pressed():
                            print("[修复] 修饰键粘滞已修复", flush=True)
                            continue
                    logger.log("检测到修饰键，暂停")
                    if config.CHECK_INTERVAL > 0:
                        time.sleep(config.CHECK_INTERVAL)
                    continue
                else:
                    modifier_stuck_count = 0
                t_modifier = time.time()
                perf_stats.record("[1] 修饰键检测", t_modifier - loop_start)

                # [2] 游戏窗口检测
                game_detector.do()
                if not game_detector.window_active:
                    logger.log("不在游戏窗口")
                    if config.CHECK_INTERVAL > 0:
                        time.sleep(config.CHECK_INTERVAL)
                    continue
                if not game_detector.pixel_match:
                    logger.log("不在游戏中")
                    if config.CHECK_INTERVAL > 0:
                        time.sleep(config.CHECK_INTERVAL)
                    continue
                t_game = time.time()
                perf_stats.record("[2] 游戏窗口检测", t_game - t_modifier)

                # [3] 村民生产状态检测
                training_detector.do()

                if config.DEBUG_MODE:
                    status_parts = []
                    status_parts.append(f"遮挡={training_detector.blocked_confidence:.3f}")
                    status_parts.append(f"村民={training_detector.confidence:.3f}")
                    if training_detector.blocked:
                        status_parts.append("状态=完全遮挡")
                    elif training_detector.in_transition:
                        if training_detector._semi_transparent_detected:
                            status_parts.append(f"状态=半透明UI")
                        else:
                            status_parts.append(f"状态=渐变中(次数={training_detector._transition_count},稳定={training_detector._stable_count})")
                    elif training_detector.found:
                        status_parts.append("状态=生产中")
                    else:
                        status_parts.append(f"状态=未遮挡且无生产(稳定={training_detector._stable_count})")
                    logger.log(" ".join(status_parts))

                if training_detector.blocked:
                    if config.DEBUG_MODE:
                        logger.log(f"[遮挡] 置信度={training_detector.blocked_confidence:.3f}")
                    else:
                        logger.log("生产队列图标被遮挡，无法判定是否有村民在生产")
                    if config.CHECK_INTERVAL > 0:
                        time.sleep(config.CHECK_INTERVAL)
                    continue

                if training_detector.in_transition:
                    if config.DEBUG_MODE:
                        logger.log(f"[渐变] 置信度={training_detector.blocked_confidence:.3f}")
                    continue

                if training_detector.found:
                    if config.DEBUG_MODE:
                        logger.log(f"[生产中] 置信度={training_detector.confidence:.3f}")
                    else:
                        logger.log("检测到村民正在生产中，跳过")
                    continue

                detection_time = time.time()
                if last_trigger_time and config.DEBUG_MODE:
                    elapsed = detection_time - last_trigger_time
                    logger.force_print(f"[时间] 距上次触发 {elapsed:.2f}秒")
                perf_stats.record("[3] 村民生产状态检测", detection_time - t_game)

                # [4] OCR识别
                ocr_start = time.time()
                current_time = ocr_start
                should_check_villagers = (current_time - last_villager_check_time) >= config.VILLAGER_CHECK_INTERVAL

                future_villager = None
                future_population = executor.submit(population_reader.do)
                future_food = executor.submit(food_reader.do)

                if should_check_villagers:
                    future_villager = executor.submit(villager_counter.do)
                    concurrent.futures.wait([future_population, future_villager, future_food])
                    last_villager_check_time = current_time
                else:
                    concurrent.futures.wait([future_population, future_food])

                if config.DEBUG_MODE:
                    ocr_total = time.time() - ocr_start
                    logger.force_print(f"[OCR耗时] {ocr_total:.3f}秒")
                t_ocr = time.time()
                perf_stats.record("[4] OCR识别", t_ocr - ocr_start)

                # [5] 条件检查
                t_cond_start = time.time()

                if population_reader.current is None:
                    if config.DEBUG_MODE:
                        logger.log(f"[识别失败] 人口 current={population_reader.current}")
                    else:
                        logger.log("人口识别失败，跳过")
                    continue

                if config.ENABLE_MAX_VILLAGERS and should_check_villagers and villager_counter.total >= config.MAX_VILLAGERS:
                    if config.DEBUG_MODE:
                        logger.log(f"[上限] 村民={villager_counter.total}/{config.MAX_VILLAGERS}")
                    else:
                        logger.log(f"村民已达上限（{villager_counter.total}/{config.MAX_VILLAGERS}，仅供参考），跳过")
                    continue

                if food_reader.amount is None:
                    if config.DEBUG_MODE:
                        logger.log(f"[识别失败] 食物 amount={food_reader.amount}")
                    else:
                        logger.log("食物识别失败，跳过")
                    continue

                if food_reader.amount < config.MIN_FOOD:
                    if config.DEBUG_MODE:
                        logger.log(f"[不足] 食物={food_reader.amount}/{config.MIN_FOOD}")
                    else:
                        logger.log(f"食物不足（{food_reader.amount}/{config.MIN_FOOD}），跳过")
                    continue

                available_slots = population_reader.limit - population_reader.current

                if available_slots <= 0:
                    if population_reader.limit == 0:
                        logger.log(f"人口已满（{population_reader.current}/{population_reader.limit}），可能尚未建造TC（游牧开局），跳过")
                    elif config.DEBUG_MODE:
                        logger.log(f"[无空位] 人口={population_reader.current}/{population_reader.limit}")
                    else:
                        logger.log(f"人口已满（{population_reader.current}/{population_reader.limit}），跳过")
                    continue

                if not acquire_lock():
                    if config.DEBUG_MODE:
                        logger.log("[锁] 获取失败")
                    else:
                        logger.log("操作进行中，跳过")
                    continue

                t_cond = time.time()
                perf_stats.record("[5] 条件检查", t_cond - t_cond_start)

                # [6] 执行操作
                t_op_start = time.time()
                try:
                    # 操作前清理：强制释放可能粘滞的修饰键
                    release_stuck_modifiers()

                    estimated_duration = config.TC_SELECT_DELAY + (config.VILLAGERS_PER_TC * config.QUEUE_DELAY) + config.BLOCK_INPUT_DURATION + 1.0
                    max_block_duration = min(estimated_duration * 2, 3.0 + config.VILLAGERS_PER_TC * 0.5)

                    blocker = input_blocked(max_duration=max_block_duration) if config.ENABLE_INPUT_BLOCK else nullcontext()

                    with blocker:
                        if config.DEBUG_MODE:
                            logger.force_print("[操作] 保存当前选中")
                        pydirectinput.keyDown('ctrl')
                        pydirectinput.press('0')
                        pydirectinput.keyUp('ctrl')
                        # 操作后立即清理修饰键，防止 Ctrl 粘滞影响后续操作
                        release_stuck_modifiers()
                        t_save = time.time()
                        perf_stats.record("[6.1] 保存当前选中", t_save - t_op_start)

                        if config.DEBUG_MODE:
                            logger.force_print("[操作] 选中TC")
                        tc_selector.do()
                        tc_counter.do()
                        t_tc = time.time()
                        perf_stats.record("[6.2] 选中TC并检测数量", t_tc - t_save)

                    if tc_counter.detection_failed:
                        if cached_tc_count > 0:
                            logger.force_print(f"[缓存] TC检测失败，使用缓存值 TC数={cached_tc_count}")
                            tc_counter.count = cached_tc_count
                            tc_counter.detection_failed = False
                        else:
                            logger.force_print(f"[错误] TC检测失败，进入冷却状态 {config.TC_DETECTION_FAILED_COOLDOWN}秒")
                            logger.force_print(f"[提示] 冷却期间会监控村民生产图标，如果检测到说明TC已建造，将自动恢复")

                            cooldown_start = time.time()
                            while time.time() - cooldown_start < config.TC_DETECTION_FAILED_COOLDOWN:
                                if not self.running:
                                    return
                                if self.paused:
                                    time.sleep(0.5)
                                    continue
                                if is_modifier_key_pressed():
                                    logger.force_print("[暂停] 检测到修饰键，暂停检测")
                                    while is_modifier_key_pressed() and self.running:
                                        time.sleep(config.CHECK_INTERVAL)
                                    logger.force_print("[恢复] 修饰键已释放，继续检测")
                                    cooldown_start = time.time()

                                has_villager_icon = cooldown_detector.has_villager_icon()
                                if has_villager_icon:
                                    elapsed = time.time() - cooldown_start
                                    logger.force_print(f"[恢复] 检测到村民生产图标，提前结束冷却（已等待{elapsed:.1f}秒）")
                                    break
                                time.sleep(config.COOLDOWN_CHECK_INTERVAL)
                            else:
                                logger.force_print(f"[冷却] 冷却时间结束，继续尝试检测TC")
                            continue
                    else:
                        cached_tc_count = tc_counter.count
                        if config.DEBUG_MODE:
                            logger.force_print(f"[缓存] 更新TC数量缓存={cached_tc_count}")

                    t_produce_start = time.time()
                    with input_blocked(max_duration=max_block_duration) if config.ENABLE_INPUT_BLOCK else nullcontext():
                        planned_villagers = config.VILLAGERS_PER_TC * tc_counter.count
                        actual_villagers = min(planned_villagers, available_slots)
                        max_villagers_by_food = food_reader.amount // config.FOOD_PER_VILLAGER
                        actual_villagers = min(actual_villagers, max_villagers_by_food)

                        if actual_villagers <= 0:
                            if config.DEBUG_MODE:
                                logger.force_print(f"[不足] 食物={food_reader.amount} 需要={config.FOOD_PER_VILLAGER}")
                            else:
                                logger.force_print(f"食物不足以生产村民（{food_reader.amount}/{config.FOOD_PER_VILLAGER}）")
                            continue

                        if actual_villagers < planned_villagers:
                            reason = []
                            if actual_villagers == available_slots:
                                reason.append("房屋不足")
                            if actual_villagers == max_villagers_by_food:
                                reason.append("食物不足")
                            reason_str = "、".join(reason)
                            if config.DEBUG_MODE:
                                logger.force_print(f"[生产] 人口={population_reader.current}/{population_reader.limit} 村民={villager_counter.total}/{config.MAX_VILLAGERS} 食物={food_reader.amount} TC={tc_counter.count} {reason_str} 生产={actual_villagers}/{planned_villagers}")
                            else:
                                logger.force_print(f"生产 {actual_villagers}/{planned_villagers} 个村民 (人口 {population_reader.current}/{population_reader.limit}, 村民 {villager_counter.total}/{config.MAX_VILLAGERS}, 食物 {food_reader.amount}, TC {tc_counter.count}, {reason_str})")
                        else:
                            if config.DEBUG_MODE:
                                logger.force_print(f"[生产] 人口={population_reader.current}/{population_reader.limit} 村民={villager_counter.total}/{config.MAX_VILLAGERS} 食物={food_reader.amount} TC={tc_counter.count} 生产={actual_villagers}")
                            else:
                                logger.force_print(f"生产 {actual_villagers} 个村民 (人口 {population_reader.current}/{population_reader.limit}, 村民 {villager_counter.total}/{config.MAX_VILLAGERS}, 食物 {food_reader.amount}, TC {tc_counter.count})")

                        if config.DEBUG_MODE:
                            logger.force_print("[操作] 排队村民")
                        villager_trainer.do(count=actual_villagers)
                        t_queue = time.time()
                        perf_stats.record("[6.3] 计算并排队村民", t_queue - t_produce_start)

                        if config.BLOCK_INPUT_DURATION > 0:
                            if config.DEBUG_MODE:
                                logger.force_print(f"[等待] {config.BLOCK_INPUT_DURATION}秒")
                            time.sleep(config.BLOCK_INPUT_DURATION)

                        if config.DEBUG_MODE:
                            logger.force_print("[操作] 恢复选中")
                        pydirectinput.press('0')

                        if config.DEBUG_MODE:
                            logger.force_print("[操作] 取消编组")
                        pydirectinput.keyDown('ctrl')
                        pydirectinput.keyDown('alt')
                        pydirectinput.press('0')
                        pydirectinput.keyUp('alt')
                        pydirectinput.keyUp('ctrl')
                        # 操作后清理：强制释放可能粘滞的修饰键
                        release_stuck_modifiers()
                        t_restore = time.time()
                        perf_stats.record("[6.4] 等待+恢复选中+取消编组", t_restore - t_queue)

                        last_trigger_time = time.time()
                        total_time = last_trigger_time - loop_start
                        perf_stats.record("[6] 执行操作（总耗时）", total_time)

                    if config.POST_OPERATION_DELAY > 0:
                        time.sleep(config.POST_OPERATION_DELAY)

                finally:
                    release_lock()

                if config.CHECK_INTERVAL > 0:
                    elapsed_loop = time.time() - loop_start
                    remaining = config.CHECK_INTERVAL - elapsed_loop
                    if remaining > 0:
                        time.sleep(remaining)

        except Exception as exc:
            if self.running:
                import traceback
                traceback.print_exc()
                self.root.after(0, lambda: self._stop())

    # ==================== 关闭 ====================

    def _on_close(self):
        """主窗口关闭：停止运行、取消定时器、恢复stdout/stderr、销毁窗口"""
        self.running = False
        self.paused = False

        # 取消内存监控定时器
        if self._mem_after_id is not None:
            try:
                self.root.after_cancel(self._mem_after_id)
            except Exception:
                pass
            self._mem_after_id = None
        if self._mem_draw_after_id is not None:
            try:
                self.root.after_cancel(self._mem_draw_after_id)
            except Exception:
                pass
            self._mem_draw_after_id = None

        # 取消CPU监控定时器
        if self._cpu_after_id is not None:
            try:
                self.root.after_cancel(self._cpu_after_id)
            except Exception:
                pass
            self._cpu_after_id = None
        if self._cpu_draw_after_id is not None:
            try:
                self.root.after_cancel(self._cpu_draw_after_id)
            except Exception:
                pass
            self._cpu_draw_after_id = None

        sys.stdout = self._original_stdout
        sys.stderr = self._original_stderr

        try:
            import main as main_module
            main_module._gui_running = False
        except ImportError:
            pass

        if self.worker_thread and self.worker_thread.is_alive():
            self.worker_thread.join(timeout=2.0)

        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    app = AOE4App()
    app.run()


if __name__ == "__main__":
    main()
