"""业务服务层：把确定性计算与数据访问组合成可用的工具。

对应 README 中的工具：
log_meal / update_weight / get_daily_summary / generate_meal_plan /
generate_recovery_plan / plan_cheat_meal
"""
from __future__ import annotations

from datetime import datetime, timedelta

from . import calculations as calc
from . import config
from . import foods as foods_module
from . import repository as repo


# ---------------------------------------------------------------- 用户档案

def ensure_profile(
    conn,
    user_id: str,
    *,
    sex: str,
    height_cm: float,
    age: int,
    activity_level: str,
    goal_weight_kg: float,
    name: str | None = None,
) -> dict:
    """创建/更新用户档案，并做输入校验。"""
    sex = "male" if str(sex).lower() in ("male", "m", "男") else "female"
    if height_cm <= 0:
        raise ValueError("身高必须大于 0")
    if age <= 0 or age > 120:
        raise ValueError("年龄需在 1–120 之间")
    if activity_level not in config.ACTIVITY_FACTORS:
        raise ValueError(f"活动水平应为 {sorted(config.ACTIVITY_FACTORS)} 之一")

    return repo.save_profile(
        conn,
        user_id,
        sex=sex,
        height_cm=height_cm,
        age=age,
        activity_level=activity_level,
        goal_weight_kg=goal_weight_kg,
        name=name,
    )


# ---------------------------------------------------------------- 体重更新

def update_weight(conn, user_id: str, *, weight_kg: float, log_date: str | None = None) -> dict:
    """记录体重，联动重算 BMI / BMR / TDEE / 建议目标 / 宏量目标。"""
    log_date = log_date or repo.today()
    profile = repo.get_profile(conn, user_id)
    if profile is None:
        raise ValueError("尚未创建用户档案，请先完成初始化（setup）")

    bmi = calc.compute_bmi(weight_kg, profile["height_cm"])
    bmr = calc.compute_bmr(weight_kg, profile["height_cm"], profile["age"], profile["sex"])
    tdee = calc.compute_tdee(bmr, profile["activity_level"])

    goal_weight = profile.get("goal_weight_kg")
    if goal_weight is None:
        goal_weight = weight_kg
    target = calc.adjust_target(tdee, profile["sex"], weight_kg, goal_weight)
    macros = calc.compute_macro_targets(weight_kg, target["target_kcal"])

    repo.insert_weight(
        conn, user_id, log_date, weight_kg, bmi["bmi"], bmi["category"],
        bmr, tdee, target["target_kcal"],
    )
    repo.update_daily_targets(
        conn, user_id, target["target_kcal"],
        macros["protein_g"], macros["fat_g"], macros["carb_g"],
    )

    return {
        "weight_kg": weight_kg,
        "date": log_date,
        "bmi": bmi,
        "bmr": bmr,
        "tdee": tdee,
        "target": target,
        "macros": macros,
    }


def update_goal(conn, user_id: str, *, goal_weight_kg: float) -> dict:
    """修改目标体重，并用最新体重联动重算建议目标 / 宏量目标。"""
    if goal_weight_kg <= 0:
        raise ValueError("目标体重必须大于 0")
    profile = repo.get_profile(conn, user_id)
    if profile is None:
        raise ValueError("尚未创建用户档案，请先完成初始化（setup）")

    latest = repo.latest_weight(conn, user_id)
    if latest is None:
        raise ValueError("还没有体重记录，请先报一次当前体重")

    current_weight = latest["weight_kg"]
    repo.update_goal_weight(conn, user_id, goal_weight_kg)

    bmr = calc.compute_bmr(current_weight, profile["height_cm"], profile["age"], profile["sex"])
    tdee = calc.compute_tdee(bmr, profile["activity_level"])
    target = calc.adjust_target(tdee, profile["sex"], current_weight, goal_weight_kg)
    macros = calc.compute_macro_targets(current_weight, target["target_kcal"])
    repo.update_daily_targets(
        conn, user_id, target["target_kcal"],
        macros["protein_g"], macros["fat_g"], macros["carb_g"],
    )

    return {
        "goal_weight_kg": goal_weight_kg,
        "current_weight_kg": current_weight,
        "target": target,
        "macros": macros,
    }


