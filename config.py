"""
配置文件 - 所有可调整的参数集中在此

重要说明：
1. 本配置基于 2560x1440 分辨率 + HDR开启
2. 如果你的分辨率不同，需要调整所有坐标参数
3. HDR开关只影响 GAME_DETECT_COLOR 像素值，不影响图片识别
4. 模板图片基于中国阵营截取，但经测试所有阵营通用
"""

# ==================== 基础参数 ====================
CHECK_INTERVAL = 0.2  # 检测循环间隔（秒）
VILLAGERS_PER_TC = 3  # 每个TC排队的村民数量
MAX_VILLAGERS = 120  # 村民数量上限，超过此数量停止自动生产
MIN_FOOD = 50  # 最低食物要求，低于此值不生产村民
FOOD_PER_VILLAGER = 50  # 单个村民需要的食物数量

# ==================== 操作时序设置 ====================
OPERATION_DELAY = 0  # 蜂鸣后延迟多久执行操作（秒），设为0立即执行
BLOCK_INPUT_DURATION = 0  # 操作后等待时长（秒），设为0最快
ENABLE_INPUT_BLOCK = True  # 是否在操作期间屏蔽物理鼠标键盘输入（需要管理员权限）

# ==================== 调试开关 ====================
DEBUG_MODE = False  # 全局调试模式：启用TC计数和村民计数的详细日志和截图
DEBUG_BLOCKED_DETECTION = False  # 遮挡检测调试：显示遮挡检测置信度和截图

# ==================== 截图区域坐标 ====================
# 注意：以下坐标基于 2560x1440 分辨率，其他分辨率需要调整

# 游戏窗口检测：检测特定像素点颜色判断是否在游戏中
# 注意：HDR开关会影响此颜色值，需要根据实际情况调整
GAME_DETECT_PIXEL = (2526, 1405)
GAME_DETECT_COLOR = (65, 78, 105)  # 这是HDR开启时的颜色值，SDR时该点的颜色为：(26,32,46)

# 村民生产队列检测区域
VILLAGER_QUEUE_REGION = (10, 970, 500, 1025)

# UI遮挡检测区域（5x5像素）
BLOCKED_DETECT_REGION = (260, 1000, 265, 1005)

# 人口显示区域（如 "50/200"）
POPULATION_REGION = (45, 1126, 151, 1183)

# TC图标检测区域（左下角）
TC_ICON_REGION = (390, 1210, 700, 1260)

# 村民总数统计区域（左下角多个数字）
VILLAGER_COUNT_REGION = (185, 1130, 240, 1420)

# 食物数量显示区域
FOOD_REGION = (50, 1222, 140, 1248)

# ==================== 模板匹配阈值 ====================
VILLAGER_MATCH_THRESHOLD = 0.6  # 村民图标匹配阈值
BLOCKED_MATCH_THRESHOLD = 0.7  # 遮挡检测匹配阈值
TC_MATCH_THRESHOLD = 0.7  # TC图标匹配阈值

# ==================== 按键延迟 ====================
# pydirectinput默认每次按键有0.1秒延迟，这里设置为0可以加速
TC_SELECT_DELAY = 0.05  # 按H键选中TC后的等待时间，最小建议值0.05秒（低于0.02秒可能无法识别多TC）
QUEUE_DELAY = 0  # 每次按Q键之间的延迟，设为0最快

# ==================== 音效设置 ====================
BEEP_FREQUENCY = 1000  # 蜂鸣声频率（Hz），范围 37~32767
BEEP_DURATION = 50  # 蜂鸣声持续时间（毫秒）
BEEP_COUNT = 0  # 触发生产时蜂鸣次数，设为0禁用蜂鸣

# ==================== 目录路径 ====================
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
DEBUG_OUTPUT_DIR = os.path.join(BASE_DIR, "debug_output")

# 模板图片路径
VILLAGER_TEMPLATE = os.path.join(TEMPLATES_DIR, "cunmin.png")
BLOCKED_TEMPLATE = os.path.join(TEMPLATES_DIR, "blocked.png")
TC_ICON_TEMPLATE = os.path.join(TEMPLATES_DIR, "tc_icon.png")

# 调试输出路径
TC_DEBUG_SCREENSHOT = os.path.join(DEBUG_OUTPUT_DIR, "tc_detection_debug.png")
VILLAGER_COUNT_DEBUG_SCREENSHOT = os.path.join(DEBUG_OUTPUT_DIR, "villager_count_debug.png")
BLOCKED_DEBUG_SCREENSHOT = os.path.join(DEBUG_OUTPUT_DIR, "blocked_detection_debug.png")
FOOD_DEBUG_SCREENSHOT = os.path.join(DEBUG_OUTPUT_DIR, "food_detection_debug.png")
