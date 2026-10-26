"""意图路由与编排层（规则版，可替换为 LLM/LangGraph）。

提供：
- route_intent：规则式意图识别
- extract_meals / parse_grams / extract_weight：确定性槽位抽取（LLM 版的替换点）
- Agent：把路由、抽取、服务组合成一次对话处理
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from . import calculations as calc
from . import config
from . import foods as foods_module
from . import repository as repo
from . import services
from . import memory
from . import llm


def route_intent(text: str) -> str:
    """规则式意图识别。"""
    t = text.strip()
    if not t:
        return "unknown"

    # 修改目标体重（需含数字）
    if "目标" in t and re.search(r"\d", t):
        return "update_goal"

    # 更正饮食记录：撤销 / 删除 / 修改
    if re.search(r"撤销|撤回|取消", t):
        return "undo_meal"
    if re.search(r"删掉|删除|去掉|移除", t) and re.search(r"刚才|最后|最近|上一条", t):
        return "undo_meal"
    if re.search(r"删掉|删除|去掉|移除|删了", t):
        return "delete_meal"
    if re.search(r"改成|改为|更正|修改|换成", t) and not re.search(r"目标|体重", t):
        return "edit_meal"

    # 体重更新 / 查询（更新需含数字）
    if re.search(r"体重|公斤|千克|kg|斤|称重|称|重了|轻了", t, re.I):
        return "update_weight" if re.search(r"\d", t) else "query_weight"

    if re.search(r"放纵餐|欺骗餐|cheat|犒劳", t, re.I):
        return "plan_cheat_meal"

    if re.search(r"补救|超标|吃多|吃超|暴食|水肿|肿了|超了", t):
        return "request_recovery"

    if re.search(r"汇总|总结|剩余|还剩|今日摄入|吃了什么|吃了啥|吃了多少|看.*记录|记录.*看", t):
        return "get_daily_summary"

    if re.search(r"吃什么|推荐|食谱|菜单|今晚|明天吃|建议.*吃|配餐|怎么吃", t):
        return "request_plan"

    if re.search(r"食物|食品|有哪些|支持.*吃|列表", t):
        return "list_foods"

    if re.search(r"帮助|help|怎么用|功能", t):
        return "help"

    # 未进食声明（“没吃早餐 / 跳过晚饭”等）——仅确认，不写入记录
    if re.search(r"没吃|没喝|跳过|不吃|空腹|没胃口|省略|免了|省了", t) and \
            re.search(r"早餐|早饭|午餐|午饭|中饭|晚餐|晚饭|零食|加餐|夜宵|宵夜", t):
        return "skip_meal"

    # 偏好食物（“我喜欢吃鸡胸肉”等）——设置优先推荐（“不喜欢”除外）
    if re.search(r"(?<!不)喜欢|爱吃|偏好", t):
        return "set_preferences"

    # 按热量估算记录（“午餐轻食 470 大卡 / 大约 500 大卡”等，无需具体食物）
    if re.search(r"\d+\s*(大卡|千卡|kcal|卡路里|卡)", t, re.I) and \
            re.search(r"轻食|估算|估计|大约|大概|约|外食|外卖|聚餐|应酬", t):
        return "log_estimated_meal"

    if re.search(r"吃了|喝了|加餐|零食|早餐|午餐|晚餐|午饭|晚饭|夜宵|记录", t):
        return "log_meal"

    return "unknown"


def parse_grams(text: str) -> list[tuple[float, str]]:
    """解析份量表达，返回 (克数, 来源)。支持 400g / 400克 / 半碗 / 一碗 / 大碗 / 一杯。"""
    results: list[tuple[float, str]] = []
    for num, unit in re.findall(r"(\d+(?:\.\d+)?)\s*(公斤|千克|kg|公克|克|g)", text, re.I):
        n = float(num)
        if unit.lower() in ("公斤", "千克", "kg"):
            n *= 1000
        results.append((n, "explicit"))
    quantity_map = {"半碗": 150.0, "小碗": 150.0, "一碗": 250.0, "大碗": 400.0, "一杯": 250.0}
    for word, g in quantity_map.items():
        if word in text:
            results.append((g, "estimate_word"))
    return results


def extract_weight(text: str) -> float | None:
    """抽取体重公斤数。支持 70kg / 70公斤 / 140斤 / 70.5。"""
    m = re.findall(r"(\d+(?:\.\d+)?)\s*(公斤|千克|kg|斤)", text, re.I)
    if m:
        num, unit = m[0]
        n = float(num)
        return n / 2.0 if unit == "斤" else n
    m2 = re.search(r"(\d+(?:\.\d+)?)", text)
    return float(m2.group(1)) if m2 else None


def detect_meal_type(text: str) -> str | None:
    """从文本显式识别餐次，无则返回 None。"""
    if "早餐" in text or "早饭" in text:
        return "breakfast"
    if "午餐" in text or "午饭" in text or "中饭" in text:
        return "lunch"
    if "晚餐" in text or "晚饭" in text:
        return "dinner"
    if "零食" in text or "加餐" in text or "夜宵" in text or "宵夜" in text:
        return "snack"
    return None


def extract_date(text: str) -> str | None:
    """从文本抽取日志日期，返回 YYYY-MM-DD；识别不到返回 None。

    支持：今天/今日、昨天/昨日、前天、大前天、X月X日/号、X日/号。
    纯「X日/号」按本月处理，若落在未来则自动退到上个月。
    """
    today_d = datetime.now()
    t = text
    if "大前天" in t:
        return (today_d - timedelta(days=3)).strftime("%Y-%m-%d")
    if "前天" in t:
        return (today_d - timedelta(days=2)).strftime("%Y-%m-%d")
    if "昨天" in t or "昨日" in t:
        return (today_d - timedelta(days=1)).strftime("%Y-%m-%d")
    if "今天" in t or "今日" in t:
        return today_d.strftime("%Y-%m-%d")

    m = re.search(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", t)
    if m:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return datetime(year, month, day).strftime("%Y-%m-%d")
        except ValueError:
            return None

    m = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]", t)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        year = today_d.year
        try:
            d = datetime(year, month, day)
        except ValueError:
            return None
        if d.date() > today_d.date():  # 未来日期视为上一年
            year -= 1
        try:
            return datetime(year, month, day).strftime("%Y-%m-%d")
        except ValueError:
            return None

    m = re.search(r"(?<!\d)(\d{1,2})\s*[日号]", t)
    if m:
        day = int(m.group(1))
        year, month = today_d.year, today_d.month
        if day > today_d.day:  # 落在本月未来，退到上个月
            month -= 1
            if month == 0:
                month, year = 12, year - 1
        try:
            return datetime(year, month, day).strftime("%Y-%m-%d")
        except ValueError:
            return None
    return None


def _matched_food_rows(conn, text: str) -> list[dict]:
    """返回文本命中的食物行（规范名子串命中 + 口语俗称兜底），未去重、最长名优先。

    俗称（foods_module.FOOD_ALIASES）仅在“该俗称对应的更具体规范名未在文本中出现”时生效，
    例如文本含「牛肉面」时，不再把「牛肉」映射到「瘦牛肉」，避免重复命中。
    """
    def base(name: str) -> str:
        return name.split("（")[0] if "（" in name else name

    rows = conn.execute(
        "SELECT food_id, name, default_grams FROM food ORDER BY length(name) DESC"
    ).fetchall()
    found = [dict(f) for f in rows if base(f["name"]) and base(f["name"]) in text]

    for alias, canonical in foods_module.FOOD_ALIASES.items():
        if alias not in text:
            continue
        if any(alias in base(f["name"]) and base(f["name"]) in text for f in rows):
            continue
        row = next((f for f in rows if f["name"] == canonical), None)
        if row is not None and not any(d["name"] == row["name"] for d in found):
            found.append(dict(row))
    return found


def extract_meals(conn, text: str) -> list[dict]:
    """从文本中抽取食物与份量（确定性版，可替换为 LLM）。

    返回 [{"food_id", "name", "grams", "is_estimated"}, ...]
    """
    found = _matched_food_rows(conn, text)

    # 去重：若某名称是另一个已匹配名称的子串，则跳过较短的
    result = []
    for f in found:
        if any(f["name"] != o["name"] and f["name"] in o["name"] and o["name"] in text
               for o in found):
            continue
        result.append(f)

    grams_list = parse_grams(text)
    entries = []
    for i, f in enumerate(result):
        grams = f["default_grams"]
        is_estimated = True
        if grams_list:
            g, source = grams_list[min(i, len(grams_list) - 1)]
            grams = g
            is_estimated = source != "explicit"
        entries.append({"food_id": f["food_id"], "name": f["name"],
                        "grams": grams, "is_estimated": is_estimated,
                        "default_grams": f["default_grams"]})
    return entries


def apply_quantity(text: str, entries: list[dict]) -> list[dict]:
    """数量词折算（确定性兜底）：『两个/一个/半个/半份…』按数量乘默认单份克数。

    只在同时满足时生效，避免误算或二次折算：
    - 恰好一个食物条目；
    - 当前克数 == 该食物的默认单份克数（说明还没折算过）；
    - 文本含数量词。
    """
    if len(entries) != 1:
        return entries
    e = entries[0]
    m = re.search(r"(半|[一二两三四五六七八九十\d]+)\s*(个|份|只|颗|根)", text)
    if not m:
        return entries
    word = m.group(1)
    cn = {"半": 0.5, "一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
          "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    factor = cn.get(word)
    if factor is None:
        try:
            factor = float(word)
        except ValueError:
            return entries
    if factor <= 0:
        return entries
    default = e.get("default_grams")
    grams = e.get("grams")
    if default and grams is not None and abs(float(grams) - float(default)) < 1e-6:
        e["grams"] = round(float(grams) * factor, 1)
    return entries


def find_foods_in_text(conn, text: str) -> list[dict]:
    """返回文本中出现的食物（最长名称优先，去除子串重复）。"""
    def base(name: str) -> str:
        return name.split("（")[0] if "（" in name else name

    found = _matched_food_rows(conn, text)
    result = []
    for f in found:
        if any(base(f["name"]) != base(o["name"]) and base(f["name"]) in base(o["name"])
               for o in found):
            continue
        result.append(f)
    return result


def _extract_grams_value(text: str) -> float | None:
    """从文本中提取一个克数（支持 125g / 125 克 / 公斤 / 半碗等）。"""
    m = re.search(r"(\d+(?:\.\d+)?)\s*(公斤|千克|kg|公克|克|g)", text, re.I)
    if m:
        n = float(m.group(1))
        return n * 1000 if m.group(2).lower() in ("公斤", "千克", "kg") else n
    for word, g in {"半碗": 150.0, "小碗": 150.0, "一碗": 250.0, "大碗": 400.0, "一杯": 250.0}.items():
        if word in text:
            return g
    m2 = re.search(r"(\d+(?:\.\d+)?)", text)
    return float(m2.group(1)) if m2 else None


def _split_edit(text: str) -> tuple[str, str]:
    """把「把X改成Y」切成 (X, Y)；无切分动词时返回 (text, '')。"""
    for v in ("改成", "改为", "更正为", "修改为", "换成"):
        i = text.find(v)
        if i >= 0:
            return text[:i], text[i + len(v):]
    return text, ""


HELP_TEXT = """我是一个减脂支持 Agent（知识科普型教练，非医生/注册营养师）。

