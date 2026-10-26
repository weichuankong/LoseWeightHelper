import unittest

from loseweight import calculations as calc


class TestBMI(unittest.TestCase):
    def test_bmi_value(self):
        r = calc.compute_bmi(70, 175)
        self.assertAlmostEqual(r["bmi"], 22.9, places=1)

    def test_categories(self):
        self.assertEqual(calc.compute_bmi(45, 170)["category"], "underweight")
        self.assertEqual(calc.compute_bmi(60, 170)["category"], "normal")
        self.assertEqual(calc.compute_bmi(75, 170)["category"], "overweight")
        self.assertEqual(calc.compute_bmi(90, 170)["category"], "obese")

    def test_invalid_height(self):
        with self.assertRaises(ValueError):
            calc.compute_bmi(70, 0)


class TestBMR(unittest.TestCase):
    def test_male(self):
        expected = 10 * 80 + 6.25 * 175 - 5 * 30 + 5
        self.assertAlmostEqual(calc.compute_bmr(80, 175, 30, "male"), expected, places=1)

    def test_female(self):
        expected = 10 * 60 + 6.25 * 165 - 5 * 30 - 161
        self.assertAlmostEqual(calc.compute_bmr(60, 165, 30, "female"), expected, places=1)


class TestTDEE(unittest.TestCase):
    def test_factors(self):
        self.assertAlmostEqual(calc.compute_tdee(1000, "sedentary"), 1200, places=1)
        self.assertAlmostEqual(calc.compute_tdee(1000, "active"), 1725, places=1)


class TestAdjustTarget(unittest.TestCase):
    def test_lose(self):
        r = calc.adjust_target(2500, "male", 90, 80)
        self.assertEqual(r["goal"], "lose")
        self.assertEqual(r["target_kcal"], 2000)

    def test_floor_female(self):
        r = calc.adjust_target(1500, "female", 60, 55)
        self.assertGreaterEqual(r["target_kcal"], 1200)

    def test_maintain(self):
        r = calc.adjust_target(2000, "male", 70, 70)
        self.assertEqual(r["goal"], "maintain")
        self.assertAlmostEqual(r["target_kcal"], 2000)


class TestMacros(unittest.TestCase):
    def test_protein_by_weight(self):
        m = calc.compute_macro_targets(80, 1800)
        self.assertAlmostEqual(m["protein_g"], 80 * 1.8, places=1)
        self.assertGreater(m["carb_g"], 0)
        self.assertGreater(m["fat_g"], 0)


class TestInferMealType(unittest.TestCase):
    def test_non_overlapping(self):
        self.assertEqual(calc.infer_meal_type(8), "breakfast")
        self.assertEqual(calc.infer_meal_type(10), "breakfast")
        self.assertEqual(calc.infer_meal_type(11), "lunch")
        self.assertEqual(calc.infer_meal_type(14), "lunch")
        self.assertEqual(calc.infer_meal_type(15), "snack")
        self.assertEqual(calc.infer_meal_type(17), "dinner")
        self.assertEqual(calc.infer_meal_type(22), "snack")


if __name__ == "__main__":
    unittest.main()
