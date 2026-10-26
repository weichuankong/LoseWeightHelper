"""确定性计算：BMI / BMR / TDEE / 热量目标 / 宏量目标 / 餐次推断。

全部为纯函数，不依赖数据库，方便单元测试。
"""
from __future__ import annotations

from . import config


def compute_bmi(weight_kg: float, height_cm: float) -> dict:
    """返回 BMI 值与亚洲成人分类。"""
    height_m = height_cm / 100.0
    if height_m <= 0:
        raise ValueError("身高必须大于 0")
    if weight_kg <= 0:
        raise ValueError("体重必须大于 0")

    bmi = weight_kg / (height_m * height_m)
    if bmi < config.BMI_UNDERWEIGHT:
        category = "underweight"
    elif bmi < config.BMI_NORMAL:
        category = "normal"
    elif bmi < config.BMI_OVERWEIGHT:
        category = "overweight"
    else:
        category = "obese"

    return {
        "bmi": round(bmi, 1),
        "category": category,
        "category_label": config.BMI_CATEGORY_LABELS[category],
        "height_m": round(height_m, 2),
    }


def compute_bmr(weight_kg: float, height_cm: float, age: int, sex: str) -> float:
    """Mifflin-St Jeor 基础代谢率（kcal/天）。"""
    sex = sex.lower()
    if sex == "male":
        bmr = 10 * weight_kg + 6.25 * height_cm - 5 * age + 5
    elif sex == "female":
        bmr = 10 * weight_kg + 6.25 * height_cm - 5 * age - 161
    else:
        raise ValueError(f"未知性别: {sex!r}，应为 male / female")
    return round(bmr, 1)


def compute_tdee(bmr: float, activity_level: str) -> float:
    """TDEE = BMR × 活动系数。"""
    factor = config.ACTIVITY_FACTORS.get(
        activity_level, config.ACTIVITY_FACTORS[config.DEFAULT_ACTIVITY_LEVEL]
    )
    return round(bmr * factor, 1)


def min_safe_kcal(sex: str) -> float:
    """按性别返回每日摄入下限（安全边界）。"""
    sex = sex.lower()
    return config.MIN_KCAL_FEMALE if sex == "female" else config.MIN_KCAL_MALE


def adjust_target(
    tdee: float,
    sex: str,
    current_weight_kg: float,
    goal_weight_kg: float,
) -> dict:
    """根据目标体重方向计算每日热量目标，并施加安全边界。

    减脂：TDEE - 500 kcal（缺口不超 750），但目标不低于性别下限；
    增重：TDEE + 300 kcal；维持：TDEE。
    """
    floor = min_safe_kcal(sex)
    diff = current_weight_kg - goal_weight_kg

    deficit = 0.0
    if diff > 1.0:
        goal = "lose"
        deficit = min(config.RECOMMENDED_DEFICIT_MAX, config.MAX_DAILY_DEFICIT)
        target = tdee - deficit
    elif diff < -1.0:
        goal = "gain"
        target = tdee + 300.0
    else:
        goal = "maintain"
        target = tdee

    target = max(target, floor)

    return {
        "goal": goal,
        "target_kcal": round(target),
        "deficit_kcal": round(deficit, 1) if goal == "lose" else 0.0,
        "floor_kcal": floor,
        "tdee": round(tdee, 1),
    }


def compute_macro_targets(weight_kg: float, target_kcal: float) -> dict:
    """计算每日宏量营养素目标。

    蛋白质按体重 1.6–2.2 g/kg（取 1.8），脂肪 25% 热量，碳水补足。
    """
    protein_per_kg = min(
        max(config.PROTEIN_PER_KG, config.PROTEIN_MIN_PER_KG),
        config.PROTEIN_MAX_PER_KG,
    )
    protein_g = round(protein_per_kg * weight_kg, 1)
    fat_g = round(target_kcal * config.FAT_RATIO / 9.0, 1)
    carb_g = round((target_kcal - protein_g * 4 - fat_g * 9) / 4.0, 1)
    if carb_g < 0:
        carb_g = 0.0

    return {"protein_g": protein_g, "fat_g": fat_g, "carb_g": carb_g}


def infer_meal_type(hour: int) -> str:
    """按时段推断餐次（区间无重叠，修正 README 的 11–12 点歧义）。

    4–10 点早餐，11–14 点午餐，15–16 点下午加餐，17–20 点晚餐，其余零食。
    """
    if 4 <= hour < 11:
        return "breakfast"
    if 11 <= hour < 15:
        return "lunch"
    if 15 <= hour < 17:
        return "snack"
    if 17 <= hour < 21:
        return "dinner"
    return "snack"
