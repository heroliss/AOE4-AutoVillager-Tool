# AOE4 自动生产村民工具

## 项目简介

《帝国时代4》自动化工具，自动检测并生产村民，解放双手专注战斗。

**默认配置：**

- ⚠️ **需要管理员权限运行**（用于输入屏蔽功能）
- 基于 2560x1440 分辨率 + HDR默认关闭
- 模板基于中国阵营，所有阵营通用
- 支持 GUI 图形界面和命令行两种运行模式

## 功能特性

- ✅ 自动检测游戏窗口和生产队列
- ✅ OCR识别人口、食物数量
- ✅ 多TC支持（自动检测并按比例生产）
- ✅ 智能房屋管理和UI遮挡检测
- ✅ 优化按键操作（shift+q批量排队）
- ✅ 输入保护（操作时临时等待避免误操作）
- ✅ GUI图形界面（实时日志、状态栏、配置管理、快捷键）
- ✅ 区域编辑器（全屏可视化调整所有截图区域）
- ✅ 吸色工具（截屏取色，自动填充SDR/HDR坐标和颜色）
- ✅ 配置热更新（修改参数实时生效，无需重启）
- ✅ PyInstaller打包支持（一键生成exe）

## 快速开始

### 游戏设置要求

**重要：** 在使用本工具前，必须在游戏中进行以下设置：

**a) 全局建造队列设置**

进入游戏 → 设置 → 用户界面 → **全局建造队列** → 设置为 **"全部显示"**

该设置会在屏幕左侧显示生产队列图标，工具需要通过这些图标来实时检测是否有村民正在生产。

**b) 快捷键设置（多TC支持）**

进入游戏 → 设置 → 控制 → 查看并重新映射控制方式 → 左侧建筑选择 → 选择所有城镇中心 → 设置为 **H键**

该设置允许工具通过H键同时选中所有城镇中心，实现多TC同时生产村民。如果不设置此快捷键，工具只能在单TC时正常工作。

### 方式一：直接运行exe（推荐）

1. 从 [Releases](../../releases) 下载最新版本的 `AOE4-AutoVillager.exe`
2. 默认管理员身份运行
3. 配置文件（`shortcuts.json`、`config_override.json`）会自动保存在exe同目录

### 方式二：从源码运行

#### 1. 安装依赖

```bash
pip install -r requirements.txt
```

#### 2. 准备模板图片（必需）

以下模板文件必须存在于 [templates/](templates/) 目录：

- `cunmin.png` - 村民图标（必需）
- `blocked.png` - UI遮挡检测（必需）

#### 3. 运行程序

**GUI 模式（推荐）：**

```bash
python gui_app.py
```

**命令行模式：**

```bash
python main.py
```

> 提示：程序使用输入屏蔽功能防止操作期间的误触，需要管理员权限才能调用Windows底层API。
>
> 运行方式：以管理员身份打开终端，然后执行上述命令。
>
> 如果不想使用管理员权限，可在配置中关闭 `ENABLE_INPUT_BLOCK` 输入屏蔽功能。

## GUI界面说明

GUI模式提供以下功能：

- **启动/停止/暂停** 控制自动生产
- **清零TC** 重置TC数量缓存
- **实时日志** 彩色分类显示运行状态
- **状态栏** 显示最新一条关键信息
- **配置摘要** 显示核心参数
- **快捷键设置** 自定义各功能的快捷键
- **配置管理** 可视化修改所有参数，实时生效

### 快捷键设置

点击"快捷键"按钮可以为各功能设置自定义快捷键：

- 点击输入框后直接按下快捷键即可自动捕获
- 支持单键（F9、Space）和组合键（Ctrl+S、Alt+P、Ctrl+Space）
- 也可手动输入快捷键（如 `Ctrl+Space`）
- 快捷键配置保存在程序同目录的 `shortcuts.json` 文件
- 删除该文件即可恢复默认（无快捷键）

### 配置管理

点击"⚙ 配置"按钮可打开配置窗口：

- 参数修改后**实时生效**，无需重启
- 点击"保存"将配置持久化到 `config_override.json`（下次启动自动加载）
- 点击"恢复默认"可将所有参数还原为初始值
- 如果所有配置都恢复默认值并保存，则自动删除配置文件

## 打包为exe

使用 [build.py](build.py) 进行打包：

