"""可替换的 LLM 适配层（DeepSeek，OpenAI 兼容协议）。

设计原则（见 IMPLEMENTATION.md 第四节）：
- LLM 只负责「意图理解」与「槽位抽取」，并返回结构化 JSON；
- 热量 / BMI / TDEE 等计算仍留在确定性代码层，避免模型编造数字；
- 未配置 API key 或调用失败时，上层自动回退到 agent 的规则版实现。

配置来源（优先级从高到低）：
1. 环境变量 DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL / DEEPSEEK_MODEL / DEEPSEEK_EFFORT
2. 项目根目录 .env 文件
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

try:
    import requests
    _HAS_REQUESTS = True
except ImportError:  # 无 requests 时退回标准库 urllib，保持核心零硬依赖
    requests = None  # type: ignore[assignment]
    _HAS_REQUESTS = False
    import urllib.request

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_EFFORT = "low"

# 与 agent.route_intent 保持一致的可选意图
KNOWN_INTENTS = [
    "log_meal", "log_estimated_meal", "update_weight", "update_goal", "query_weight", "get_daily_summary",
    "request_plan", "request_recovery", "plan_cheat_meal",
    "undo_meal", "delete_meal", "edit_meal",
    "skip_meal", "set_preferences",
    "list_foods", "help", "unknown",
]

_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def _load_dotenv(path: Path) -> dict:
    env = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


_DOTENV = _load_dotenv(_ENV_FILE)


def get_config() -> dict:
    def pick(env_key: str, default: str) -> str:
        val = os.environ.get(env_key)
        if val is not None:
            return val
        return _DOTENV.get(env_key, default)

    return {
        "api_key": pick("DEEPSEEK_API_KEY", ""),
        "base_url": pick("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL),
        "model": pick("DEEPSEEK_MODEL", DEFAULT_MODEL),
        "effort": pick("DEEPSEEK_EFFORT", DEFAULT_EFFORT),
    }


def is_configured() -> bool:
    key = get_config()["api_key"].strip()
    return bool(key) and key.lower() not in ("none", "disabled", "null")


def get_client() -> "LLMClient | None":
    if not is_configured():
        return None
    return LLMClient()


def _chat_completion(cfg: dict, system: str, user: str, max_tokens: int) -> str:
    url = cfg["base_url"].rstrip("/") + "/chat/completions"
    payload = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
    }
    effort = (cfg.get("effort") or "").strip()
    if effort:
        payload["effort"] = effort

    headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json",
    }

    if _HAS_REQUESTS:
        resp = requests.post(url, headers=headers, json=payload, timeout=120)
        resp.raise_for_status()
        data = resp.json()
    else:
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST"
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    return (data["choices"][0]["message"].get("content") or "").strip()


class LLMClient:
    def __init__(self, cfg: dict | None = None):
        self.cfg = cfg or get_config()

    def chat(self, system: str, user: str, max_tokens: int = 4000) -> str:
        return _chat_completion(self.cfg, system, user, max_tokens)


def parse_json(text: str):
    """从模型输出中稳健地解析 JSON（容忍代码块围栏与前后杂文本）。"""
    text = (text or "").strip()
    if not text:
        raise ValueError("空输出")
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        starts = [i for i in (text.find("["), text.find("{")) if i >= 0]
        ends = [i for i in (text.rfind("]"), text.rfind("}")) if i >= 0]
        if starts and ends and min(starts) < max(ends):
            return json.loads(text[min(starts):max(ends) + 1])
        raise


def route_intent_llm(client: LLMClient, text: str) -> str:
    system = (
        "你是减脂 Agent 的意图分类器。判断用户这句话想做什么，"
        "只输出一个 JSON 对象 {\"intent\": \"<意图>\"}，不要任何解释或代码块。\n"
        f"可选意图：{', '.join(KNOWN_INTENTS)}。\n"
        "判例：「吃了鸡胸肉」→log_meal；「今天 70 公斤」→update_weight；「目标体重改成 100kg」→update_goal；「我多重」→query_weight；"
        "「今天吃了什么/汇总」→get_daily_summary；「晚餐吃什么/推荐食谱」→request_plan；"
        "「吃多了怎么办/补救」→request_recovery；「放纵餐/欺骗餐」→plan_cheat_meal；"
        "「撤销/撤回刚才」→undo_meal；「删掉早餐的鸡蛋」→delete_meal；"
        "「把荞麦面改成125g/鸡蛋改成鸡胸肉」→edit_meal（仅食物更正，目标体重仍属 update_goal）；"
        "「没吃早餐/跳过晚饭/不吃午饭」→skip_meal；"
        "「我喜欢吃鸡胸肉/我爱吃虾」→set_preferences；"
        "「午餐轻食约470大卡/大约500大卡（无具体食物）」→log_estimated_meal；"
        "「食物列表」→list_foods；「帮助」→help；其它→unknown。"
    )
    content = client.chat(system, f"用户消息：{text}", max_tokens=800)
    data = parse_json(content)
    intent = data.get("intent") if isinstance(data, dict) else None
    if intent not in KNOWN_INTENTS:
        raise ValueError(f"LLM 返回未知意图：{intent!r}")
    return intent


def extract_meals_llm(client: LLMClient, text: str, food_names: list[str]) -> list[dict]:
    system = (
        "你是减脂 Agent 的饮食信息抽取器。从用户消息中抽取食物、份量与餐次，"
        "只输出一个 JSON 数组，不要任何解释或代码块。\n"
        "数组每项字段：name（食物名，尽量用下方食物库中的规范名称）、"
        "grams（克数，数字；无法确定就 null）、"
        "meal_type（早餐/午餐/晚餐/零食，无法确定就 null）、"
        "is_estimated（份量是否估算，true/false）。\n"
        "「一碗/半碗/大碗/一杯/一份」等无明确克数时，grams 填 null 且 is_estimated=true。\n"
        f"可用食物库：{', '.join(food_names)}"
    )
    content = client.chat(system, f"用户消息：{text}", max_tokens=2000)
    data = parse_json(content)
    if not isinstance(data, list):
        raise ValueError("LLM 抽取结果不是数组")
    meals = []
    for item in data:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        grams = item.get("grams")
        if grams is not None and not isinstance(grams, (int, float)):
            grams = None
        meals.append({
            "name": name,
            "grams": grams,
            "meal_type": item.get("meal_type"),
            "is_estimated": bool(item.get("is_estimated", True)),
        })
    return meals


def normalize_meal_type(value) -> str | None:
    """把中文/英文餐次归一化到 breakfast/lunch/dinner/snack。"""
    if value is None:
        return None
    v = str(value).strip()
    if v in ("早餐", "早饭", "breakfast"):
        return "breakfast"
    if v in ("午餐", "午饭", "中饭", "lunch"):
        return "lunch"
    if v in ("晚餐", "晚饭", "dinner"):
        return "dinner"
    if v in ("零食", "加餐", "夜宵", "宵夜", "snack"):
        return "snack"
    return None
