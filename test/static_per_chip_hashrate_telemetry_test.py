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
        lock = snapshot.index("pthread_mutex_lock(&m_mutex)")
        now = snapshot.index("esp_timer_get_time()")
        self.assertLess(lock, now)
        self.assertLess(now, snapshot.index("m_chipHashrate[i]"))
        self.assertLess(snapshot.index("m_chipHashrateUpdatedMs[i]"),
                        snapshot.index("pthread_mutex_unlock(&m_mutex)",
                                       snapshot.index("m_chipHashrateUpdatedMs[i]")))
        self.assertIn("isfinite(hashrate) && hashrate >= 0.0f", snapshot)
        self.assertIn("updatedMs == 0 ? UINT32_MAX : nowMs - updatedMs", snapshot)
        self.assertNotIn("uint64_t nowMs", snapshot)

        # The exact unsigned expression used by the implementation must remain
        # valid when the 32-bit monotonic millisecond counter wraps.
        uint32_max = (1 << 32) - 1
        updated_before_wrap = uint32_max - 4
        now_after_wrap = 3
        self.assertEqual((now_after_wrap - updated_before_wrap) & uint32_max, 8)

    def test_start_state_and_rx_arrays_have_one_mutex_owned_lifetime(self) -> None:
        source = _read("main/tasks/hashrate_monitor_task.cpp")
        start = _function_body(source, "bool HashrateMonitor::start")
        reply = _function_body(source, "void HashrateMonitor::onRegisterReply")

        # Allocations remain local until all four arrays can be published in one
        # critical section. No failure path frees a still-published member.
        publication_lock = start.index("pthread_mutex_lock(&m_mutex)")
        publication = start.index("m_chipHashrate = chipHashrate")
        count_publication = start.index("m_asicCount = asicCount")
        publication_unlock = start.index("pthread_mutex_unlock(&m_mutex)", publication)
        self.assertLess(publication_lock, publication)
        self.assertLess(publication, count_publication)
        self.assertLess(count_publication, publication_unlock)
        self.assertNotIn("delete[] m_chipHashrate", start)
        self.assertNotIn("delete[] m_chipHashrateUpdatedMs", start)
        self.assertNotIn("delete[] m_prevResponse", start)
        self.assertNotIn("delete[] m_prevCounter", start)

        task_failure = start.index("if (xTaskCreatePSRAM")
        detach_lock = start.index("pthread_mutex_lock(&m_mutex)", task_failure)
        count_reset = start.index("m_asicCount = 0", detach_lock)
        pointer_reset = start.index("m_chipHashrate = nullptr", count_reset)
        detach_unlock = start.index("pthread_mutex_unlock(&m_mutex)", pointer_reset)
        first_delete = start.index("delete[] chipHashrate", detach_unlock)
        self.assertLess(detach_lock, count_reset)
        self.assertLess(count_reset, pointer_reset)
        self.assertLess(pointer_reset, detach_unlock)
        self.assertLess(detach_unlock, first_delete)

        # RX counter state and the telemetry update share that same ownership
        # lock, so rollback cannot detach/free an array in use.
        reply_lock = reply.index("pthread_mutex_lock(&m_mutex)")
        self.assertLess(reply_lock, reply.index("m_prevResponse[asic_idx]"))
        self.assertIn("!m_chipHashrateUpdatedMs", reply)
        self.assertIn("!m_prevResponse", reply)
        self.assertNotIn("setChipHashrate", reply)
        self.assertLess(reply.index("m_chipHashrateUpdatedMs[asic_idx] ="),
                        reply.rindex("pthread_mutex_unlock(&m_mutex)"))

    def test_dashboard_publishes_aligned_per_chip_arrays(self) -> None:
        dashboard = _function_body(
            _read("main/http_server/v2/handler_v2_dashboard.cpp"),
            "esp_err_t GET_V2_dashboard",
        )
        self.assertIn('perf["chipHashrates"].to<JsonArray>()', dashboard)
        self.assertIn('perf["chipHashrateAgesMs"].to<JsonArray>()', dashboard)
        self.assertEqual(dashboard.count("copyChipHashrateSnapshot("), 1)
        self.assertNotIn("esp_timer_get_time() / 1000ULL", dashboard)
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