def set_preferences(conn, user_id: str, preferences: list[str]) -> dict:
    """追加偏好食物（只保留食物库中真实存在的规范名，与已有偏好合并去重）。"""
    profile = repo.get_profile(conn, user_id)
    if profile is None:
        raise ValueError("尚未创建用户档案，请先完成初始化（setup）")

    valid = []
    for name in preferences:
        if foods_module.find_food_by_name(conn, name) and name not in valid:
            valid.append(name)
    merged = list(dict.fromkeys([*(profile.get("preferences") or []), *valid]))
    repo.update_preferences(conn, user_id, merged)
    return {"preferences": merged, "added": valid}


# ---------------------------------------------------------------- 饮食记录

def log_meal(
    conn,
    user_id: str,
    *,
    meal_type: str,
    food_id: int,
    grams: float,
    log_date: str | None = None,
    is_estimated: bool = False,
) -> dict:
    """写入一条饮食记录，返回该条记录与当日汇总。"""
    log_date = log_date or repo.today()
    if meal_type not in config.MEAL_TYPES:
        raise ValueError(f"餐次应为 {config.MEAL_TYPES} 之一")

    food = foods_module.get_food(conn, food_id)
    if food is None:
        raise ValueError(f"未知食物 id: {food_id}")
    nutrition = foods_module.get_nutrition(conn, food_id, grams)

    duplicate_id = repo.find_duplicate_meal(conn, user_id, log_date, meal_type, food_id, grams)
    entry_id = repo.insert_meal(
        conn, user_id, log_date, meal_type, food_id, food["name"], grams,
        nutrition["kcal"], nutrition["protein_g"], nutrition["fat_g"],
        nutrition["carb_g"], is_estimated,
    )

    entry = {**nutrition, "id": entry_id, "meal_type": meal_type,
             "is_estimated": is_estimated}
    summary = get_daily_summary(conn, user_id, log_date)
    return {"entry": entry, "summary": summary, "duplicate": duplicate_id is not None}


def log_estimated_meal(
    conn,
    user_id: str,
    *,
    meal_type: str,
    kcal: float,
    label: str = "轻食",
    log_date: str | None = None,
) -> dict:
    """按估算热量记录一餐（无需具体食物，用于难以精确计算热量时）。

    只记录热量，宏量营养素按 0 计（未知），并标记 is_estimated=1。
    """
    log_date = log_date or repo.today()
    if meal_type not in config.MEAL_TYPES:
        raise ValueError(f"餐次应为 {config.MEAL_TYPES} 之一")
    if kcal <= 0:
        raise ValueError("估算热量必须大于 0")

    label = (label or "轻食").strip()
    food_name = f"{label}（估算）"
    entry_id = repo.insert_meal(
        conn, user_id, log_date, meal_type, None, food_name, 0.0,
        round(float(kcal), 1), 0.0, 0.0, 0.0, True,
    )
    entry = {
        "id": entry_id, "meal_type": meal_type, "name": food_name,
        "grams": 0.0, "kcal": round(float(kcal), 1),
        "protein_g": 0.0, "fat_g": 0.0, "carb_g": 0.0,
        "is_estimated": True,
    }
    summary = get_daily_summary(conn, user_id, log_date)
    return {"entry": entry, "summary": summary, "duplicate": False}


# ---------------------------------------------------------------- 更正饮食记录

def undo_last_meal(conn, user_id: str) -> dict:
    """撤销最近一条饮食记录。"""
    meal = repo.latest_meal(conn, user_id)
    if meal is None:
        raise ValueError("还没有饮食记录可以撤销")
    repo.delete_meal(conn, meal["id"])
    return {"deleted": meal, "summary": get_daily_summary(conn, user_id, meal["log_date"])}


