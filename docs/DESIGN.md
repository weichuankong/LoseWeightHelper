能不能帮我写一个减脂Agent，包含以下功能：
1、持久化对话
2、根据我和它的对话信息记录我某天某顿饭/零食吃了什么，并记录热量摄入
3、根据我汇报的体重数据更新BMI
4、为我提供更加合理的食谱，或者提供补救措施
5、帮我规划偶尔的放纵餐

下面是为你设计的减脂 Agent 完整方案，涵盖你提出的 5 项功能。方案以“工具调用 + 分层记忆 + 规则约束”为核心，不依赖单一模型的自由生成，尽量把热量计算、BMI 更新、补救逻辑落在确定性的代码或工具层，LLM 负责意图理解、信息抽取和自然语言交互。

---

## 一、整体架构

```text
用户消息
   │
   ▼
┌─────────────────────────────────────────────────────────┐
│  会话层：短期记忆（当前会话上下文） + 长期记忆（用户档案） │
│  · 短期：最近 N 轮对话、当前追问的槽位                    │
│  · 长期：身高、年龄、性别、活动系数、目标体重、饮食偏好    │
└─────────────────────────────────────────────────────────┘
   │
   ▼
┌─────────────────────────────────────────────────────────┐
│  路由 / 意图识别                                          │
│  log_meal / update_weight / request_plan /                 │
│  request_recovery / plan_cheat_meal / ask_question         │
└─────────────────────────────────────────────────────────┘
   │
   ▼
┌─────────────────────────────────────────────────────────┐
│  工具层（确定性计算 + 数据读写）                          │
│  · food_lookup / log_meal / update_weight / compute_bmi    │
│  · compute_tdee / get_daily_summary / generate_meal_plan   │
│  · generate_recovery_plan / plan_cheat_meal                │
└─────────────────────────────────────────────────────────┘
   │
   ▼
┌─────────────────────────────────────────────────────────┐
│  持久化存储                                               │
│  · 对话历史（按 session_id / user_id）                     │
│  · 饮食日志（user_id, date, meal_type, food_id, grams, kcal）│
│  · 体重 / BMI 历史（user_id, date, weight_kg, bmi）        │
│  · 用户档案（身高、年龄、性别、TDEE 参数、偏好）           │
└─────────────────────────────────────────────────────────┘
```


## 二、功能一：持久化对话

采用**短期记忆 + 长期记忆分层**方案。

**短期记忆**：保留当前会话最近 20–30 轮消息，用于处理“刚才说的那个零食不算了”“那中午呢”这类指代和追问。可以用 session_id 做键，每次请求拼接最近消息。

**长期记忆**：不保存全部原文，而是抽取结构化信息写入用户档案：

- 固定档案：身高、年龄、性别、活动水平、目标体重、饮食禁忌；
- 偏好画像：喜欢/讨厌的食物、常吃的零食、外食频率；
- 行为模式：通常几点汇报饮食、是否容易在晚间摄入高热量食物。

长期记忆的写入时机可以在每次 `log_meal` 和 `update_weight` 成功后，由 Agent 将“用户今天提到喜欢吃鸡胸肉但讨厌西兰花”这类信息以 `user_preference` 条目追加。实现上可以用 SQLite（轻量个人使用）或 Postgres（多用户）。LangGraph 的内置检查点功能可以直接实现短期上下文和跨会话长期状态的持久化。


## 三、功能二：记录饮食与热量摄入

### 3.1 食物热量数据源

不建议完全依赖 LLM“估算”。更可靠的做法是接入结构化食物营养数据库。可用的中国食物数据源包括：

- **cn-food-mcp**：提供约 1725 种中国食物的营养数据，每 100g 含 25 项营养素，支持 `search_food` 和 `get_nutrition` 工具调用。
- **中国食物成分表第 6 版 JSON 数据**：GitHub 上有开源项目将标准版表格转为 JSON，包含能量和一般营养成分。

食物查询工具的设计：

```python
def search_food(query: str) -> list[FoodCandidate]:
    """按名称搜索食物，返回候选列表（id, name, energy_per_100g）"""

def get_nutrition(food_id: int, grams: float) -> NutritionResult:
    """按克数计算实际热量和宏量营养素"""
```

### 3.2 多轮信息抽取槽位

