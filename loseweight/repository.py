"""数据访问层：对 SQLite 的增删查。"""
from __future__ import annotations

import json
from datetime import datetime


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today() -> str:
    """本地时区的今日日期（YYYY-MM-DD）。"""
    return datetime.now().strftime("%Y-%m-%d")


# ---------------------------------------------------------------- 用户档案

def save_profile(
    conn,
    user_id: str,
    *,
    sex: str,
    height_cm: float,
    age: int,
    activity_level: str,
    goal_weight_kg: float | None = None,
    name: str | None = None,
    daily_target_kcal: float | None = None,
    daily_protein_g: float | None = None,
    daily_fat_g: float | None = None,
    daily_carb_g: float | None = None,
    preferences: list[str] | None = None,
    restrictions: list[str] | None = None,
) -> dict:
    preferences_json = json.dumps(preferences or [], ensure_ascii=False)
    restrictions_json = json.dumps(restrictions or [], ensure_ascii=False)
    now = _now()
    conn.execute(
        """
        INSERT INTO user_profile
          (user_id, name, sex, height_cm, age, activity_level, goal_weight_kg,
           daily_target_kcal, daily_protein_g, daily_fat_g, daily_carb_g,
           preferences_json, restrictions_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
          name = excluded.name,
          sex = excluded.sex,
          height_cm = excluded.height_cm,
          age = excluded.age,
          activity_level = excluded.activity_level,
          goal_weight_kg = excluded.goal_weight_kg,
          daily_target_kcal = excluded.daily_target_kcal,
          daily_protein_g = excluded.daily_protein_g,
          daily_fat_g = excluded.daily_fat_g,
          daily_carb_g = excluded.daily_carb_g,
          preferences_json = excluded.preferences_json,
          restrictions_json = excluded.restrictions_json,
          updated_at = excluded.updated_at
        """,
        (
            user_id, name, sex, height_cm, age, activity_level, goal_weight_kg,
            daily_target_kcal, daily_protein_g, daily_fat_g, daily_carb_g,
            preferences_json, restrictions_json, now, now,
        ),
    )
    return get_profile(conn, user_id)