def delete_meal_entry(
    conn,
    user_id: str,
    *,
    meal_type: str | None = None,
    food_id: int | None = None,
    log_date: str | None = None,
) -> dict:
    """删除今天（或指定日期）匹配餐次/食物的最近一条记录。"""
    log_date = log_date or repo.today()
    meals = repo.get_meals_for_date(conn, user_id, log_date)
    candidates = [
        m for m in meals
        if (meal_type is None or m["meal_type"] == meal_type)
        and (food_id is None or m["food_id"] == food_id)
    ]
    if not candidates:
        raise ValueError("没有找到匹配的饮食记录")
    target = candidates[-1]
    repo.delete_meal(conn, target["id"])
    return {
        "deleted": target,
        "match_count": len(candidates),
        "summary": get_daily_summary(conn, user_id, log_date),
    }


def edit_meal_entry(
    conn,
    user_id: str,
    *,
    food_id: int,
    new_grams: float | None = None,
    new_food_id: int | None = None,
    log_date: str | None = None,
) -> dict:
    """修改今天（或指定日期）最近一条指定食物记录：改份量或换成另一食物。"""
    log_date = log_date or repo.today()
    meals = [m for m in repo.get_meals_for_date(conn, user_id, log_date)
             if m["food_id"] == food_id]
    if not meals:
        raise ValueError("没有找到该食物的记录，无法修改")
    target = meals[-1]

    if new_food_id is not None and new_food_id != food_id:
        new_food = foods_module.get_food(conn, new_food_id)
        if new_food is None:
            raise ValueError(f"未知食物 id: {new_food_id}")
        grams = new_grams
        if grams is None:
            old_food = foods_module.get_food(conn, food_id)
            if old_food and abs(target["grams"] - float(old_food["default_grams"])) < 1e-6:
                grams = float(new_food["default_grams"])
            else:
                grams = target["grams"]
        nutrition = foods_module.get_nutrition(conn, new_food_id, grams)
        repo.update_meal(
            conn, target["id"], food_id=new_food["food_id"], food_name=new_food["name"],
            grams=nutrition["grams"], kcal=nutrition["kcal"],
            protein_g=nutrition["protein_g"], fat_g=nutrition["fat_g"],
            carb_g=nutrition["carb_g"],
        )
    elif new_grams is not None:
        if new_grams <= 0:
            raise ValueError("份量必须大于 0")
        nutrition = foods_module.get_nutrition(conn, food_id, new_grams)
        repo.update_meal(
            conn, target["id"], food_id=food_id, food_name=nutrition["name"],
            grams=nutrition["grams"], kcal=nutrition["kcal"],
            protein_g=nutrition["protein_g"], fat_g=nutrition["fat_g"],
            carb_g=nutrition["carb_g"],
        )
    else:
        raise ValueError("请说明要改成的新份量（如「改成125g」）或新食物（如「改成鸡胸肉」）")

    return {
        "before": target,
        "updated": repo.get_meal(conn, target["id"]),
        "summary": get_daily_summary(conn, user_id, log_date),
    }



# ---------------------------------------------------------------- 当日汇总