用户说“中午吃了一碗牛肉面”，Agent 需要补齐的槽位是：

| 槽位 | 示例 | 缺失时处理 |
|---|---|---|
| 日期 | 今天 / 2025-XX-XX | 默认今天 |
| 餐次 | 早餐 / 午餐 / 晚餐 / 零食 | 追问或按时段推断 |
| 食物名称 | 牛肉面 | 必须 |
| 份量 | 一碗 / 约 400g / 大碗 | 缺失时用标准份量估算并**明确告知用户这是估算** |
| 热量 | 由 food_lookup 计算 | 不由 LLM 直接编造 |

如果用户一次报了多项（“中午吃了牛肉面和一杯奶茶”），Agent 应拆成多条 `log_meal` 调用，每一条独立关联 food_id 和 grams。

写入后返回当日热量汇总：

```text
已记录：午餐 · 牛肉面（约 400g）≈ 480 kcal
今日累计：早餐 320 + 午餐 480 + 零食 150 = 950 kcal
今日目标：1,500 kcal，剩余额度 550 kcal
```

**餐次默认规则**：12 点前→早餐，11–14 点→午餐，17–20 点→晚餐，其余时段或明确说“零食/加餐”→零食。不确定时追问一次，而不是猜测。


## 四、功能三：体重数据更新 BMI

### 4.1 BMI 计算与分类

```python
def compute_bmi(weight_kg: float, height_m: float) -> dict:
    bmi = weight_kg / (height_m ** 2)
    if bmi < 18.5:       category = "偏瘦"
    elif bmi < 24:       category = "正常"
    elif bmi < 28:       category = "超重"
    else:                category = "肥胖"
    return {"bmi": round(bmi, 1), "category": category}
```

采用 WHO 亚洲标准：正常 18.5–23.9，超重 24–27.9，肥胖 ≥28。

### 4.2 体重更新触发的联动

用户报告体重后，Agent 除了写入 `weight_log`，还应触发：

1. **重新计算 BMI**，写入 `bmi_history`；
2. **重新计算 BMR / TDEE**（如果用户档案中的体重参与计算）。采用 Mifflin–St Jeor 公式：

   - 男性：`BMR = 10×体重 + 6.25×身高(cm) − 5×年龄 + 5`
   - 女性：`BMR = 10×体重 + 6.25×身高(cm) − 5×年龄 − 161`
   - `TDEE = BMR × 活动系数`（久坐 1.2，轻度 1.375，中度 1.55，高度 1.725）

3. **判断是否需要调整热量目标**。如果体重持续下降，TDEE 会降低，原缺口可能变得过大或过小，Agent 应提示用户“你的 TDEE 已从 2100 更新为 2050，建议日摄入目标从 1600 调整为 1550”。

安全边界：女性日摄入不低于 1200 kcal，男性不低于 1500 kcal；热量缺口建议控制在 300–500 kcal/天，或 500–750 kcal/天范围内，避免过大缺口。


## 五、功能四：合理食谱与补救措施

### 5.1 食谱生成逻辑

食谱不是“每顿都生成新菜谱”，而是**基于当日剩余热量和宏量营养素缺口**做分配。工具定义：

```python
def generate_meal_plan(
    remaining_kcal: float,
    remaining_protein_g: float,
    meal_type: str,
    preferences: list[str],
    restrictions: list[str],
) -> MealPlan
```

宏量分配建议：减脂期蛋白质可设为每日总热量的 20–30%，慢碳 40–50%，优质脂肪 20–30%；蛋白质按体重可参考 1.6–2.2 g/kg 范围内设定。食谱生成时优先从食物库中筛选符合剩余额度的组合，而不是让 LLM 凭空写菜名。

### 5.2 补救措施：区分“超了”与“没超”

**场景 A：某一餐吃多了，但当日总热量尚未明显超标。**
Agent 不需要“惩罚性补救”，只需要在剩余餐次中调整：

- 如果超出约 200–300 kcal，建议下一餐减少主食约 1/3、增加蔬菜体积，蛋白质保持不降；
- 如果超出 500 kcal 以上，下一餐可采用“蛋白质 + 大量蔬菜 + 极少量慢碳”的结构，但不建议跳过正餐。

**场景 B：当日已经明确超标较多，或连续多日超标。**
此时给出的是**多日平衡策略**，而不是“明天断食”：

