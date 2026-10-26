import os
import unittest

from loseweight import agent, db, foods

# 测试环境禁用 LLM（空 key 覆盖 .env 中的真实 key），确保走确定性规则、避免网络依赖
os.environ["DEEPSEEK_API_KEY"] = ""
from loseweight import repository as repo
from loseweight import services


def make_profile(conn, user_id="u1", sex="male", height=175, age=30,
                 level="light", goal=70, weight=80):
    services.ensure_profile(
        conn, user_id, sex=sex, height_cm=height, age=age,
        activity_level=level, goal_weight_kg=goal,
    )
    return services.update_weight(conn, user_id, weight_kg=weight)


class BaseTest(unittest.TestCase):
    def setUp(self):
        os.environ["DEEPSEEK_API_KEY"] = ""  # 禁用 LLM，确保走确定性规则
        self.conn = db.connect(":memory:")
        db.init_db(self.conn)

    def tearDown(self):
        self.conn.close()


class TestFoodDifferentiation(BaseTest):
    def test_buckwheat_not_noodles(self):
        entries = agent.extract_meals(self.conn, "小半份荞麦面")
        names = [e["name"] for e in entries]
        self.assertIn("荞麦面（煮）", names)
        self.assertNotIn("面条（煮）", names)

    def test_skinless_thigh_not_thigh(self):
        entries = agent.extract_meals(self.conn, "去皮鸡腿一个")
        names = [e["name"] for e in entries]
        self.assertIn("去皮鸡腿", names)
        self.assertNotIn("鸡腿", names)

    def test_beef_alias(self):
        entries = agent.extract_meals(self.conn, "吃了牛肉 150 克")
        names = [e["name"] for e in entries]
        self.assertIn("瘦牛肉", names)

    def test_beef_alias_not_triggered_by_beef_noodle(self):
        found = agent.find_foods_in_text(self.conn, "牛肉面")
        names = [f["name"] for f in found]
        self.assertIn("牛肉面", names)
        self.assertNotIn("瘦牛肉", names)

    def test_quantity_two_eggs(self):
        entries = agent.extract_meals(self.conn, "两个茶叶蛋")
        agent.apply_quantity("两个茶叶蛋", entries)
        self.assertEqual(entries[0]["grams"], 100.0)

    def test_quantity_no_double_scale(self):
        entries = agent.extract_meals(self.conn, "茶叶蛋")
        entries[0]["grams"] = 100.0  # 模拟 LLM 已折算成两个蛋
        agent.apply_quantity("两个茶叶蛋", entries)
        self.assertEqual(entries[0]["grams"], 100.0)

    def test_new_foods_present(self):
        self.assertIsNotNone(foods.find_food_by_name(self.conn, "茶叶蛋"))
        self.assertIsNotNone(foods.find_food_by_name(self.conn, "油菜心"))
        self.assertIsNotNone(foods.find_food_by_name(self.conn, "烤蔬菜（混合）"))


class TestUpdateWeight(BaseTest):
    def test_update_weight(self):
        r = make_profile(self.conn)
        self.assertEqual(r["weight_kg"], 80)
        self.assertGreater(r["bmi"]["bmi"], 0)
        self.assertGreater(r["tdee"], r["bmr"])
        self.assertIn(r["target"]["goal"], ("lose", "maintain", "gain"))
        p = repo.get_profile(self.conn, "u1")
        self.assertEqual(p["daily_target_kcal"], r["target"]["target_kcal"])


class TestLogMeal(BaseTest):
    def test_log_and_summary(self):
        make_profile(self.conn)
        chicken = foods.find_food_by_name(self.conn, "鸡胸肉")
        r = services.log_meal(
            self.conn, "u1", meal_type="lunch", food_id=chicken["food_id"], grams=150
        )
        self.assertFalse(r["duplicate"])
        self.assertEqual(r["summary"]["total_kcal"], r["entry"]["kcal"])
        self.assertAlmostEqual(r["entry"]["kcal"], chicken["energy_kcal"] * 1.5, places=1)

    def test_duplicate_detection(self):
        make_profile(self.conn)
        chicken = foods.find_food_by_name(self.conn, "鸡胸肉")
        services.log_meal(self.conn, "u1", meal_type="lunch", food_id=chicken["food_id"], grams=150)
        r2 = services.log_meal(self.conn, "u1", meal_type="lunch", food_id=chicken["food_id"], grams=150)
        self.assertTrue(r2["duplicate"])

    def test_log_estimated_meal(self):
        make_profile(self.conn)
        r = services.log_estimated_meal(
            self.conn, "u1", meal_type="lunch", kcal=470, label="轻食"
        )
        self.assertEqual(r["entry"]["kcal"], 470.0)
        self.assertTrue(r["entry"]["is_estimated"])
        self.assertIn("轻食", r["entry"]["name"])
        self.assertEqual(r["summary"]["total_kcal"], 470.0)


