# 截图性能优化方案

## 问题分析

从日志可以看出：
```
[性能] 村民检测总耗时=120.32ms
  - 截图=54.89ms          (45.6%)
  - 遮挡截图=62.01ms      (51.5%)
  - 遮挡检测=2.78ms       (2.3%)
  - 模板匹配=0.64ms       (0.5%)
```

**截图占用了97%的时间！**

## 优化方案

### 1. ✅ 合并截图区域（已完成）

**当前：** 分别截取队列区域和遮挡区域（2次截图）
**优化：** 一次截取包含两个区域的大区域，然后裁剪

```python
# 优化前
screenshot1 = ImageGrab.grab(bbox=(10, 970, 500, 1025))   # 队列
screenshot2 = ImageGrab.grab(bbox=(260, 950, 290, 990))   # 遮挡

# 优化后
merged_img = capture_region_np(merged_left, merged_top, merged_right, merged_bottom)
queue_screenshot = merged_gray[queue_rel_top:queue_rel_bottom, queue_rel_left:queue_rel_right]
blocked_screenshot = merged_gray[blocked_rel_top:blocked_rel_bottom, blocked_rel_left:blocked_rel_right]
```

**预期提升：** 截图时间减少约50%（从110ms降到55ms）

### 2. ✅ 禁用调试模式下的PNG保存（已完成）

**当前：** 每次都保存PNG文件
**优化：** 只在需要时保存，或使用内存缓存

```python
# 添加配置项
DEBUG_SAVE_SCREENSHOTS = False  # 是否保存调试截图到文件

# 只在需要时保存
if DEBUG_MODE and DEBUG_SAVE_SCREENSHOTS:
    img.save(DEBUG_SCREENSHOT_PATH)
```

**预期提升：** 减少5-10ms的I/O开销

### 3. ✅ 使用mss库替代PIL.ImageGrab（已完成）

**原因：** mss是专门为截图优化的库，比PIL快2-3倍

```python
import mss

with mss.mss() as sct:
    monitor = {"top": 950, "left": 10, "width": 490, "height": 75}
    screenshot = sct.grab(monitor)
    img = np.array(screenshot)
```

**预期提升：** 截图时间减少50-70%（从55ms降到15-25ms）

### 4. ✅ 降低截图频率（已完成）

**当前：** CHECK_INTERVAL = 0.1秒，每秒检测10次
**优化：** 根据游戏状态动态调整

```python
# 生产中：降低检测频率
if training_detected:
    time.sleep(CHECK_INTERVAL * 3)  # 生产中每0.3秒检测一次
elif blocked:
    time.sleep(CHECK_INTERVAL * 2)  # 遮挡时每0.2秒检测一次
else:
    time.sleep(CHECK_INTERVAL)  # 空闲时每0.1秒检测一次
```

**预期提升：** CPU占用降低60-70%

### 5. ⏳ 使用共享内存截图（高级）

**原理：** 使用Windows的Desktop Duplication API，零拷贝截图

**预期提升：** 截图时间降到1-5ms

## 实施状态

1. ✅ **已完成：** 合并截图区域（简单，效果好）
2. ✅ **已完成：** 禁用PNG保存（配置项）
3. ✅ **已完成：** 使用mss库（需要安装依赖）
4. ✅ **已完成：** 动态调整检测频率
5. ⏳ **长期：** 共享内存截图（复杂度高）

## 预期效果

| 优化项 | 当前耗时 | 优化后 | 提升 |
|--------|---------|--------|------|
| 合并截图 | 110ms | 55ms | 2x |
| 禁用PNG保存 | 55ms | 45ms | 1.2x |
| 使用mss | 45ms | 15ms | 3x |
| **总计** | **110ms** | **15ms** | **7.3x** |

## 实施细节

### 创建的新文件
- `screenshot_util.py`: 统一截图工具模块，封装mss库

### 修改的文件
- `villager_training_detector.py`: 使用mss + 合并截图区域
- `tc_counter.py`: 使用mss + DEBUG_SAVE_SCREENSHOTS检查
- `villager_counter.py`: 使用mss + 新日志系统
- `food_reader.py`: 使用mss + 新日志系统
- `main.py`: 动态检测频率（生产中3x，遮挡2x）

## 其他发现

### OCR耗时正常
```
[OCR耗时] 人口=0.399s 食物=0.342s 村民=0.293s
[OCR总计] 0.402秒
```
OCR耗时0.4秒是正常的，因为：
- 使用CPU模式（配置禁用GPU）
- 深度学习模型推理需要时间
- 已经通过共享Reader实例优化

### TC检测截图也慢
```
[TC      ] PERF     | 总耗时=69.21ms
[TC      ] PERF     |   截图=61.53ms  (89%)
```
同样的问题，已通过mss库优化。
