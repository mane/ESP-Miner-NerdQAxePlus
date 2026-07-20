from pathlib import Path
import unittest


REPO = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (REPO / relative_path).read_text()


def _function_body(source: str, signature: str) -> str:
    start = source.index(signature)
    brace_start = source.index("{", start)
    depth = 0
    for index in range(brace_start, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[brace_start:index + 1]
    raise AssertionError(f"Could not find body for {signature}")


class PerChipHashrateTelemetryContractTest(unittest.TestCase):
    def test_monitor_copies_one_safe_coherent_snapshot(self) -> None:
        header = _read("main/tasks/hashrate_monitor_task.h")
        source = _read("main/tasks/hashrate_monitor_task.cpp")

        self.assertIn("struct ChipHashrateSample", header)
        self.assertIn("float hashrateGhs = 0.0f", header)
        self.assertIn("uint32_t ageMs = UINT32_MAX", header)
        self.assertIn("mutable pthread_mutex_t m_mutex", header)
        self.assertIn("copyChipHashrateSnapshot", header)

        snapshot = _function_body(
            source, "size_t HashrateMonitor::copyChipHashrateSnapshot"
        )
        self.assertLess(snapshot.index("pthread_mutex_lock(&m_mutex)"),
                        snapshot.index("m_chipHashrate[i]"))
        self.assertLess(snapshot.index("m_chipHashrateUpdatedMs[i]"),
                        snapshot.index("pthread_mutex_unlock(&m_mutex)",
                                       snapshot.index("m_chipHashrateUpdatedMs[i]")))
        self.assertIn("isfinite(hashrate) && hashrate >= 0.0f", snapshot)
        self.assertIn("updatedMs == 0 ? UINT32_MAX : now32 - updatedMs", snapshot)

    def test_dashboard_publishes_aligned_per_chip_arrays(self) -> None:
        dashboard = _function_body(
            _read("main/http_server/v2/handler_v2_dashboard.cpp"),
            "esp_err_t GET_V2_dashboard",
        )
        self.assertIn('perf["chipHashrates"].to<JsonArray>()', dashboard)
        self.assertIn('perf["chipHashrateAgesMs"].to<JsonArray>()', dashboard)
        self.assertEqual(dashboard.count("copyChipHashrateSnapshot("), 1)
        self.assertIn("for (int i = 0; i < asicCount; ++i)", dashboard)
        self.assertIn("chipHashrates.add(0.0f)", dashboard)
        self.assertIn("chipHashrateAgesMs.add(UINT32_MAX)", dashboard)

    def test_web_contract_and_fallback_include_arrays(self) -> None:
        model = _read("main/http_server/axe-os/src/app/models/IDashboardV2.ts")
        service = _read("main/http_server/axe-os/src/app/services/system.service.ts")

        self.assertIn("chipHashrates: number[]", model)
        self.assertIn("chipHashrateAgesMs: number[]", model)
        self.assertIn("chipHashrates: []", service)
        self.assertIn("chipHashrateAgesMs: []", service)


if __name__ == "__main__":
    unittest.main()
