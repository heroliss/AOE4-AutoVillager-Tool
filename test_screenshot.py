"""测试截图功能"""
from screenshot_util import capture_region, capture_region_np
from config import FOOD_REGION, TC_ICON_REGION
import cv2

print("测试mss截图功能...")

# 测试capture_region (返回PIL Image)
print("\n1. 测试capture_region (PIL Image):")
try:
    left, top, right, bottom = FOOD_REGION
    img = capture_region(left, top, right, bottom)
    print(f"   食物区域: {img.size} {img.mode}")
    img.save("debug_output/test_food_pil.png")
    print("   保存成功: debug_output/test_food_pil.png")
except Exception as e:
    print(f"   错误: {e}")

# 测试capture_region_np (返回numpy数组)
print("\n2. 测试capture_region_np (numpy BGR):")
try:
    left, top, right, bottom = TC_ICON_REGION
    img = capture_region_np(left, top, right, bottom)
    print(f"   TC区域: {img.shape} {img.dtype}")
    cv2.imwrite("debug_output/test_tc_np.png", img)
    print("   保存成功: debug_output/test_tc_np.png")
except Exception as e:
    print(f"   错误: {e}")

print("\n测试完成！请检查debug_output目录中的截图。")
