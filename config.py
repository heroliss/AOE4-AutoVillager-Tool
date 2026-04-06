"""
配置文件 - 所有可调整的参数集中在此

重要说明：
1. 本配置基于 2560x1440 分辨率 + HDR开启
2. 如果你的分辨率不同，需要调整所有坐标参数
3. HDR开关只影响 GAME_DETECT_COLOR 像素值，不影响图片识别
4. 模板图片基于中国阵营截取，但经测试所有阵营通用

性能优化说明：
- USE_GPU: 对于小图片OCR，CPU模式通常比GPU更快（GPU有数据传输开销）
- OCR_IMAGE_SCALE: 图片缩放比例，越小越快但可能影响准确率
- VILLAGER_CHECK_INTERVAL: 村民数量变化慢，不需要频繁检查
"""

# ==================== 基础参数 ====================
CHECK_INTERVAL = 0.1  # 检测循环间隔（秒），平衡响应速度和CPU占用
VILLAGERS_PER_TC = 3  # 每个TC排队的村民数量
MAX_VILLAGERS = 120  # 村民数量上限，超过此数量停止自动生产
MIN_FOOD = 50  # 最低食物要求，低于此值不生产村民
FOOD_PER_VILLAGER = 50  # 单个村民需要的食物数量
VILLAGER_CHECK_INTERVAL = 10.0  # 村民数量检查间隔（秒），村民数量变化慢，不需要频繁检查

# ==================== 操作时序设置 ====================
OPERATION_DELAY = 0  # 蜂鸣后延迟多久执行操作（秒），设为0立即执行
BLOCK_INPUT_DURATION = 0  # 操作后等待时长（秒），设为0最快
ENABLE_INPUT_BLOCK = True  # 是否在操作期间屏蔽物理鼠标键盘输入（需要管理员权限）
POST_OPERATION_DELAY = 3.0  # 操作完成后等待游戏UI更新的时间（秒），避免连续触发（联网游戏需要更长延迟），设为0禁用 （对于蒙古开局tc没展开无法生产农民的情况，这个值可以调大些）

# ==================== 调试开关 ====================
# 调试开关说明：
# - DEBUG_MODE: 全局调试开关，控制TC/村民/食物等模块的详细日志和截图
# - DEBUG_BLOCKED_DETECTION: 遮挡检测专项调试，独立开关（不依赖DEBUG_MODE）
# - DEBUG_TRAINING_DETECTION: 村民生产检测专项调试，显示每次检测的置信度
# - DEBUG_PERFORMANCE: 性能分析开关，显示各模块耗时（独立于其他开关）
# - DEBUG_SAVE_SCREENSHOTS: 是否保存调试截图到文件（关闭可提升5-10ms性能）
#
# 推荐配置：
# - 日常使用：全部False
# - 排查问题：开启DEBUG_MODE，关闭DEBUG_SAVE_SCREENSHOTS
# - 性能优化：开启DEBUG_PERFORMANCE
# - 遮挡误判：开启DEBUG_BLOCKED_DETECTION

DEBUG_MODE = False  # 全局调试：TC/村民/食物模块的详细日志和截图
DEBUG_BLOCKED_DETECTION = False  # 遮挡检测：显示置信度和截图（独立开关）
DEBUG_TRAINING_DETECTION = False  # 村民生产：显示每次检测的置信度
DEBUG_PERFORMANCE = False  # 性能分析：显示各模块详细耗时（独立开关）
DEBUG_SAVE_SCREENSHOTS = False  # 保存调试截图：关闭可提升5-10ms性能

# ==================== OCR设置 ====================
USE_GPU = False  # 是否使用GPU加速OCR（小图片OCR时CPU更快，GPU有数据传输开销）
OCR_IMAGE_SCALE = 1  # OCR图片缩放比例，越小越快但可能影响准确率

# ==================== 截图区域坐标 ====================
# 注意：以下坐标基于 2560x1440 分辨率，其他分辨率需要调整