- 未来 3 天内每日额外增加 100–150 kcal 的活动消耗，或每日饮食减少 100–150 kcal，把多余热量分摊到多日；
- 不建议用极端节食“补回来”，因为过度节食会触发代谢补偿，反而拖慢减脂。

**场景 C：用户吃了高盐/高油的一餐，感觉“肿了”。**
先区分是脂肪增加还是水分潴留。高盐饮食导致的水肿通常在 1–3 天内自然代谢，Agent 应明确告诉用户“这不是长胖”，避免引发焦虑性极端行为。同时建议次日增加饮水和钾含量较高的蔬菜，而不是减少饮水。


## 六、功能五：规划放纵餐

放纵餐不是“随便吃”，而是**有计划的热量窗口**。规则可参考：

- **频率**：一般每周不超过 1 次，每月不超过 4 次；BMI ≥24 者可每周 1 次，BMI <24 者可每 10 天 1 次。
- **热量额度**：以日常摄入目标的 10–15% 作为额外放纵额度。例如日常目标 1500 kcal，放纵餐可控制在 1650–1725 kcal 左右，集中在 1–2 餐。
- **时间**：可安排在训练后 2 小时内，或傍晚 17–19 点皮质醇较低时段。
- **结构**：避免纯糖脂组合，尽量保留蛋白质和复合碳水的基础框架。

工具定义：

```python
def plan_cheat_meal(
    daily_target_kcal: float,
    bmi: float,
    last_cheat_date: date | None,
    training_schedule: dict | None,
) -> CheatMealPlan:
    """
    返回：建议日期、热量上限、推荐时段、结构建议、
          以及“这一餐之后不需要额外惩罚”的说明
    """
```

Agent 还应在放纵餐**之后**主动做一次轻量追踪：询问用户实际吃了什么，记录热量，如果超出计划额度，自动转入功能四的“补救”逻辑。这样放纵餐就形成了一个闭环，而不是一个孤立事件。


## 七、系统提示词草稿

```text
你是一个减脂支持 Agent，角色是“知识科普型教练”，不是医生或注册营养师。
你的任务：帮助用户记录饮食、追踪体重与 BMI、在合理范围内调整食谱、
规划偶尔的放纵餐，并在用户吃多时提供平衡策略而不是惩罚性建议。

【安全边界】
- 不诊断疾病，不调整药物，不给出极端节食方案。
- 如果用户提到孕期、哺乳期、糖尿病、高血压、进食障碍史、
  正在服用影响体重/代谢的药物，或要求“一周瘦 5 公斤”等极端目标，
  不给出具体饮食/热量方案，改为建议咨询医生或注册营养师。
- 女性每日摄入不低于 1200 kcal，男性不低于 1500 kcal；
  不建议连续多日热量缺口超过 750 kcal/天。

【工具使用原则】
- 热量数据优先从 food_lookup 工具获取，不凭空编造食物热量。
- 份量缺失时用标准份量估算，并在回复中明确标注“估算”。
- 体重更新后必须重新计算 BMI 和 TDEE，并判断是否提示调整目标。
- 食谱和补救方案必须基于“剩余热量/剩余宏量营养素”生成，
  不输出与当日数据无关的通用模板。

【沟通风格】
- 鼓励、具体、不评判。用户吃多时不使用“失败”“破功”等措辞。
- 每次饮食记录后返回简明确认和当日汇总，不堆砌无关建议。
- 需要追问时一次只问一个最关键的信息。
```


## 八、核心流程伪代码

