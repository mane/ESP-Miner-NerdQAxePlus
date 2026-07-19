import json
import re
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


class AdaptiveHashrateApiContractTest(unittest.TestCase):
    def test_v2_mining_patch_is_type_and_range_checked_before_writes(self) -> None:
        source = _read("main/http_server/v2/handler_v2_settings.cpp")
        body = _function_body(source, "esp_err_t PATCH_V2_settings")

        first_write = body.index("Config::set")
        for guard in (
            'doc["frequency"].is<uint16_t>()',
            'doc["coreVoltage"].is<uint16_t>()',
            'doc["jobInterval"].is<uint16_t>()',
            'doc["hashrateGovernor"].is<JsonObject>()',
            'governorPatch["enabled"].is<bool>()',
            'governorPatch["maxFrequency"].is<uint16_t>()',
            'governorPatch["powerLimitW"].is<float>()',
            "isSupportedAsicFrequency",
            "MIN_ASIC_JOB_INTERVAL_MS",
            "MAX_ASIC_JOB_INTERVAL_MS",
            "isfinite(powerLimit)",
        ):
            self.assertLess(body.index(guard), first_write, guard)

        self.assertIn("normalizedGovernorMaxFrequency(board, requestedFrequency, requestedCoreVoltage)", body)
        self.assertIn("Governor maximum must be at least the base frequency", body)
        self.assertIn("governorMax > HASHRATE_GOVERNOR_LOW_VOLTAGE_MAX_MHZ", body)
        self.assertIn("requestedCoreVoltage < HASHRATE_GOVERNOR_HIGH_FREQUENCY_MIN_MV", body)
        self.assertIn("Config::setHashrateGovernorEnabled", body)
        self.assertIn("Config::setHashrateGovernorMaxFrequency", body)
        self.assertIn("Config::setHashrateGovernorPowerLimit10", body)

    def test_legacy_mining_patch_rejects_invalid_types_and_ranges_atomically(self) -> None:
        body = _function_body(_read("main/http_server/handler_system.cpp"),
                              "esp_err_t PATCH_update_settings")
        first_write = body.index("Config::set")
        for guard in (
            'doc["frequency"].is<uint16_t>()',
            'doc["coreVoltage"].is<uint16_t>()',
            'doc["jobInterval"].is<uint16_t>()',
            "isSupportedAsicFrequency",
            "MIN_ASIC_JOB_INTERVAL_MS",
            "MAX_ASIC_JOB_INTERVAL_MS",
            "Config::getHashrateGovernorMaxFrequency() > 500",
            'doc["coreVoltage"].as<uint16_t>() < 1300',
        ):
            self.assertLess(body.index(guard), first_write, guard)

    def test_settings_and_dashboard_publish_distinct_frequency_semantics(self) -> None:
        settings = _function_body(_read("main/http_server/v2/handler_v2_settings.cpp"),
                                  "esp_err_t GET_V2_settings")
        dashboard = _function_body(_read("main/http_server/v2/handler_v2_dashboard.cpp"),
                                   "esp_err_t GET_V2_dashboard")

        self.assertIn('doc["frequency"]', settings)
        self.assertIn('doc["effectiveFrequency"]', settings)
        self.assertIn('doc["hashrateGovernor"]', settings)
        self.assertIn("normalizedGovernorMaxFrequency", settings)
        self.assertIn("normalizedGovernorPowerLimit10", settings)

        for field in (
            'perf["frequency"]',
            'perf["configuredFrequency"]',
            'perf["actualFrequency"]',
            'perf["duplicateHWNonces"]',
            'perf["shareQueueDrops"]',
            'perf["hashrateGovernor"]',
        ):
            self.assertIn(field, dashboard)
        self.assertIn("getEffectiveAsicFrequency", dashboard)
        self.assertIn("getActualAsicFrequency", dashboard)

    def test_web_form_uses_nested_schema_and_matches_firmware_limits(self) -> None:
        component = _read("main/http_server/axe-os/src/app/pages/edit/edit.component.ts")
        template = _read("main/http_server/axe-os/src/app/pages/edit/edit.component.html")
        settings_model = _read("main/http_server/axe-os/src/app/models/ISettingsV2.ts")
        dashboard_model = _read("main/http_server/axe-os/src/app/models/IDashboardV2.ts")

        self.assertIn("effectiveFrequency: number", settings_model)
        self.assertIn("hashrateGovernor: ISettingsV2HashrateGovernor", settings_model)
        self.assertIn("configuredFrequency: number", dashboard_model)
        self.assertIn("actualFrequency: number", dashboard_model)
        self.assertIn("formGroupName=\"hashrateGovernor\"", template)
        self.assertIn("Validators.min(100), Validators.max(5000)", component)
        self.assertIn("Validators.min(nextBase), Validators.max(550)", component)
        self.assertIn("Validators.min(30), Validators.max(69.0)", component)
        self.assertIn("hashrateGovernorVoltageValidator", component)
        self.assertIn("maxFrequency > 500 && coreVoltage < 1300", component)
        self.assertIn("option.value >= nextBase && option.value <= 550", component)
        self.assertIn("hashrateGovernor: {", component)
        self.assertIn("powerLimitW: Number(f.hashrateGovernor.powerLimitW)", component)

    def test_new_nvs_keys_fit_esp_idf_key_limit(self) -> None:
        header = _read("main/nvs_config.h")
        keys = dict(re.findall(r'#define\s+(NVS_CONFIG_HASH_GOVERNOR_\w+)\s+"([^"]+)"', header))
        self.assertEqual(set(keys), {
            "NVS_CONFIG_HASH_GOVERNOR_ENABLE",
            "NVS_CONFIG_HASH_GOVERNOR_MAX",
            "NVS_CONFIG_HASH_GOVERNOR_POWER10",
        })
        for name, value in keys.items():
            self.assertLessEqual(len(value), 15, name)

    def test_modified_translation_catalogs_are_valid_json(self) -> None:
        for language in ("en", "it"):
            path = REPO / f"main/http_server/axe-os/src/assets/i18n/{language}.json"
            catalog = json.loads(path.read_text())
            self.assertIn("HASHRATE_GOVERNOR", catalog["SETTINGS"])
            self.assertIn("HASHRATE_GOVERNOR_POWER_LIMIT_HINT", catalog["SETTINGS"])


if __name__ == "__main__":
    unittest.main()