class TestUpdateGoal(BaseTest):
    def test_update_goal_lose(self):
        make_profile(self.conn)  # current=80, goal=70
        r = services.update_goal(self.conn, "u1", goal_weight_kg=60)
        self.assertEqual(r["target"]["goal"], "lose")
        p = repo.get_profile(self.conn, "u1")
        self.assertEqual(p["goal_weight_kg"], 60)
        self.assertEqual(p["daily_target_kcal"], r["target"]["target_kcal"])

    def test_update_goal_gain(self):
        make_profile(self.conn)
        r = services.update_goal(self.conn, "u1", goal_weight_kg=90)
        self.assertEqual(r["target"]["goal"], "gain")

    def test_update_goal_invalid(self):
        make_profile(self.conn)
        with self.assertRaises(ValueError):
            services.update_goal(self.conn, "u1", goal_weight_kg=-5)


class TestMealPlan(BaseTest):
    def test_plan(self):
        make_profile(self.conn)
        plan = services.generate_meal_plan(
            self.conn, "u1", remaining_kcal=500, remaining_protein_g=30
        )
        self.assertEqual(plan["type"], "meal_plan")
        self.assertEqual(len(plan["plans"]), 3)
        for p in plan["plans"]:
            self.assertTrue(p["items"])
            self.assertGreater(p["total"]["kcal"], 0)
        proteins = {p["items"][0]["name"] for p in plan["plans"]}
        self.assertEqual(len(proteins), 3)

    def test_three_plans_distinct(self):
        make_profile(self.conn)
        plan = services.generate_meal_plan(
            self.conn, "u1", remaining_kcal=500, remaining_protein_g=30
        )
        combos = {tuple(it["name"] for it in p["items"]) for p in plan["plans"]}
        self.assertEqual(len(combos), 3)

    def test_preferences_prioritized(self):
        make_profile(self.conn)
        chicken = foods.find_food_by_name(self.conn, "鸡胸肉")
        services.set_preferences(self.conn, "u1", [chicken["name"]])
        plan = services.generate_meal_plan(
            self.conn, "u1", remaining_kcal=500, remaining_protein_g=30
        )
        self.assertEqual(plan["plans"][0]["items"][0]["name"], "鸡胸肉")

    def test_minimize_repetition(self):
        make_profile(self.conn)
        p1 = services.generate_meal_plan(
            self.conn, "u1", remaining_kcal=500, remaining_protein_g=30
        )
        p2 = services.generate_meal_plan(
            self.conn, "u1", remaining_kcal=500, remaining_protein_g=30
        )
        self.assertNotEqual(
            p1["plans"][0]["items"][0]["name"],
            p2["plans"][0]["items"][0]["name"],
        )


class TestRecovery(BaseTest):
    def test_recovery_levels(self):
        make_profile(self.conn)
        self.assertEqual(services.generate_recovery_plan(self.conn, "u1")["level"], "none")
        nuts = foods.find_food_by_name(self.conn, "混合坚果")
        services.log_meal(self.conn, "u1", meal_type="snack", food_id=nuts["food_id"], grams=1000)
        self.assertEqual(services.generate_recovery_plan(self.conn, "u1")["level"], "heavy")


class TestCheatMeal(BaseTest):
    def test_cheat(self):
        make_profile(self.conn)
        plan = services.plan_cheat_meal(self.conn, "u1")
        self.assertEqual(plan["type"], "cheat_meal_plan")
        self.assertEqual(len(plan["budget_kcal"]), 2)
        self.assertLess(plan["budget_kcal"][0], plan["budget_kcal"][1])
        self.assertIsNotNone(repo.latest_cheat_meal(self.conn, "u1"))


