# AOE4 自动生产村民工具

## 项目简介

《帝国时代4》自动化工具，自动检测并生产村民，解放双手专注战斗。

**配置说明：**

- 基于 2560x1440 分辨率 + HDR开启
- 模板基于中国阵营，所有阵营通用
- 其他分辨率需调整 [config.py](config.py) 坐标参数

## 功能特性

- ✅ 自动检测游戏窗口和生产队列
- ✅ OCR识别人口、食物数量
- ✅ 多TC支持（自动检测并按比例生产）
- ✅ 智能房屋管理和UI遮挡检测
- ✅ GPU加速OCR识别
- ✅ 优化按键操作（shift+q批量排队）
- ✅ 输入保护（操作时临时等待避免误操作）

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. GPU加速配置（可选但推荐）

**检查CUDA版本：**

```bash
nvidia-smi
```

**安装对应PyTorch版本：**

```bash
# CUDA 13.0+
pip3 install torch torchvision --index-url https://download.pytorch.org/whl/cu130

# CUDA 12.1-12.6
pip3 install torch torchvision --index-url https://download.pytorch.org/whl/cu121

# CUDA 11.8
pip3 install torch torchvision --index-url https://download.pytorch.org/whl/cu118
```

**验证安装：**

```python
import torch
print(f"CUDA可用: {torch.cuda.is_available()}")
print(f"GPU: {torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU模式'}")
```

> **注意：** CUDA向下兼容，高版本CUDA可使用低版本PyTorch。无GPU时自动使用CPU模式（速度稍慢但完全可用）。

### 3. 准备模板图片（必需）

以下模板文件必须存在于 [templates/](templates/) 目录：

- `cunmin.png` - 村民图标（必需）
- `tc_icon.png` - TC图标（必需）
- `blocked.png` - UI遮挡检测（必需）

### 4. 运行程序

```bash
python main.py
```

## 配置说明

所有参数在 [config.py](config.py) 中管理：

### 基础参数

```python
VILLAGERS_PER_TC = 4    # 每个TC排队数量
MAX_VILLAGERS = 120     # 村民总数上限
MIN_FOOD = 50           # 最低食物要求
CHECK_INTERVAL = 0.4    # 检测间隔（秒）
```

### 调试模式

```python
DEBUG_MODE = False                    # 全局调试（生成截图和详细日志）
DEBUG_BLOCKED_DETECTION = False       # 遮挡检测调试
```

### 截图区域（2560x1440分辨率）

```python
GAME_DETECT_PIXEL = (2526, 1405)              # 游戏窗口检测点
VILLAGER_QUEUE_REGION = (10, 970, 500, 1025) # 生产队列区域
POPULATION_REGION = (45, 1126, 151, 1183)     # 人口显示区域
FOOD_REGION = (50, 1222, 140, 1248)           # 食物显示区域
TC_ICON_REGION = (390, 1210, 700, 1260)       # TC图标区域
```

> 其他分辨率需按比例调整坐标。启用 `DEBUG_MODE` 查看 [debug_output/](debug_output/) 截图确认区域。

## 工作流程

```
游戏窗口检测 → 生产队列检测 → UI遮挡检测 → 人口识别 → 
村民总数检查 → 食物检查 → 人口空位计算 → TC检测 → 
生产数量计算 → 执行生产（shift+q批量排队）→ ESC取消选中
```

## 常见问题

### 程序启动失败：找不到blocked.png

**原因：** UI遮挡检测是必需功能，缺少模板文件会导致程序无法启动。

**解决：**

1. 确认 [templates/blocked.png](templates/blocked.png) 文件存在
2. 如果文件丢失，需要重新截取一个5x5像素的UI特征图片
3. 该模板用于检测生产队列UI是否被遮挡，防止误判

### 频繁误判为没有村民在生产

**原因：** UI遮挡检测不准确或阈值设置不当。

**解决：**

1. 启用 `DEBUG_BLOCKED_DETECTION = True`
2. 查看 [debug_output/blocked_detection_debug.png](debug_output/blocked_detection_debug.png)
3. 调整 `BLOCKED_MATCH_THRESHOLD` 阈值（默认0.6）
4. 确认 `BLOCKED_DETECT_REGION` 坐标正确

### 识别不准确

1. 启用 `DEBUG_MODE = True`
2. 查看 [debug_output/](debug_output/) 截图
3. 调整 [config.py](config.py) 中的坐标和阈值

### 一直提示"不在游戏窗口"

1. 游戏中截图查看坐标 (2526, 1405) 的颜色
2. 修改 `GAME_DETECT_PIXEL` 和 `GAME_DETECT_COLOR`

### TC数量识别错误

1. 确认 [templates/tc_icon.png](templates/tc_icon.png) 正确
2. 检查 `TC_ICON_REGION` 坐标
3. 调整 `TC_MATCH_THRESHOLD` 阈值

### GPU加速未启用

1. 确认NVIDIA显卡和驱动已安装
2. 重装PyTorch（见上方GPU配置）
3. 重装EasyOCR：
   ```bash
   pip uninstall easyocr -y
   pip install easyocr
   ```

## 性能对比


| 模式    | OCR速度   | 适用场景   |
| --------- | ----------- | ------------ |
| GPU加速 | ~0.1秒/次 | NVIDIA显卡 |
| CPU模式 | ~0.3秒/次 | 无独立显卡 |

## 目录结构

```
auto_train_villager_standalone/
├── config.py                        # 配置文件
├── main.py                          # 主程序
├── game_detector.py                 # 游戏窗口检测
├── villager_training_detector.py    # 村民生产检测
├── population_reader.py             # 人口OCR识别
├── villager_counter.py              # 村民计数
├── food_reader.py                   # 食物识别
├── tc_counter.py                    # TC数量检测
├── tc_selector.py                   # TC选择
├── villager_trainer.py              # 生产执行
├── lock.py                          # 文件锁
├── requirements.txt                 # 依赖包
├── templates/                       # 模板图片
└── debug_output/                    # 调试输出
```

## 使用技巧

1. **首次使用**：开启调试模式确认检测正常
2. **正式使用**：关闭调试模式减少性能开销
3. **多显示器**：游戏在主显示器
4. **窗口模式**：使用全屏窗口模式
5. **UI缩放**：游戏UI缩放100%

## 注意事项

- 仅供学习交流使用
- 建议在单人模式或允许使用工具的环境中使用
- 坐标需根据实际分辨率调整

## 技术栈

- Python 3.8+
- OpenCV - 图像处理
- EasyOCR - 文字识别（GPU加速）
- Pillow - 屏幕截图
- pydirectinput - 键盘模拟

## 许可证

MIT License
