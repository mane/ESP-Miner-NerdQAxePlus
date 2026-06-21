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


if __name__ == "__main__":
    unittest.main()
