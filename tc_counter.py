"""
TC数量检测模块

检测策略：
1. 预检测：检测单TC区域是否匹配tc_single.png专用模板
   - 匹配 → 只有1个TC，直接返回（最快路径）
   - 不匹配 → 进入主检测流程
2. 主检测阶段1：用完整图标匹配TC_ICON_REGION区域的tc_number_1.png
   - 置信度低于阈值 → 未选中TC，重新按H键（最多10次）
   - 置信度达到阈值 → 有多个TC，进入阶段2
3. 主检测阶段2：用左上角20×20区域精确匹配所有数字模板
   - tc_number_1.png → 2个TC（1+1）
   - tc_number_2.png → 3个TC（1+2）
   - 以此类推

性能优化：
- 模板缓存：首次加载后缓存所有模板，避免重复I/O
- 灰度图匹配：减少计算量，提升性能
- 提前退出：高置信度匹配时提前结束搜索
- mss截图：使用mss库替代PIL.ImageGrab（2-3x提升）
- 自动重试：UI未刷新时自动重试，确保检测成功

模板文件：
- templates/tc_single.png：单TC预检测专用（灰度图）
- templates/tc_number_N.png：多TC数字模板（N从1开始）
"""
import os
import time
import cv2
from config import *
from screenshot_util import capture_region_np
from logger import log_tc, perf_stats

# 检测区域和阈值配置
REGION = TC_ICON_REGION  # 多TC主检测区域
DEBUG_SCREENSHOT_PATH = TC_DEBUG_SCREENSHOT
MATCH_THRESHOLD = TC_MATCH_THRESHOLD
CROP_SIZE = 20  # 阶段2匹配时裁剪的左上角区域尺寸
EARLY_EXIT_THRESHOLD = 0.95  # 提前退出阈值，置信度超过此值直接返回

# 全局模板缓存
_template_cache = {}
_template_crop_cache = {}
_single_tc_template = None  # 单TC预检测专用模板
_max_tc_number = None  # 动态检测到的最大TC编号模板（避免硬编码搜索范围）


def _detect_max_tc_number():
    """
    动态检测可用的最大TC编号模板
    扫描 templates/tc_number_N.png，找到最大的N
    避免硬编码 range(1, 21) 在模板不足时做无用匹配
    """
    max_num = 0
    for num in range(1, 50):  # 理论上限50，实际找到不存在的就停止
        template_path = os.path.join(TEMPLATES_DIR, f"tc_number_{num}.png")
        if os.path.exists(template_path):
            max_num = num
        else:
            break  # 编号连续，遇到不存在的即可停止
    return max_num


def _get_max_tc_number():
    """获取最大TC编号（带缓存）"""
    global _max_tc_number
    if _max_tc_number is None:
        _max_tc_number = _detect_max_tc_number()
        if DEBUG_MODE:
            print(f"[TC] 检测到最大TC编号模板: tc_number_{_max_tc_number}.png")
    return _max_tc_number


def _load_single_tc_template():
    """加载单TC预检测专用模板（灰度图，带缓存）"""
    global _single_tc_template
    if _single_tc_template is None:
        if os.path.exists(TC_SINGLE_TEMPLATE):
            _single_tc_template = cv2.imread(TC_SINGLE_TEMPLATE, cv2.IMREAD_GRAYSCALE)
    return _single_tc_template


def _load_template_full(num):
    """加载完整模板（阶段1使用），带缓存"""
    cache_key = f"full_{num}"
    if cache_key in _template_cache:
        return _template_cache[cache_key]

    template_path = os.path.join(TEMPLATES_DIR, f"tc_number_{num}.png")
    if not os.path.exists(template_path):
        return None

    template = cv2.imread(template_path, cv2.IMREAD_GRAYSCALE)
    if template is not None:
        _template_cache[cache_key] = template
    return template


def _load_template_crop(num):
    """加载裁剪后的模板（阶段2使用），带缓存"""
    cache_key = f"crop_{num}"
    if cache_key in _template_crop_cache:
        return _template_crop_cache[cache_key]

    template_path = os.path.join(TEMPLATES_DIR, f"tc_number_{num}.png")
    if not os.path.exists(template_path):
        return None

    template = cv2.imread(template_path, cv2.IMREAD_COLOR)
    if template is None:
        return None

    # 裁剪左上角区域
    if template.shape[0] < CROP_SIZE or template.shape[1] < CROP_SIZE:
        return None

    template_crop = template[0:CROP_SIZE, 0:CROP_SIZE]
    template_gray = cv2.cvtColor(template_crop, cv2.COLOR_BGR2GRAY)

    _template_crop_cache[cache_key] = template_gray
    return template_gray


