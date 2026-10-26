"""记忆层：持久化对话与长期记忆（偏好/行为）。"""
from __future__ import annotations

from . import repository as repo


def save_turn(conn, user_id: str, session_id: str, user_text: str, assistant_text: str) -> None:
    """保存一轮对话（用户 + 助手）。"""
    repo.insert_message(conn, user_id, session_id, "user", user_text)
    repo.insert_message(conn, user_id, session_id, "assistant", assistant_text)


def load_recent(conn, session_id: str, limit: int = 20) -> list[dict]:
    """加载最近 N 轮对话（短期记忆）。"""
    return repo.recent_messages(conn, session_id, limit)


def remember(
    conn,
    user_id: str,
    memory_type: str,
    content: str,
    relevance_score: float = 1.0,
    expires_at: str | None = None,
) -> int:
    """追加一条长期记忆（偏好 / 行为 / 备注）。"""
    return repo.insert_memory(conn, user_id, memory_type, content, relevance_score, expires_at)
