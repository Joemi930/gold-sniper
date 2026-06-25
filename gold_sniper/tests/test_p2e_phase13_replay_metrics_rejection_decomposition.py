import unittest

from gold_sniper.replay.replay_metrics import build_replay_metrics


def _metrics():
    decisions = [
        {
            "decision": "WATCH_ONLY",
            "setup_type": "POI_REACTION",
            "setup_grade": "C",
            "poi_rejection_code": "POI_UNCLASSIFIED_LEGACY_REJECTED",
            "poi_rejection_severity": "RECOVERABLE",
            "poi_rejection_recoverable": True,
            "poi_rejection_fatal": False,
            "micro_contract_status": "MICRO_CONFIRMED",
            "micro_confirmed": True,
            "poi_micro_synergy": True,
        },
        {
            "decision": "REJECT",
            "setup_type": "SWEEP_REVERSAL",
            "setup_grade": "D",
            "poi_rejection_code": "POI_DIRECTION_MISMATCH",
            "poi_rejection_severity": "FATAL",
            "poi_rejection_recoverable": False,
            "poi_rejection_fatal": True,
            "micro_contract_status": "MICRO_OUTSIDE_POI",
            "poi_micro_synergy": False,
        },
    ]
    return build_replay_metrics(
        decisions,
        symbol="XAUUSD",
        timeframe="1m",
        date_start=None,
        date_end=None,
        data_profile={},
    )


class TestP2EPhase13ReplayMetricsRejectionDecomposition(unittest.TestCase):
    def test_exposes_code_distribution(self):
        metrics = _metrics()
        self.assertEqual(metrics["poi_rejection_code_distribution"]["POI_UNCLASSIFIED_LEGACY_REJECTED"], 1)

    def test_exposes_recoverable_count(self):
        self.assertEqual(_metrics()["poi_rejection_recoverable_count"], 1)

    def test_exposes_fatal_count(self):
        self.assertEqual(_metrics()["poi_rejection_fatal_count"], 1)

    def test_exposes_micro_confirmed_recoverable_count(self):
        self.assertEqual(_metrics()["micro_confirmed_recoverable_poi_count"], 1)

    def test_exposes_synergy_by_rejection_code(self):
        metrics = _metrics()
        self.assertEqual(
            metrics["poi_micro_synergy_by_rejection_code"]["POI_UNCLASSIFIED_LEGACY_REJECTED"]["synergy_true"],
            1,
        )


if __name__ == "__main__":
    unittest.main()
