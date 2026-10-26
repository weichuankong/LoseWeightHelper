"""SQLite 连接、建表与食物种子数据初始化。

默认数据库文件位于 <项目根>/data/loseweight.db；
测试可传入 ":memory:" 使用内存库。
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from . import foods as foods_module

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "loseweight.db"

SCHEMA = """
-- 用户档案（固定档案 + 当前热量/宏量目标）
CREATE TABLE IF NOT EXISTS user_profile (
    user_id TEXT PRIMARY KEY,
    name TEXT,
    sex TEXT NOT NULL,
    height_cm REAL NOT NULL,
    age INTEGER NOT NULL,
    activity_level TEXT NOT NULL DEFAULT 'sedentary',
    goal_weight_kg REAL,
    daily_target_kcal REAL,
    daily_protein_g REAL,
    daily_fat_g REAL,
    daily_carb_g REAL,
    preferences_json TEXT,
    restrictions_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- 食物营养库（每 100g）
CREATE TABLE IF NOT EXISTS food (
    food_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL,
    energy_kcal REAL NOT NULL,
    protein_g REAL NOT NULL,
    fat_g REAL NOT NULL,
    carb_g REAL NOT NULL,
    default_grams REAL NOT NULL,
    source TEXT
);

-- 饮食日志
CREATE TABLE IF NOT EXISTS meal_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    log_date TEXT NOT NULL,
    meal_type TEXT NOT NULL,
    food_id INTEGER,
    food_name TEXT,
    grams REAL NOT NULL,
    kcal REAL NOT NULL,
    protein_g REAL NOT NULL,
    fat_g REAL NOT NULL,
    carb_g REAL NOT NULL,
    is_estimated INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

-- 体重与 BMI 历史（同时记录 BMR/TDEE/建议目标，便于追溯目标变化）
CREATE TABLE IF NOT EXISTS weight_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    log_date TEXT NOT NULL,
    weight_kg REAL NOT NULL,
    bmi REAL,
    bmi_category TEXT,
    bmr REAL,
    tdee REAL,
    suggested_target_kcal REAL,
    created_at TEXT NOT NULL
);

-- 对话历史（持久化对话，功能一）
CREATE TABLE IF NOT EXISTS conversation (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- 长期记忆 / 偏好笔记
CREATE TABLE IF NOT EXISTS agent_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    memory_type TEXT NOT NULL,
    content TEXT NOT NULL,
    relevance_score REAL NOT NULL DEFAULT 1.0,
    created_at TEXT NOT NULL,
    expires_at TEXT
);

-- 计划日志（食谱 / 补救 / 放纵餐等，统一落库便于追踪）
CREATE TABLE IF NOT EXISTS plan_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    plan_type TEXT NOT NULL,
    plan_date TEXT NOT NULL,
    content_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- 放纵餐追踪（功能五闭环：pending -> tracked）
CREATE TABLE IF NOT EXISTS cheat_meal_plan (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    planned_date TEXT NOT NULL,
    kcal_budget REAL,
    recommendation TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    tracked_kcal REAL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_meal_user_date ON meal_log(user_id, log_date);
CREATE INDEX IF NOT EXISTS idx_weight_user_date ON weight_log(user_id, log_date);
CREATE INDEX IF NOT EXISTS idx_conv_session ON conversation(session_id, id);
CREATE INDEX IF NOT EXISTS idx_memory_user ON agent_memory(user_id);
"""


def connect(db_path=DEFAULT_DB_PATH):
    """打开数据库连接，返回带 Row 工厂的连接。"""
    if str(db_path) == ":memory:":
        conn = sqlite3.connect(":memory:")
    else:
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def session(db_path=DEFAULT_DB_PATH):
    """事务型连接上下文管理器：成功提交，异常回滚。"""
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(conn):
    """建表并写入食物种子数据（幂等）。"""
    conn.executescript(SCHEMA)
    foods_module.seed_foods(conn)
