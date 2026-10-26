# 实施说明

本项目是对 `Readme.md` 中「减脂 Agent 方案」的可落地实现，聚焦 **确定性工具层 + 持久化**，并已接入可替换的 LLM 适配层（DeepSeek）：有 API key 时走 LLM 意图判定 / 抽槽，无 key 或调用失败时自动回退到规则路由 + 确定性槽位抽取。

## 一、运行方式

```bash
# 1) 启动（首次会引导建立个人档案）
python main.py

# 2) 运行测试（零依赖，标准库 unittest）
python -m unittest discover -s tests -v
```

数据默认落在 `data/loseweight.db`；可用 `--db` 指定路径、`--user` 指定用户。

LLM 模式（可选）：在项目根目录创建 `.env`（参考 `.env.example`）填入
`DEEPSEEK_API_KEY`，启动后自动启用 DeepSeek 意图判定与抽槽；未配置时自动回退规则版。

## 二、项目结构

```
loseweight/
  config.py        # 常量、安全边界、活动系数、放纵餐参数
  calculations.py  # 纯函数：BMI/BMR/TDEE/目标/宏量/餐次推断
  db.py            # SQLite 连接、schema、种子初始化
  foods.py         # 内置食物库 + search_food/get_nutrition
  repository.py    # 数据访问（profile/meal/weight/conversation/memory/plan）
  services.py      # 业务工具：log_meal/update_weight/summary/plan/recovery/cheat
  memory.py        # 持久化对话 + 长期记忆
  agent.py         # 意图路由 + 槽位抽取 + 编排（LLM 优先、规则回退）
  llm.py           # DeepSeek 适配层（意图分类 / 抽槽 / JSON 解析 / 配置）
main.py            # CLI 入口
tests/             # unittest 测试
```

## 三、相对原设计的修正

1. **补 `conversation` 表**：功能一「持久化对话」真正落库（原 schema 缺失）。
2. **统一 BMI 存储**：BMI 并入 `weight_log`（原正文的 `bmi_history` 与 schema 不一致）。
3. **补 `food` 表**：内置常见中式食物种子，后续可用 cn-food-mcp 批量导入替换。
4. **补 `plan_log` / `cheat_meal_plan`**：计划与放纵餐可追踪，闭环「放纵餐事后追踪」。
5. **修正餐次时段重叠**：4–10 早餐 / 11–14 午餐 / 15–16 加餐 / 17–20 晚餐 / 其余零食。
6. **目标可追溯**：`weight_log` 记录 `tdee / suggested_target_kcal`，目标变化可回溯。
7. **补宏量目标存储**：`user_profile` 增加 `daily_protein_g / fat_g / carb_g`。
8. **活动系数枚举化**：`sedentary/light/moderate/active` 替代裸 float。
9. **幂等去重**：`log_meal` 检测同餐重复记录并提示。
10. **安全兜底在代码层**：`adjust_target` 强制下限（女 1200 / 男 1500 kcal）、缺口上限 750 kcal。

## 四、后续扩展

- 已接入 `loseweight/llm.py`（DeepSeek，OpenAI 兼容协议）：`agent` 在 LLM 可用时用 `route_intent_llm` / `extract_meals_llm` 做意图与抽槽，失败自动回退规则版；热量计算仍在 `foods.get_nutrition` 等确定性层完成。可替换为其它兼容服务（OpenAI / Claude / Ollama）或 LangGraph，服务层与工具签名不变。
- 用 cn-food-mcp / 中国食物成分表第 6 版 JSON 批量导入 `food` 表。
- 增加 `cheat_meal_plan.status` 的「事后追踪」追问流程（pending → tracked）。
