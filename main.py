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

[0] 循环开始 → 等待 CHECK_INTERVAL（降低CPU占用）

[1] 修饰键检测（Shift/Ctrl/Alt）
    └── 按下 → 输出提示并继续（下次循环会跳过）

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
    ├── 保存当前选中（Ctrl+0）
    ├── 选中TC并检测数量
    ├── 计算生产数量
    ├── 执行排队操作（Q键）
    ├── 等待 BLOCK_INPUT_DURATION
    ├── 恢复选中（0）
    ├── 取消编组（Ctrl+Alt+0）
    └── 等待 POST_OPERATION_DELAY

[7] 释放锁 → 继续下次循环

=================== 延迟说明 ===================

手动延迟（可配置）：
  - CHECK_INTERVAL         = 0.05s # 循环检测间隔
  - BLOCK_INPUT_DURATION   = 0s    # 操作后等待
  - POST_OPERATION_DELAY   = 3.0s  # 操作后UI更新等待
  - QUEUE_DELAY            = 0s    # 排队按键间隔

运行延迟（检测耗时）：
  - 窗口标题检测   < 1ms
  - 像素颜色检测   < 1ms
  - 村民生产检测   ~50-100ms（模板匹配）
  - OCR识别（3项） ~0.3-0.5s（并行执行）

