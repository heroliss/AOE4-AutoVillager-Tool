"""
统一日志工具模块
提供模块化、分类的日志输出，便于阅读和调试
"""
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
