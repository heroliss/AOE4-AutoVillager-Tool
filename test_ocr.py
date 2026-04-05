"""测试OCR功能"""
from food_reader import FoodReader
from villager_counter import VillagerCounter
from config import FOOD_REGION, VILLAGER_COUNT_REGION
from screenshot_util import capture_region
import numpy as np

print("测试OCR功能...")

# 测试食物识别
print("\n1. 测试食物识别:")
try:
    food_reader = FoodReader()
    food_reader.do()
    print(f"   食物数量: {food_reader.amount}")
except Exception as e:
    print(f"   错误: {e}")
    import traceback
    traceback.print_exc()

# 测试村民计数
print("\n2. 测试村民计数:")
try:
    villager_counter = VillagerCounter()
    villager_counter.do()
    print(f"   村民总数: {villager_counter.total}")
    print(f"   识别数字: {villager_counter.numbers}")
except Exception as e:
    print(f"   错误: {e}")
    import traceback
    traceback.print_exc()

# 测试截图格式
print("\n3. 测试截图格式:")
try:
    left, top, right, bottom = FOOD_REGION
    img = capture_region(left, top, right, bottom)
    img_array = np.array(img)
    print(f"   PIL Image: size={img.size} mode={img.mode}")
    print(f"   Numpy array: shape={img_array.shape} dtype={img_array.dtype}")
    print(f"   前3个像素: {img_array[0, 0:3]}")
except Exception as e:
    print(f"   错误: {e}")
    import traceback
    traceback.print_exc()

print("\n测试完成！")
