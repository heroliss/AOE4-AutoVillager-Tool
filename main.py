"""
AOE4 自动生产村民工具

满足以下全部条件时自动生产村民：
  1. 当前处于游戏窗口
  2. 生产队列中没有村民正在生产
  3. UI未被遮挡（三态检测：完全遮挡/完全未遮挡/渐变中）
  4. 人口有空间
  5. 村民总数未达上限
  6. 食物充足

=================== 主循环流程 ===================

[1] 修饰键检测（Shift/Ctrl/Alt）
    └── 按下 → 输出提示并继续（下次循环会跳过）
    └── 连续50次检测 → 可能粘滞，强制释放修饰键

[2] 游戏窗口检测（双重检测）
    ├── 窗口标题检测（极快，<1ms）
    │   └── 不匹配 → "不在游戏窗口" → 继续下次循环
    │
    └── 像素颜色检测（极快，<1ms）
        └── 不匹配 → "不在游戏中" → 继续下次循环

[3] 村民生产状态检测（快速，模板匹配）
    ├── 完全遮挡 → 输出提示并继续（DEBUG模式输出置信度）
    ├── 渐变中 → 静默跳过（DEBUG模式输出置信度）
    ├── 生产中 → 输出提示并继续（DEBUG模式输出置信度）
    └── 未遮挡且无生产 → 继续下一步

[4] OCR识别（慢速，并行执行）
    ├── 人口识别（~0.3-0.5秒）
    ├── 食物识别（~0.3-0.5秒）
    └── 村民总数（~0.3-0.5秒，每3秒检查一次）

[5] 条件检查
    ├── 人口识别失败 → 输出提示并跳过
    ├── 村民已达上限 → 输出提示并跳过
    ├── 食物识别失败 → 输出提示并跳过
    ├── 食物不足 → 输出提示并跳过
    ├── 人口已满 → 输出提示并跳过
    └── 操作进行中 → 输出提示并跳过

[6] 获取锁并执行操作
    ├── 6.1 保存当前选中（Ctrl+0）
    ├── 6.2 选中TC并检测数量
    ├── 6.3 根据空位和食物计算生产数量
    ├── 6.4 执行排队操作（Q键）
    ├── 6.5 等待 BLOCK_INPUT_DURATION（排队后缓冲）
    ├── 6.6 恢复选中（0）
    ├── 6.7 取消编组（Ctrl+Alt+0）
    └── 6.8 等待 POST_OPERATION_DELAY（UI更新等待）

[7] 释放锁 → 补充等待 CHECK_INTERVAL（仅当循环总耗时不足时）

=================== 延迟说明 ===================

手动延迟（可配置）：
  - CHECK_INTERVAL         = 0.1s  # 循环检测间隔
  - BLOCK_INPUT_DURATION   = 0s    # 操作后等待
  - POST_OPERATION_DELAY   = 3.0s  # 操作后UI更新等待
  - QUEUE_DELAY            = 0s    # 排队按键间隔

运行延迟（检测耗时）：
  - 窗口标题检测   < 1ms
  - 像素颜色检测   < 1ms
  - 村民生产检测   ~50-100ms（模板匹配）
  - OCR识别（3项） ~0.3-0.5s（并行执行）

检测循环总耗时（无操作时）：
  - 最小：~100ms（CHECK_INTERVAL，仅快速跳过路径）
  - 正常：~150-200ms（检测约100ms + 补充间隔）
  - OCR触发时：~300-500ms（无需额外间隔，OCR耗时已超过CHECK_INTERVAL）

=================== 性能优化 ===================
  - 优先执行快速检测（模板匹配），只有在需要时才执行慢速OCR
  - 并行执行多个OCR任务，减少总耗时
  - 村民数量每3秒检查一次（变化慢，无需频繁检查）
  - 默认使用CPU模式OCR（小图片时CPU比GPU更快）
  - Windows GetPixel API 直接读取像素（绕过截图，<0.1ms）

=================== UI遮挡检测技术 ===================
  - 三态判断：完全遮挡(≥0.7)/完全未遮挡(<0.1)/渐变中(0.1-0.7)
  - 稳定性检测：所有状态需连续2次检测才认为稳定
  - 渐变误判检测：连续3次渐变且置信度变化<0.05认为是场景误判

=================== 半透明UI检测技术 ===================
  - 策略1：中等置信度(0.3-0.65) + 快速变化(>0.1)
  - 策略2：置信度突然下降（从>0.6降到<0.5，变化>0.2）
  - 策略3：连续下降检测（最近3次持续下降，总变化>0.1）

=================== TC数量缓存机制 ===================
  - 成功检测后更新缓存，检测失败时使用缓存值
  - 避免UI遮挡导致的"没有TC"误判
  - 冷却期间监控村民图标，检测到后提前结束冷却

=================== 配置说明 ===================
  - 本工具基于 2560x1440 分辨率 + HDR开启
  - 模板图片基于中国阵营，但所有阵营通用
  - 如需其他分辨率，请调整 config.py 中的坐标

=================== 特殊场景 ===================
  - 游牧开局：开局无TC，人口上限为0（如5/0），需建造TC后才能生产村民
  - 食物为0：EasyOCR对单个"0"识别能力差，使用三级兜底策略确保正确识别

按 Ctrl+C 退出。
"""

