import importlib.util
import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "device_health_check", ROOT / "scripts" / "device_health_check.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class DeviceHealthCheckTest(unittest.TestCase):
    def setUp(self):
        self.identify = {"deviceModel": "NerdQAxe+"}
        self.system = {
            "deviceModel": "NerdQAxe+",
            "asicModel": "BM1368",
            "version": "test",
            "uptimeSeconds": 100,
            "memory": {"freeHeap": 1_000_000, "freeHeapInt": 100_000},
        }
        self.dashboard = {
            "system": {"shutdown": False, "boardError": 0, "overheatTemp": 70},
            "performance": {"hashRate": 2500},
            "power": {"watts": 60},
            "thermal": {
                "asicTemp": 55,
                "vrTemp": 65,
                "fans": [{"speed": 80, "rpm": 2500}, {"speed": 80, "rpm": 2200}],
            },
            "stratum": {"pools": [{"connected": True}]},
        }
        self.settings = {"fans": [{"label": "M2"}, {"label": "M1"}]}

    def test_healthy_miner_reports_efficiency_without_errors(self):
        summary, findings = MODULE.evaluate_health(
            self.identify, self.system, self.dashboard, self.settings, "NerdQAxe+"
        )
        self.assertTrue(summary["healthy"])
        self.assertAlmostEqual(summary["efficiencyJPerTh"], 24.0)
        self.assertEqual(findings, [])

    def test_fan_stall_is_warning_but_shutdown_and_pool_loss_are_errors(self):
        self.dashboard["thermal"]["fans"][1]["rpm"] = 0
        self.dashboard["system"]["shutdown"] = True
        self.dashboard["stratum"]["pools"][0]["connected"] = False

        summary, findings = MODULE.evaluate_health(
            self.identify, self.system, self.dashboard, self.settings
        )

        self.assertFalse(summary["healthy"])
        self.assertTrue(any(item.level == "WARN" and "M1" in item.message for item in findings))
        self.assertGreaterEqual(sum(item.level == "ERROR" for item in findings), 2)

    def test_expected_version_mismatch_is_an_error(self):
        summary, findings = MODULE.evaluate_health(
            self.identify,
            self.system,
            self.dashboard,
            self.settings,
            expected_version="new-version",
        )

        self.assertFalse(summary["healthy"])
        self.assertTrue(any("firmware version" in item.message for item in findings))


if __name__ == "__main__":
    unittest.main()
