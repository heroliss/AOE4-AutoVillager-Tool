"""
村民生产状态检测模块
检测生产队列中是否有村民正在生产，以及UI是否被遮挡
使用灰度图匹配提升性能

性能优化：
- 使用mss库替代PIL.ImageGrab（2-3x提升）
- 合并截图区域，一次截图裁剪出两个子区域（减少50%截图时间）
- 灰度图模板匹配，比彩色图快约30%

UI遮挡检测技术：
- 三态判断：完全遮挡、完全未遮挡、渐变中
- 置信度区间：
  * [0, BLOCKED_TRANSITION_THRESHOLD) → 完全未遮挡（立即确定）
  * [BLOCKED_TRANSITION_THRESHOLD, BLOCKED_MATCH_THRESHOLD) → 渐变中（需连续检测）
  * [BLOCKED_MATCH_THRESHOLD, 1.0] → 完全遮挡（立即确定）

渐变误判检测：
- 非渐变状态（完全遮挡/完全未遮挡）：一次检测立即确定
- 渐变状态：需要连续3次检测，且置信度变化<0.05才认为是场景颜色误判
- 解决问题：游戏场景颜色正好落在渐变区间时的误判

半透明UI检测技术：
- 策略1：中等置信度区间(0.3-0.65) + 快速变化(>0.1)
- 策略2：置信度突然下降（从>0.6降到<0.5，变化>0.2）
- 策略3：连续下降检测（最近3次持续下降，总变化>0.1）
- 解决问题：UI渐入渐出动画期间的误触发
"""
import os
import time
import cv2
import numpy as np
from config import (
    VILLAGER_QUEUE_REGION,
    BLOCKED_DETECT_REGION,
    VILLAGER_TEMPLATE,
    BLOCKED_TEMPLATE,
    BLOCKED_DEBUG_SCREENSHOT,
    VILLAGER_MATCH_THRESHOLD,
    BLOCKED_MATCH_THRESHOLD,
    BLOCKED_TRANSITION_THRESHOLD,
    DEBUG_MODE,
    DEBUG_BLOCKED_DETECTION,
    DEBUG_SAVE_SCREENSHOTS,
    DEBUG_PERFORMANCE
)
from screenshot_util import capture_region_np
from logger import log_blocked, log_training, log_perf

# 模板缓存（灰度图）
_template_gray = None
_blocked_template_gray = None
_blocked_detection_initialized = False  # 遮挡检测是否已初始化（用于避免重复打印）


def _get_template():
    """获取村民模板（灰度图，带缓存）"""
    global _template_gray
    if _template_gray is None:
        _template_gray = cv2.imread(VILLAGER_TEMPLATE, cv2.IMREAD_GRAYSCALE)
    return _template_gray


def _get_blocked_template():
    """获取遮挡模板（灰度图，带缓存和自动缩放）"""
    global _blocked_template_gray
    if _blocked_template_gray is None:
        if os.path.exists(BLOCKED_TEMPLATE):
            template = cv2.imread(BLOCKED_TEMPLATE, cv2.IMREAD_GRAYSCALE)

            # 获取目标区域尺寸
            left, top, right, bottom = BLOCKED_DETECT_REGION
            target_width = right - left
            target_height = bottom - top

            # 如果模板尺寸超过目标区域，自动缩放
            if template.shape[0] > target_height or template.shape[1] > target_width:
                _blocked_template_gray = cv2.resize(template, (target_width, target_height))
                if DEBUG_BLOCKED_DETECTION:
                    print(f"[遮挡模板] 自动缩放 {template.shape[1]}x{template.shape[0]} -> {target_width}x{target_height}")
            else:
                _blocked_template_gray = template
    return _blocked_template_gray