class TCCounter:
    """TC数量检测器"""

    def __init__(self):
        self.count = 1  # 默认1个TC
        self.detection_failed = False  # 检测是否失败

    def do(self):
        """执行TC数量检测，带自动重试"""
        t_start = time.time() if DEBUG_PERFORMANCE else None

        # 重置失败标志
        self.detection_failed = False

        # 等待UI刷新（因为外部刚按了H键）
        time.sleep(TC_RETRY_DELAY)

        # 重试循环：每次都检测两个区域
        for retry in range(TC_MAX_RETRY):
            log_tc("重试", f"第{retry+1}/{TC_MAX_RETRY}次检测")

            # 1. 先检测单TC区域（快速路径）
            # 只在第一次保存调试截图
            if self._check_single_tc_region(save_debug=(retry == 0)):
                self.count = 1
                log_tc("结果", f"单TC区域匹配 TC数=1")
                if DEBUG_PERFORMANCE:
                    t_total = time.time() - t_start
                    perf_stats.record("[6.2] TC检测(单TC)", t_total)
                return

            # 2. 检测多TC区域
            # 只在第一次保存调试截图
            screenshot = self._capture(retry_count=(retry if retry < 3 else -1))  # 只保存前3次
            t_capture = time.time() if DEBUG_PERFORMANCE else None

            log_tc("截图", f"多TC区域 尺寸={screenshot.shape[1]}x{screenshot.shape[0]}")

            detected_number = self._match_numbered_tc(screenshot)
            t_match = time.time() if DEBUG_PERFORMANCE else None

            if detected_number is not None:
                self.count = 1 + detected_number
                log_tc("结果", f"匹配到tc_number_{detected_number}.png TC数={self.count}")

                if DEBUG_PERFORMANCE:
                    t_total = time.time() - t_start
                    perf_stats.record("[6.2] TC截图", (t_capture-t_start))
                    perf_stats.record("[6.2] TC匹配", (t_match-t_capture))
                    perf_stats.record("[6.2] TC检测(多TC)", t_total)
                return

            # 两个区域都未检测到TC，等待后重试
            if retry < TC_MAX_RETRY - 1:
                log_tc("重试", f"两个区域都未检测到TC 等待UI刷新后重试")
                time.sleep(TC_RETRY_DELAY)  # 等待UI刷新

        # 所有重试都失败，标记为检测失败
        log_tc("失败", f"重试{TC_MAX_RETRY}次后仍未检测到TC 进入冷却状态")
        self.detection_failed = True

        if DEBUG_PERFORMANCE:
            t_total = time.time() - t_start
            perf_stats.record("[6.2] TC检测(失败)", t_total)

    def _check_single_tc_region(self, save_debug=False):
        """
        预检测：检测单TC区域是否匹配tc_single.png专用模板
        使用灰度图匹配，无需缩放，性能最优

        参数：
            save_debug: 是否保存调试截图（只在第一次调用时保存）

        返回：True表示匹配（只有1个TC），False表示不匹配（需要继续主检测）
        """
        template = _load_single_tc_template()
        if template is None:
            log_tc("预检测", "未找到tc_single.png 跳过预检测")
            return False

        left, top, right, bottom = SINGLE_TC_REGION
        screenshot = capture_region_np(left, top, right, bottom)
        screenshot_gray = cv2.cvtColor(screenshot, cv2.COLOR_BGR2GRAY)

        # 保存调试截图（只在第一次时保存）
        if save_debug and DEBUG_MODE and DEBUG_SAVE_SCREENSHOTS:
            try:
                from PIL import Image
                debug_path = os.path.join(DEBUG_OUTPUT_DIR, "tc_single_region_debug.png")
                img_rgb = cv2.cvtColor(screenshot, cv2.COLOR_BGR2RGB)
                Image.fromarray(img_rgb).save(debug_path)
                log_tc("预检测截图", f"{debug_path}")

                debug_gray_path = os.path.join(DEBUG_OUTPUT_DIR, "tc_single_region_gray.png")
                cv2.imwrite(debug_gray_path, screenshot_gray)
                log_tc("预检测灰度", f"{debug_gray_path}")

                # 同时保存模板用于对比
                template_path = os.path.join(DEBUG_OUTPUT_DIR, "tc_single_template.png")
                cv2.imwrite(template_path, template)
                log_tc("预检测模板", f"{template_path}")
            except Exception as e:
                log_tc("预检测截图", f"保存失败: {e}")

        if DEBUG_MODE:
            log_tc("预检测", f"截图尺寸={screenshot_gray.shape[1]}x{screenshot_gray.shape[0]} 模板尺寸={template.shape[1]}x{template.shape[0]}")
            log_tc("预检测", f"区域坐标=({left},{top},{right},{bottom})")

        # 检查模板是否超过截图尺寸
        if template.shape[0] > screenshot_gray.shape[0] or template.shape[1] > screenshot_gray.shape[1]:
            log_tc("预检测", f"模板超过截图尺寸，无法匹配")
            return False

        # 灰度图模板匹配
        result = cv2.matchTemplate(screenshot_gray, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, _ = cv2.minMaxLoc(result)

        log_tc("预检测", f"置信度={max_val:.4f} 阈值={MATCH_THRESHOLD:.4f} 匹配={'成功' if max_val >= MATCH_THRESHOLD else '失败'}")

        return max_val >= MATCH_THRESHOLD

    def _capture(self, retry_count=0):
        """截取TC图标区域（多TC主检测区域）"""
        left, top, right, bottom = REGION
        img_bgr = capture_region_np(left, top, right, bottom)

        # 只保存前3次重试的截图
        if retry_count >= 0 and DEBUG_MODE and DEBUG_SAVE_SCREENSHOTS:
            try:
                from PIL import Image
                img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
                # 保存时带上重试次数，避免覆盖
                debug_path = DEBUG_SCREENSHOT_PATH.replace('.png', f'_retry{retry_count}.png')
                Image.fromarray(img_rgb).save(debug_path)
                log_tc("截图", f"{debug_path}")
            except Exception as e:
                log_tc("截图", f"保存失败: {e}")

        return img_bgr

    def _match_numbered_tc(self, screenshot):
        """
        两阶段匹配策略：
        阶段1：用完整图标匹配tc_number_1.png判断是否有多TC
        阶段2：用左上角20*20区域精确匹配所有数字

        返回：匹配到的数字N（对应1+N个TC），未匹配到返回None
        """
        # 阶段1：完整图标匹配判断是否有多TC
        if not self._has_tc_icon(screenshot):
            return None

        # 阶段2：左上角区域精确匹配数字
        return self._match_number_in_top_left(screenshot)

    def _has_tc_icon(self, screenshot):
        """阶段1：用完整图标匹配tc_number_1.png判断是否有TC图标显示"""
        template = _load_template_full(1)
        if template is None:
            if DEBUG_MODE:
                print(f"[TC阶段1] 未找到tc_number_1.png")
            return False

        # 自动缩放模板（如果模板超过截图尺寸）
        template = self._resize_if_needed(template, screenshot)
        if template is None:
            return False

        # 灰度图匹配
        screenshot_gray = cv2.cvtColor(screenshot, cv2.COLOR_BGR2GRAY)
        result = cv2.matchTemplate(screenshot_gray, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, _ = cv2.minMaxLoc(result)

        if DEBUG_MODE:
            print(f"[TC阶段1] 置信度={max_val:.4f} 阈值={MATCH_THRESHOLD:.4f}")

        return max_val >= MATCH_THRESHOLD

    def _match_number_in_top_left(self, screenshot):
        """阶段2：用左上角20*20区域精确匹配所有数字"""
        if DEBUG_MODE:
            print(f"[TC阶段2] 检测到多TC，用左上角区域精确匹配...")

        # 裁剪截图左上角区域
        if screenshot.shape[0] < CROP_SIZE or screenshot.shape[1] < CROP_SIZE:
            if DEBUG_MODE:
                print(f"[TC阶段2] 截图过小，默认返回1")
            return 1

        screenshot_crop = screenshot[0:CROP_SIZE, 0:CROP_SIZE]
        screenshot_gray = cv2.cvtColor(screenshot_crop, cv2.COLOR_BGR2GRAY)

        if DEBUG_MODE:
            debug_path = os.path.join(DEBUG_OUTPUT_DIR, "tc_match_region.png")
            cv2.imwrite(debug_path, screenshot_crop)
            print(f"[TC阶段2] 匹配区域={debug_path}")

        # 遍历所有数字模板，找最佳匹配（动态上限，基于实际存在的模板文件）
        best_number = 1
        best_confidence = 0

        for num in range(1, _get_max_tc_number() + 1):
            template_gray = _load_template_crop(num)
            if template_gray is None:
                if DEBUG_MODE:
                    print(f"[TC阶段2] 未找到tc_number_{num}.png，停止搜索")
                break

            # 模板匹配
            result = cv2.matchTemplate(screenshot_gray, template_gray, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, _ = cv2.minMaxLoc(result)

            if DEBUG_MODE:
                print(f"[TC阶段2] tc_number_{num}.png 置信度={max_val:.4f}")

            if max_val > best_confidence:
                best_confidence = max_val
                best_number = num

                # 提前退出优化：置信度非常高时直接返回
                if max_val >= EARLY_EXIT_THRESHOLD:
                    if DEBUG_MODE:
                        print(f"[TC阶段2] 置信度超过{EARLY_EXIT_THRESHOLD}，提前退出")
                    break

        if DEBUG_MODE:
            print(f"[TC阶段2] 最佳匹配=tc_number_{best_number}.png 置信度={best_confidence:.4f}")

        return best_number

    def _resize_if_needed(self, template, screenshot):
        """自动缩放过大的模板，保持宽高比"""
        t_h, t_w = template.shape[:2]
        s_h, s_w = screenshot.shape[:2]

        if t_h <= s_h and t_w <= s_w:
            return template

        # 缩放到截图尺寸的90%以内
        scale = min((s_h * 0.9) / t_h, (s_w * 0.9) / t_w)
        new_size = (int(t_w * scale), int(t_h * scale))

        if DEBUG_MODE:
            print(f"[TC] 模板过大({t_w}x{t_h})，缩放到{new_size}")

        return cv2.resize(template, new_size, interpolation=cv2.INTER_AREA)
