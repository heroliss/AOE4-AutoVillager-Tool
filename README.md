# AOE4 自动生产村民工具

## 项目简介

《帝国时代4》自动化工具，自动检测并生产村民，解放双手专注战斗。

**默认配置：**

- ⚠️ **需要管理员权限运行**（用于输入屏蔽功能）
- 基于 2560x1440 分辨率 + HDR开启
- 模板基于中国阵营，所有阵营通用
- 其他配置需调整 [config.py](config.py) 参数（详见下方配置说明）

## 功能特性

- ✅ 自动检测游戏窗口和生产队列
- ✅ OCR识别人口、食物数量
- ✅ 多TC支持（自动检测并按比例生产）
- ✅ 智能房屋管理和UI遮挡检测
- ✅ 优化按键操作（shift+q批量排队）
- ✅ 输入保护（操作时临时等待避免误操作）

## 快速开始

> **⚠️ 重要提示：本工具需要以管理员身份运行**
> 
> 程序使用输入屏蔽功能防止操作期间的误触，需要管理员权限才能调用Windows底层API。
> 
> **运行方式：**
> - Windows：右键 `main.py` → 选择"以管理员身份运行"
> - 命令行：以管理员身份打开终端，然后执行 `python main.py`
> 
> 如果不想使用管理员权限，可在 [config.py](config.py) 中设置 `ENABLE_INPUT_BLOCK = False` 关闭输入屏蔽功能（可能会有误操作风险）。

### 1. 游戏设置要求

**重要：** 在使用本工具前，必须在游戏中进行以下设置：

**a) 全局建造队列设置**

进入游戏 → 设置 → 用户界面 → **全局建造队列** → 设置为 **"全部显示"**

该设置会在屏幕左侧显示生产队列图标，工具需要通过这些图标来实时检测是否有村民正在生产。

**b) 快捷键设置（多TC支持）**

进入游戏 → 设置 → 控制 → 查看并重新映射控制方式  → 左侧建筑选择 → 选择所有城镇中心 → 设置为 **H键**

该设置允许工具通过H键同时选中所有城镇中心，实现多TC同时生产村民。如果不设置此快捷键，工具只能在单TC时正常工作。

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

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

所有参数在 [config.py](config.py) 中管理。

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

### 不同分辨率适配

默认配置基于 **2560x1440** 分辨率。如果你使用其他分辨率，需要按比例调整以下所有坐标参数：

```python
# 游戏窗口检测点（右下角某个固定颜色的像素）
GAME_DETECT_PIXEL = (2526, 1405)

# 村民生产队列区域（左下角队列图标区域）
VILLAGER_QUEUE_REGION = (10, 970, 500, 1025)

# UI遮挡检测区域（队列区域内的特征点）
BLOCKED_DETECT_REGION = (260, 1000, 265, 1005)

# 人口显示区域（如 "50/200"）
POPULATION_REGION = (45, 1126, 151, 1183)

# 食物数量显示区域
FOOD_REGION = (50, 1222, 140, 1248)

# TC图标检测区域（左下角建筑图标区域）
TC_ICON_REGION = (390, 1210, 700, 1260)

# 村民总数统计区域（左下角数字区域）
VILLAGER_COUNT_REGION = (185, 1130, 240, 1420)
```

**调整方法：**

1. 启用 `DEBUG_MODE = True`
2. 运行程序，查看 [debug_output/](debug_output/) 目录下的截图
3. 根据截图调整坐标，确保区域覆盖正确的UI元素
4. 重复测试直到识别准确

### HDR 设置适配

HDR 开关只影响游戏窗口检测的颜色值，不影响其他功能。

```python
# HDR 开启时（默认）
GAME_DETECT_COLOR = (65, 78, 105)

# HDR 关闭时，需要修改为
GAME_DETECT_COLOR = (26, 32, 46)
```

**如何获取正确的颜色值：**

1. 进入游戏，截取全屏
2. 使用截图工具查看 `GAME_DETECT_PIXEL` 坐标处的 RGB 颜色值
3. 将颜色值填入 `GAME_DETECT_COLOR`

