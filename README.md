# 减脂 Agent · LoseWeight Helper

一个本地优先的中文减脂助理：记录饮食与热量、追踪体重 / BMI、推荐食谱、给出补救建议、规划放纵餐。核心计算（热量 / 宏量 / BMI / TDEE）全部落在确定性代码层，**LLM 只负责意图理解与信息抽取**；未配置 API key 时自动回退到纯规则模式，离线也能用。

## 功能

- 🥗 **饮食记录**：按「食物 + 克数」精确计算热量与宏量营养素；支持「轻食 / 估算」直接按热量记录（难以精确计算时）；同餐重复自动检测
- ⚖️ **体重追踪**：汇报体重后自动更新 BMI / TDEE / 建议日摄入，目标变化可回溯
- 📋 **食谱推荐**：一次生成三套互不相同的方案，偏好食物优先、近期重复食物降权
- 🩹 **补救措施**：吃多了 / 水肿等场景的调整建议
- 🎉 **放纵餐规划**：按 BMI / 上次放纵时间给出建议
- 🧠 **持久化记忆**：短期对话 + 长期用户档案（偏好 / 忌口），SQLite 存储
- 🤖 **可替换 LLM**：DeepSeek（OpenAI 兼容协议），无 key 自动回退规则模式

## 快速开始

```bash
git clone https://github.com/weichuankong/LoseWeightHelper.git
cd LoseWeightHelper
python main.py        # 首次运行会引导建立个人档案
```

核心功能仅依赖 Python 标准库（`sqlite3` / `unittest` / `argparse`）；`requests` 可选（缺失时 LLM 层自动回退到 `urllib`）：

```bash
pip install -r requirements.txt   # 可选
```

可选启用 LLM（不配置则走规则模式）：

```bash
cp .env.example .env   # 然后填入 DEEPSEEK_API_KEY
```

## 使用示例

```
你> 午餐吃了鸡胸肉 150 克
Agent> 已记录：午餐 · 鸡胸肉 150g ≈ 200 kcal ...

你> 午餐轻食约 470 大卡       # 无法精确计算时按热量估算
你> 今天吃了什么              # 当日热量汇总
你> 晚餐吃什么                # 三套互不相同的推荐方案
你> 我喜欢吃鸡胸肉、虾         # 设置偏好，推荐时优先
你> 撤销刚才                  # 更正最近一条记录
```

## 项目结构

```
loseweight/
  config.py        # 常量、安全边界、活动系数
  calculations.py  # 纯函数：BMI / BMR / TDEE / 目标 / 宏量 / 餐次推断
  db.py            # SQLite 连接、schema、种子初始化
  foods.py         # 内置食物库 + 俗称映射 + 营养换算
  repository.py    # 数据访问层（profile / meal / weight / memory / plan）
  services.py      # 业务工具：log_meal / summary / plan / recovery / cheat
  memory.py        # 持久化对话 + 长期记忆
  agent.py         # 意图路由 + 槽位抽取 + 编排（LLM 优先、规则回退）
  llm.py           # DeepSeek 适配层（意图分类 / 抽槽 / JSON 解析）
main.py            # CLI 入口
tests/             # unittest 测试
```

## 测试

```bash
python -m unittest discover -s tests
```

## 数据与隐私

`.env` 与 `data/` 已被 `.gitignore` 排除，API key 与个人数据不会进入仓库。

## 文档

- [`docs/DESIGN.md`](docs/DESIGN.md) —— 原始方案设计（槽位抽取、伪代码、schema）
- [`IMPLEMENTATION.md`](IMPLEMENTATION.md) —— 实施说明与相对原设计的修正

## 许可证

[MIT](LICENSE) © 2026 Chenkai Wang
