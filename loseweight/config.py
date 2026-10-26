"""全局常量、安全边界与活动系数映射。

安全兜底放在代码层（而不是只写在 prompt 里）：
女性日摄入 >= 1200 kcal、男性 >= 1500 kcal、单日缺口上限 750 kcal。
"""
from __future__ import annotations

# 中国 / WHO 亚洲成人 BMI 分级（kg/m²）
BMI_UNDERWEIGHT = 18.5
BMI_NORMAL = 24.0
BMI_OVERWEIGHT = 28.0

BMI_CATEGORY_LABELS = {
    "underweight": "偏瘦",
    "normal": "正常",
    "overweight": "超重",
    "obese": "肥胖",
}

# 安全边界（kcal / 天）
MIN_KCAL_FEMALE = 1200.0
MIN_KCAL_MALE = 1500.0
RECOMMENDED_DEFICIT_MIN = 300.0
RECOMMENDED_DEFICIT_MAX = 500.0
MAX_DAILY_DEFICIT = 750.0

# 活动系数（Mifflin-St Jeor 的 TDEE 乘数）
ACTIVITY_FACTORS = {
    "sedentary": 1.2,   # 久坐
    "light": 1.375,     # 轻度活动
    "moderate": 1.55,   # 中度活动
    "active": 1.725,    # 高度活动
}
DEFAULT_ACTIVITY_LEVEL = "sedentary"

MEAL_TYPES = ("breakfast", "lunch", "dinner", "snack")
MEAL_TYPE_LABELS = {
    "breakfast": "早餐",
    "lunch": "午餐",
    "dinner": "晚餐",
    "snack": "零食",
}

# 宏量营养素：蛋白质按体重 1.6–2.2 g/kg，默认取 1.8 g/kg
PROTEIN_PER_KG = 1.8
PROTEIN_MIN_PER_KG = 1.6
PROTEIN_MAX_PER_KG = 2.2
FAT_RATIO = 0.25  # 脂肪占总热量比例

# 放纵餐：日常目标基础上增加 10–15%
CHEAT_EXTRA_MIN = 0.10
CHEAT_EXTRA_MAX = 0.15
CHEAT_WEEKLY_LIMIT = 1
CHEAT_INTERVAL_BMI_THRESHOLD = 24.0
CHEAT_INTERVAL_FREQUENT_DAYS = 7   # BMI >= 24：每周 1 次
CHEAT_INTERVAL_INFREQUENT_DAYS = 10  # BMI < 24：每 10 天 1 次