class TestAgent(BaseTest):
    def test_handle_weight(self):
        make_profile(self.conn)
        ag = agent.Agent(self.conn, user_id="u1")
        reply = ag.handle("今天 79 公斤")
        self.assertIn("已记录体重", reply)
        self.assertIn("BMI", reply)

    def test_handle_update_goal(self):
        make_profile(self.conn)
        ag = agent.Agent(self.conn, user_id="u1")
        reply = ag.handle("目标体重改成 60 公斤")
        self.assertIn("目标体重已更新", reply)
        self.assertIn("kg", reply)

    def test_handle_meal(self):
        make_profile(self.conn)
        ag = agent.Agent(self.conn, user_id="u1")
        reply = ag.handle("午餐吃了鸡胸肉 150 克")
        self.assertIn("已记录", reply)
        self.assertIn("鸡胸肉", reply)

    def test_handle_quantity(self):
        make_profile(self.conn)
        ag = agent.Agent(self.conn, user_id="u1")
        reply = ag.handle("早餐两个茶叶蛋")
        self.assertIn("茶叶蛋 100g", reply)

    def test_handle_summary(self):
        make_profile(self.conn)
        ag = agent.Agent(self.conn, user_id="u1")
        ag.handle("午餐吃了鸡胸肉 150 克")
        self.assertIn("饮食汇总", ag.handle("今天吃了什么"))

    def test_conversation_persisted(self):
        make_profile(self.conn)
        ag = agent.Agent(self.conn, user_id="u1", session_id="s1")
        ag.handle("今天 79 公斤")
        msgs = repo.recent_messages(self.conn, "s1")
        self.assertGreaterEqual(len(msgs), 2)
        roles = [m["role"] for m in msgs]
        self.assertIn("user", roles)
        self.assertIn("assistant", roles)


class TestCorrections(BaseTest):
    def test_route_intents(self):
        self.assertEqual(agent.route_intent("撤销刚才"), "undo_meal")
        self.assertEqual(agent.route_intent("删掉最后一条"), "undo_meal")
        self.assertEqual(agent.route_intent("删掉早餐的鸡蛋"), "delete_meal")
        self.assertEqual(agent.route_intent("把荞麦面改成125g"), "edit_meal")
        self.assertEqual(agent.route_intent("鸡蛋改成鸡胸肉"), "edit_meal")
        self.assertEqual(agent.route_intent("目标体重改成 60 公斤"), "update_goal")
        self.assertEqual(agent.route_intent("10月23日，没吃早餐"), "skip_meal")
        self.assertEqual(agent.route_intent("跳过晚饭"), "skip_meal")
        self.assertEqual(agent.route_intent("我喜欢吃鸡胸肉"), "set_preferences")
        self.assertEqual(agent.route_intent("午餐轻食，大约470大卡"), "log_estimated_meal")

    def test_undo_last_meal(self):
        make_profile(self.conn)
        chicken = foods.find_food_by_name(self.conn, "鸡胸肉")
        egg = foods.find_food_by_name(self.conn, "鸡蛋")
        services.log_meal(self.conn, "u1", meal_type="lunch", food_id=chicken["food_id"], grams=150)
        services.log_meal(self.conn, "u1", meal_type="breakfast", food_id=egg["food_id"], grams=50)
        r = services.undo_last_meal(self.conn, "u1")
        self.assertEqual(r["deleted"]["food_name"], "鸡蛋")
        self.assertEqual(len(repo.get_meals_for_date(self.conn, "u1", repo.today())), 1)

    def test_delete_meal_entry(self):
        make_profile(self.conn)
        egg = foods.find_food_by_name(self.conn, "鸡蛋")
        chicken = foods.find_food_by_name(self.conn, "鸡胸肉")
        services.log_meal(self.conn, "u1", meal_type="breakfast", food_id=egg["food_id"], grams=50)
        services.log_meal(self.conn, "u1", meal_type="lunch", food_id=chicken["food_id"], grams=150)
        r = services.delete_meal_entry(self.conn, "u1", meal_type="breakfast", food_id=egg["food_id"])
        self.assertEqual(r["deleted"]["food_name"], "鸡蛋")
        remaining = repo.get_meals_for_date(self.conn, "u1", repo.today())
        self.assertEqual([m["food_name"] for m in remaining], ["鸡胸肉"])

    def test_delete_meal_not_found(self):
        make_profile(self.conn)
        with self.assertRaises(ValueError):
            services.delete_meal_entry(self.conn, "u1", meal_type="lunch", food_id=999)

    def test_edit_meal_grams(self):
        make_profile(self.conn)
        soba = foods.find_food_by_name(self.conn, "荞麦面（煮）")
        services.log_meal(self.conn, "u1", meal_type="lunch", food_id=soba["food_id"], grams=250)
        r = services.edit_meal_entry(self.conn, "u1", food_id=soba["food_id"], new_grams=125)
        self.assertEqual(r["updated"]["grams"], 125.0)
        self.assertAlmostEqual(r["updated"]["kcal"], soba["energy_kcal"] * 1.25, places=1)

    def test_edit_meal_food(self):
        make_profile(self.conn)
        egg = foods.find_food_by_name(self.conn, "鸡蛋")
        chicken = foods.find_food_by_name(self.conn, "鸡胸肉")
        services.log_meal(self.conn, "u1", meal_type="breakfast", food_id=egg["food_id"], grams=50)
        r = services.edit_meal_entry(self.conn, "u1", food_id=egg["food_id"], new_food_id=chicken["food_id"])
        self.assertEqual(r["updated"]["food_name"], "鸡胸肉")
        self.assertEqual(r["updated"]["grams"], chicken["default_grams"])