def get_daily_summary(conn, user_id: str, log_date: str | None = None) -> dict:
    log_date = log_date or repo.today()
    meals = repo.get_meals_for_date(conn, user_id, log_date)
    profile = repo.get_profile(conn, user_id)

    totals = {"kcal": 0.0, "protein_g": 0.0, "fat_g": 0.0, "carb_g": 0.0}
    by_meal = {mt: {"kcal": 0.0, "count": 0} for mt in config.MEAL_TYPES}
    for m in meals:
        mt = m["meal_type"] if m["meal_type"] in by_meal else "snack"
        totals["kcal"] += m["kcal"]
        totals["protein_g"] += m["protein_g"]
        totals["fat_g"] += m["fat_g"]
        totals["carb_g"] += m["carb_g"]
        by_meal[mt]["kcal"] += m["kcal"]
        by_meal[mt]["count"] += 1

    target_kcal = (profile or {}).get("daily_target_kcal")
    target_protein = (profile or {}).get("daily_protein_g")
    target_fat = (profile or {}).get("daily_fat_g")
    target_carb = (profile or {}).get("daily_carb_g")

    def remaining(target, consumed):
        return round(target - consumed, 1) if target is not None else None

    return {
        "date": log_date,
        "target_kcal": target_kcal,
        "target_protein_g": target_protein,
        "target_fat_g": target_fat,
        "target_carb_g": target_carb,
        "total_kcal": round(totals["kcal"], 1),
        "total_protein_g": round(totals["protein_g"], 1),
        "total_fat_g": round(totals["fat_g"], 1),
        "total_carb_g": round(totals["carb_g"], 1),
        "remaining_kcal": remaining(target_kcal, totals["kcal"]),
        "remaining_protein_g": remaining(target_protein, totals["protein_g"]),
        "remaining_fat_g": remaining(target_fat, totals["fat_g"]),
        "remaining_carb_g": remaining(target_carb, totals["carb_g"]),
        "by_meal": {k: {"kcal": round(v["kcal"], 1), "count": v["count"]}
                    for k, v in by_meal.items()},
        "meals": meals,
    }


# ---------------------------------------------------------------- 食谱生成

def _rank_foods(conn, category: str, profile: dict, recent_names: set[str]) -> list[dict]:
    """对某类食物排序，返回优先级从高到低的候选列表。

    规则：偏好食物优先（+1000）；最近推荐过的靠后（-100，可重复但尽量避开）；
    同档按营养密度排序（蛋白质=蛋白/热量比，其余=低热量优先）。
    """
    rows = [dict(r) for r in conn.execute("SELECT * FROM food WHERE category = ?", (category,)).fetchall()]

    def base(name: str) -> str:
        return name.split("（")[0] if "（" in name else name

    restrictions = set(profile.get("restrictions") or [])
    prefs = set(profile.get("preferences") or [])
    rows = [r for r in rows
            if r["name"] not in restrictions and base(r["name"]) not in restrictions]

    def is_pref(f: dict) -> bool:
        return f["name"] in prefs or base(f["name"]) in prefs

    def is_recent(f: dict) -> bool:
        return f["name"] in recent_names or base(f["name"]) in recent_names

    def metric(f: dict) -> float:
        if category == "protein":
            return f["protein_g"] / max(f["energy_kcal"], 1e-9)
        return 1.0 / max(f["energy_kcal"], 1e-9)

    def score(f: dict) -> float:
        s = metric(f)
        if is_pref(f):
            s += 1000.0
        if is_recent(f):
            s -= 100.0
        return s

    return sorted(rows, key=score, reverse=True)


def _grams_for_kcal(food: dict, kcal_budget: float, min_g: float = 40, max_g: float = 400) -> float:
    if food["energy_kcal"] <= 0:
        return food["default_grams"]
    grams = kcal_budget / food["energy_kcal"] * 100
    return round(max(min_g, min(max_g, grams)))


def _grams_for_protein(food: dict, protein_needed: float, min_g: float = 50, max_g: float = 300) -> float:
    if food["protein_g"] <= 0:
        return min_g
    grams = protein_needed / food["protein_g"] * 100
    return round(max(min_g, min(max_g, grams)))


def _food_portion(food: dict, grams: float) -> dict:
    factor = grams / 100.0
    return {
        "name": food["name"],
        "category": food["category"],
        "grams": round(grams, 1),
        "kcal": round(food["energy_kcal"] * factor, 1),
        "protein_g": round(food["protein_g"] * factor, 1),
        "fat_g": round(food["fat_g"] * factor, 1),
        "carb_g": round(food["carb_g"] * factor, 1),
    }


