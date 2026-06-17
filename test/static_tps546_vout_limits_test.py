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


class Tps546VoutLimitContractTest(unittest.TestCase):
    def test_vout_limits_are_written_as_absolute_scaled_voltages(self):
        source = (REPO / "main/boards/drivers/rev7/TPS546.cpp").read_text()
        body = _function_body(source, "static bool TPS546_write_vout_limit_ratios")

        # PMBus VOUT limit registers store absolute voltages.  The constants in
        # TPS546.h are ratios relative to the current VOUT_COMMAND, so writing the
        # raw ratio values would configure invalid limits for stacked voltage
        # domains (for example 1.25V instead of 2.40V * 1.25).
        self.assertIn("vout_command * ratio", body)
        self.assertIn("float_2_ulinear16(limit)", body)

        raw_constant_writes = re.findall(
            r"float_2_ulinear16\(\s*(TPS546_INIT_VOUT_[A-Z_]+)\s*\)",
            body,
        )
        self.assertEqual([], raw_constant_writes)

        for command in (
            "PMBUS_VOUT_OV_FAULT_LIMIT",
            "PMBUS_VOUT_OV_WARN_LIMIT",
            "PMBUS_VOUT_MARGIN_HIGH",
            "PMBUS_VOUT_MARGIN_LOW",
            "PMBUS_VOUT_UV_WARN_LIMIT",
            "PMBUS_VOUT_UV_FAULT_LIMIT",
        ):
            self.assertIn(command, body)


if __name__ == "__main__":
    unittest.main()
