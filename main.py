"""减脂 Agent 命令行入口。"""
from __future__ import annotations

import argparse

from loseweight import agent as agent_mod
from loseweight import db
from loseweight import repository as repo
from loseweight import services


def setup_profile(conn, user_id: str) -> None:
    print("=" * 52)
    print("首次使用，请建立个人档案（只需一次）")
    print("=" * 52)
    sex = input("性别（男/女 或 male/female）：").strip().lower()
    sex = "male" if sex in ("男", "male", "m") else "female"
    height_cm = float(input("身高（cm）：").strip())
    age = int(input("年龄（岁）：").strip())
    print("活动水平：sedentary=久坐 | light=轻度 | moderate=中度 | active=高度")
    level = input("活动水平（默认 sedentary）：").strip() or "sedentary"
    goal = float(input("目标体重（kg）：").strip())
    weight = float(input("当前体重（kg）：").strip())

    services.ensure_profile(
        conn, user_id, sex=sex, height_cm=height_cm, age=age,
        activity_level=level, goal_weight_kg=goal,
    )
    res = services.update_weight(conn, user_id, weight_kg=weight)
    bmi = res["bmi"]
    print("\n档案已建立：")
    print(f"  性别 {sex}，身高 {height_cm:.0f}cm，年龄 {age}，活动水平 {level}")
    print(f"  当前体重 {weight:.1f}kg，BMI {bmi['bmi']}（{bmi['category_label']}）")
    print(f"  TDEE {res['tdee']:.0f} kcal，建议日摄入 {res['target']['target_kcal']} kcal")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="减脂支持 Agent")
    parser.add_argument("--db", default=None, help="数据库文件路径（默认 data/loseweight.db）")
    parser.add_argument("--user", default="default", help="用户 ID")
    args = parser.parse_args()

    db_path = args.db or db.DEFAULT_DB_PATH
    with db.session(db_path) as conn:
        db.init_db(conn)
        conn.commit()
        if repo.get_profile(conn, args.user) is None:
            setup_profile(conn, args.user)
            conn.commit()

        ag = agent_mod.Agent(conn, user_id=args.user)
        mode = "LLM（DeepSeek）" if ag.llm is not None else "规则模式（未配置 API key）"
        print(f"减脂 Agent 已就绪（{mode}）。输入「帮助」查看功能，输入「退出」结束。")
        while True:
            try:
                text = input("\n你> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n再见！")
                break
            if text in ("退出", "exit", "quit", "q"):
                print("再见！")
                break
            print(f"\nAgent> {ag.handle(text)}")


if __name__ == "__main__":
    main()
