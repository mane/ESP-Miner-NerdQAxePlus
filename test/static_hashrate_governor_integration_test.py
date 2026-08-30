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
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[brace_start:index + 1]
    raise AssertionError(f"Could not find body for {signature}")


class HashrateGovernorIntegrationContractTest(unittest.TestCase):
    def test_governor_uses_fresh_complete_hardware_hashrate(self) -> None:
        monitor_header = _read("main/tasks/hashrate_monitor_task.h")
        monitor_source = _read("main/tasks/hashrate_monitor_task.cpp")
        power_source = _read("main/tasks/power_management_task.cpp")

        self.assertIn("std::atomic<float> m_smoothedHashrate", monitor_header)
        self.assertIn("m_chipHashrateUpdatedMs", monitor_header)
        self.assertIn("getFreshSmoothedTotalChipHashrate", monitor_header)

        publish = _function_body(monitor_source, "bool HashrateMonitor::publishTotalIfComplete")
        self.assertIn("pthread_mutex_lock(&m_mutex)", publish)
        self.assertIn("nowMs - updatedMs > 2U * HR_INTERVAL", publish)
        self.assertIn("complete = false", publish)

        update = _function_body(power_source, "void PowerManagementTask::updateHashrateGovernor")
        self.assertIn("getFreshSmoothedTotalChipHashrate", update)
        self.assertIn("hasFreshHashrate", update)
        self.assertNotIn("SYSTEM_MODULE.getCurrentHashrate()", update)

    def test_hardware_qualified_guards_and_emergency_ramp_are_preserved(self) -> None:
        source = _read("main/tasks/power_management_task.cpp")
        update = _function_body(source, "void PowerManagementTask::updateHashrateGovernor")
        apply_settings = _function_body(source, "void PowerManagementTask::applyRuntimeAsicSettings")

        self.assertIn("m_vrTempInt - 10.0f", update)
        self.assertIn("getSpeedPerc(0) >= 30", update)
        self.assertIn("getRPM(0) > 0", update)
        self.assertNotIn("getRPM(1)", update)
        self.assertIn("getSharesRejectedSnapshot()", update)
        self.assertIn("if (decision.emergency)", update)
        self.assertIn("m_governorEmergency = true", update)
        self.assertIn("fabs(sample.frequencyMhz - (double) m_runtimeFrequencyTarget)", update)
        self.assertIn("m_governorEmergency ? 25.0f : 6.25f", apply_settings)

    def test_frequency_options_are_converted_and_legacy_nvs_is_qualified(self) -> None:
        power = _function_body(
            _read("main/tasks/power_management_task.cpp"),
            "void PowerManagementTask::syncHashrateGovernorConfiguration",
        )
        self.assertIn("std::vector<uint16_t> governorFrequencies", power)
        self.assertIn("frequency <= UINT16_MAX", power)
        self.assertIn("governorFrequencies, baseFrequency", power)
        self.assertIn("GOVERNOR_HIGH_FREQUENCY_MIN_MV", power)
        self.assertIn("GOVERNOR_LOW_VOLTAGE_CAP_MHZ", power)
        self.assertIn("std::min<uint16_t>(powerLimit10, 690)", power)

        nvs = _read("main/nvs_config.h")
        self.assertIn("getHashrateGovernorPowerLimit10()", nvs)
        self.assertIn("NVS_CONFIG_HASH_GOVERNOR_POWER10, 690", nvs)

        board = _function_body(_read("main/boards/board.cpp"), "void Board::loadSettings")
        self.assertIn("!isSupportedAsicFrequency", board)
        self.assertIn("fallbackFrequency", board)
        self.assertIn("unsupported configured ASIC frequency", board)

        nerdqaxe = _read("main/boards/nerdqaxeplus.cpp")
        self.assertIn("525, 528, 531, 534, 537, 540, 550", nerdqaxe)

    def test_http_reads_one_locked_governor_snapshot(self) -> None:
        header = _read("main/tasks/power_management_task.h")
        source = _read("main/tasks/power_management_task.cpp")
        self.assertIn("struct HashrateGovernorStatus", header)
        snapshot = _function_body(source, "void PowerManagementTask::copyHashrateGovernorStatus")
        self.assertLess(snapshot.index("lock();"), snapshot.index("status->enabled"))
        self.assertLess(snapshot.index("status->lastReason"), snapshot.index("unlock();"))

        forbidden = (
            "POWER_MANAGEMENT_MODULE.isHashrateGovernorEnabled()",
            "POWER_MANAGEMENT_MODULE.getHashrateGovernorTargetFrequency()",
            "POWER_MANAGEMENT_MODULE.getHashrateGovernorStableFrequency()",
            "POWER_MANAGEMENT_MODULE.getHashrateGovernorUtilization()",
            "POWER_MANAGEMENT_MODULE.getHashrateGovernorState()",
            "POWER_MANAGEMENT_MODULE.getHashrateGovernorReason()",
        )
        for path in (
            "main/http_server/v2/handler_v2_settings.cpp",
            "main/http_server/v2/handler_v2_dashboard.cpp",
        ):
            endpoint = _read(path)
            self.assertIn("copyHashrateGovernorStatus", endpoint, path)
            for call in forbidden:
                self.assertNotIn(call, endpoint, path)

    def test_enabled_governor_cap_covers_base_across_both_settings_apis(self) -> None:
        v2 = _function_body(
            _read("main/http_server/v2/handler_v2_settings.cpp"),
            "esp_err_t PATCH_V2_settings",
        )
        legacy = _function_body(
            _read("main/http_server/handler_system.cpp"),
            "esp_err_t PATCH_update_settings",
        )
        self.assertIn("governorEnabled && governorMax < requestedFrequency", v2)
        self.assertIn("Config::isHashrateGovernorEnabled()", legacy)
        self.assertIn("frequency exceeds the enabled governor maximum", legacy)


if __name__ == "__main__":
    unittest.main()