```bash
python build.py              # 打包GUI CPU精简版（推荐，约300MB）
python build.py --full       # 打包GUI完整版（含GPU支持，约2GB）
python build.py --cli        # 打包命令行版本
python build.py --clean      # 清理包括虚拟环境（强制重新下载依赖）
```

**CPU精简版原理：** 自动创建临时虚拟环境，安装CPU-only版本的PyTorch后打包，确保不包含CUDA库，体积大幅缩小。CPU版中GPU加速选项不可用（配置窗口中不显示）。虚拟环境默认保留以便复用。

打包完成后，exe文件在 `build/aoe4_gui_cpu/` 目录中。

## 配置说明

所有参数在 [config.py](config.py) 中管理，也可通过GUI配置窗口修改（包括所有截图区域坐标）。

### 基础参数

```python
VILLAGERS_PER_TC = 3    # 每个TC排队数量
MAX_VILLAGERS = 120     # 村民总数上限（⚠统计不含移动/建造/战斗中的村民，仅供参考，功能不稳定）
ENABLE_MAX_VILLAGERS = False  # 是否启用村民上限检测（因统计不准，默认关闭）
MIN_FOOD = 50           # 最低食物要求
VILLAGER_CHECK_INTERVAL = 3  # 村民数量检查间隔（秒），村民数量变化慢，不需要频繁检查
```

### 按键设置

```python
TC_SELECT_KEY = 'h'         # 选中所有TC的快捷键（需与游戏内"选择所有城镇中心"设置一致）
VILLAGER_QUEUE_KEY = 'q'    # 生产村民的快捷键（需与游戏内设置一致）
ENABLE_SHIFT_QUEUE = True   # 是否使用Shift+按键批量排队（每次5个），关闭则逐个排队
```

### OCR性能优化

```python
USE_GPU = False          # 是否使用GPU加速OCR（⚠不建议开启，小图片OCR时CPU更快；CPU版exe中此选项无效）
OCR_IMAGE_SCALE = 1      # OCR图片缩放比例（0.5=缩小到50%），越小越快但可能影响准确率
```

**说明：**

- ⚠ **不建议开启GPU加速**，对于小图片OCR，CPU模式通常比GPU更快（GPU有数据传输开销）
- CPU版exe中不包含CUDA库，GPU加速选项不可用（配置窗口中不显示该选项）
- `OCR_IMAGE_SCALE` 可根据识别准确率调整：
  - `1.0`：默认模式，识别准确
  - `0.5`：快速模式，适合大部分情况
  - `0.25`：极速模式，可能导致识别失败

### 操作时序设置

```python
POST_OPERATION_DELAY = 3.0  # 操作完成后等待游戏UI更新的时间（秒），避免连续触发
```

### 调试模式

```python
DEBUG_MODE = False               # 全局调试（TC/村民/食物/遮挡/生产检测的详细日志）
DEBUG_PERFORMANCE = False        # 性能分析（显示各模块详细耗时，独立开关）
DEBUG_SAVE_SCREENSHOTS = False   # 保存调试截图（需配合全局调试，关闭可提升5-10ms）
```

**推荐配置：**

- 日常使用：全部关闭
- 排查问题：开启 `DEBUG_MODE`，关闭 `DEBUG_SAVE_SCREENSHOTS`
- 性能优化：开启 `DEBUG_PERFORMANCE`

### 不同分辨率适配

默认配置基于 **2560x1440** 分辨率。如果你使用其他分辨率，需要按比例调整以下所有坐标参数：

```python
# 游戏窗口检测（SDR和HDR各有独立的坐标和颜色）
GAME_DETECT_PIXEL_SDR = (2526, 1405)  # SDR检测点坐标
GAME_DETECT_COLOR_SDR = (26, 32, 46)   # SDR检测点颜色
GAME_DETECT_PIXEL_HDR = (2526, 1405)  # HDR检测点坐标
GAME_DETECT_COLOR_HDR = (65, 78, 105)  # HDR检测点颜色

# 村民生产队列区域（左下角队列图标区域）
VILLAGER_QUEUE_REGION = (10, 970, 500, 1025)

# UI遮挡检测区域（队列区域内的特征点）
BLOCKED_DETECT_REGION = (265, 950, 280, 970)

# 人口显示区域（如 "50/200"）
POPULATION_REGION = (50, 1140, 150, 1170)

# 食物数量显示区域
FOOD_REGION = (50, 1222, 140, 1248)

# TC图标检测区域（左下角建筑图标区域）
TC_ICON_REGION = (444, 1212, 492, 1259)

# 村民总数统计区域（左下角数字区域）
VILLAGER_COUNT_REGION = (185, 1130, 240, 1420)
```

