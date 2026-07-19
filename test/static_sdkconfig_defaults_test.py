import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SDKCONFIG_DEFAULTS = ROOT / "sdkconfig.defaults"


class SdkconfigDefaultsContractTest(unittest.TestCase):
    def setUp(self):
        self.defaults = SDKCONFIG_DEFAULTS.read_text()

    def test_release_build_selects_performance_optimization_only(self):
        self.assertIn("CONFIG_COMPILER_OPTIMIZATION_PERF=y", self.defaults)
        self.assertNotIn("CONFIG_COMPILER_OPTIMIZATION_LEVEL_DEBUG=y", self.defaults)
        self.assertNotIn("CONFIG_COMPILER_OPTIMIZATION_DEBUG=y", self.defaults)

    def test_idf_53_defaults_do_not_assign_obsolete_kconfig_symbols(self):
        obsolete_symbols = (
            "CONFIG_ESP32S3_SPIRAM_SUPPORT=",
            "CONFIG_ESP32S3_DEFAULT_CPU_FREQ_240=",
            "CONFIG_MBEDTLS_SERVER_NAME_INDICATION=",
        )
        for symbol in obsolete_symbols:
            with self.subTest(symbol=symbol):
                self.assertNotIn(symbol, self.defaults)

        self.assertIn("CONFIG_SPIRAM=y", self.defaults)
        self.assertIn("CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ_240=y", self.defaults)

    def test_unused_lvgl_examples_are_not_built(self):
        self.assertIn("CONFIG_LV_BUILD_EXAMPLES=n", self.defaults)


if __name__ == "__main__":
    unittest.main()