# 立即打印启动信息（避免模块导入期间的卡顿）
print()
print("=" * 50, flush=True)
print("  AOE4 自动生产村民工具", flush=True)
print("  正在加载模块，请稍候...", flush=True)
print("=" * 50, flush=True)
print(flush=True)

# 按顺序导入模块，显示加载进度
print("  [>] 加载基础模块...", flush=True)
import sys
import time

print("  [>] 加载配置...", flush=True)
from config import *  # 命令行模式：启动后配置不变，静态绑定即可（GUI模式使用 import config 动态引用）

print("  [>] 加载游戏检测模块...", flush=True)
from game_detector import GameDetector

print("  [>] 加载村民生产检测模块...", flush=True)
from villager_training_detector import VillagerTrainingDetector

print("  [>] 加载OCR模块...", flush=True)
from population_reader import PopulationReader

print("  [>] 加载操作模块...", flush=True)
from villager_trainer import VillagerTrainer
from tc_counter import TCCounter
from tc_selector import TCSelector

print("  [>] 加载统计模块...", flush=True)
from villager_counter import VillagerCounter
from food_reader import FoodReader

print("  [>] 加载工具模块...", flush=True)
from lock import acquire_lock, release_lock, cleanup_lock
from input_blocker import input_blocked

print("  [>] 加载输入控制...", flush=True)
import pydirectinput
from input_config import *  # noqa: F401, F403

from contextlib import nullcontext
from logger import perf_stats
import concurrent.futures

# GUI模式控制标志（由gui_app.py设置）
_gui_running = True

# 修饰键检测（用户按下Shift/Ctrl/Alt时暂停检测）
import ctypes
user32 = ctypes.windll.user32

# 虚拟键码
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12  # Alt键

def is_modifier_key_pressed():
    """检测用户是否按下了修饰键（Shift/Ctrl/Alt）"""
    return (
        (user32.GetAsyncKeyState(VK_SHIFT) & 0x8000) != 0 or
        (user32.GetAsyncKeyState(VK_CONTROL) & 0x8000) != 0 or
        (user32.GetAsyncKeyState(VK_MENU) & 0x8000) != 0
    )


def release_stuck_modifiers():
    """
    强制释放所有可能粘滞的修饰键（Shift/Ctrl/Alt）

    解决的问题：
    - BlockInput 不屏蔽 GetAsyncKeyState，用户在操作期间按下的修饰键会被检测到
    - pydirectinput 的 keyDown/keyUp 与物理按键冲突时，可能导致修饰键"粘滞"
    - 粘滞的修饰键会导致 is_modifier_key_pressed() 一直返回 True，程序永久暂停

    注意：此函数通过发送虚拟 keyUp 来清理状态，仅在程序操作流程中使用，
    不影响用户的实际按键意图（因为调用时机都在操作前后）
    """
    for key_name in ('shift', 'ctrl', 'alt'):
        pydirectinput.keyUp(key_name)
    # 短暂等待确保操作系统处理完按键释放事件
    time.sleep(0.01)

