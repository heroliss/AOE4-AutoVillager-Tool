"""
配置文件 - 所有可调整的参数集中在此

重要说明：
1. 本配置基于 2560x1440 分辨率
2. 如果你的分辨率不同，需要调整所有坐标参数
3. HDR开关影响游戏检测颜色值，通过 _get_game_detect_color() 动态获取
4. 模板图片基于中国阵营截取，但经测试所有阵营通用

性能优化说明：
- USE_GPU: 对于小图片OCR，CPU模式通常比GPU更快（GPU有数据传输开销）
- OCR_IMAGE_SCALE: 图片缩放比例，越小越快但可能影响准确率
- VILLAGER_CHECK_INTERVAL: 村民数量变化慢，不需要频繁检查（默认3秒）
"""

import os

# ==================== 基础参数 ====================
CHECK_INTERVAL = 0.1  # 检测循环间隔（秒），平衡响应速度和CPU占用
VILLAGERS_PER_TC = 3  # 每个TC排队的村民数量
MAX_VILLAGERS = 120  # 村民数量上限，超过此数量停止自动生产（注意：移动/建造/战斗中的村民无法统计，仅供参考）
ENABLE_MAX_VILLAGERS = False  # 是否启用村民上限检测（因统计不准，默认关闭）
MIN_FOOD = 50  # 最低食物要求，低于此值不生产村民
FOOD_PER_VILLAGER = 50  # 单个村民需要的食物数量
VILLAGER_CHECK_INTERVAL = 3  # 村民数量检查间隔（秒），村民数量变化慢，不需要频繁检查

# ==================== 按键设置 ====================
TC_SELECT_KEY = 'h'  # 选中所有TC的快捷键（需在游戏中设置"选择所有城镇中心"对应按键）
VILLAGER_QUEUE_KEY = 'q'  # 生产村民的快捷键
ENABLE_SHIFT_QUEUE = True  # 是否使用Shift+按键批量排队（每次5个），关闭则逐个排队

# ==================== 操作时序设置 ====================
BLOCK_INPUT_DURATION = 0  # 操作后等待时长（秒），设为0最快
ENABLE_INPUT_BLOCK = True  # 是否在操作期间屏蔽物理鼠标键盘输入（需要管理员权限）
POST_OPERATION_DELAY = 3.0  # 操作完成后等待游戏UI更新的时间（秒），避免连续触发（联网游戏需要更长延迟），设为0禁用 （对于蒙古开局tc没展开无法生产农民的情况，这个值可以调大些）

# ==================== 调试开关 ====================
# 调试开关说明：
# - DEBUG_MODE: 全局调试开关，控制所有模块的详细日志（TC/村民/食物/遮挡/生产检测）
# - DEBUG_PERFORMANCE: 性能分析开关，显示各模块耗时（独立开关）
# - DEBUG_SAVE_SCREENSHOTS: 是否保存调试截图到文件（需配合DEBUG_MODE，关闭可提升5-10ms性能）
#
# 推荐配置：
# - 日常使用：全部False
# - 排查问题：开启DEBUG_MODE，关闭DEBUG_SAVE_SCREENSHOTS
# - 性能优化：开启DEBUG_PERFORMANCE

DEBUG_MODE = False  # 全局调试：所有模块的详细日志
DEBUG_PERFORMANCE = False  # 性能分析：显示各模块详细耗时（独立开关）
DEBUG_SAVE_SCREENSHOTS = False  # 保存调试截图：需配合DEBUG_MODE，关闭可提升5-10ms性能

# ==================== OCR设置 ====================
USE_GPU = False  # 是否使用GPU加速OCR（⚠不建议开启，小图片OCR时CPU更快；CPU版exe中此选项无效）
OCR_IMAGE_SCALE = 1  # OCR图片缩放比例，越小越快但可能影响准确率

# ==================== 截图区域坐标 ====================
# 注意：以下坐标基于 2560x1440 分辨率，其他分辨率需要调整

# 游戏窗口检测：检测特定像素点颜色判断是否在游戏中
# SDR和HDR各有独立的坐标和颜色值，通过HDR开关切换使用哪一组
GAME_DETECT_PIXEL_SDR = (2526, 1405)  # SDR检测像素点坐标
GAME_DETECT_PIXEL_HDR = (2526, 1405)  # HDR检测像素点坐标（通常与SDR相同，但可单独调整）
HDR_ENABLED = False  # HDR开关，True=使用HDR组坐标和颜色，False=使用SDR组
GAME_DETECT_COLOR_SDR = (26, 32, 46)  # SDR（HDR关闭）时的颜色值
GAME_DETECT_COLOR_HDR = (65, 78, 105)  # HDR开启时的颜色值

def _get_game_detect_pixel():
    """根据HDR开关返回对应的检测像素坐标（动态获取，支持运行时切换）"""
    return GAME_DETECT_PIXEL_HDR if HDR_ENABLED else GAME_DETECT_PIXEL_SDR