## 工作流程

### 主循环流程

```
游戏窗口检测 → 生产队列检测 → UI遮挡检测 → 人口识别 → 
村民总数检查 → 食物检查 → 人口空位计算 → TC检测 → 
生产数量计算 → 执行生产操作 → 继续循环
```

### 生产操作详细流程

当检测到需要生产村民时，程序会执行以下原子操作（整个过程屏蔽物理输入，防止误操作）：

1. **🔒 开始屏蔽输入** - 阻止物理鼠标键盘输入，确保操作不被打断
2. **📦 Ctrl+0** - 将当前选中的单位保存到0号编组（临时）
3. **🔔 播放蜂鸣** - 可选的音效提醒（默认关闭）
4. **🏰 按H键** - 选中所有城镇中心（需要在游戏中设置H键快捷键）
5. **👷 Shift+Q** - 批量排队村民（根据TC数量和资源计算数量）
6. **⎋ ESC** - 取消TC选中状态
7. **🔄 按0键** - 恢复之前选中的单位
8. **🗑️ Ctrl+Alt+0** - 取消0号临时编组
9. **🔓 结束屏蔽** - 恢复物理输入控制

整个操作耗时约0.1-0.3秒，完全不影响正常游戏操作。

## 核心技术

### 1. 双重游戏窗口检测

程序使用两层检测机制确保只在游戏中运行：

- **窗口标题检测**：检查当前活跃窗口是否为"Age of Empires IV"
- **像素颜色检测**：检测游戏UI特定位置的固定颜色值

两个条件同时满足才认为在游戏中，避免误触发。

### 2. 输入屏蔽机制

使用Windows底层API（`BlockInput`）在操作期间临时屏蔽物理鼠标键盘输入：

- **需要管理员权限**：右键以管理员身份运行程序
- **自动超时保护**：最长5秒自动解除，防止卡死
- **可选功能**：可在 [config.py](config.py) 中设置 `ENABLE_INPUT_BLOCK = False` 关闭

### 3. 模板匹配技术

使用OpenCV的模板匹配算法检测UI元素：

- **村民图标检测**：检测生产队列中是否有村民图标（阈值0.6）
- **TC图标检测**：统计左下角TC图标数量，支持多TC（阈值0.7）
- **UI遮挡检测**：检测队列区域是否被其他UI遮挡（阈值0.7）

不同元素使用不同阈值以平衡准确性和鲁棒性。

### 4. OCR文字识别

使用EasyOCR识别游戏内数字：

- **人口识别**：识别"当前/上限"格式（如"50/200"）
- **食物识别**：识别资源数字
- **村民计数**：识别左下角村民总数
- **GPU加速**：支持CUDA加速，提升识别速度（可选）

### 5. 智能生产计算

综合多个因素计算最优生产数量：

```python
# 基础计划：每个TC排队N个村民
planned = VILLAGERS_PER_TC × TC数量

# 限制1：人口上限
available_slots = (人口上限 - 当前人口) - 已在队列的村民数

# 限制2：村民总数上限
remaining = MAX_VILLAGERS - 当前村民总数

# 限制3：食物资源
max_by_food = 当前食物 ÷ 50

# 最终生产数量
actual = min(planned, available_slots, remaining, max_by_food)
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

## GPU加速配置（可选）

程序默认使用CPU模式，完全可用。如需GPU加速OCR识别，可按以下步骤配置：

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

> **注意：** CUDA向下兼容，高版本CUDA可使用低版本PyTorch。

**GPU加速未启用排查：**

1. 确认NVIDIA显卡和驱动已安装
2. 重装PyTorch（见上方命令）
3. 重装EasyOCR：
   ```bash
   pip uninstall easyocr -y
   pip install easyocr
   ```

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
- EasyOCR - 文字识别
- Pillow - 屏幕截图
- pydirectinput - 键盘模拟

## 许可证

MIT License
