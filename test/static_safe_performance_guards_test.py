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


class SafePerformanceGuardsContractTest(unittest.TestCase):
    def test_tmp451_mux_exp_stores_active_high_constructor_argument(self) -> None:
        source = _read("main/boards/drivers/tmp451_mux_exp.cpp")
        constructor = source[source.index("Tmp451MuxExp::Tmp451MuxExp"):source.index("esp_err_t Tmp451MuxExp::init")]
        self.assertIn("m_mux_active_high(mux_active_high)", constructor)

    def test_vr_frequency_zero_is_rejected_before_register_math_and_nvs_save(self) -> None:
        asic_source = _read("components/bm1397/asic.cpp")
        vr_to_reg = _function_body(asic_source, "uint32_t Asic::vrFreqToReg")
        set_vr = _function_body(asic_source, "void Asic::setVrFrequency")
        self.assertIn("freq_hz == 0", vr_to_reg)
        self.assertIn("freq_hz == 0", set_vr)
        self.assertLess(set_vr.index("freq_hz == 0"), set_vr.index("setVrFreqReg"))

        board_load = _function_body(_read("main/boards/board.cpp"), "void Board::loadSettings")
        self.assertIn("m_vrFrequency == 0", board_load)
        self.assertIn("m_defaultVrFrequency", board_load)

        for path in ("main/http_server/v2/handler_v2_settings.cpp", "main/http_server/handler_system.cpp"):
            source = _read(path)
            self.assertIn("uint32_t vrFrequency = doc[\"vrFrequency\"].as<uint32_t>();", source, path)
            self.assertIn("if (vrFrequency > 0)", source, path)

    def test_emc2101_fan_speed_accepts_percent_or_normalized_duty_and_saturates_register(self) -> None:
        body = _function_body(_read("main/boards/drivers/nerdaxe/EMC2101.cpp"), "void EMC2101_set_fan_speed")
        self.assertIn("if (percent > 1.0f) percent = percent / 100.0f;", body)
        self.assertIn("if (percent > 1.0f) percent = 1.0f;", body)
        self.assertIn("63.0f * percent", body)
        self.assertIn("if (speed > 63) speed = 63;", body)


if __name__ == "__main__":
    unittest.main()