我可以帮你：
1. 记录饮食   「吃了鸡胸肉 150 克」/「午餐吃了牛肉面」/「午餐轻食约 470 大卡」
2. 记录体重   「今天 70 公斤」（自动更新 BMI / TDEE / 目标）
3. 改目标体重 「目标体重改成 90 公斤」
4. 查当日汇总 「今天吃了什么」/「汇总」
5. 推荐食谱   「晚餐吃什么」/「推荐食谱」（每次给出 3 套方案）
6. 补救措施   「吃多了怎么办」/「补救」
7. 放纵餐     「想安排放纵餐」
8. 查看食物库 「食物」
9. 更正记录   「撤销刚才」/「删掉早餐的鸡蛋」/「把荞麦面改成 125g」
10. 偏好食物  「我喜欢吃鸡胸肉、虾」/「我爱吃西兰花」

安全边界：女性日摄入不低于 1200 kcal，男性不低于 1500 kcal；
我不诊断疾病、不调整药物、不给出极端节食方案。"""


class Agent:
    """把路由、抽取、服务组合成一次对话处理。"""

    def __init__(self, conn, user_id: str = "default", session_id: str | None = None, use_llm: bool = True):
        self.conn = conn
        self.user_id = user_id
        self.session_id = session_id or f"session-{user_id}"
        self.llm = llm.get_client() if use_llm else None

    def handle(self, text: str) -> str:
        text = text.strip()
        if not text:
            return "请告诉我你想做什么（记录饮食 / 体重 / 汇总 / 食谱 / 补救 / 放纵餐）。"

        intent = self._route(text)
        try:
            reply = self._dispatch(intent, text)
        except ValueError as e:
            reply = str(e)

        memory.save_turn(self.conn, self.user_id, self.session_id, text, reply)
        self.conn.commit()
        return reply

    # ------------------------------------------------------------ 意图路由

    def _route(self, text: str) -> str:
        """优先 LLM 意图判定，失败/未配置时回退规则版。"""
        if self.llm is not None:
            try:
                return llm.route_intent_llm(self.llm, text)
            except Exception:
                pass
        return route_intent(text)

    # ------------------------------------------------------------ 分派

    def _dispatch(self, intent: str, text: str) -> str:
        if intent == "update_weight":
            w = extract_weight(text)
            if w is None:
                return "我识别到你想记录体重，但没找到数字。请告诉我具体公斤数，例如「今天 70.5 公斤」。"
            return self._format_weight(services.update_weight(self.conn, self.user_id, weight_kg=w))

        if intent == "query_weight":
            return self._format_query_weight()

        if intent == "update_goal":
            w = extract_weight(text)
            if w is None:
                return "我识别到你想改目标体重，但没找到数字。请告诉我目标公斤数，例如「目标体重改成 90 公斤」。"
            return self._format_goal(services.update_goal(self.conn, self.user_id, goal_weight_kg=w))

        if intent == "log_meal":
            return self._log_meal(text)

        if intent == "log_estimated_meal":
            return self._log_estimated_meal(text)

        if intent == "skip_meal":
            return self._skip_meal(text)

        if intent == "set_preferences":
            return self._set_preferences(text)

        if intent == "undo_meal":
            return self._undo_meal(text)

        if intent == "delete_meal":
            return self._delete_meal(text)

        if intent == "edit_meal":
            return self._edit_meal(text)

        if intent == "get_daily_summary":
            return self._format_summary(
                services.get_daily_summary(self.conn, self.user_id, log_date=extract_date(text))
            )

        if intent == "request_plan":
            meal_type = detect_meal_type(text) or calc.infer_meal_type(datetime.now().hour)
            return self._format_plan(
                services.generate_meal_plan(self.conn, self.user_id, meal_type=meal_type)
            )

        if intent == "request_recovery":
            if re.search(r"水肿|肿了|肿", text):
                return services.edema_advice()["message"]
            return self._format_recovery(services.generate_recovery_plan(self.conn, self.user_id))

        if intent == "plan_cheat_meal":
            return self._format_cheat(services.plan_cheat_meal(self.conn, self.user_id))

        if intent == "list_foods":
            return self._format_foods()

        if intent == "help":
            return HELP_TEXT

        return ("抱歉，我没听懂。你可以：记录饮食（「吃了鸡胸肉 150 克」）、报体重（「今天 70 公斤」）、"
                "查汇总（「今天吃了什么」）、要食谱（「晚餐吃什么」）、补救（「吃多了怎么办」）、"
                "放纵餐（「想安排放纵餐」）。输入「帮助」查看完整说明。")

    # ------------------------------------------------------------ 各意图处理

    def _log_meal(self, text: str) -> str:
        entries = apply_quantity(text, self._extract_meals(text))
        if not entries:
            return "没有识别到明确食物。请说食物名，例如「吃了鸡胸肉 150 克」。支持的食物可输入「食物」查看。"

        log_date = extract_date(text) or repo.today()
        fallback_meal = detect_meal_type(text) or calc.infer_meal_type(datetime.now().hour)
        lines = []
        summary = None
        for e in entries:
            meal_type = e.get("meal_type") or fallback_meal
            label = config.MEAL_TYPE_LABELS[meal_type]
            r = services.log_meal(
                self.conn, self.user_id, meal_type=meal_type,
                food_id=e["food_id"], grams=e["grams"], is_estimated=e["is_estimated"],
                log_date=log_date,
            )
            est = "（估算份量）" if e["is_estimated"] else ""
            dup = "（⚠ 与已有记录完全相同，疑似重复）" if r["duplicate"] else ""
            lines.append(
                f"已记录：{label} · {r['entry']['name']} {r['entry']['grams']:.0f}g "
                f"≈ {r['entry']['kcal']:.0f} kcal{est}{dup}"
            )
            summary = r["summary"]

        if summary is not None:
            s = summary
            day_label = "今日" if s["date"] == repo.today() else f"{s['date']} 当日"
            lines.append(f"{day_label}累计：{s['total_kcal']:.0f} kcal")
            if s["target_kcal"] is not None:
                lines.append(f"{day_label}目标：{s['target_kcal']:.0f} kcal，剩余额度 {s['remaining_kcal']:.0f} kcal")
        return "\n".join(lines)

    def _log_estimated_meal(self, text: str) -> str:
        m = re.search(r"(\d+(?:\.\d+)?)\s*(大卡|千卡|kcal|卡路里|卡)", text, re.I)
        if not m:
            return "请说明估算热量，例如「午餐轻食约 470 大卡」。"
        kcal = float(m.group(1))
        log_date = extract_date(text) or repo.today()
        meal_type = detect_meal_type(text) or calc.infer_meal_type(datetime.now().hour)
        label = "轻食" if "轻食" in text else "估算餐"
        r = services.log_estimated_meal(
            self.conn, self.user_id, meal_type=meal_type, kcal=kcal, label=label,
            log_date=log_date,
        )
        mt_label = config.MEAL_TYPE_LABELS[meal_type]
        lines = [f"已记录：{mt_label} · {r['entry']['name']} ≈ {r['entry']['kcal']:.0f} kcal（估算）"]
        s = r["summary"]
        day_label = "今日" if s["date"] == repo.today() else f"{s['date']} 当日"
        lines.append(f"{day_label}累计：{s['total_kcal']:.0f} kcal")
        if s["target_kcal"] is not None:
            lines.append(f"{day_label}目标：{s['target_kcal']:.0f} kcal，剩余额度 {s['remaining_kcal']:.0f} kcal")
        return "\n".join(lines)

    def _skip_meal(self, text: str) -> str:
        mt = detect_meal_type(text)
        label = config.MEAL_TYPE_LABELS.get(mt, mt) if mt else "这一餐"
        return f"好的，{label}没吃，按 0 kcal 计，无需额外记录。"

    def _set_preferences(self, text: str) -> str:
        found = find_foods_in_text(self.conn, text)
        if not found:
            return "没识别到具体食物。例如「我喜欢吃鸡胸肉、虾、西兰花」。"
        r = services.set_preferences(self.conn, self.user_id, [f["name"] for f in found])
        if not r["preferences"]:
            return "没识别到具体食物。例如「我喜欢吃鸡胸肉、虾、西兰花」。"
        return "已记住你的偏好：" + "、".join(r["preferences"]) + "。以后推荐会优先考虑这些食物。"

    # ------------------------------------------------------------ 更正处理

    def _undo_meal(self, text: str) -> str:
        try:
            r = services.undo_last_meal(self.conn, self.user_id)
        except ValueError as e:
            return str(e)
        return self._format_undo(r)

    def _delete_meal(self, text: str) -> str:
        foods = find_foods_in_text(self.conn, text)
        meal_type = detect_meal_type(text)
        if not foods and meal_type is None:
            return "没找到要删除的食物或餐次。例如「删掉早餐的鸡蛋」或「删掉鸡蛋」。"
        try:
            r = services.delete_meal_entry(
                self.conn, self.user_id, meal_type=meal_type,
                food_id=foods[0]["food_id"] if foods else None,
                log_date=extract_date(text),
            )
        except ValueError as e:
            return str(e)
        return self._format_delete(r)

    def _edit_meal(self, text: str) -> str:
        left, right = _split_edit(text)
        target_food = find_foods_in_text(self.conn, left) or find_foods_in_text(self.conn, text)
        if not target_food:
            return "没找到要修改的食物。例如「把荞麦面改成125g」或「鸡蛋改成鸡胸肉」。"
        target = target_food[0]
        new_grams = _extract_grams_value(right)
        new_food = None
        for f in find_foods_in_text(self.conn, right):
            if f["food_id"] != target["food_id"]:
                new_food = f
                break
        if new_grams is None and new_food is None:
            return "没识别到要改成什么。请说明新份量（如「改成125g」）或新食物（如「改成鸡胸肉」）。"
        try:
            r = services.edit_meal_entry(
                self.conn, self.user_id, food_id=target["food_id"],
                new_grams=new_grams,
                new_food_id=new_food["food_id"] if new_food else None,
                log_date=extract_date(text),
            )
        except ValueError as e:
            return str(e)
        return self._format_edit(r)

    # ------------------------------------------------------------ 槽位抽取（LLM 优先，规则回退）

    def _extract_meals(self, text: str) -> list[dict]:
        if self.llm is not None:
            try:
                names = [f["name"] for f in foods_module.list_foods(self.conn)]
                raw = llm.extract_meals_llm(self.llm, text, names)
                resolved = self._resolve_llm_meals(raw)
                if resolved:
                    return resolved
            except Exception:
                pass
        return extract_meals(self.conn, text)

    def _resolve_llm_meals(self, raw: list[dict]) -> list[dict]:
        entries = []
        for item in raw:
            food = self._match_food(item.get("name", ""))
            if food is None:
                continue  # 无法映射到食物库：跳过，避免 LLM 编造热量
            grams = item.get("grams")
            if grams is None or not isinstance(grams, (int, float)):
                grams = float(food.get("default_grams") or 100.0)
                is_estimated = True
            else:
                grams = float(grams)
                is_estimated = bool(item.get("is_estimated", False))
            entries.append({
                "food_id": food["food_id"],
                "name": food["name"],
                "grams": grams,
                "is_estimated": is_estimated,
                "default_grams": float(food.get("default_grams") or 100.0),
                "meal_type": llm.normalize_meal_type(item.get("meal_type")),
            })
        return entries

    def _match_food(self, name: str) -> dict | None:
        exact = foods_module.find_food_by_name(self.conn, name)
        if exact:
            return exact
        all_foods = foods_module.list_foods(self.conn)
        for row in all_foods:
            if row["name"].split("（")[0] == name:
                return row
        for row in all_foods:
            base = row["name"].split("（")[0]
            if base and base in name:
                return row
        for row in all_foods:
            if name and name in row["name"]:
                return row
        return None

    # ------------------------------------------------------------ 格式化

    def _format_weight(self, r: dict) -> str:
        bmi = r["bmi"]
        t = r["target"]
        lines = [
            f"已记录体重：{r['weight_kg']:.1f} kg",
            f"BMI：{bmi['bmi']}（{bmi['category_label']}）",
            f"BMR：{r['bmr']:.0f} kcal，TDEE：{r['tdee']:.0f} kcal",
        ]
        if t["goal"] == "lose":
            lines.append(f"建议日摄入目标：{t['target_kcal']} kcal（缺口 {t['deficit_kcal']:.0f} kcal，下限 {t['floor_kcal']:.0f} kcal）")
        else:
            lines.append(f"建议日摄入目标：{t['target_kcal']} kcal（{t['goal']}）")
        m = r["macros"]
        lines.append(f"宏量目标：蛋白质 {m['protein_g']}g / 脂肪 {m['fat_g']}g / 碳水 {m['carb_g']}g")
        return "\n".join(lines)

    def _format_query_weight(self) -> str:
        latest = repo.latest_weight(self.conn, self.user_id)
        if latest is None:
            return "还没有体重记录。请先报体重，例如「今天 70 公斤」。"
        return (
            f"最近记录（{latest['log_date']}）：体重 {latest['weight_kg']:.1f} kg，"
            f"BMI {latest['bmi']}（{latest['bmi_category']}）。"
            f"TDEE {latest['tdee']:.0f} kcal，建议目标 {latest['suggested_target_kcal']:.0f} kcal。"
        )

    def _format_goal(self, r: dict) -> str:
        t = r["target"]
        m = r["macros"]
        lines = [f"目标体重已更新为：{r['goal_weight_kg']:.1f} kg（当前 {r['current_weight_kg']:.1f} kg）"]
        if t["goal"] == "lose":
            lines.append(f"建议日摄入目标：{t['target_kcal']} kcal（缺口 {t['deficit_kcal']:.0f} kcal，下限 {t['floor_kcal']:.0f} kcal）")
        elif t["goal"] == "gain":
            lines.append(f"建议日摄入目标：{t['target_kcal']} kcal（增重，TDEE + 300 kcal）")
        else:
            lines.append(f"建议日摄入目标：{t['target_kcal']} kcal（维持）")
        lines.append(f"宏量目标：蛋白质 {m['protein_g']}g / 脂肪 {m['fat_g']}g / 碳水 {m['carb_g']}g")
        return "\n".join(lines)

    def _format_summary(self, s: dict) -> str:
        b = s["by_meal"]
        meal_part = " · ".join(
            f"{config.MEAL_TYPE_LABELS[mt]} {b[mt]['kcal']:.0f}" for mt in config.MEAL_TYPES
        )
        head = f"今日（{s['date']}）饮食汇总" if s["date"] == repo.today() else f"{s['date']} 饮食汇总"
        lines = [
            head,
            meal_part,
            f"合计：{s['total_kcal']:.0f} kcal（蛋白质 {s['total_protein_g']:.0f}g / "
            f"脂肪 {s['total_fat_g']:.0f}g / 碳水 {s['total_carb_g']:.0f}g）",
        ]
        if s["target_kcal"] is not None:
            lines.append(f"目标：{s['target_kcal']:.0f} kcal，剩余 {s['remaining_kcal']:.0f} kcal")
        for m in s["meals"]:
            if m["is_estimated"]:
                # 估算餐无份量，只展示热量
                lines.append(f"  - {config.MEAL_TYPE_LABELS.get(m['meal_type'], m['meal_type'])} "
                             f"{m['food_name']} ≈ {m['kcal']:.0f} kcal（估算）")
            else:
                lines.append(f"  - {config.MEAL_TYPE_LABELS.get(m['meal_type'], m['meal_type'])} "
                             f"{m['food_name']} {m['grams']:.0f}g = {m['kcal']:.0f} kcal")
        return "\n".join(lines)

    def _format_plan(self, plan: dict) -> str:
        if not plan.get("plans"):
            return plan.get("message", "")
        lines = []
        for i, p in enumerate(plan["plans"], 1):
            lines.append(f"方案 {i}：{p['message']}")
            for it in p.get("items", []):
                lines.append(f"  - {it['name']} {it['grams']:.0f}g（{it['kcal']:.0f} kcal，蛋白质 {it['protein_g']:.0f}g）")
        return "\n".join(lines)

    def _format_recovery(self, plan: dict) -> str:
        return plan["message"]

    def _format_cheat(self, plan: dict) -> str:
        return plan["message"]

    def _format_undo(self, r: dict) -> str:
        m = r["deleted"]
        s = r["summary"]
        label = config.MEAL_TYPE_LABELS.get(m["meal_type"], m["meal_type"])
        lines = [f"已撤销：{label} · {m['food_name']} {m['grams']:.0f}g（{m['kcal']:.0f} kcal）"]
        if s["target_kcal"] is not None:
            d = "今日" if s["date"] == repo.today() else s["date"]
            lines.append(f"{d}累计：{s['total_kcal']:.0f} kcal，剩余 {s['remaining_kcal']:.0f} kcal")
        return "\n".join(lines)

    def _format_delete(self, r: dict) -> str:
        m = r["deleted"]
        s = r["summary"]
        label = config.MEAL_TYPE_LABELS.get(m["meal_type"], m["meal_type"])
        note = f"（共 {r['match_count']} 条匹配，已删最近一条）" if r["match_count"] > 1 else ""
        lines = [f"已删除：{label} · {m['food_name']} {m['grams']:.0f}g（{m['kcal']:.0f} kcal）{note}"]
        if s["target_kcal"] is not None:
            d = "今日" if s["date"] == repo.today() else s["date"]
            lines.append(f"{d}累计：{s['total_kcal']:.0f} kcal，剩余 {s['remaining_kcal']:.0f} kcal")
        return "\n".join(lines)

    def _format_edit(self, r: dict) -> str:
        b = r["before"]
        u = r["updated"]
        s = r["summary"]
        label = config.MEAL_TYPE_LABELS.get(u["meal_type"], u["meal_type"])
        lines = [
            f"已修改：{label} · {b['food_name']} {b['grams']:.0f}g "
            f"→ {u['food_name']} {u['grams']:.0f}g（{u['kcal']:.0f} kcal）",
        ]
        if s["target_kcal"] is not None:
            d = "今日" if s["date"] == repo.today() else s["date"]
            lines.append(f"{d}累计：{s['total_kcal']:.0f} kcal，剩余 {s['remaining_kcal']:.0f} kcal")
        return "\n".join(lines)

    def _format_foods(self) -> str:
        foods = []
        for cat, cat_label in foods_module.CATEGORY_LABELS.items():
            items = [f["name"] for f in foods_module.list_foods(self.conn, cat)]
            if items:
                foods.append(f"{cat_label}：{'、'.join(items)}")
        if not foods:
            return "食物库为空。"
        return "支持的食物（可按名称记录）：\n" + "\n".join(foods)