**调整方法：**

1. **使用区域编辑器**（推荐）：在配置窗口点击"区域编辑"，全屏显示所有区域框，拖拽调整后按 Enter 保存
2. **使用吸色工具**：在配置窗口点击"吸色工具"，选择 SDR/HDR 目标后截屏取色，自动填充坐标和颜色值
3. **手动调整**：启用 `DEBUG_MODE = True`，查看 [debug_output/](debug_output/) 截图，在配置窗口手动修改坐标

### 自定义模板图片

在 exe 同目录下创建 `user_templates/` 文件夹，放入与内置模板同名的图片文件即可替换：

- 例如放置 `user_templates/cunmin.png` 即可替换村民图标模板
- 在配置窗口点击"模板图片"可查看所有模板及替换状态
- 替换优先级：`user_templates/` 中的同名文件 > 内置 `templates/`
- 修改后需重启程序生效

### HDR 设置适配

SDR 和 HDR 各有独立的坐标和颜色值，通过 HDR 开关切换使用哪一组。在 GUI 配置窗口的核心参数区切换 HDR 开关，游戏状态检测点区会标注当前使用的是哪组配置。

```python
# 默认配置（HDR关闭）
HDR_ENABLED = False
GAME_DETECT_PIXEL_SDR = (2526, 1405)  # SDR检测点坐标
GAME_DETECT_COLOR_SDR = (26, 32, 46)   # SDR检测点颜色
GAME_DETECT_PIXEL_HDR = (2526, 1405)  # HDR检测点坐标
GAME_DETECT_COLOR_HDR = (65, 78, 105)  # HDR检测点颜色
```

- HDR 关闭时，使用 `GAME_DETECT_PIXEL_SDR` + `GAME_DETECT_COLOR_SDR`
- HDR 开启时，使用 `GAME_DETECT_PIXEL_HDR` + `GAME_DETECT_COLOR_HDR`
- 使用吸色工具可快速获取坐标和颜色值，自动填充到对应组
- 不同分辨率下坐标和颜色值可能都需要重新获取

## 工作流程

### 主循环流程

```
游戏窗口检测 → 生产队列检测 → UI遮挡检测 → 
OCR识别（人口、食物、村民数） → 资源检查 → 
人口空位计算 → TC检测 → 生产数量计算 → 
执行生产操作 → 继续循环
```

**性能优化：**

- 优先执行快速检测（模板匹配，几毫秒）
- 只有在"没有村民生产"时才执行慢速OCR（0.1-0.2秒）
- 村民数量每3秒检查一次（变化慢，无需频繁检查）
- 人口和食物并行OCR，减少等待时间
- 修饰键检测暂停不影响快捷键识别

### 生产操作详细流程

当检测到需要生产村民时，程序会执行以下原子操作（整个过程屏蔽物理输入，防止误操作）：

1. **🔒 开始屏蔽输入** - 阻止物理鼠标键盘输入，确保操作不被打断
2. **📦 Ctrl+0** - 将当前选中的单位保存到0号编组（临时）
3. **🏰 按H键** - 选中所有城镇中心（需要在游戏中设置H键为"选择所有城镇中心"的快捷键）
4. **👷 Shift+Q** - 批量排队村民（根据TC数量和资源计算数量）
5. **🔄 按0键** - 恢复之前选中的单位
6. **🗑️ Ctrl+Alt+0** - 取消0号临时编组
7. **🔓 结束屏蔽** - 恢复物理输入控制

整个操作耗时极短，几乎不影响正常游戏操作。

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
- **可选功能**：可在配置中关闭 `ENABLE_INPUT_BLOCK`

### 3. 修饰键暂停机制

当检测到按住 Shift、Ctrl 或 Alt 键时，临时暂停自动生产：

- 按住修饰键时暂停，松开后自动恢复
- 暂停不影响GUI快捷键的识别
- 目的是防止按键冲突

