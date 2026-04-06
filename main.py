"""
AOE4 自动生产村民工具
满足以下全部条件时自动生产村民：
  1. 当前处于游戏窗口
  2. 生产队列中没有村民正在生产
  3. UI未被遮挡
  4. 人口有空间
  5. 村民总数未达上限
  6. 食物充足

性能优化：
  - 优先执行快速检测（模板匹配），只有在需要时才执行慢速OCR
  - 并行执行多个OCR任务，减少总耗时
  - 村民数量每10秒检查一次（变化慢，无需频繁检查）
  - 默认使用CPU模式OCR（小图片时CPU比GPU更快）
  - 使用mss库替代PIL.ImageGrab（2-3x提升）
  - 合并截图区域，减少截图次数

配置说明：
- 本工具基于 2560x1440 分辨率 + HDR开启
- 模板图片基于中国阵营，但所有阵营通用
- 如需其他分辨率，请调整 config.py 中的坐标

按 Ctrl+C 退出。
"""

import winsound
import time
import sys
import pydirectinput
from config import *
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
from logger import log_main


from contextlib import nullcontext


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
    print("=" * 60)
    print("AOE4 自动生产村民工具")
    print("=" * 60)

    # 清理残留的锁文件（防止上次异常退出导致的锁残留）
    cleanup_lock()

    # 检测GPU加速状态
    try:
        import torch
        gpu_available = torch.cuda.is_available()
        if gpu_available:
            print(f"GPU加速: 已启用 ({torch.cuda.get_device_name(0)})")
        else:
            print("GPU加速: 未启用（使用CPU）")
    except:
        print("GPU加速: 未启用（使用CPU）")

    print(f"村民上限: {MAX_VILLAGERS}")
    print(f"最低食物: {MIN_FOOD}")
    print(f"每TC排队: {VILLAGERS_PER_TC}")
    print("=" * 60)
    print()

    # 初始化所有检测器
    game_detector      = GameDetector()
    training_detector  = VillagerTrainingDetector()
    population_reader  = PopulationReader()
    tc_selector        = TCSelector()
    tc_counter         = TCCounter()
    villager_counter   = VillagerCounter()
    food_reader        = FoodReader()
    logger             = LogMerger()

    # 预热OCR模型，避免第一次使用时延迟
    print("正在预热OCR模型...")
    warmup_start = time.time()
    population_reader.do()  # 触发OCR初始化
    warmup_time = time.time() - warmup_start
    print(f"OCR预热完成，耗时 {warmup_time:.2f}秒\n")

    print("程序已启动，按 Ctrl+C 退出\n")

    # 调试：记录上次触发生产的时间
    last_trigger_time = None
    last_villager_check_time = 0  # 上次检查村民数量的时间

    try:
        while True:
            loop_start = time.time()

            # 1. 检查是否在游戏窗口
            game_detector.do()
            if not game_detector.in_game:
                logger.log("不在游戏窗口")
                continue

            # 2. 优先检查是否有村民正在生产（快速，模板匹配）
            training_detector.do()

            if training_detector.blocked:
                if DEBUG_MODE:
                    logger.log(f"[遮挡] 置信度={training_detector.blocked_confidence:.3f} 阈值={BLOCKED_MATCH_THRESHOLD:.3f}")
                else:
                    logger.log("UI被遮挡，跳过")

                # 动态调整检测频率：UI被遮挡时降低检测频率
                time.sleep(CHECK_INTERVAL * 2)
                continue

            # 检测UI是否正在渐变（渐入渐出动画中）
            if training_detector.in_transition:
                if DEBUG_MODE:
                    logger.log(f"[渐变] 置信度={training_detector.blocked_confidence:.3f} 区间=[{BLOCKED_TRANSITION_THRESHOLD:.2f}, {BLOCKED_MATCH_THRESHOLD:.2f}]")
                else:
                    logger.log("UI渐变中，跳过")

                # 不额外延迟，直接进入下一次循环快速检测
                # 稳定性检测机制会自动过滤快速变化的状态
                continue

            if training_detector.found:
                if DEBUG_MODE:
                    logger.log(f"[生产中] 置信度={training_detector.confidence:.3f} 阈值={VILLAGER_MATCH_THRESHOLD:.3f}")
                else:
                    logger.log("村民生产中")

                # 动态调整检测频率：生产中时降低检测频率
                time.sleep(CHECK_INTERVAL * 3)
                continue

            # 调试：记录检测到"没有村民生产"的时间
            detection_time = time.time()
            if last_trigger_time and DEBUG_MODE:
                elapsed = detection_time - last_trigger_time
                logger.force_print(f"[时间] 距上次触发 {elapsed:.2f}秒")

            # 3. 只有在没有村民生产时，才执行慢速OCR操作
            ocr_start = time.time()

            # 优化：村民数量变化慢，不需要每次都检查
            current_time = time.time()
            should_check_villagers = (current_time - last_villager_check_time) >= VILLAGER_CHECK_INTERVAL

            # 并行执行OCR操作以节省时间
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                pop_start = time.time()
                future_population = executor.submit(population_reader.do)

                food_start = time.time()
                future_food = executor.submit(food_reader.do)

                # 只在需要时检查村民数量
                if should_check_villagers:
                    villager_start = time.time()
                    future_villager = executor.submit(villager_counter.do)
                    concurrent.futures.wait([future_population, future_villager, future_food])
                    last_villager_check_time = current_time
                    if DEBUG_MODE:
                        pop_time = time.time() - pop_start
                        food_time = time.time() - food_start
                        villager_time = time.time() - villager_start
                        logger.force_print(f"[OCR耗时] 人口={pop_time:.3f}s 食物={food_time:.3f}s 村民={villager_time:.3f}s")
                else:
                    concurrent.futures.wait([future_population, future_food])
                    if DEBUG_MODE:
                        pop_time = time.time() - pop_start
                        food_time = time.time() - food_start
                        logger.force_print(f"[OCR耗时] 人口={pop_time:.3f}s 食物={food_time:.3f}s")

            if DEBUG_MODE:
                ocr_total = time.time() - ocr_start
                logger.force_print(f"[OCR总计] {ocr_total:.3f}秒")

            # 3.1 检查人口识别结果
            if population_reader.current is None:
                if DEBUG_MODE:
                    logger.log(f"[识别失败] 人口 current={population_reader.current} limit={population_reader.limit}")
                else:
                    logger.log("人口识别失败")
                continue

            # 3.2 检查村民总数是否超过上限
            if should_check_villagers and villager_counter.total >= MAX_VILLAGERS:
                if DEBUG_MODE:
                    logger.log(f"[上限] 村民={villager_counter.total}/{MAX_VILLAGERS}")
                else:
                    logger.log(f"村民已达上限 {villager_counter.total}/{MAX_VILLAGERS}")
                continue

            # 3.3 检查食物是否充足
            if food_reader.amount is None:
                if DEBUG_MODE:
                    logger.log(f"[识别失败] 食物 amount={food_reader.amount}")
                else:
                    logger.log("食物识别失败")
                continue

            if food_reader.amount < MIN_FOOD:
                if DEBUG_MODE:
                    logger.log(f"[不足] 食物={food_reader.amount}/{MIN_FOOD}")
                else:
                    logger.log(f"食物不足 {food_reader.amount}/{MIN_FOOD}")
                continue

            # 4. 计算可用人口空位
            available_slots = population_reader.limit - population_reader.current

            # 如果没有空位，跳过（不选中TC）
            if available_slots <= 0:
                if DEBUG_MODE:
                    logger.log(f"[无空位] 人口={population_reader.current}/{population_reader.limit}")
                else:
                    logger.log(f"人口已满 {population_reader.current}/{population_reader.limit}")
                continue

            # 5. 获取锁，防止并发执行
            if not acquire_lock():
                if DEBUG_MODE:
                    logger.log("[锁] 获取失败")
                else:
                    logger.log("操作进行中")
                continue

            # 6. 执行生产村民操作
            try:
                # 6.1 计算预估操作时长：蜂鸣 + 延迟 + 选中TC + 排队 + 操作后等待
                beep_duration = (BEEP_DURATION / 1000.0 + 0.1) * BEEP_COUNT
                estimated_duration = beep_duration + OPERATION_DELAY + TC_SELECT_DELAY + (VILLAGERS_PER_TC * QUEUE_DELAY) + BLOCK_INPUT_DURATION + 1.0
                max_block_duration = min(estimated_duration * 2, 5.0)  # 最多5秒

                # 8.2 屏蔽输入并执行所有操作（从蜂鸣开始）
                blocker = input_blocked(max_duration=max_block_duration) if ENABLE_INPUT_BLOCK else nullcontext()

                with blocker:
                    # 6.2.1 保存当前选中的单位（使用Ctrl+0编组）
                    if DEBUG_MODE:
                        logger.force_print("[操作] 保存当前选中")
                    pydirectinput.keyDown('ctrl')
                    pydirectinput.press('0')
                    pydirectinput.keyUp('ctrl')

                    # 6.2.2 播放蜂鸣声提醒（此时输入已屏蔽）
                    for _ in range(BEEP_COUNT):
                        winsound.Beep(BEEP_FREQUENCY, BEEP_DURATION)
                        time.sleep(0.1)

                    # 6.2.3 操作前延迟，给用户反应时间
                    if OPERATION_DELAY > 0:
                        if DEBUG_MODE:
                            logger.force_print(f"[延迟] 等待{OPERATION_DELAY}秒")
                        time.sleep(OPERATION_DELAY)
                    # 6.2.3 选中TC并检测数量
                    if DEBUG_MODE:
                        logger.force_print("[操作] 选中TC")
                    tc_selector.do()
                    tc_counter.do()

                # with块结束，输入屏蔽自动解除

                # 检查TC检测是否失败
                if tc_counter.detection_failed:
                    logger.force_print(f"[错误] TC检测失败（可能还没有建造TC），进入冷却状态 {TC_DETECTION_FAILED_COOLDOWN}秒")
                    logger.force_print(f"[提示] 冷却期间会监控村民生产图标，如果检测到说明TC已建造，将自动恢复")

                    # 冷却等待，期间监控村民生产图标
                    cooldown_start = time.time()
                    cooldown_check_interval = 1.0  # 每秒检查一次

                    while time.time() - cooldown_start < TC_DETECTION_FAILED_COOLDOWN:
                        # 检查是否有村民生产图标
                        cooldown_detector = VillagerTrainingDetector()
                        has_villager_icon = cooldown_detector.has_villager_icon()

                        if has_villager_icon:
                            elapsed = time.time() - cooldown_start
                            logger.force_print(f"[恢复] 检测到村民生产图标，TC已建造，提前结束冷却（已等待{elapsed:.1f}秒）")
                            break

                        time.sleep(cooldown_check_interval)
                    else:
                        # 冷却时间到，未检测到村民图标
                        logger.force_print(f"[冷却] 冷却时间结束，继续尝试检测TC")

                    continue

                # TC检测成功，继续执行生产逻辑（需要重新屏蔽输入）
                with input_blocked(max_duration=max_block_duration) if ENABLE_INPUT_BLOCK else nullcontext():
                    planned_villagers = VILLAGERS_PER_TC * tc_counter.count

                    # 8.2.4 根据可用空位和食物调整生产数量
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

                    # 6.2.5 显示操作信息
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

                    # 6.2.6 执行排队操作
                    if DEBUG_MODE:
                        logger.force_print("[操作] 排队村民")
                    VillagerTrainer().do(count=actual_villagers)

                    # 6.2.7 操作后等待，让操作完全完成
                    if BLOCK_INPUT_DURATION > 0:
                        if DEBUG_MODE:
                            logger.force_print(f"[等待] {BLOCK_INPUT_DURATION}秒")
                        time.sleep(BLOCK_INPUT_DURATION)

                    # 6.2.8 恢复之前选中的单位（按0）
                    if DEBUG_MODE:
                        logger.force_print("[操作] 恢复选中")
                    pydirectinput.press('0')

                    # 6.2.9 取消编组（Ctrl+Alt+0）
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

            # 循环间隔，降低CPU占用
            if CHECK_INTERVAL > 0:
                time.sleep(CHECK_INTERVAL)

    except KeyboardInterrupt:
        logger.force_print("\n程序已退出")
        sys.exit(0)


if __name__ == "__main__":
    main()
