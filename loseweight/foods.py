"""结构化食物数据库（每 100g 营养值）与查询工具。

默认内置一份常见中式食物表作为开箱即用的底座；
后续可用 cn-food-mcp / 中国食物成分表第 6 版 JSON 批量导入替换或扩充。
"""
from __future__ import annotations

# (name, category, energy_kcal, protein_g, fat_g, carb_g, default_grams)
FOOD_SEED = [
    # 主食
    ("米饭", "staple", 116, 2.6, 0.3, 25.9, 150),
    ("馒头", "staple", 223, 7.0, 1.1, 47.0, 100),
    ("面条（煮）", "staple", 110, 3.6, 0.4, 22.8, 250),
    ("荞麦面（煮）", "staple", 108, 4.8, 0.4, 20.5, 250),
    ("全麦面包", "staple", 246, 9.7, 4.2, 43.0, 60),
    ("燕麦片", "staple", 367, 15.0, 6.7, 66.0, 40),
    ("玉米", "staple", 112, 4.0, 1.2, 22.8, 150),
    ("红薯", "staple", 99, 1.1, 0.2, 24.7, 150),
    ("土豆", "staple", 77, 2.0, 0.2, 17.2, 150),
    # 蛋白质
    ("鸡胸肉", "protein", 133, 24.6, 1.9, 0.0, 120),
    ("鸡腿", "protein", 181, 16.0, 13.0, 0.0, 100),
    ("去皮鸡腿", "protein", 160, 21.0, 8.0, 0.0, 100),
    ("鸡蛋", "protein", 144, 13.3, 8.8, 2.8, 50),
    ("茶叶蛋", "protein", 144, 13.3, 8.8, 2.8, 50),
    ("瘦牛肉", "protein", 106, 20.2, 2.3, 1.2, 100),
    ("猪里脊", "protein", 143, 20.2, 6.2, 1.5, 100),
    ("三文鱼", "protein", 139, 17.2, 7.8, 0.0, 100),
    ("虾", "protein", 93, 18.6, 0.8, 2.8, 100),
    ("豆腐", "protein", 81, 8.1, 3.7, 4.2, 150),
    # 蔬菜
    ("西兰花", "vegetable", 36, 4.1, 0.6, 4.3, 150),
    ("菠菜", "vegetable", 24, 2.6, 0.3, 2.8, 150),
    ("油菜心", "vegetable", 23, 1.8, 0.5, 3.3, 150),
    ("生菜", "vegetable", 15, 1.3, 0.3, 1.3, 150),
    ("番茄", "vegetable", 20, 0.9, 0.2, 3.5, 150),
    ("黄瓜", "vegetable", 16, 0.8, 0.2, 2.9, 150),
    ("胡萝卜", "vegetable", 39, 1.0, 0.2, 8.8, 100),
    ("菌菇", "vegetable", 24, 2.5, 0.3, 3.5, 100),
    # 水果
    ("苹果", "fruit", 53, 0.4, 0.2, 13.7, 200),
    ("香蕉", "fruit", 93, 1.4, 0.2, 22.0, 100),
    ("橙子", "fruit", 48, 0.8, 0.2, 11.1, 150),
    ("蓝莓", "fruit", 57, 0.7, 0.3, 14.5, 100),
    # 乳制品
    ("牛奶", "dairy", 54, 3.0, 3.2, 3.4, 250),
    ("无糖酸奶", "dairy", 72, 2.5, 2.7, 9.3, 100),
    # 零食
    ("薯片", "snack", 548, 7.0, 37.0, 49.0, 30),
    ("巧克力", "snack", 589, 4.3, 40.0, 51.0, 30),
    ("混合坚果", "snack", 600, 20.0, 53.0, 20.0, 25),
    # 复合菜品（估算值，按每 100g 计）
    ("牛肉面", "composite", 120, 5.0, 4.0, 16.0, 400),
    ("奶茶", "composite", 70, 1.5, 3.0, 9.0, 500),
    ("烤蔬菜（混合）", "composite", 80, 2.0, 5.0, 7.0, 200),
    ("炒饭", "composite", 160, 5.0, 6.0, 22.0, 300),
    ("饺子", "composite", 200, 8.0, 7.0, 25.0, 200),
]

# 口语俗称 → 库内规范名：把用户常见叫法映射到精确条目（匹配时兜底）
FOOD_ALIASES = {
    "牛肉": "瘦牛肉",
    "猪肉": "猪里脊",
    "酸奶": "无糖酸奶",
}

CATEGORY_LABELS = {
    "staple": "主食",
    "protein": "蛋白质",
    "vegetable": "蔬菜",
    "fruit": "水果",
    "dairy": "乳制品",
    "snack": "零食",
    "composite": "复合菜品",
}


def seed_foods(conn):
    """写入内置种子数据（幂等：按 name 去重，新增条目会自动补齐到旧库）。"""
    conn.executemany(
        """INSERT INTO food
           (name, category, energy_kcal, protein_g, fat_g, carb_g, default_grams, source)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(name) DO NOTHING""",
        [(*row, "builtin") for row in FOOD_SEED],
    )


def search_food(conn, query: str, limit: int = 8) -> list[dict]:
    """按名称模糊搜索，返回候选列表。"""
    q = f"%{query}%"
    rows = conn.execute(
        """SELECT food_id, name, category, energy_kcal, protein_g, fat_g, carb_g, default_grams
           FROM food WHERE name LIKE ? ORDER BY length(name), food_id LIMIT ?""",
        (q, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def get_food(conn, food_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM food WHERE food_id = ?", (food_id,)).fetchone()
    return dict(row) if row else None


def find_food_by_name(conn, name: str) -> dict | None:
    row = conn.execute("SELECT * FROM food WHERE name = ?", (name,)).fetchone()
    return dict(row) if row else None


def list_foods(conn, category: str | None = None) -> list[dict]:
    if category:
        rows = conn.execute(
            "SELECT * FROM food WHERE category = ? ORDER BY energy_kcal", (category,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM food ORDER BY category, energy_kcal").fetchall()
    return [dict(r) for r in rows]


def get_nutrition(conn, food_id: int, grams: float) -> dict:
    """按克数计算实际热量与宏量营养素。"""
    food = get_food(conn, food_id)
    if food is None:
        raise ValueError(f"食物不存在: {food_id}")
    factor = grams / 100.0
    return {
        "food_id": food["food_id"],
        "name": food["name"],
        "grams": round(grams, 1),
        "kcal": round(food["energy_kcal"] * factor, 1),
        "protein_g": round(food["protein_g"] * factor, 1),
        "fat_g": round(food["fat_g"] * factor, 1),
        "carb_g": round(food["carb_g"] * factor, 1),
    }
