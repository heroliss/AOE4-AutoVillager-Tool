# 日志系统优化说明

## 调试开关

### 当前的3个调试开关

```python
DEBUG_MODE = False               # 全局调试：TC/村民/食物/遮挡/生产检测的详细日志
DEBUG_PERFORMANCE = False        # 性能分析（独立开关）
DEBUG_SAVE_SCREENSHOTS = False   # 保存调试截图（需配合DEBUG_MODE，关闭可提升5-10ms）
```

### 依赖关系图

```
DEBUG_MODE
├── TC检测日志 (tc_counter.py)
├── 村民计数日志 (villager_counter.py)
├── 食物识别日志 (food_reader.py)
├── 遮挡检测日志 (villager_training_detector.py)
├── 村民生产检测日志 (villager_training_detector.py)
└── 主程序日志 (main.py)

DEBUG_PERFORMANCE (独立)
├── TC检测性能 (tc_counter.py)
├── 村民检测性能 (villager_training_detector.py)
└── 其他模块性能

DEBUG_SAVE_SCREENSHOTS (依赖DEBUG_MODE)
└── 保存调试截图到 debug_output/ 目录
```

**开关说明：**
- `DEBUG_MODE` 是全局调试开关，控制所有模块的详细日志输出
- `DEBUG_PERFORMANCE` 是独立开关，可在 `DEBUG_MODE=False` 时单独开启
- `DEBUG_SAVE_SCREENSHOTS` 需配合 `DEBUG_MODE` 使用，单独开启无效

## 新的日志格式

### 统一格式

```
[模块名称] 级别     | 消息内容
```

### 示例输出

#### 开启 DEBUG_MODE

```
[TC      ] 截图     | 尺寸=49x49
[TC      ] 阶段1    | 完整图标置信度=0.8523 阈值=0.7000
[TC      ] 阶段2    | 检测到多TC，用左上角区域精确匹配...
[TC      ] 阶段2    | 匹配区域=debug_output/tc_match_region.png
[TC      ] 阶段2    | tc_number_1.png 置信度=0.7234
[TC      ] 阶段2    | tc_number_2.png 置信度=0.9512
[TC      ] 阶段2    | 未找到tc_number_3.png，停止搜索
[TC      ] 阶段2    | 最佳匹配=tc_number_2.png 置信度=0.9512
[TC      ] 结果     | 匹配到tc_number_2.png TC数=3
[BLOCKED ] 截图     | debug_output/blocked_detection_debug.png
[BLOCKED ] 截图     | 尺寸=30x40
[BLOCKED ] 模板     | 尺寸=30x40
[BLOCKED ] 结果     | 置信度=0.3245 阈值=0.4000 状态=未遮挡
[TRAINING] 检测     | 置信度=0.8234 阈值=0.6000 状态=检测到
```

#### 开启 DEBUG_PERFORMANCE

```
[TC      ] PERF     | 总耗时=15.23ms
[TC      ] PERF     |   截图=5.12ms
[TC      ] PERF     |   匹配=10.11ms
[VILLAGER] PERF     | 总耗时=8.45ms
[VILLAGER] PERF     |   截图=2.34ms
[VILLAGER] PERF     |   遮挡截图=1.23ms
[VILLAGER] PERF     |   遮挡检测=2.11ms
[VILLAGER] PERF     |   模板匹配=2.77ms
```

## 日志模块化优势

### 1. 清晰的模块分类
- 每行日志都有明确的模块标识
- 8字符宽度对齐，易于阅读

### 2. 统一的格式
- `[模块名称] 级别 | 消息`
- 便于grep过滤：`grep "\[TC\]" log.txt`

### 3. 简化的开关控制
- 全局调试：只需开启 `DEBUG_MODE`
- 性能分析：独立开关 `DEBUG_PERFORMANCE`
- 调试截图：`DEBUG_SAVE_SCREENSHOTS` 控制是否保存截图文件

### 4. 便于日志分析
```bash
# 只看TC相关日志
grep "\[TC\]" output.log

# 只看性能数据
grep "PERF" output.log

# 只看结果
grep "结果" output.log
```

## 推荐配置场景

### 场景1：日常使用
```python
DEBUG_MODE = False
DEBUG_PERFORMANCE = False
DEBUG_SAVE_SCREENSHOTS = False
```
**输出：** 只有关键信息和错误

### 场景2：排查问题
```python
DEBUG_MODE = True
DEBUG_PERFORMANCE = False
DEBUG_SAVE_SCREENSHOTS = False
```
**输出：** TC/村民/食物/遮挡/生产检测的详细日志

### 场景3：性能优化
```python
DEBUG_MODE = False
DEBUG_PERFORMANCE = True
DEBUG_SAVE_SCREENSHOTS = False
```
**输出：** 只有性能分析数据

### 场景4：全面调试
```python
DEBUG_MODE = True
DEBUG_PERFORMANCE = True
DEBUG_SAVE_SCREENSHOTS = True
```
**输出：** 所有详细信息和调试截图（日志量大）

## 使用新日志系统

### 在代码中使用

```python
from logger import log_tc, log_perf, log_blocked

# TC检测日志（受DEBUG_MODE控制）
log_tc("截图", f"尺寸={width}x{height}")
log_tc("结果", f"TC数={count}")

# 性能日志（受DEBUG_PERFORMANCE控制）
log_perf("TC", f"总耗时={time}ms")

# 遮挡检测日志（受DEBUG_MODE控制）
log_blocked("结果", f"置信度={conf} 状态={status}")
```

### 日志级别说明

- **截图**: 截图相关信息
- **阶段1/阶段2**: 匹配过程
- **结果**: 最终结果
- **PERF**: 性能数据
- **INFO**: 通用信息（总是显示）
- **ERROR**: 错误信息（总是显示）

## 迁移进度

### 已完成
- ✅ 创建统一日志模块 `logger.py`
- ✅ 更新配置文件说明
- ✅ 完整更新 `tc_counter.py`
- ✅ 更新 `villager_training_detector.py`
- ✅ 更新 `villager_counter.py`
- ✅ 更新 `food_reader.py`
- ✅ 更新 `main.py`
- ✅ 所有模块改为 `import config` 动态引用，支持配置热更新
- ✅ 调试开关合并：`DEBUG_BLOCKED_DETECTION` 和 `DEBUG_TRAINING_DETECTION` 合并到 `DEBUG_MODE`

## 总结

新的日志系统提供：
1. **模块化**：每个模块独立的日志标识
2. **可控性**：3个开关，简洁明了
3. **可读性**：统一格式，易于阅读和过滤
4. **可分析性**：便于grep、awk等工具处理
