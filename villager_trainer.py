"""
村民生产操作模块
执行排队村民的按键操作，使用shift+q优化
"""
import time
import pydirectinput
from config import QUEUE_DELAY
from input_config import *  # noqa: F401, F403

DELAY = QUEUE_DELAY


class VillagerTrainer(object):
    """连续排队村民（假设TC已经被选中）"""

    def do(self, count=4):
        """
        排队生产村民
        :param count: 要排队的村民数量
        """
        self._queue_villagers(count)

    def _queue_villagers(self, count):
        """优化的排队逻辑：使用shift+q加速"""
        # 计算需要多少次shift+q（每次5个）和剩余的单次q
        shift_q_count = count // 5
        remaining_q_count = count % 5

        # 先执行shift+q（批量排队）
        for _ in range(shift_q_count):
            pydirectinput.keyDown('shift')
            pydirectinput.press('q')
            pydirectinput.keyUp('shift')
            if DELAY > 0:
                time.sleep(DELAY)

        # 再执行剩余的单次q
        for _ in range(remaining_q_count):
            pydirectinput.press("q")
            if DELAY > 0:
                time.sleep(DELAY)

        # 操作完成后按ESC取消TC选中状态（防止TC面板遮挡后续检测）
        pydirectinput.press("escape")
        if DELAY > 0:
            time.sleep(DELAY)