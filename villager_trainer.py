"""
村民生产操作模块
执行排队村民的按键操作，支持Shift批量排队
"""
import time
import pydirectinput
import config
from input_config import *  # noqa: F401, F403


class VillagerTrainer(object):
    """连续排队村民（假设TC已经被选中）"""

    def do(self, count=4):
        """
        排队生产村民
        :param count: 要排队的村民数量
        """
        self._queue_villagers(count)

    def _queue_villagers(self, count):
        """排队逻辑：根据配置决定是否使用Shift批量排队"""
        delay = config.QUEUE_DELAY
        queue_key = config.VILLAGER_QUEUE_KEY

        if config.ENABLE_SHIFT_QUEUE:
            # Shift批量排队：每次5个
            shift_q_count = count // 5
            remaining_q_count = count % 5

            for _ in range(shift_q_count):
                pydirectinput.keyDown('shift')
                pydirectinput.press(queue_key)
                pydirectinput.keyUp('shift')
                if delay > 0:
                    time.sleep(delay)

            for _ in range(remaining_q_count):
                pydirectinput.press(queue_key)
                if delay > 0:
                    time.sleep(delay)
        else:
            # 逐个排队
            for _ in range(count):
                pydirectinput.press(queue_key)
                if delay > 0:
                    time.sleep(delay)

        # 操作完成后按ESC取消TC选中状态（防止TC面板遮挡后续检测）
        pydirectinput.press("escape")
        if delay > 0:
            time.sleep(delay)