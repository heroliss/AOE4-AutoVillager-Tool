"""
统一日志工具模块
提供模块化、分类的日志输出，便于阅读和调试
"""
from collections import deque
from config import DEBUG_MODE, DEBUG_BLOCKED_DETECTION, DEBUG_TRAINING_DETECTION, DEBUG_PERFORMANCE


class Logger:
    """统一日志输出类"""

    # 日志级别颜色（可选，终端支持ANSI颜色时使用）
    COLORS = {
        'TC': '\033[94m',      # 蓝色
        'VILLAGER': '\033[92m', # 绿色
        'FOOD': '\033[93m',     # 黄色
        'BLOCKED': '\033[91m',  # 红色
        'TRAINING': '\033[95m', # 紫色
        'PERF': '\033[96m',     # 青色
        'MAIN': '\033[97m',     # 白色
        'RESET': '\033[0m'
    }

    @staticmethod
    def _format(module, level, message):
        """格式化日志消息"""
        return f"[{module:8s}] {level:8s} | {message}"

    @staticmethod
    def tc(level, message):
        """TC检测日志"""
        if DEBUG_MODE:
            print(Logger._format("TC", level, message))

    @staticmethod
    def villager(level, message):
        """村民计数日志"""
        if DEBUG_MODE:
            print(Logger._format("VILLAGER", level, message))

    @staticmethod
    def food(level, message):
        """食物识别日志"""
        if DEBUG_MODE:
            print(Logger._format("FOOD", level, message))

    @staticmethod
    def blocked(level, message):
        """遮挡检测日志（独立开关）"""
        if DEBUG_BLOCKED_DETECTION:
            print(Logger._format("BLOCKED", level, message))

    @staticmethod
    def training(level, message):
        """村民生产检测日志"""
        if DEBUG_TRAINING_DETECTION:
            print(Logger._format("TRAINING", level, message))

    @staticmethod
    def perf(module, message):
        """性能分析日志（独立开关）"""
        if DEBUG_PERFORMANCE:
            print(Logger._format(module, "PERF", message))

    @staticmethod
    def main(level, message):
        """主程序日志"""
        if DEBUG_MODE:
            print(Logger._format("MAIN", level, message))

    @staticmethod
    def info(module, message):
        """通用信息日志（总是显示）"""
        print(Logger._format(module, "INFO", message))

    @staticmethod
    def error(module, message):
        """错误日志（总是显示）"""
        print(Logger._format(module, "ERROR", message))


class PerfStats:
    """
    性能统计收集器 - 折叠重复日志，定期输出统计摘要

    使用方式：
        stats = PerfStats(report_interval=20)  # 每20次采样输出一次统计
        stats.record("[0] 循环间隔", 0.100)
        stats.record("[1] 修饰键检测", 0.001)
        ...
        stats.maybe_report()  # 在循环末尾调用，达到间隔时自动输出统计
    """

    def __init__(self, report_interval=20):
        self.report_interval = report_interval  # 每隔多少次采样输出一次统计
        self._samples = {}   # stage_name -> deque([elapsed, ...], maxlen=200)
        self._loop_count = 0

    def record(self, stage, elapsed):
        """记录一个阶段的耗时（秒）"""
        if not DEBUG_PERFORMANCE:
            return
        if stage not in self._samples:
            self._samples[stage] = deque(maxlen=200)  # 自动淘汰旧值，避免内存无限增长
        self._samples[stage].append(elapsed)

    def maybe_report(self):
        """在循环末尾调用，达到间隔时输出统计摘要"""
        if not DEBUG_PERFORMANCE:
            return
        self._loop_count += 1
        if self._loop_count >= self.report_interval:
            self.report()
            self._loop_count = 0

    def report(self):
        """输出所有阶段的统计摘要（最好/最差/平均）"""
        if not self._samples:
            return

        # 按阶段名排序，确保输出顺序稳定
        sorted_stages = sorted(self._samples.keys())
        max_samples = max((len(v) for v in self._samples.values()), default=0)

        print(f"\n{'='*62}")
        print(f"  性能统计摘要（最近 {max_samples} 次采样）")
        print(f"{'='*62}")
        print(f"  {'阶段':<30s} {'最好':>8s} {'最差':>8s} {'平均':>8s} {'最近':>8s}")
        print(f"  {'-'*30} {'-'*8} {'-'*8} {'-'*8} {'-'*8}")

        for stage in sorted_stages:
            values = self._samples[stage]
            if not values:
                continue
            best = min(values)
            worst = max(values)
            avg = sum(values) / len(values)
            latest = values[-1]

            print(f"  {stage:<30s} {self._fmt(best):>8s} {self._fmt(worst):>8s} {self._fmt(avg):>8s} {self._fmt(latest):>8s}")

        print(f"{'='*62}\n")

    @staticmethod
    def _fmt(v):
        """格式化耗时值"""
        if v < 0.001:
            return f"{v*1000:.1f}ms"
        elif v < 1.0:
            return f"{v:.3f}秒"
        else:
            return f"{v:.2f}秒"

    def reset(self):
        """重置所有统计数据"""
        self._samples.clear()
        self._loop_count = 0


# 全局单例
perf_stats = PerfStats(report_interval=20)


# 便捷函数
def log_tc(level, message):
    """TC检测日志"""
    Logger.tc(level, message)


def log_villager(level, message):
    """村民计数日志"""
    Logger.villager(level, message)


def log_food(level, message):
    """食物识别日志"""
    Logger.food(level, message)


def log_blocked(level, message):
    """遮挡检测日志"""
    Logger.blocked(level, message)


def log_training(level, message):
    """村民生产检测日志"""
    Logger.training(level, message)


def log_perf(module, message):
    """性能分析日志"""
    Logger.perf(module, message)


def log_main(level, message):
    """主程序日志"""
    Logger.main(level, message)


def log_info(module, message):
    """通用信息日志"""
    Logger.info(module, message)


def log_error(module, message):
    """错误日志"""
    Logger.error(module, message)