class TestCorrectionHandle(BaseTest):
    def test_handle_undo(self):
        make_profile(self.conn)
        ag = agent.Agent(self.conn, user_id="u1")
        ag.handle("午餐吃了鸡胸肉 150 克")
        reply = ag.handle("撤销刚才")
        self.assertIn("已撤销", reply)
        self.assertIn("鸡胸肉", reply)

    def test_handle_delete(self):
        make_profile(self.conn)
        ag = agent.Agent(self.conn, user_id="u1")
        ag.handle("早餐吃了鸡蛋")
        reply = ag.handle("删掉早餐的鸡蛋")
        self.assertIn("已删除", reply)

    def test_handle_edit(self):
        make_profile(self.conn)
        ag = agent.Agent(self.conn, user_id="u1")
        ag.handle("午餐吃了荞麦面")
        reply = ag.handle("把荞麦面改成125g")
        self.assertIn("已修改", reply)
        self.assertIn("125g", reply)

    def test_handle_skip(self):
        make_profile(self.conn)
        ag = agent.Agent(self.conn, user_id="u1")
        reply = ag.handle("10月23日，没吃早餐")
        self.assertIn("没吃", reply)
        self.assertIn("0 kcal", reply)
        self.assertNotIn("没有识别到", reply)

    def test_handle_set_preferences(self):
        make_profile(self.conn)
        ag = agent.Agent(self.conn, user_id="u1")
        reply = ag.handle("我喜欢吃鸡胸肉、虾")
        self.assertIn("偏好", reply)
        p = repo.get_profile(self.conn, "u1")
        self.assertIn("鸡胸肉", p["preferences"])
        self.assertIn("虾", p["preferences"])

    def test_handle_set_preferences_alias(self):
        make_profile(self.conn)
        ag = agent.Agent(self.conn, user_id="u1")
        reply = ag.handle("记录偏好牛肉，记录偏好荞麦面")
        self.assertIn("偏好", reply)
        p = repo.get_profile(self.conn, "u1")
        self.assertIn("瘦牛肉", p["preferences"])
        self.assertIn("荞麦面（煮）", p["preferences"])

    def test_handle_estimated_meal(self):
        make_profile(self.conn)
        ag = agent.Agent(self.conn, user_id="u1")
        reply = ag.handle("记录一下，午餐为轻食，大约470大卡")
        self.assertIn("已记录", reply)
        self.assertIn("轻食", reply)
        self.assertIn("470", reply)


if __name__ == "__main__":
    unittest.main()
