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
from lock import acquire_lock, release_lock
from input_blocker import input_blocked


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

    # 检测GPU加速状态
    try:
        import torch
        gpu_available = torch.cuda.is_available()
        if gpu_available:
            print(f"GPU加速: 已启用 (设备: {torch.cuda.get_device_name(0)})")
        else:
            print("GPU加速: 未启用 (使用CPU模式)")
    except:
        print("GPU加速: 未启用 (使用CPU模式)")

    print(f"村民数量上限: {MAX_VILLAGERS}")
    print(f"最低食物要求: {MIN_FOOD}")
    print(f"每个TC排队数量: {VILLAGERS_PER_TC}")
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
    print(f"OCR模型预热完成，耗时 {warmup_time:.2f} 秒\n")

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
                logger.log("不在游戏窗口，跳过")
                continue

            # 2. 优先检查是否有村民正在生产（快速，模板匹配）
            training_detector.do()
            if training_detector.blocked:
                logger.log(f"生产队列UI被遮挡（相似度 {training_detector.blocked_confidence}），跳过")
                continue

            if training_detector.found:
                logger.log(f"村民生产中（相似度 {training_detector.confidence}），跳过")
                continue

            # 调试：记录检测到"没有村民生产"的时间
            detection_time = time.time()
            if last_trigger_time:
                elapsed = detection_time - last_trigger_time
                print(f"[调试] 距离上次触发生产已过 {elapsed:.2f} 秒")

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
                    print(f"[调试] 人口OCR: {(time.time() - pop_start):.3f}s, 食物OCR: {(time.time() - food_start):.3f}s, 村民OCR: {(time.time() - villager_start):.3f}s")
                else:
                    concurrent.futures.wait([future_population, future_food])
                    print(f"[调试] 人口OCR: {(time.time() - pop_start):.3f}s, 食物OCR: {(time.time() - food_start):.3f}s")

            ocr_end = time.time()
            print(f"[调试] OCR操作耗时: {(ocr_end - ocr_start):.3f} 秒")

            # 3.1 检查人口识别结果
            if population_reader.current is None:
                if DEBUG_MODE:
                    print(f"[调试] 人口识别失败: current={population_reader.current}, limit={population_reader.limit}")
                logger.log("人口识别失败，跳过")
                continue

            # 3.2 检查村民总数是否超过上限
            if villager_counter.total >= MAX_VILLAGERS:
                logger.log(f"村民数量已达上限 ({villager_counter.total}/{MAX_VILLAGERS})，跳过")
                continue

            # 3.3 检查食物是否充足
            if food_reader.amount is None:
                if DEBUG_MODE:
                    print(f"[调试] 食物识别失败: amount={food_reader.amount}")
                logger.log("食物识别失败，跳过")
                continue

            if food_reader.amount < MIN_FOOD:
                logger.log(f"食物不足 ({food_reader.amount}/{MIN_FOOD})，跳过")
                continue

            # 4. 计算可用人口空位
            available_slots = population_reader.limit - population_reader.current

            # 如果没有空位，跳过（不选中TC）
            if available_slots <= 0:
                logger.log(f"人口 {population_reader.current}/{population_reader.limit}，无可用空位，跳过")
                continue

            # 5. 获取锁，防止并发执行
            if not acquire_lock():
                logger.log("获取锁失败，跳过")
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
                    print("[执行操作] 保存当前选中...")
                    pydirectinput.keyDown('ctrl')
                    pydirectinput.press('0')
                    pydirectinput.keyUp('ctrl')

                    # 6.2.2 播放蜂鸣声提醒（此时输入已屏蔽）
                    for _ in range(BEEP_COUNT):
                        winsound.Beep(BEEP_FREQUENCY, BEEP_DURATION)
                        time.sleep(0.1)

                    # 6.2.3 操作前延迟，给用户反应时间
                    if OPERATION_DELAY > 0:
                        print(f"[准备操作] {OPERATION_DELAY} 秒后执行按键操作...")
                        time.sleep(OPERATION_DELAY)
                    # 6.2.3 选中TC并检测数量
                    print("[执行操作] 选中TC...")
                    tc_selector.do()
                    tc_counter.do()
                    planned_villagers = VILLAGERS_PER_TC * tc_counter.count

                    # 8.2.4 根据可用空位和食物调整生产数量
                    actual_villagers = min(planned_villagers, available_slots)
                    max_villagers_by_food = food_reader.amount // FOOD_PER_VILLAGER
                    actual_villagers = min(actual_villagers, max_villagers_by_food)

                    # 如果计算后没有可生产的村民，跳过
                    if actual_villagers <= 0:
                        logger.force_print(f"食物不足以生产村民（食物 {food_reader.amount}，需要 {FOOD_PER_VILLAGER}）")
                        continue

                    # 6.2.5 显示操作信息
                    if actual_villagers < planned_villagers:
                        reason = []
                        if actual_villagers == available_slots:
                            reason.append("房屋不足")
                        if actual_villagers == max_villagers_by_food:
                            reason.append("食物不足")
                        reason_str = "、".join(reason)
                        logger.force_print(f"人口 {population_reader.current}/{population_reader.limit}，村民 {villager_counter.total}/{MAX_VILLAGERS}，食物 {food_reader.amount}，检测到 {tc_counter.count} 个TC，{reason_str}，生产 {actual_villagers}/{planned_villagers} 个村民")
                    else:
                        logger.force_print(f"人口 {population_reader.current}/{population_reader.limit}，村民 {villager_counter.total}/{MAX_VILLAGERS}，食物 {food_reader.amount}，检测到 {tc_counter.count} 个TC，触发生产 {actual_villagers} 个村民")

                    # 6.2.6 执行排队操作
                    print("[执行操作] 排队村民...")
                    VillagerTrainer().do(count=actual_villagers)

                    # 6.2.7 操作后等待，让操作完全完成
                    if BLOCK_INPUT_DURATION > 0:
                        print(f"[操作完成] 等待 {BLOCK_INPUT_DURATION} 秒...")
                        time.sleep(BLOCK_INPUT_DURATION)

                    # 6.2.8 恢复之前选中的单位（按0）
                    print("[执行操作] 恢复之前选中...")
                    pydirectinput.press('0')

                    # 6.2.9 取消编组（Ctrl+Alt+0）
                    print("[执行操作] 取消临时编组...")
                    pydirectinput.keyDown('ctrl')
                    pydirectinput.keyDown('alt')
                    pydirectinput.press('0')
                    pydirectinput.keyUp('alt')
                    pydirectinput.keyUp('ctrl')

                    # 记录触发时间
                    last_trigger_time = time.time()
                    print(f"[调试] 本次操作总耗时: {(last_trigger_time - loop_start):.3f} 秒")

            finally:
                release_lock()

            # 操作完成后，等待游戏UI更新（避免连续触发）
            if POST_OPERATION_DELAY > 0:
                time.sleep(POST_OPERATION_DELAY)

    except KeyboardInterrupt:
        logger.force_print("\n程序已退出")
        sys.exit(0)


if __name__ == "__main__":
    main()