检测循环总耗时（无操作时）：
  - 最小：~50ms（CHECK_INTERVAL）
  - 正常：~150-200ms（检测约100ms + 间隔50ms）
  - OCR触发时：~300-500ms + CHECK_INTERVAL

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
from config import *

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
        user32.GetAsyncKeyState(VK_SHIFT) & 0x8000 != 0 or
        user32.GetAsyncKeyState(VK_CONTROL) & 0x8000 != 0 or
        user32.GetAsyncKeyState(VK_MENU) & 0x8000 != 0
    )

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
    print("  [>] 检测GPU加速...", flush=True)
    gpu_status = "未启用（使用CPU）"
    try:
        import torch
        if torch.cuda.is_available():
            gpu_status = f"已启用 ({torch.cuda.get_device_name(0)})"
    except:
        pass
    print(f"       GPU: {gpu_status}", flush=True)

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
    logger             = LogMerger()

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
    print(f"  村民上限: {MAX_VILLAGERS}  |  最低食物: {MIN_FOOD}  |  每TC排队: {VILLAGERS_PER_TC}", flush=True)
    print("  程序已就绪，等待进入游戏...", flush=True)
    print("=" * 50, flush=True)
    print()
    print("  按 Ctrl+C 退出\n", flush=True)

    # 调试：记录上次触发生产的时间
    last_trigger_time = None
    last_villager_check_time = 0  # 上次检查村民数量的时间
    cached_tc_count = 0  # 缓存的TC数量，开局为0

    try:
        while True:
            loop_start = time.time()

            # 循环间隔，降低CPU占用
            if CHECK_INTERVAL > 0:
                time.sleep(CHECK_INTERVAL)

            # 0. 检查用户是否按下了修饰键（Shift/Ctrl/Alt）
            if is_modifier_key_pressed():
                logger.log("检测到修饰键，暂停")
                continue

            # 1. 游戏窗口检测
            game_detector.do()

            # 1.1 状态处理
            if not game_detector.window_active:
                # 不在游戏窗口（窗口标题不匹配）
                logger.log("不在游戏窗口")
                continue

            if not game_detector.pixel_match:
                # 在游戏窗口但不在游戏中（如主菜单、加载画面）
                logger.log("不在游戏中")
                continue

            # 2. 村民生产状态检测（模板匹配）
            training_detector.do()

            # 调试模式：打印所有检测结果
            if DEBUG_BLOCKED_DETECTION:
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

            # 3. OCR识别（并行执行）
            ocr_start = time.time()
            current_time = time.time()
            should_check_villagers = (current_time - last_villager_check_time) >= VILLAGER_CHECK_INTERVAL

            # 并行执行OCR操作以节省时间
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
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

            # 3.1 检查人口识别结果
            if population_reader.current is None:
                if DEBUG_MODE:
                    logger.log(f"[识别失败] 人口 current={population_reader.current} limit={population_reader.limit}")
                else:
                    logger.log("人口识别失败，跳过")
                continue

            # 3.2 检查村民总数是否超过上限
            if should_check_villagers and villager_counter.total >= MAX_VILLAGERS:
                if DEBUG_MODE:
                    logger.log(f"[上限] 村民={villager_counter.total}/{MAX_VILLAGERS}")
                else:
                    logger.log(f"村民已达上限（{villager_counter.total}/{MAX_VILLAGERS}），跳过")
                continue

            # 3.3 检查食物是否充足
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

            # 4. 计算可用人口空位
            available_slots = population_reader.limit - population_reader.current

            # 如果没有空位，跳过（不选中TC）
            if available_slots <= 0:
                if DEBUG_MODE:
                    logger.log(f"[无空位] 人口={population_reader.current}/{population_reader.limit}")
                else:
                    logger.log(f"人口已满（{population_reader.current}/{population_reader.limit}），跳过")
                continue

            # 5. 获取锁，防止并发执行
            if not acquire_lock():
                if DEBUG_MODE:
                    logger.log("[锁] 获取失败")
                else:
                    logger.log("操作进行中，跳过")
                continue

            # 6. 执行生产村民操作
            try:
                # 6.1 计算预估操作时长：选中TC + 排队 + 操作后等待
                estimated_duration = TC_SELECT_DELAY + (VILLAGERS_PER_TC * QUEUE_DELAY) + BLOCK_INPUT_DURATION + 1.0
                max_block_duration = min(estimated_duration * 2, 5.0)  # 最多5秒

                # 6.2 屏蔽输入并执行所有操作
                blocker = input_blocked(max_duration=max_block_duration) if ENABLE_INPUT_BLOCK else nullcontext()

                with blocker:
                    # 6.2.1 保存当前选中的单位（使用Ctrl+0编组）
                    if DEBUG_MODE:
                        logger.force_print("[操作] 保存当前选中")
                    pydirectinput.keyDown('ctrl')
                    pydirectinput.press('0')
                    pydirectinput.keyUp('ctrl')

                    # 6.2.2 选中TC并检测数量
                    if DEBUG_MODE:
                        logger.force_print("[操作] 选中TC")
                    tc_selector.do()
                    tc_counter.do()

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
                with input_blocked(max_duration=max_block_duration) if ENABLE_INPUT_BLOCK else nullcontext():
                    planned_villagers = VILLAGERS_PER_TC * tc_counter.count

                    # 6.2.5 根据可用空位和食物调整生产数量
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

                    # 6.2.6 显示操作信息
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

                    # 6.2.7 执行排队操作
                    if DEBUG_MODE:
                        logger.force_print("[操作] 排队村民")
                    VillagerTrainer().do(count=actual_villagers)

                    # 6.2.8 操作后等待，让操作完全完成
                    if BLOCK_INPUT_DURATION > 0:
                        if DEBUG_MODE:
                            logger.force_print(f"[等待] {BLOCK_INPUT_DURATION}秒")
                        time.sleep(BLOCK_INPUT_DURATION)

                    # 6.2.9 恢复之前选中的单位（按0）
                    if DEBUG_MODE:
                        logger.force_print("[操作] 恢复选中")
                    pydirectinput.press('0')

                    # 6.2.10 取消编组（Ctrl+Alt+0）
                    if DEBUG_MODE:
                        logger.force_print("[操作] 取消编组")
                    pydirectinput.keyDown('ctrl')
                    pydirectinput.keyDown('alt')
                    pydirectinput.press('0')
                    pydirectinput.keyUp('alt')
                    pydirectinput.keyUp('ctrl')

                    # 记录触发时间
                    last_trigger_time = time.time()
                    if DEBUG_MODE:
                        total_time = last_trigger_time - loop_start
                        logger.force_print(f"[总耗时] {total_time:.3f}秒")

                # 操作完成后，等待游戏UI更新（避免连续触发）
                # 必须在释放锁之前等待，确保下次循环时村民图标已出现
                if POST_OPERATION_DELAY > 0:
                    time.sleep(POST_OPERATION_DELAY)

            finally:
                release_lock()

    except KeyboardInterrupt:
        logger.force_print("\n程序已退出")
        sys.exit(0)


if __name__ == "__main__":
    main()