print("  [✓] 所有模块加载完成!\n", flush=True)


class LogMerger:
    """合并重复的日志输出"""
    def __init__(self):
        self.last_message = None
        self.repeat_count = 0
        self.printed_first = False

    def log(self, message):
        """打印日志，相同的日志会合并显示"""
        if message == self.last_message:
            self.repeat_count += 1
        else:
            self._flush()
            self.last_message = message
            self.repeat_count = 1
            self.printed_first = False
            # 立即打印第一次出现的日志
            print(message, end='', flush=True)
            self.printed_first = True

    def _flush(self):
        """输出累积的重复次数"""
        if self.last_message and self.printed_first:
            if self.repeat_count > 1:
                print(f" x{self.repeat_count}")
            else:
                print()  # 只打印换行

    def force_print(self, message):
        """强制立即打印，不合并"""
        self._flush()
        print(message)
        self.last_message = None
        self.repeat_count = 0
        self.printed_first = False


def main():
    # 1. 检测GPU状态
    gpu_available = False
    gpu_name = ""
    try:
        import torch
        if torch.cuda.is_available():
            gpu_available = True
            gpu_name = torch.cuda.get_device_name(0)
    except Exception:
        pass
    ocr_mode = "GPU加速" if (USE_GPU and gpu_available) else "CPU模式"
    if gpu_available:
        print(f"  [>] GPU: {gpu_name} (可用) | OCR模式: {ocr_mode}", flush=True)
    else:
        print(f"  [>] OCR模式: {ocr_mode}", flush=True)

    # 2. 清理残留锁文件
    print("  [>] 清理残留文件...", flush=True)
    cleanup_lock()

    # 3. 初始化各模块
    print("  [>] 初始化检测器...", flush=True)
    game_detector      = GameDetector()
    training_detector  = VillagerTrainingDetector()
    population_reader  = PopulationReader()
    tc_selector        = TCSelector()
    tc_counter         = TCCounter()
    villager_counter   = VillagerCounter()
    food_reader        = FoodReader()
    cooldown_detector  = VillagerTrainingDetector()
    villager_trainer   = VillagerTrainer()  # 复用实例，避免每次循环创建
    logger             = LogMerger()
    executor           = concurrent.futures.ThreadPoolExecutor(max_workers=3)  # 复用线程池

    # 4. 预热OCR模型
    print(flush=True)
    print("  [>] 预热OCR模型...", flush=True)
    warmup_start = time.time()
    population_reader.do()  # 触发OCR初始化
    warmup_time = time.time() - warmup_start
    print(f"       耗时 {warmup_time:.2f}秒", flush=True)

    # 加载完成
    print(flush=True)
    print("=" * 50, flush=True)
    max_villagers_str = f"村民上限: {MAX_VILLAGERS}(已启用，仅供参考)" if ENABLE_MAX_VILLAGERS else "村民上限: 未启用"
    print(f"  {max_villagers_str}  |  最低食物: {MIN_FOOD}  |  每TC排队: {VILLAGERS_PER_TC}", flush=True)
    print(f"  选所有TC键: {TC_SELECT_KEY.upper()}  |  出农键: {VILLAGER_QUEUE_KEY.upper()}  |  Shift排队: {'开' if ENABLE_SHIFT_QUEUE else '关'}", flush=True)
    print("  程序已就绪，等待进入游戏...", flush=True)
    print("=" * 50, flush=True)
    print()
    print("  按 Ctrl+C 退出\n", flush=True)

    # 调试：记录上次触发生产的时间
    last_trigger_time = None
    last_villager_check_time = 0  # 上次检查村民数量的时间
    cached_tc_count = 0  # 缓存的TC数量，开局为0
    modifier_stuck_count = 0  # 修饰键连续检测计数，用于检测粘滞

    try:
        while True:
            perf_stats.maybe_report()
            loop_start = time.time()

            # [1] 检查用户是否按下了修饰键（Shift/Ctrl/Alt）
            if is_modifier_key_pressed():
                modifier_stuck_count += 1
                # 连续检测到修饰键超过阈值，可能是粘滞（pydirectinput keyDown/keyUp冲突）
                # 强制释放修饰键以恢复正常状态
                if modifier_stuck_count >= 50:
                    print(f"[修复] 修饰键连续检测{modifier_stuck_count}次，可能粘滞，强制释放")
                    release_stuck_modifiers()
                    modifier_stuck_count = 0
                    # 等待一个短暂时间让系统处理释放事件
                    time.sleep(0.05)
                    # 如果释放后仍然检测到，说明是用户真的在按着修饰键
                    if not is_modifier_key_pressed():
                        print("[修复] 修饰键粘滞已修复")
                        continue
                logger.log("检测到修饰键，暂停")
                # 短暂等待避免CPU空转
                if CHECK_INTERVAL > 0:
                    time.sleep(CHECK_INTERVAL)
                continue
            else:
                modifier_stuck_count = 0
            t_modifier = time.time()
            perf_stats.record("[1] 修饰键检测", t_modifier - loop_start)

            # [2] 游戏窗口检测
            game_detector.do()

            # 2.1 状态处理
            if not game_detector.window_active:
                # 不在游戏窗口（窗口标题不匹配）
                logger.log("不在游戏窗口")
                if CHECK_INTERVAL > 0:
                    time.sleep(CHECK_INTERVAL)
                continue

            if not game_detector.pixel_match:
                # 在游戏窗口但不在游戏中（如主菜单、加载画面）
                logger.log("不在游戏中")
                if CHECK_INTERVAL > 0:
                    time.sleep(CHECK_INTERVAL)
                continue
            t_game = time.time()
            perf_stats.record("[2] 游戏窗口检测", t_game - t_modifier)

            # [3] 村民生产状态检测（模板匹配）
            training_detector.do()

            # 调试模式：打印所有检测结果
            if DEBUG_MODE:
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
                if DEBUG_MODE:
                    logger.log(f"[遮挡] 置信度={training_detector.blocked_confidence:.3f} 阈值={BLOCKED_MATCH_THRESHOLD:.3f}")
                else:
                    logger.log("生产队列图标被遮挡，无法判定是否有村民在生产")
                if CHECK_INTERVAL > 0:
                    time.sleep(CHECK_INTERVAL)
                continue

            # UI渐变中：静默跳过
            if training_detector.in_transition:
                if DEBUG_MODE:
                    logger.log(f"[渐变] 置信度={training_detector.blocked_confidence:.3f} 区间=[{BLOCKED_TRANSITION_THRESHOLD:.2f}, {BLOCKED_MATCH_THRESHOLD:.2f}]")
                continue

            # 村民正在生产中：静默跳过
            if training_detector.found:
                if DEBUG_MODE:
                    logger.log(f"[生产中] 置信度={training_detector.confidence:.3f} 阈值={VILLAGER_MATCH_THRESHOLD:.3f}")
                else:
                    logger.log("检测到村民正在生产中，跳过")
                continue

            # 调试：记录检测到"没有村民生产"的时间
            detection_time = time.time()
            if last_trigger_time and DEBUG_MODE:
                elapsed = detection_time - last_trigger_time
                logger.force_print(f"[时间] 距上次触发 {elapsed:.2f}秒")
            perf_stats.record("[3] 村民生产状态检测", detection_time - t_game)

            # [4] OCR识别（并行执行）
            ocr_start = time.time()
            current_time = ocr_start  # 避免重复调用 time.time()
            should_check_villagers = (current_time - last_villager_check_time) >= VILLAGER_CHECK_INTERVAL

            # 并行执行OCR操作以节省时间（复用线程池，避免每次创建/销毁）
            future_villager = None
            future_population = executor.submit(population_reader.do)
            future_food = executor.submit(food_reader.do)

            # 只在需要时检查村民数量
            if should_check_villagers:
                future_villager = executor.submit(villager_counter.do)
                concurrent.futures.wait([future_population, future_villager, future_food])
                last_villager_check_time = current_time
            else:
                concurrent.futures.wait([future_population, future_food])

            if DEBUG_MODE:
                ocr_total = time.time() - ocr_start
                logger.force_print(f"[OCR耗时] {ocr_total:.3f}秒")
            t_ocr = time.time()
            perf_stats.record("[4] OCR识别", t_ocr - ocr_start)

            # [5] 条件检查
            t_cond_start = time.time()

            # 5.1 检查人口识别结果
            if population_reader.current is None:
                if DEBUG_MODE:
                    logger.log(f"[识别失败] 人口 current={population_reader.current} limit={population_reader.limit}")
                else:
                    logger.log("人口识别失败，跳过")
                continue

            # 5.2 检查村民总数是否超过上限（需启用，且统计值仅供参考）
            if ENABLE_MAX_VILLAGERS and should_check_villagers and villager_counter.total >= MAX_VILLAGERS:
                if DEBUG_MODE:
                    logger.log(f"[上限] 村民={villager_counter.total}/{MAX_VILLAGERS}")
                else:
                    logger.log(f"村民已达上限（{villager_counter.total}/{MAX_VILLAGERS}，仅供参考），跳过")
                continue

            # 5.3 检查食物是否充足
            if food_reader.amount is None:
                if DEBUG_MODE:
                    logger.log(f"[识别失败] 食物 amount={food_reader.amount}")
                else:
                    logger.log("食物识别失败，跳过")
                continue

            if food_reader.amount < MIN_FOOD:
                if DEBUG_MODE:
                    logger.log(f"[不足] 食物={food_reader.amount}/{MIN_FOOD}")
                else:
                    logger.log(f"食物不足（{food_reader.amount}/{MIN_FOOD}），跳过")
                continue

            # 5.4 计算可用人口空位
            available_slots = population_reader.limit - population_reader.current

            # 如果没有空位，跳过（不选中TC）
            if available_slots <= 0:
                if population_reader.limit == 0:
                    logger.log(f"人口已满（{population_reader.current}/{population_reader.limit}），可能尚未建造TC（游牧开局），跳过")
                elif DEBUG_MODE:
                    logger.log(f"[无空位] 人口={population_reader.current}/{population_reader.limit}")
                else:
                    logger.log(f"人口已满（{population_reader.current}/{population_reader.limit}），跳过")
                continue

            # 5.5 获取锁，防止并发执行
            if not acquire_lock():
                if DEBUG_MODE:
                    logger.log("[锁] 获取失败")
                else:
                    logger.log("操作进行中，跳过")
                continue

            t_cond = time.time()
            perf_stats.record("[5] 条件检查", t_cond - t_cond_start)

            # [6] 执行生产村民操作
            t_op_start = time.time()
            try:
                # 6.1 操作前清理：强制释放可能粘滞的修饰键
                # BlockInput 不屏蔽 GetAsyncKeyState，操作期间用户按下的修饰键
                # 会导致 pydirectinput 的 keyDown/keyUp 与物理按键冲突，造成修饰键粘滞
                release_stuck_modifiers()

                # 6.2 计算预估操作时长：选中TC + 排队 + 操作后等待
                # 时长与TC数量相关：多TC时排队按键更多，需要更长的屏蔽时间
                estimated_duration = TC_SELECT_DELAY + (VILLAGERS_PER_TC * QUEUE_DELAY) + BLOCK_INPUT_DURATION + 1.0  # +1.0s为按键操作缓冲
                max_block_duration = min(estimated_duration * 2, 3.0 + VILLAGERS_PER_TC * 0.5)  # 基础3秒 + 每TC排队0.5秒缓冲

                # 6.3 屏蔽输入并执行所有操作
                blocker = input_blocked(max_duration=max_block_duration) if ENABLE_INPUT_BLOCK else nullcontext()

                with blocker:
                    # 6.3.1 保存当前选中的单位（使用Ctrl+0编组）
                    if DEBUG_MODE:
                        logger.force_print("[操作] 保存当前选中")
                    pydirectinput.keyDown('ctrl')
                    pydirectinput.press('0')
                    pydirectinput.keyUp('ctrl')
                    # 操作后立即清理修饰键，防止 Ctrl 粘滞影响后续操作
                    release_stuck_modifiers()
                    t_save = time.time()
                    perf_stats.record("[6.1] 保存当前选中", t_save - t_op_start)

                    # 6.3.2 选中TC并检测数量
                    if DEBUG_MODE:
                        logger.force_print("[操作] 选中TC")
                    tc_selector.do()
                    tc_counter.do()
                    t_tc = time.time()
                    perf_stats.record("[6.2] 选中TC并检测数量", t_tc - t_save)

                # with块结束，输入屏蔽自动解除

                # 检查TC检测是否失败
                if tc_counter.detection_failed:
                    # 如果有缓存的TC数量，使用缓存值继续生产
                    if cached_tc_count > 0:
                        logger.force_print(f"[缓存] TC检测失败，使用缓存值 TC数={cached_tc_count}")
                        tc_counter.count = cached_tc_count
                        tc_counter.detection_failed = False
                    else:
                        # 没有缓存值，进入冷却状态
                        logger.force_print(f"[错误] TC检测失败（可能还没有建造TC），进入冷却状态 {TC_DETECTION_FAILED_COOLDOWN}秒")
                        logger.force_print(f"[提示] 冷却期间会监控村民生产图标，如果检测到说明TC已建造，将自动恢复")

                        # 冷却等待，期间监控村民生产图标
                        cooldown_start = time.time()

                        while time.time() - cooldown_start < TC_DETECTION_FAILED_COOLDOWN:
                            # 检查修饰键（冷却期间用户可能想操作游戏）
                            if is_modifier_key_pressed():
                                logger.force_print("[暂停] 检测到修饰键，暂停检测")
                                # 等待修饰键释放
                                while is_modifier_key_pressed():
                                    time.sleep(CHECK_INTERVAL)
                                logger.force_print("[恢复] 修饰键已释放，继续检测")
                                cooldown_start = time.time()  # 重置冷却计时

                            # 复用预创建的检测器，避免重复初始化
                            has_villager_icon = cooldown_detector.has_villager_icon()

                            if has_villager_icon:
                                elapsed = time.time() - cooldown_start
                                logger.force_print(f"[恢复] 检测到村民生产图标，TC已建造，提前结束冷却（已等待{elapsed:.1f}秒）")
                                break

                            time.sleep(COOLDOWN_CHECK_INTERVAL)
                        else:
                            # 冷却时间到，未检测到村民图标
                            logger.force_print(f"[冷却] 冷却时间结束，继续尝试检测TC")

                        continue
                else:
                    # TC检测成功，更新缓存
                    cached_tc_count = tc_counter.count
                    if DEBUG_MODE:
                        logger.force_print(f"[缓存] 更新TC数量缓存={cached_tc_count}")

                # TC检测成功，继续执行生产逻辑（需要重新屏蔽输入）
                t_produce_start = time.time()
                with input_blocked(max_duration=max_block_duration) if ENABLE_INPUT_BLOCK else nullcontext():
                    planned_villagers = VILLAGERS_PER_TC * tc_counter.count

                    # 6.2.3 根据可用空位和食物调整生产数量
                    actual_villagers = min(planned_villagers, available_slots)
                    max_villagers_by_food = food_reader.amount // FOOD_PER_VILLAGER
                    actual_villagers = min(actual_villagers, max_villagers_by_food)

                    # 如果计算后没有可生产的村民，跳过
                    if actual_villagers <= 0:
                        if DEBUG_MODE:
                            logger.force_print(f"[不足] 食物={food_reader.amount} 需要={FOOD_PER_VILLAGER}")
                        else:
                            logger.force_print(f"食物不足以生产村民（{food_reader.amount}/{FOOD_PER_VILLAGER}）")
                        continue

                    # 6.2.4 显示操作信息
                    if actual_villagers < planned_villagers:
                        reason = []
                        if actual_villagers == available_slots:
                            reason.append("房屋不足")
                        if actual_villagers == max_villagers_by_food:
                            reason.append("食物不足")
                        reason_str = "、".join(reason)
                        if DEBUG_MODE:
                            logger.force_print(f"[生产] 人口={population_reader.current}/{population_reader.limit} 村民={villager_counter.total}/{MAX_VILLAGERS} 食物={food_reader.amount} TC={tc_counter.count} {reason_str} 生产={actual_villagers}/{planned_villagers}")
                        else:
                            logger.force_print(f"生产 {actual_villagers}/{planned_villagers} 个村民 (人口 {population_reader.current}/{population_reader.limit}, 村民 {villager_counter.total}/{MAX_VILLAGERS}, 食物 {food_reader.amount}, TC {tc_counter.count}, {reason_str})")
                    else:
                        if DEBUG_MODE:
                            logger.force_print(f"[生产] 人口={population_reader.current}/{population_reader.limit} 村民={villager_counter.total}/{MAX_VILLAGERS} 食物={food_reader.amount} TC={tc_counter.count} 生产={actual_villagers}")
                        else:
                            logger.force_print(f"生产 {actual_villagers} 个村民 (人口 {population_reader.current}/{population_reader.limit}, 村民 {villager_counter.total}/{MAX_VILLAGERS}, 食物 {food_reader.amount}, TC {tc_counter.count})")

                    # 6.2.5 执行排队操作
                    if DEBUG_MODE:
                        logger.force_print("[操作] 排队村民")
                    villager_trainer.do(count=actual_villagers)
                    t_queue = time.time()
                    perf_stats.record("[6.3] 计算并排队村民", t_queue - t_produce_start)

                    # 6.2.6 操作后等待，让操作完全完成
                    if BLOCK_INPUT_DURATION > 0:
                        if DEBUG_MODE:
                            logger.force_print(f"[等待] {BLOCK_INPUT_DURATION}秒")
                        time.sleep(BLOCK_INPUT_DURATION)

                    # 6.2.7 恢复之前选中的单位（按0）
                    if DEBUG_MODE:
                        logger.force_print("[操作] 恢复选中")
                    pydirectinput.press('0')

                    # 6.2.8 取消编组（Ctrl+Alt+0）
                    if DEBUG_MODE:
                        logger.force_print("[操作] 取消编组")
                    pydirectinput.keyDown('ctrl')
                    pydirectinput.keyDown('alt')
                    pydirectinput.press('0')
                    pydirectinput.keyUp('alt')
                    pydirectinput.keyUp('ctrl')

                    # 6.2.9 操作后清理：强制释放可能粘滞的修饰键
                    # 防止 pydirectinput 的 keyDown/keyUp 与物理按键冲突导致修饰键粘滞
                    # 例如：BlockInput 期间用户物理按下 Ctrl，keyUp('ctrl') 可能无法正确释放
                    release_stuck_modifiers()

                    t_restore = time.time()
                    perf_stats.record("[6.4] 等待+恢复选中+取消编组", t_restore - t_queue)

                    # 记录触发时间
                    last_trigger_time = time.time()
                    total_time = last_trigger_time - loop_start
                    perf_stats.record("[6] 执行操作（总耗时）", total_time)

                # 操作完成后，等待游戏UI更新（避免连续触发）
                # 必须在释放锁之前等待，确保下次循环时村民图标已出现
                if POST_OPERATION_DELAY > 0:
                    time.sleep(POST_OPERATION_DELAY)

            finally:
                release_lock()

            # [0] 循环间隔，降低CPU占用
            # 放在循环末尾：如果本轮已有OCR/操作耗时，则减少等待时间
            # 仅在循环总耗时不足 CHECK_INTERVAL 时补充等待
            if CHECK_INTERVAL > 0:
                elapsed_loop = time.time() - loop_start
                remaining = CHECK_INTERVAL - elapsed_loop
                if remaining > 0:
                    time.sleep(remaining)

    except KeyboardInterrupt:
        logger.force_print("\n程序已退出")
        sys.exit(0)


if __name__ == "__main__":
    main()