class VillagerTrainingDetector(object):
    """检测生产队列中是否有村民正在生产，使用灰度图模板匹配"""

    def __init__(self):
        self.found = False
        self.confidence = 0.0
        self.blocked = False  # UI是否被遮挡
        self.blocked_confidence = 0.0  # 遮挡检测置信度
        self.in_transition = False  # UI是否正在渐变（渐入渐出动画中）
        self._blocked_check_enabled = False

        # 渐变误判检测（只对渐变状态生效）
        self._transition_count = 0  # 连续检测到渐变的次数
        self._last_transition_confidence = 0.0  # 上次渐变的置信度
        self._transition_threshold = 3  # 连续多少次认为是误判
        self._confidence_change_threshold = 0.05  # 置信度变化阈值

        # 稳定性检测（防止UI快速显隐时的误判）
        self._last_state = None  # 上次的状态 ('blocked', 'clear', 'transition')
        self._stable_count = 0  # 当前状态持续次数
        self._stable_threshold = 2  # 需要连续多少次相同状态才认为稳定

        # 半透明UI检测（检测UI渐入渐出动画）
        self._recent_confidences = []  # 最近的置信度历史（用于检测置信度变化模式）
        self._confidence_history_size = 5  # 保留最近5次的置信度（捕获完整的UI动画周期）
        self._semi_transparent_detected = False  # 是否检测到半透明UI（用于调试输出）

        self._init_blocked_detection()

    def _init_blocked_detection(self):
        """初始化遮挡检测，检查模板是否可用"""
        global _blocked_detection_initialized

        if not os.path.exists(BLOCKED_TEMPLATE):
            raise FileNotFoundError(
                f"错误: 未找到 blocked.png 模板文件！\n"
                f"路径: {BLOCKED_TEMPLATE}\n"
                f"UI遮挡检测是必需功能，否则会频繁误判为没有村民在生产。\n"
                f"请确保 templates/blocked.png 文件存在。"
            )

        blocked_template = _get_blocked_template()
        if blocked_template is None:
            raise RuntimeError(f"错误: 无法加载 blocked.png 模板文件")

        # 获取截图区域尺寸
        left, top, right, bottom = BLOCKED_DETECT_REGION
        screenshot_width = right - left
        screenshot_height = bottom - top

        self._blocked_check_enabled = True

        # 只在调试模式下打印初始化信息
        if not _blocked_detection_initialized and DEBUG_MODE:
            print(f"[遮挡检测] 已启用 区域={screenshot_width}x{screenshot_height} 模板={blocked_template.shape[1]}x{blocked_template.shape[0]}")
            _blocked_detection_initialized = True

    def has_villager_icon(self):
        """
        快速检测是否有村民生产图标（不检查遮挡）
        用于冷却期间监控TC是否已建造

        返回：True表示有村民图标，False表示没有
        """
        # 截取队列区域
        left, top, right, bottom = VILLAGER_QUEUE_REGION
        img_bgr = capture_region_np(left, top, right, bottom)
        screenshot = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)

        # 模板匹配
        template = _get_template()
        result = cv2.matchTemplate(screenshot, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, _ = cv2.minMaxLoc(result)

        return max_val >= VILLAGER_MATCH_THRESHOLD

    def do(self):
        t_start = time.time() if DEBUG_PERFORMANCE else None

        # 合并截图：一次截取包含队列和遮挡区域的大区域，然后裁剪
        screenshot, blocked_screenshot = self._capture_merged()
        t_capture = time.time() if DEBUG_PERFORMANCE else None

        # 遮挡检测
        if self._blocked_check_enabled:
            # 保存调试截图（仅在调试模式下）
            if DEBUG_BLOCKED_DETECTION and DEBUG_SAVE_SCREENSHOTS:
                try:
                    from PIL import Image
                    img = cv2.cvtColor(blocked_screenshot, cv2.COLOR_GRAY2RGB)
                    Image.fromarray(img).save(BLOCKED_DEBUG_SCREENSHOT)
                    log_blocked("截图", f"{BLOCKED_DEBUG_SCREENSHOT}")
                    log_blocked("截图", f"尺寸={blocked_screenshot.shape[1]}x{blocked_screenshot.shape[0]}")
                except Exception as e:
                    log_blocked("截图", f"保存失败: {e}")

            self._check_blocked(blocked_screenshot)
            t_blocked_check = time.time() if DEBUG_PERFORMANCE else None

        if not self.blocked:
            self._match(screenshot)
            t_match = time.time() if DEBUG_PERFORMANCE else None

            # 如果置信度异常低，保存调试截图
            if DEBUG_BLOCKED_DETECTION and self.confidence < 0.5:
                try:
                    from PIL import Image
                    import os
                    debug_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "debug_output")
                    os.makedirs(debug_dir, exist_ok=True)
                    debug_path = os.path.join(debug_dir, "villager_low_confidence.png")
                    img = cv2.cvtColor(screenshot, cv2.COLOR_GRAY2RGB)
                    Image.fromarray(img).save(debug_path)
                    log_training("截图", f"置信度异常低，已保存截图到 {debug_path}")
                except Exception as e:
                    log_training("截图", f"保存失败: {e}")

        if DEBUG_PERFORMANCE:
            t_total = time.time() - t_start
            log_perf("VILLAGER", f"总耗时={t_total*1000:.2f}ms")
            log_perf("VILLAGER", f"  截图={((t_capture-t_start)*1000):.2f}ms")
            if self._blocked_check_enabled:
                log_perf("VILLAGER", f"  遮挡检测={((t_blocked_check-t_capture)*1000):.2f}ms")
            if not self.blocked:
                log_perf("VILLAGER", f"  模板匹配={((t_match-t_blocked_check if self._blocked_check_enabled else t_match-t_capture)*1000):.2f}ms")

    def _capture_merged(self):
        """
        合并截图：一次截取包含队列和遮挡区域的大区域，然后裁剪
        性能优化：减少50%的截图次数

        返回：
            (queue_screenshot, blocked_screenshot) 两个灰度图
        """
        # 计算合并区域（包含队列和遮挡两个区域）
        queue_left, queue_top, queue_right, queue_bottom = VILLAGER_QUEUE_REGION
        blocked_left, blocked_top, blocked_right, blocked_bottom = BLOCKED_DETECT_REGION

        merged_left = min(queue_left, blocked_left)
        merged_top = min(queue_top, blocked_top)
        merged_right = max(queue_right, blocked_right)
        merged_bottom = max(queue_bottom, blocked_bottom)

        # 一次截图
        merged_img = capture_region_np(merged_left, merged_top, merged_right, merged_bottom)
        merged_gray = cv2.cvtColor(merged_img, cv2.COLOR_BGR2GRAY)

        # 裁剪出队列区域（相对坐标）
        queue_rel_left = queue_left - merged_left
        queue_rel_top = queue_top - merged_top
        queue_rel_right = queue_right - merged_left
        queue_rel_bottom = queue_bottom - merged_top
        queue_screenshot = merged_gray[queue_rel_top:queue_rel_bottom, queue_rel_left:queue_rel_right]

        # 裁剪出遮挡区域（相对坐标）
        blocked_rel_left = blocked_left - merged_left
        blocked_rel_top = blocked_top - merged_top
        blocked_rel_right = blocked_right - merged_left
        blocked_rel_bottom = blocked_bottom - merged_top
        blocked_screenshot = merged_gray[blocked_rel_top:blocked_rel_bottom, blocked_rel_left:blocked_rel_right]

        return queue_screenshot, blocked_screenshot

    def _check_blocked(self, screenshot):
        """
        检测UI是否被遮挡（灰度图匹配）

        状态判断：
        - 置信度 >= BLOCKED_MATCH_THRESHOLD: 完全遮挡
        - 置信度 < BLOCKED_TRANSITION_THRESHOLD: 完全未遮挡
        - 介于两者之间: 渐变状态

        稳定性检测：
        - 所有状态都需要连续2次检测才认为稳定
        - 防止UI快速显隐时的瞬间误判

        渐变误判检测：
        - 如果连续3次检测到"渐变中"，且置信度变化很小（<0.05）
        - 说明不是真正的渐变，而是场景颜色正好在渐变区间
        - 此时强制认为"未遮挡"
        """
        blocked_template = _get_blocked_template()

        log_blocked("模板", f"尺寸={blocked_template.shape[1]}x{blocked_template.shape[0]}")

        result = cv2.matchTemplate(screenshot, blocked_template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, _ = cv2.minMaxLoc(result)
        self.blocked_confidence = round(float(max_val), 4)

        # 第一步：判断原始状态
        if max_val >= BLOCKED_MATCH_THRESHOLD:
            current_state = 'blocked'
            status = '完全遮挡'
        elif max_val < BLOCKED_TRANSITION_THRESHOLD:
            current_state = 'clear'
            status = '未遮挡'
        else:
            current_state = 'transition'
            status = '渐变中'

        # 第二步：稳定性检测
        if current_state == self._last_state:
            self._stable_count += 1
        else:
            self._last_state = current_state
            self._stable_count = 1

        # 如果状态不稳定（快速变化），强制认为"渐变中"
        if self._stable_count < self._stable_threshold:
            self.blocked = False
            self.in_transition = True
            self._transition_count = 0  # 重置渐变误判计数
            status = f'{status}(不稳定{self._stable_count}/{self._stable_threshold})'
            log_blocked("结果", f"置信度={self.blocked_confidence:.4f} 阈值=[{BLOCKED_TRANSITION_THRESHOLD:.2f}, {BLOCKED_MATCH_THRESHOLD:.2f}] 状态={status}")
            return

        # 第三步：状态已稳定，应用原始判断
        if current_state == 'blocked':
            self.blocked = True
            self.in_transition = False
            self._transition_count = 0
        elif current_state == 'clear':
            self.blocked = False
            self.in_transition = False
            self._transition_count = 0
        else:  # transition
            # 渐变误判检测
            confidence_change = abs(self.blocked_confidence - self._last_transition_confidence)
            self._transition_count += 1

            if self._transition_count >= self._transition_threshold and confidence_change < self._confidence_change_threshold:
                # 误判：场景颜色正好在渐变区间
                self.blocked = False
                self.in_transition = False
                status = f'误判修正(连续{self._transition_count}次,变化{confidence_change:.3f})'
                log_blocked("修正", f"检测到渐变误判，强制认为未遮挡")
            else:
                # 真正的渐变
                self.blocked = False
                self.in_transition = True
                status = f'{status}(稳定,{self._transition_count}次,变化{confidence_change:.3f})'

            self._last_transition_confidence = self.blocked_confidence

        log_blocked("结果", f"置信度={self.blocked_confidence:.4f} 阈值=[{BLOCKED_TRANSITION_THRESHOLD:.2f}, {BLOCKED_MATCH_THRESHOLD:.2f}] 状态={status}")

    def _match(self, screenshot):
        """使用灰度图模板匹配检测村民图标"""
        template = _get_template()
        result = cv2.matchTemplate(screenshot, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, _ = cv2.minMaxLoc(result)
        self.confidence = round(float(max_val), 4)
        self.found = max_val >= VILLAGER_MATCH_THRESHOLD

        # 记录置信度历史（用于半透明UI检测）
        self._recent_confidences.append(max_val)
        if len(self._recent_confidences) > self._confidence_history_size:
            self._recent_confidences.pop(0)

        # 半透明UI检测：通过置信度变化模式识别UI渐入渐出动画
        # 当UI叠加在村民图标上时，置信度会快速变化或持续下降
        # 通过检测这些模式，避免在UI动画期间误触发生产
        if len(self._recent_confidences) >= 3:
            min_conf = min(self._recent_confidences)
            max_conf = max(self._recent_confidences)
            confidence_range = max_conf - min_conf

            # 策略1：中等置信度区间 + 快速变化
            # 适用场景：UI正在渐入或渐出，置信度在中等范围内快速波动
            if 0.3 <= max_val < 0.65 and confidence_range > 0.1:
                self.in_transition = True
                self.found = False
                self._semi_transparent_detected = True
                status = f'半透明UI(置信度={self.confidence:.4f},变化={confidence_range:.3f})'
                log_training("检测", status)

                if DEBUG_BLOCKED_DETECTION:
                    try:
                        from PIL import Image
                        import os
                        debug_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "debug_output")
                        os.makedirs(debug_dir, exist_ok=True)
                        debug_path = os.path.join(debug_dir, "villager_semi_transparent.png")
                        img = cv2.cvtColor(screenshot, cv2.COLOR_GRAY2RGB)
                        Image.fromarray(img).save(debug_path)
                        log_training("截图", f"检测到半透明UI，已保存截图到 {debug_path}")
                    except Exception as e:
                        log_training("截图", f"保存失败: {e}")
                return

            # 策略2：置信度突然下降
            # 适用场景：村民图标从清晰可见变为模糊/消失，UI正在渐出动画中
            # 检测条件：当前<0.5，历史最高>0.6，变化幅度>0.2
            if max_val < 0.5 and max_conf > 0.6 and confidence_range > 0.2:
                self.in_transition = True
                self.found = False
                self._semi_transparent_detected = True
                status = f'UI动画中(置信度={self.confidence:.4f},从{max_conf:.3f}降至{max_val:.3f})'
                log_training("检测", status)

                if DEBUG_BLOCKED_DETECTION:
                    try:
                        from PIL import Image
                        import os
                        debug_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "debug_output")
                        os.makedirs(debug_dir, exist_ok=True)
                        debug_path = os.path.join(debug_dir, "villager_ui_animation.png")
                        img = cv2.cvtColor(screenshot, cv2.COLOR_GRAY2RGB)
                        Image.fromarray(img).save(debug_path)
                        log_training("截图", f"检测到UI动画，已保存截图到 {debug_path}")
                    except Exception as e:
                        log_training("截图", f"保存失败: {e}")
                return

            # 策略3：连续下降检测
            # 适用场景：UI正在渐出动画的尾声，置信度持续下降
            # 检测条件：最近3次持续下降（允许小幅波动±0.05），总体下降>0.1，当前<0.4
            if len(self._recent_confidences) >= 3 and max_val < 0.4:
                last_3 = self._recent_confidences[-3:]
                # 检查是否连续下降（允许小幅波动±0.05）
                is_declining = True
                for i in range(len(last_3) - 1):
                    if last_3[i+1] > last_3[i] + 0.05:  # 如果后一个比前一个高出0.05以上
                        is_declining = False
                        break

                if is_declining and (last_3[0] - last_3[-1]) > 0.1:  # 总体下降>0.1
                    self.in_transition = True
                    self.found = False
                    self._semi_transparent_detected = True
                    status = f'UI渐出中(置信度={self.confidence:.4f},连续下降{last_3[0]:.3f}→{last_3[-1]:.3f})'
                    log_training("检测", status)

                    if DEBUG_BLOCKED_DETECTION:
                        try:
                            from PIL import Image
                            import os
                            debug_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "debug_output")
                            os.makedirs(debug_dir, exist_ok=True)
                            debug_path = os.path.join(debug_dir, "villager_fading_out.png")
                            img = cv2.cvtColor(screenshot, cv2.COLOR_GRAY2RGB)
                            Image.fromarray(img).save(debug_path)
                            log_training("截图", f"检测到UI渐出，已保存截图到 {debug_path}")
                        except Exception as e:
                            log_training("截图", f"保存失败: {e}")
                    return

        # 重置半透明UI检测标志
        self._semi_transparent_detected = False

        status = '检测到' if self.found else '未检测到'
        log_training("检测", f"置信度={self.confidence:.4f} 阈值={VILLAGER_MATCH_THRESHOLD:.4f} 状态={status}")
