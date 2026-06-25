from __future__ import annotations

import unittest

from gold_sniper.strategy.poi_star_rating import evaluate_poi_star_rating


class TestPoiStarRating(unittest.TestCase):
    def test_poi_five_star_ob(self) -> None:
        result = evaluate_poi_star_rating(
            {
                "poi_type": "OB",
                "has_fvg": True,
                "sweep_before_creation": True,
                "extreme_of_range": True,
                "lifecycle": "FRESH",
                "created_session": "NY_KILLZONE",
            }
        )
        self.assertEqual(result.stars, 5)
        self.assertTrue(result.is_5_star_ob)
        self.assertEqual(result.grade, "A+")

    def test_ob_fvg_stack_five_criteria_is_five_star_ob_like(self) -> None:
        result = evaluate_poi_star_rating(
            {
                "poi_type": "OB_FVG_STACK",
                "has_fvg": True,
                "sweep_before_creation": True,
                "extreme_of_range": True,
                "lifecycle": "FRESH",
                "created_session": "LONDON_KILLZONE",
            }
        )
        self.assertEqual(result.stars, 5)
        self.assertTrue(result.is_5_star_ob)
        self.assertTrue(result.is_5_star_poi)

    def test_poi_four_star_is_near_miss_not_invalid(self) -> None:
        result = evaluate_poi_star_rating(
            {
                "poi_type": "OB",
                "has_fvg": True,
                "sweep_before_creation": True,
                "extreme_of_range": True,
                "lifecycle": "FRESH",
            }
        )
        self.assertEqual(result.stars, 4)
        self.assertTrue(result.near_miss)
        self.assertNotEqual(result.grade, "INVALID")

    def test_fvg_three_star(self) -> None:
        result = evaluate_poi_star_rating(
            {"poi_type": "FVG", "lifecycle": "FRESH", "aligned_with_bias": True, "premium_discount_ok": True}
        )
        self.assertTrue(result.is_3_star_fvg)
        self.assertEqual(result.stars, 3)

    def test_fvg_alone_does_not_become_five_star_ob(self) -> None:
        result = evaluate_poi_star_rating(
            {"poi_type": "FVG", "lifecycle": "FRESH", "aligned_with_bias": True, "premium_discount_ok": True}
        )
        self.assertFalse(result.is_5_star_ob)
        self.assertFalse(result.is_5_star_poi)

    def test_bpr_detected_as_criterion(self) -> None:
        result = evaluate_poi_star_rating({"poi_type": "FVG", "bpr_available": True})
        self.assertTrue(result.criteria["bpr_available"])


if __name__ == "__main__":
    unittest.main()