```python
async def handle_message(user_id: str, text: str, session_id: str):
    history = load_short_term(session_id)
    profile = load_profile(user_id)

    intent = await route_intent(text, history, profile)

    if intent == "log_meal":
        meals = extract_meals(text, history)          # LLM 抽取槽位
        for meal in meals:
            candidates = search_food(meal.food_name)
            chosen = await resolve_food(candidates, meal, history)
            nutrition = get_nutrition(chosen.id, meal.grams or default_grams(meal))
            save_meal_log(user_id, meal, nutrition)
        summary = get_daily_summary(user_id, today())
        reply = format_log_confirmation(meals, summary)

    elif intent == "update_weight":
        weight = extract_weight(text)
        save_weight_log(user_id, weight, today())
        bmi = compute_bmi(weight, profile.height_m)
        tdee = compute_tdee(weight, profile)
        suggested_target = adjust_target(tdee, bmi, profile.goal)
        reply = format_weight_update(weight, bmi, tdee, suggested_target)

    elif intent == "request_plan":
        summary = get_daily_summary(user_id, today())
        plan = generate_meal_plan(
            remaining_kcal=summary.remaining_kcal,
            remaining_protein_g=summary.remaining_protein,
            meal_type=intent.meal_type,
            preferences=profile.preferences,
            restrictions=profile.restrictions,
        )
        reply = format_plan(plan)

    elif intent == "request_recovery":
        overage = get_overage(user_id, today())
        recovery = generate_recovery_plan(overage, profile)
        reply = format_recovery(recovery)

    elif intent == "plan_cheat_meal":
        plan = plan_cheat_meal(
            daily_target_kcal=profile.daily_target,
            bmi=compute_bmi(profile.weight, profile.height_m)["bmi"],
            last_cheat_date=profile.last_cheat_date,
            training_schedule=profile.training_schedule,
        )
        reply = format_cheat_plan(plan)

    save_short_term(session_id, text, reply)
    return reply
```


## 九、存储 schema（最小可用版）

```sql
-- 用户档案
CREATE TABLE user_profile (
    user_id TEXT PRIMARY KEY,
    height_m REAL,
    age INTEGER,
    sex TEXT,
    activity_factor REAL,
    goal_weight_kg REAL,
    daily_target_kcal REAL,
    preferences_json TEXT,
    restrictions_json TEXT
);

-- 饮食日志
CREATE TABLE meal_log (
    id INTEGER PRIMARY KEY,
    user_id TEXT,
    log_date DATE,
    meal_type TEXT,          -- breakfast/lunch/dinner/snack
    food_name TEXT,
    food_id INTEGER,
    grams REAL,
    kcal REAL,
    protein_g REAL,
    fat_g REAL,
    carb_g REAL,
    is_estimated BOOLEAN,
    created_at TIMESTAMP
);

-- 体重与 BMI 历史
CREATE TABLE weight_log (
    id INTEGER PRIMARY KEY,
    user_id TEXT,
    log_date DATE,
    weight_kg REAL,
    bmi REAL,
    bmi_category TEXT,
    created_at TIMESTAMP
);

-- 长期记忆 / 偏好笔记
CREATE TABLE agent_memory (
    id INTEGER PRIMARY KEY,
    user_id TEXT,
    memory_type TEXT,        -- preference / behavior / note
    content TEXT,
    relevance_score REAL DEFAULT 1.0,
    created_at TIMESTAMP,
    expires_at TIMESTAMP
);
```


## 十、实施建议

**第一版最小闭环**：先实现 `log_meal`、`update_weight` 和 `get_daily_summary` 三个工具，配合 SQLite 短期记忆，确认用户可以自然地汇报“吃了什么”和“体重多少”，Agent 能正确写入和汇总。这一步做稳之后，再接入 `generate_meal_plan`、`generate_recovery_plan` 和 `plan_cheat_meal`。

**食物数据**：如果不想维护食物库，可以直接接入 `cn-food-mcp`，它已经提供了搜索和营养查询的工具接口。但要注意，自动匹配可能遇到“一碗牛肉面”这种复合菜品匹配不到精确条目，此时需要 Agent 追问或使用同类近似值并标注估算。

**安全兜底要放在工具层而不只是 prompt 里**：最低热量限制、缺口上限、极端目标拦截，应该在代码中做硬校验。仅仅在系统提示词里写“不要建议极端节食”，模型仍可能在用户反复要求下松动；在 `generate_meal_plan` 和 `adjust_target` 中加入 `assert target_kcal >= 1200 or sex == "male" and target_kcal >= 1500` 这类约束，才是可靠的边界。

**放纵餐需要“事后追踪”才算完整**：只规划、不追踪，放纵餐很容易变成数据黑洞。建议在 `plan_cheat_meal` 返回时同时写入一条 `pending_cheat_tracking` 记录，到预定日期后主动询问用户实际摄入，自动并入当日汇总。这样放纵餐的数据也能被后续的体重调整和补救逻辑使用，而不是一个被遗忘的例外。