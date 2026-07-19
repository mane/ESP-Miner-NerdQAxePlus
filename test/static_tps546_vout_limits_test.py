from pathlib import Path
import re
import unittest

REPO = Path(__file__).resolve().parents[1]


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


class Tps53647VoltageContractTest(unittest.TestCase):
    def test_vout_conversion_uses_absolute_voltage_and_bounds_vid_register(self):
        source = (REPO / "main/boards/drivers/TPS53647.cpp").read_text()
        body = _function_body(source, "uint8_t TPS53647::volt_to_vid")

        self.assertIn("volts - m_hwMinVoltage", body)
        self.assertIn("/ 0.005f", body)
        self.assertIn("register_value > 0xFF", body)
        self.assertIn("return 0;", body)

        raw_ratio_writes = re.findall(r"float_2_ulinear16\(", body)
        self.assertEqual([], raw_ratio_writes)

    def test_set_vout_propagates_pmbus_write_errors(self):
        source = (REPO / "main/boards/drivers/TPS53647.cpp").read_text()
        body = _function_body(source, "bool TPS53647::set_vout")

        self.assertIn(
            "esp_err_t err = write_word(PMBUS_VOUT_COMMAND, (uint16_t) vid);",
            body,
        )
        self.assertIn("if (err != ESP_OK)", body)
        error_check = body.index("if (err != ESP_OK)")
        success_log = body.index('ESP_LOGI(TAG, "Vout changed')
        self.assertIn("return false;", body[error_check:success_log])
        self.assertLess(error_check, success_log)


if __name__ == "__main__":
    unittest.main()