# 游戏窗口检测：检测特定像素点颜色判断是否在游戏中
# 注意：HDR开关会影响此颜色值，需要根据实际情况调整
GAME_DETECT_PIXEL = (2526, 1405)
GAME_DETECT_COLOR = (65, 78, 105)  # 这是HDR开启时的颜色值，SDR时该点的颜色为：(26,32,46)

# 村民生产队列检测区域
VILLAGER_QUEUE_REGION = (10, 970, 500, 1025)

# UI遮挡检测区域
BLOCKED_DETECT_REGION = (265, 950, 280, 970)

# 人口显示区域（如 "50/200"）
POPULATION_REGION = (50, 1140, 150, 1170)

# TC图标检测区域（左下角）
TC_ICON_REGION = (444, 1212, 492, 1259)

# 单TC预检测区域（用于快速判断是否只有1个TC）
SINGLE_TC_REGION = (300, 1140, 354, 1194)

# 村民总数统计区域（左下角多个数字）
VILLAGER_COUNT_REGION = (185, 1130, 240, 1420)

# 食物数量显示区域
FOOD_REGION = (50, 1222, 140, 1248)

# ==================== 模板匹配阈值 ====================
VILLAGER_MATCH_THRESHOLD = 0.7  # 村民图标匹配阈值
BLOCKED_MATCH_THRESHOLD = 0.7  # 遮挡检测匹配阈值（完全遮挡）
BLOCKED_TRANSITION_THRESHOLD = 0.3  # 遮挡渐变下限阈值，低于此值认为完全未遮挡
# 说明：置信度区间划分
#   [0, 0.3) = 完全未遮挡（立即确定）
#   [0.3, 0.7) = 渐变中（需连续3次检测，置信度变化<0.05认为是场景误判）
#   [0.7, 1.0] = 完全遮挡（立即确定）
# 如果频繁误判渐变，可以缩小区间（如改为0.35-0.65）；如果渐变检测不到，可以扩大区间
TC_MATCH_THRESHOLD = 0.7  # TC图标匹配阈值

# ==================== 按键延迟 ====================
# pydirectinput默认每次按键有0.1秒延迟，这里设置为0可以加速
TC_SELECT_DELAY = 0.03  # 按H键选中TC后的等待时间（秒）
TC_RETRY_DELAY = 0.01  # TC检测失败后重试H键的等待时间（秒），给游戏UI足够的刷新时间
TC_MAX_RETRY = 50  # TC检测失败时的最大重试次数
TC_DETECTION_FAILED_COOLDOWN = 1000.0  # TC检测失败后的冷却时间（秒），避免频繁重试
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

# 自动创建调试输出目录
if not os.path.exists(DEBUG_OUTPUT_DIR):
    os.makedirs(DEBUG_OUTPUT_DIR)

# 模板图片路径
VILLAGER_TEMPLATE = os.path.join(TEMPLATES_DIR, "cunmin.png")
BLOCKED_TEMPLATE = os.path.join(TEMPLATES_DIR, "blocked.png")
TC_ICON_TEMPLATE = os.path.join(TEMPLATES_DIR, "tc_icon.png")
TC_SINGLE_TEMPLATE = os.path.join(TEMPLATES_DIR, "tc_single.png")  # 单TC预检测专用模板

# 调试输出路径
TC_DEBUG_SCREENSHOT = os.path.join(DEBUG_OUTPUT_DIR, "tc_detection_debug.png")
VILLAGER_COUNT_DEBUG_SCREENSHOT = os.path.join(DEBUG_OUTPUT_DIR, "villager_count_debug.png")
BLOCKED_DEBUG_SCREENSHOT = os.path.join(DEBUG_OUTPUT_DIR, "blocked_detection_debug.png")
FOOD_DEBUG_SCREENSHOT = os.path.join(DEBUG_OUTPUT_DIR, "food_detection_debug.png")
