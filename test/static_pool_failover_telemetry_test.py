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


class PoolFailoverTelemetryContractTest(unittest.TestCase):
    def test_failover_reports_both_configs_and_stats_only_on_active_pool(self) -> None:
        source = _read("main/stratum/stratum_manager_fallback.cpp")
        body = _function_body(source, "void StratumManagerFallback::getManagerInfoJson")

        self.assertIn("for (int i = 0; i < 2; i++)", body)
        self.assertIn("const bool active = i == static_cast<int>(m_selected)", body)
        self.assertIn('pool["active"] = active', body)
        self.assertIn("m_stratumTasks[i]", body)
        self.assertIn("m_pingTasks[i]", body)
        self.assertIn("m_stratumConfig[i]", body)
        self.assertNotIn("m_stratumTasks[m_selected]", body)

        active_branch = body[body.index("if (active)"):]
        for field in (
            "poolDifficulty",
            "networkDifficulty",
            "accepted",
            "rejected",
            "bestDiff",
        ):
            self.assertIn(f'pool["{field}"]', active_branch)
            self.assertEqual(body.count(f'pool["{field}"]'), 1)

    def test_dual_pool_marks_both_config_entries_active(self) -> None:
        body = _function_body(
            _read("main/stratum/stratum_manager_dual_pool.cpp"),
            "void StratumManagerDualPool::getManagerInfoJson",
        )

        self.assertIn("for (int i = 0; i < 2; i++)", body)
        self.assertIn('pool["active"] = true', body)

    def test_dashboard_enrichment_preserves_config_index_mapping(self) -> None:
        body = _function_body(
            _read("main/http_server/v2/handler_v2_dashboard.cpp"),
            "esp_err_t GET_V2_dashboard",
        )

        self.assertIn("Config::getStratumURL(), Config::getStratumFallbackURL()", body)
        self.assertIn('pool["host"] = copiedJsonStringOrEmpty(urls[i])', body)
        self.assertIn('pool["port"] = ports[i]', body)
        self.assertIn('pool["user"] = copiedJsonStringOrEmpty(users[i])', body)

    def test_config_reconnect_resets_only_the_affected_pool_session(self) -> None:
        manager = _function_body(
            _read("main/stratum/stratum_manager.cpp"),
            "void StratumManager::loadSettings(bool reconnect)",
        )
        self.assertLess(
            manager.index("if (!requiresReconnect[i])"),
            manager.index("resetPoolSessionStats(i)"),
        )
        self.assertLess(
            manager.index("resetPoolSessionStats(i)"),
            manager.index("m_verificationCheckCount[i] = 0"),
        )

        fallback_header = _read("main/stratum/stratum_manager_fallback.h")
        fallback_pool_reset = _function_body(
            fallback_header, "virtual void resetPoolSessionStats(int pool) override"
        )
        fallback_manual_reset = _function_body(
            fallback_header, "virtual void resetSessionStats() override"
        )
        self.assertIn("m_accepted = 0", fallback_pool_reset)
        self.assertIn("m_stratumTasks[pool]->m_poolErrors = 0", fallback_pool_reset)
        self.assertNotIn("m_foundBlocks", fallback_pool_reset)
        self.assertIn("m_foundBlocks = 0", fallback_manual_reset)
        self.assertIn("resetPoolSessionStats(i)", fallback_manual_reset)

        dual_header = _read("main/stratum/stratum_manager_dual_pool.h")
        dual_pool_reset = _function_body(
            dual_header, "virtual void resetPoolSessionStats(int pool) override"
        )
        dual_manual_reset = _function_body(
            dual_header, "virtual void resetSessionStats() override"
        )
        self.assertIn("m_accepted[pool] = 0", dual_pool_reset)
        self.assertIn("m_rejected[pool] = 0", dual_pool_reset)
        self.assertIn("std::max(m_bestSessionDiff[0], m_bestSessionDiff[1])", dual_pool_reset)
        self.assertNotIn("m_foundBlocks", dual_pool_reset)
        self.assertIn("m_foundBlocks = 0", dual_manual_reset)
        self.assertIn("resetPoolSessionStats(i)", dual_manual_reset)

    def test_found_block_overlay_only_reacts_to_counter_increments(self) -> None:
        body = _function_body(_read("main/system.cpp"), "void System::task()")

        self.assertIn("if (foundBlocks > lastFoundBlocks)", body)
        self.assertNotIn("foundBlocks != lastFoundBlocks", body)
        self.assertLess(
            body.index("if (foundBlocks > lastFoundBlocks)"),
            body.index("lastFoundBlocks = foundBlocks"),
        )


if __name__ == "__main__":
    unittest.main()