def _build_plan(remaining_kcal: float, remaining_protein_g: float, picks: dict) -> dict:
    """由指定「蛋白质 / 主食 / 蔬菜」三元组构造一餐方案。"""
    protein_food = picks.get("protein")
    carb_food = picks.get("staple")
    veg_food = picks.get("vegetable")

    items = []
    total = {"kcal": 0.0, "protein_g": 0.0, "fat_g": 0.0, "carb_g": 0.0}

    def add(item):
        items.append(item)
        for k in ("kcal", "protein_g", "fat_g", "carb_g"):
            total[k] += item[k]

    if protein_food:
        add(_food_portion(protein_food, _grams_for_protein(protein_food, remaining_protein_g)))
    if veg_food:
        add(_food_portion(veg_food, veg_food["default_grams"] * 2))
    if carb_food:
        left_kcal = max(0.0, remaining_kcal - total["kcal"])
        add(_food_portion(carb_food, _grams_for_kcal(carb_food, left_kcal, min_g=40, max_g=200)))

    total = {k: round(v, 1) for k, v in total.items()}
    parts = "、".join(f"{it['name']} {it['grams']:.0f}g" for it in items)
    return {
        "items": items,
        "total": total,
        "message": f"{parts}。合计约 {total['kcal']:.0f} kcal，蛋白质 {total['protein_g']:.0f}g。",
    }


def generate_meal_plan(
    conn,
    user_id: str,
    *,
    meal_type: str = "dinner",
    remaining_kcal: float | None = None,
    remaining_protein_g: float | None = None,
    n: int = 3,
) -> dict:
    """基于剩余热量 / 剩余蛋白质生成多套（默认 3 套）互不相同的一餐组合，并落库。

    选取规则：
    1. 偏好食物优先（profile.preferences 里的食物排在各类别最前）；
    2. 最近推荐过的食物靠后，尽量不重复（可重复，但被压低优先级）；
    3. 各套方案在蛋白质 / 主食 / 蔬菜上取不同名次（轮换），尽量互不相同。
    """
    profile = repo.get_profile(conn, user_id)
    if profile is None:
        raise ValueError("尚未创建用户档案，请先完成初始化（setup）")

    if remaining_kcal is None or remaining_protein_g is None:
        summary = get_daily_summary(conn, user_id)
        remaining_kcal = summary["remaining_kcal"]
        remaining_protein_g = summary["remaining_protein_g"]
    if remaining_kcal is None:
        remaining_kcal = profile.get("daily_target_kcal") or 1500.0
    if remaining_protein_g is None:
        remaining_protein_g = profile.get("daily_protein_g") or 60.0

    if remaining_kcal <= 0:
        result = {
            "type": "meal_plan", "meal_type": meal_type,
            "remaining_kcal": round(remaining_kcal, 1),
            "remaining_protein_g": round(remaining_protein_g, 1),
            "message": "今日热量已用尽，建议只补充低热量蔬菜并多喝水，不要强行加餐。",
            "plans": [],
        }
        repo.insert_plan(conn, user_id, "meal_plan", repo.today(), result)
        return result

    recent_names = set(repo.recent_meal_plan_foods(conn, user_id, limit=3))
    ranked = {
        "protein": _rank_foods(conn, "protein", profile, recent_names),
        "staple": _rank_foods(conn, "staple", profile, recent_names),
        "vegetable": _rank_foods(conn, "vegetable", profile, recent_names),
    }

    plans = []
    for i in range(n):
        picks = {
            cat: ranked[cat][i % len(ranked[cat])] if ranked[cat] else None
            for cat in ("protein", "staple", "vegetable")
        }
        plans.append(_build_plan(remaining_kcal, remaining_protein_g, picks))

    result = {
        "type": "meal_plan",
        "meal_type": meal_type,
        "remaining_kcal": round(remaining_kcal, 1),
        "remaining_protein_g": round(remaining_protein_g, 1),
        "plans": plans,
    }
    repo.insert_plan(conn, user_id, "meal_plan", repo.today(), result)
    return result


# ---------------------------------------------------------------- 补救措施

