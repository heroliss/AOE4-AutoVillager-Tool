"""
输入配置模块
统一管理 pydirectinput 的配置，避免重复代码
"""
import pydirectinput

# 关闭安全检查（防止鼠标移到角落时退出）
pydirectinput.FAILSAFE = False

# 设置 pydirectinput 的内置延迟为 0（默认是 0.1 秒）
pydirectinput.PAUSE = 0.0