def _get_game_detect_color():
    """根据HDR开关返回对应的检测颜色（动态获取，支持运行时切换）"""
    return GAME_DETECT_COLOR_HDR if HDR_ENABLED else GAME_DETECT_COLOR_SDR

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
VILLAGER_MATCH_THRESHOLD = 0.6  # 村民图标匹配阈值
BLOCKED_MATCH_THRESHOLD = 0.7  # 遮挡检测匹配阈值（完全遮挡）
BLOCKED_TRANSITION_THRESHOLD = 0.1  # 遮挡渐变下限阈值，低于此值认为完全未遮挡
# 说明：置信度区间划分
#   [0, 0.1) = 完全未遮挡（需连续2次检测确认稳定）
#   [0.1, 0.7) = 渐变中（需连续2次检测确认稳定，且连续3次渐变时置信度变化<0.05认为是场景误判）
#   [0.7, 1.0] = 完全遮挡（需连续2次检测确认稳定）
# 如果频繁误判渐变，可以缩小区间（如改为0.15-0.65）；如果渐变检测不到，可以扩大区间
TC_MATCH_THRESHOLD = 0.7  # TC图标匹配阈值

# ==================== 按键延迟 ====================
# pydirectinput默认每次按键有0.1秒延迟，这里设置为0可以加速
TC_SELECT_DELAY = 0.03  # 按选中TC键后的等待时间（秒）
TC_RETRY_DELAY = 0.01  # TC检测失败后重试的等待时间（秒），给游戏UI足够的刷新时间
TC_MAX_RETRY = 50  # TC检测失败时的最大重试次数
TC_DETECTION_FAILED_COOLDOWN = 1000.0  # TC检测失败后的冷却时间（秒），避免频繁重试。值很大是因为冷却期间会监控村民图标，检测到后提前结束
COOLDOWN_CHECK_INTERVAL = 0.5  # 冷却期间的检测间隔（秒）
QUEUE_DELAY = 0  # 每次按生产键之间的延迟，设为0最快

# ==================== 目录路径 ====================
# 内部目录：PyInstaller打包后指向 _MEIPASS 临时解压目录，用于读取打包的资源文件
# 外部目录：exe所在目录（或开发时项目根目录），用于保存用户数据
import sys

# 内部基础目录（打包后的资源目录 / 开发时的项目根目录）
if getattr(sys, 'frozen', False):
    _INTERNAL_DIR = sys._MEIPASS
else:
    _INTERNAL_DIR = os.path.dirname(os.path.abspath(__file__))

# 外部基础目录（exe所在目录 / 开发时的项目根目录）
if getattr(sys, 'frozen', False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

# 模板目录（内部，打包在exe中）
TEMPLATES_DIR = os.path.join(_INTERNAL_DIR, "templates")

# 用户自定义模板目录（外部，与exe同目录下的user_templates文件夹）
USER_TEMPLATES_DIR = os.path.join(APP_DIR, "user_templates")

# 调试输出目录（外部，保存在exe同目录下，仅在需要保存截图时才创建）
DEBUG_OUTPUT_DIR = os.path.join(APP_DIR, "debug_output")


def _resolve_template(internal_path):
    """解析模板路径：如果用户模板目录存在同名文件则使用用户版本，否则使用内置版本
    
    :param internal_path: 内置模板的完整路径
    :return: (实际使用的路径, 是否使用了用户替换)
    """
    filename = os.path.basename(internal_path)
    user_path = os.path.join(USER_TEMPLATES_DIR, filename)
    if os.path.exists(user_path):
        return user_path, True
    return internal_path, False


def get_template_info():
    """获取所有模板图片的信息（供GUI显示用）
    
    :return: 列表，每项为 (模板名称, 内置路径, 用户路径或None, 是否使用用户替换)
    """
    template_names = [
        ("村民图标 (cunmin.png)", "cunmin.png"),
        ("遮挡图标 (blocked.png)", "blocked.png"),
        ("单TC预检测 (tc_single.png)", "tc_single.png"),
    ]
    # 动态扫描 tc_number_N.png
    for i in range(1, 50):
        fname = f"tc_number_{i}.png"
        internal = os.path.join(TEMPLATES_DIR, fname)
        if not os.path.exists(internal):
            break
        template_names.append((f"TC数量{i} ({fname})", fname))

    info = []
    for display_name, filename in template_names:
        internal_path = os.path.join(TEMPLATES_DIR, filename)
        user_path = os.path.join(USER_TEMPLATES_DIR, filename)
        has_user = os.path.exists(user_path)
        info.append((display_name, internal_path, user_path if has_user else None, has_user))
    return info


# 模板图片路径（使用 _resolve_template 支持用户替换）
VILLAGER_TEMPLATE, _ = _resolve_template(os.path.join(TEMPLATES_DIR, "cunmin.png"))
BLOCKED_TEMPLATE, _ = _resolve_template(os.path.join(TEMPLATES_DIR, "blocked.png"))
TC_SINGLE_TEMPLATE, _ = _resolve_template(os.path.join(TEMPLATES_DIR, "tc_single.png"))

# 调试输出路径
TC_DEBUG_SCREENSHOT = os.path.join(DEBUG_OUTPUT_DIR, "tc_detection_debug.png")
VILLAGER_COUNT_DEBUG_SCREENSHOT = os.path.join(DEBUG_OUTPUT_DIR, "villager_count_debug.png")
BLOCKED_DEBUG_SCREENSHOT = os.path.join(DEBUG_OUTPUT_DIR, "blocked_detection_debug.png")
FOOD_DEBUG_SCREENSHOT = os.path.join(DEBUG_OUTPUT_DIR, "food_detection_debug.png")