def get_profile(conn, user_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM user_profile WHERE user_id = ?", (user_id,)).fetchone()
    if row is None:
        return None
    p = dict(row)
    p["preferences"] = json.loads(p.pop("preferences_json") or "[]")
    p["restrictions"] = json.loads(p.pop("restrictions_json") or "[]")
    return p


def update_daily_targets(
    conn,
    user_id: str,
    kcal: float,
    protein_g: float,
    fat_g: float,
    carb_g: float,
) -> None:
    conn.execute(
        """UPDATE user_profile
           SET daily_target_kcal = ?, daily_protein_g = ?, daily_fat_g = ?,
               daily_carb_g = ?, updated_at = ?
           WHERE user_id = ?""",
        (kcal, protein_g, fat_g, carb_g, _now(), user_id),
    )


def update_goal_weight(conn, user_id: str, goal_weight_kg: float) -> None:
    conn.execute(
        "UPDATE user_profile SET goal_weight_kg = ?, updated_at = ? WHERE user_id = ?",
        (goal_weight_kg, _now(), user_id),
    )


def update_preferences(conn, user_id: str, preferences: list[str]) -> None:
    """覆盖写入偏好食物（规范化名称列表）。"""
    conn.execute(
        "UPDATE user_profile SET preferences_json = ?, updated_at = ? WHERE user_id = ?",
        (json.dumps(preferences or [], ensure_ascii=False), _now(), user_id),
    )


# ---------------------------------------------------------------- 饮食日志

def insert_meal(
    conn,
    user_id: str,
    log_date: str,
    meal_type: str,
    food_id: int | None,
    food_name: str,
    grams: float,
    kcal: float,
    protein_g: float,
    fat_g: float,
    carb_g: float,
    is_estimated: bool,
) -> int:
    cur = conn.execute(
        """INSERT INTO meal_log
           (user_id, log_date, meal_type, food_id, food_name, grams, kcal,
            protein_g, fat_g, carb_g, is_estimated, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (user_id, log_date, meal_type, food_id, food_name, grams, kcal,
         protein_g, fat_g, carb_g, int(is_estimated), _now()),
    )
    return cur.lastrowid


def get_meals_for_date(conn, user_id: str, log_date: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM meal_log WHERE user_id = ? AND log_date = ? ORDER BY created_at, id",
        (user_id, log_date),
    ).fetchall()
    return [dict(r) for r in rows]


def find_duplicate_meal(
    conn, user_id: str, log_date: str, meal_type: str, food_id: int, grams: float
) -> int | None:
    row = conn.execute(
        """SELECT id FROM meal_log
           WHERE user_id = ? AND log_date = ? AND meal_type = ? AND food_id = ?
             AND abs(grams - ?) < 0.01
           ORDER BY id DESC LIMIT 1""",
        (user_id, log_date, meal_type, food_id, grams),
    ).fetchone()
    return row["id"] if row else None


def get_meal(conn, meal_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM meal_log WHERE id = ?", (meal_id,)).fetchone()
    return dict(row) if row else None


def latest_meal(conn, user_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM meal_log WHERE user_id = ? ORDER BY id DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    return dict(row) if row else None


def delete_meal(conn, meal_id: int) -> None:
    conn.execute("DELETE FROM meal_log WHERE id = ?", (meal_id,))


def update_meal(
    conn,
    meal_id: int,
    *,
    food_id: int | None,
    food_name: str,
    grams: float,
    kcal: float,
    protein_g: float,
    fat_g: float,
    carb_g: float,
) -> None:
    conn.execute(
        """UPDATE meal_log
           SET food_id = ?, food_name = ?, grams = ?, kcal = ?,
               protein_g = ?, fat_g = ?, carb_g = ?
           WHERE id = ?""",
        (food_id, food_name, grams, kcal, protein_g, fat_g, carb_g, meal_id),
    )



# ---------------------------------------------------------------- 体重日志

def insert_weight(
    conn,
    user_id: str,
    log_date: str,
    weight_kg: float,
    bmi: float | None,
    bmi_category: str | None,
    bmr: float | None,
    tdee: float | None,
    suggested_target_kcal: float | None,
) -> int:
    cur = conn.execute(
        """INSERT INTO weight_log
           (user_id, log_date, weight_kg, bmi, bmi_category, bmr, tdee,
            suggested_target_kcal, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (user_id, log_date, weight_kg, bmi, bmi_category, bmr, tdee,
         suggested_target_kcal, _now()),
    )
    return cur.lastrowid


def latest_weight(conn, user_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM weight_log WHERE user_id = ? ORDER BY log_date DESC, id DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    return dict(row) if row else None


def weight_history(conn, user_id: str, limit: int = 30) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM weight_log WHERE user_id = ? ORDER BY log_date DESC, id DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()
    return list(reversed([dict(r) for r in rows]))


# ---------------------------------------------------------------- 对话历史

def insert_message(conn, user_id: str, session_id: str, role: str, content: str) -> int:
    cur = conn.execute(
        "INSERT INTO conversation (user_id, session_id, role, content, created_at) VALUES (?, ?, ?, ?, ?)",
        (user_id, session_id, role, content, _now()),
    )
    return cur.lastrowid


def recent_messages(conn, session_id: str, limit: int = 20) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM conversation WHERE session_id = ? ORDER BY id DESC LIMIT ?",
        (session_id, limit),
    ).fetchall()
    return list(reversed([dict(r) for r in rows]))


# ---------------------------------------------------------------- 长期记忆

def insert_memory(
    conn,
    user_id: str,
    memory_type: str,
    content: str,
    relevance_score: float = 1.0,
    expires_at: str | None = None,
) -> int:
    cur = conn.execute(
        """INSERT INTO agent_memory
           (user_id, memory_type, content, relevance_score, created_at, expires_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (user_id, memory_type, content, relevance_score, _now(), expires_at),
    )
    return cur.lastrowid


def list_memories(
    conn, user_id: str, memory_type: str | None = None, limit: int = 20
) -> list[dict]:
    if memory_type:
        rows = conn.execute(
            "SELECT * FROM agent_memory WHERE user_id = ? AND memory_type = ? ORDER BY id DESC LIMIT ?",
            (user_id, memory_type, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM agent_memory WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- 计划与放纵餐

def insert_plan(conn, user_id: str, plan_type: str, plan_date: str, content: dict) -> int:
    cur = conn.execute(
        "INSERT INTO plan_log (user_id, plan_type, plan_date, content_json, created_at) VALUES (?, ?, ?, ?, ?)",
        (user_id, plan_type, plan_date, json.dumps(content, ensure_ascii=False), _now()),
    )
    return cur.lastrowid


def recent_meal_plan_foods(conn, user_id: str, limit: int = 3) -> list[str]:
    """最近若干次推荐中出现过的食物名（新在前、去重），用于减少重复推荐。

    兼容单方案旧格式（content_json 直接含 items）与多方案新格式（含 plans 列表）。
    """
    rows = conn.execute(
        "SELECT content_json FROM plan_log WHERE user_id = ? AND plan_type = 'meal_plan' "
        "ORDER BY id DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()
    names: list[str] = []
    for r in rows:
        try:
            content = json.loads(r["content_json"])
        except (TypeError, ValueError):
            continue
        plans = content.get("plans") or [content]
        for p in plans:
            for it in p.get("items", []):
                name = it.get("name")
                if name and name not in names:
                    names.append(name)
    return names


def insert_cheat_meal(
    conn, user_id: str, planned_date: str, kcal_budget: float, recommendation: str
) -> int:
    now = _now()
    cur = conn.execute(
        """INSERT INTO cheat_meal_plan
           (user_id, planned_date, kcal_budget, recommendation, status, created_at, updated_at)
           VALUES (?, ?, ?, ?, 'pending', ?, ?)""",
        (user_id, planned_date, kcal_budget, recommendation, now, now),
    )
    return cur.lastrowid


def latest_cheat_meal(conn, user_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM cheat_meal_plan WHERE user_id = ? ORDER BY planned_date DESC, id DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    return dict(row) if row else None