### 4. 模板匹配技术

使用OpenCV的模板匹配算法检测UI元素：

- **村民图标检测**：检测生产队列中是否有村民图标（阈值0.6）
- **TC图标检测**：统计左下角TC图标数量，支持多TC（阈值0.7）
- **UI遮挡检测**：检测队列区域是否被其他UI遮挡（阈值0.7）

不同元素使用不同阈值以平衡准确性和鲁棒性。

### 5. UI遮挡检测技术

使用三态判断机制检测生产队列UI是否被遮挡：

**置信度区间划分：**

- **完全未遮挡**（置信度 < 0.1）：立即确定，无需等待
- **渐变中**（0.1 ≤ 置信度 < 0.7）：需要连续检测判断
- **完全遮挡**（置信度 ≥ 0.7）：立即确定，无需等待

**稳定性检测：**

所有状态都需要连续2次检测才认为稳定，防止UI快速显隐时的瞬间误判。

**渐变误判检测：**

游戏场景颜色可能正好落在渐变区间（0.1-0.7），导致误判为"渐变中"。解决方案：

- 连续检测3次渐变状态
- 如果置信度变化 < 0.05，说明不是真正的渐变动画
- 强制认为"未遮挡"，避免误判

### 6. 半透明UI检测技术

当UI叠加在村民图标上时，会导致置信度异常，可能误判为"没有村民生产"。使用三策略检测系统识别UI渐入渐出动画：

**策略1：中等置信度 + 快速变化**

- 适用场景：UI正在渐入或渐出，置信度在0.3-0.65范围内快速波动
- 检测条件：当前置信度在0.3-0.65之间，且最近5次置信度变化幅度 > 0.1

**策略2：置信度突然下降**

- 适用场景：村民图标从清晰可见变为模糊/消失，UI正在渐出动画中
- 检测条件：当前置信度 < 0.5，历史最高 > 0.6，变化幅度 > 0.2

**策略3：连续下降检测**

- 适用场景：UI正在渐出动画的尾声，置信度持续下降
- 检测条件：最近3次持续下降（允许小幅波动±0.05），总体下降 > 0.1，当前 < 0.4

### 7. TC数量缓存机制

为防止UI遮挡导致TC检测失败，使用缓存机制：

- 开局缓存值为0
- 每次成功检测到TC数量后更新缓存
- TC检测失败时使用缓存值继续生产
- 如果缓存为0且检测失败，进入冷却状态
- 冷却期间每秒检查村民生产图标，检测到则提前结束冷却

### 8. OCR文字识别

使用EasyOCR识别游戏内数字：

- **人口识别**：识别"当前/上限"格式（如"50/200"）
- **食物识别**：识别资源数字
- **村民计数**：识别左下角村民总数（每3秒更新一次）
- **性能优化**：默认使用CPU模式，支持图片缩放，并行执行多个OCR任务

### 9. 智能生产计算

综合多个因素计算最优生产数量：

```python
# 基础计划：每个TC排队N个村民
planned = VILLAGERS_PER_TC × TC数量

# 限制1：人口上限
available_slots = (人口上限 - 当前人口) - 已在队列的村民数

# 限制2：村民总数上限（需启用，⚠仅供参考，不含移动/建造/战斗中的村民，功能不稳定）
remaining = MAX_VILLAGERS - 当前村民总数

# 限制3：食物资源
max_by_food = 当前食物 ÷ 50

# 最终生产数量
actual = min(planned, available_slots, remaining, max_by_food)
```

### 10. 配置热更新

所有通过GUI暴露的配置项均支持实时生效：

- 使用 `import config; config.X` 动态引用模式（而非 `from config import X` 静态绑定）
- 修改配置后立即影响工作线程行为，无需重启
- 配置通过 `config_override.json` 持久化，启动时自动加载

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

1. 启用 `DEBUG_MODE = True`
2. 查看 [debug_output/blocked_detection_debug.png](debug_output/blocked_detection_debug.png)
3. 调整以下阈值：
   - `BLOCKED_MATCH_THRESHOLD = 0.7` - 完全遮挡阈值
   - `BLOCKED_TRANSITION_THRESHOLD = 0.1` - 渐变下限阈值
4. 确认 `BLOCKED_DETECT_REGION` 坐标正确

### 识别不准确