def generate_recovery_plan(conn, user_id: str, log_date: str | None = None) -> dict:
    log_date = log_date or repo.today()
    summary = get_daily_summary(conn, user_id, log_date)
    remaining = summary["remaining_kcal"]
    over_kcal = max(0.0, -(remaining or 0.0))

    if over_kcal <= 0:
        plan = {"type": "recovery_plan", "level": "none",
                "message": "今日热量未超标，无需补救，继续保持即可。"}
    elif over_kcal <= 300:
        plan = {"type": "recovery_plan", "level": "light",
                "message": "轻度超标：下一餐主食减少约 1/3、增加蔬菜体积，蛋白质保持不变即可，无需惩罚性节食。"}
    elif over_kcal <= 500:
        plan = {"type": "recovery_plan", "level": "moderate",
                "message": "中度超标：下一餐采用「蛋白质 + 大量蔬菜 + 极少量慢碳」结构，不要跳过正餐。"}
    else:
        plan = {"type": "recovery_plan", "level": "heavy",
                "message": "明显超标：未来 3 天每日额外增加 100–150 kcal 活动消耗，或每日饮食减少 100–150 kcal，把多余热量分摊到多日，不建议极端节食。"}
    plan["over_kcal"] = round(over_kcal, 1)
    repo.insert_plan(conn, user_id, "recovery_plan", log_date, plan)
    return plan


def edema_advice() -> dict:
    """高盐/高油导致“感觉肿了”的场景：区分水肿与脂肪增加。"""
    return {
        "type": "recovery_plan",
        "level": "edema",
        "message": "高盐饮食导致的水肿通常在 1–3 天内自然代谢，这不是长胖。建议次日增加饮水、多吃钾含量高的蔬菜（如菠菜、番茄），而不是减少饮水。",
    }


# ---------------------------------------------------------------- 放纵餐

def plan_cheat_meal(conn, user_id: str, log_date: str | None = None) -> dict:
    log_date = log_date or repo.today()
    profile = repo.get_profile(conn, user_id)
    if profile is None:
        raise ValueError("尚未创建用户档案，请先完成初始化（setup）")

    latest_w = repo.latest_weight(conn, user_id)
    bmi = latest_w["bmi"] if latest_w else None
    daily_target = profile.get("daily_target_kcal") or 1500.0

    extra_min = daily_target * config.CHEAT_EXTRA_MIN
    extra_max = daily_target * config.CHEAT_EXTRA_MAX

    last = repo.latest_cheat_meal(conn, user_id)
    interval = (
        config.CHEAT_INTERVAL_FREQUENT_DAYS
        if bmi is not None and bmi >= config.CHEAT_INTERVAL_BMI_THRESHOLD
        else config.CHEAT_INTERVAL_INFREQUENT_DAYS
    )
    next_date = _next_available_date(log_date, last["planned_date"] if last else None, interval)

    plan = {
        "type": "cheat_meal_plan",
        "planned_date": next_date,
        "budget_kcal": [round(daily_target + extra_min), round(daily_target + extra_max)],
        "time": "训练后 2 小时内，或傍晚 17–19 点",
        "structure": "保留蛋白质和复合碳水的基础框架，避免纯糖脂组合",
        "frequency_note": "每周不超过 1 次，每月不超过 4 次",
        "message": (
            f"建议 {next_date} 安排放纵餐，热量控制在 "
            f"{round(daily_target + extra_min)}–{round(daily_target + extra_max)} kcal "
            f"（日常目标 {daily_target:.0f} kcal 的 10–15% 额外额度）。"
            f"这一餐之后不需要额外惩罚，正常回到原计划即可。"
        ),
    }
    repo.insert_cheat_meal(
        conn, user_id, next_date, round(daily_target + extra_max), plan["message"]
    )
    repo.insert_plan(conn, user_id, "cheat_meal", log_date, plan)
    return plan


def _next_available_date(log_date: str, last_cheat_date: str | None, interval_days: int) -> str:
    today_d = datetime.strptime(log_date, "%Y-%m-%d")
    if last_cheat_date:
        last_d = datetime.strptime(last_cheat_date, "%Y-%m-%d")
        eligible = last_d + timedelta(days=interval_days)
        if eligible > today_d:
            return eligible.strftime("%Y-%m-%d")
    return (today_d + timedelta(days=3)).strftime("%Y-%m-%d")