1. 启用 `DEBUG_MODE = True`
2. 查看 [debug_output/](debug_output/) 截图
3. 在GUI配置窗口中调整坐标和阈值

### 一直提示"不在游戏窗口"

1. 确认 HDR 开关设置正确（核心参数区的"游戏HDR"）
2. 使用吸色工具重新获取坐标和颜色（最简单）
3. 或手动修改：HDR关闭修改 `GAME_DETECT_PIXEL_SDR` + `GAME_DETECT_COLOR_SDR`，HDR开启修改对应的 HDR 组

### TC数量识别错误

1. 确认 templates 目录中的 `tc_single.png` 和 `tc_number_*.png` 正确
2. 检查 `TC_ICON_REGION` 坐标
3. 调整 `TC_MATCH_THRESHOLD` 阈值
4. 增大 `TC_SELECT_DELAY` 以预留更多图像刷新时间

### 快捷键设置无效

- 部分组合键可能被系统占用（如 Ctrl+Space 被输入法拦截）
- 可在输入框中手动输入快捷键（如 `Ctrl+Space`）
- 确保 `shortcuts.json` 文件保存在exe同目录

### exe启动慢

- PyInstaller onefile 模式需要先解压到临时目录，首次启动会稍慢
- 后续启动会利用系统缓存，速度会快一些

## GPU加速配置（可选，不推荐）

程序默认使用CPU模式进行OCR识别。**⚠不建议开启GPU加速**，对于本工具的小图片OCR任务，CPU模式通常比GPU更快（GPU有数据传输开销）。

**注意：** CPU版exe（推荐下载的版本）不包含CUDA库，GPU加速选项在配置窗口中不显示。只有在完整版exe或从源码运行并安装了CUDA版PyTorch时才可使用GPU加速。

如果你想尝试GPU加速，可以：

1. 在配置中设置 `USE_GPU = True`
2. 按以下步骤安装CUDA和PyTorch

**检查CUDA版本：**

```bash
nvidia-smi
```

**安装对应PyTorch版本：**

```bash
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

## 目录结构

```
AOE4-AutoVillager-Tool/
├── gui_app.py                     # GUI图形界面（Tkinter）
├── main.py                        # 命令行主程序
├── build.py                       # PyInstaller打包脚本
├── config.py                      # 配置文件
├── game_detector.py               # 游戏窗口检测
├── villager_training_detector.py  # 村民生产检测
├── population_reader.py           # 人口OCR识别
├── villager_counter.py            # 村民计数
├── food_reader.py                 # 食物识别
├── tc_counter.py                  # TC数量检测
├── tc_selector.py                 # TC选择
├── villager_trainer.py            # 生产执行
├── ocr_util.py                    # OCR工具模块
├── screenshot_util.py             # 截图工具模块
├── logger.py                      # 日志模块
├── input_blocker.py               # 输入屏蔽模块
├── input_config.py                # 输入配置
├── lock.py                        # 文件锁
├── requirements.txt               # 依赖包
├── templates/                     # 内置模板图片
│   ├── cunmin.png                 # 村民图标
│   ├── blocked.png                # UI遮挡检测
│   ├── tc_single.png              # 单TC预检测
│   └── tc_number_*.png            # TC数量数字模板
└── debug_output/                  # 调试输出（自动生成）
```

## 使用技巧

1. **首次使用**：开启调试模式确认检测正常
2. **正式使用**：关闭调试模式减少性能开销
3. **多显示器**：游戏在主显示器
4. **窗口模式**：使用全屏窗口模式
5. **UI缩放**：游戏UI缩放100%
6. **游牧开局**：程序会自动跳过没有TC的情况，建造TC并手动生产第一个村民后即可正常工作
7. **长时间运行**：如内存占用较高，可停止后重新启动来释放并重新加载OCR模型

## 注意事项

- 仅供学习交流使用
- 建议在单人模式或允许使用工具的环境中使用
- 坐标需根据实际分辨率调整
- 需要管理员权限运行（输入屏蔽功能）

## 技术栈

- Python 3.8+
- OpenCV - 图像处理与模板匹配
- EasyOCR - 文字识别
- mss - 高速屏幕截图
- Pillow - 图像处理
- pydirectinput - 键盘模拟
- Tkinter - GUI界面
- PyInstaller - 打包为exe

## 许可证

MIT License
