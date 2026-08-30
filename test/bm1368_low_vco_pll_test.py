from pathlib import Path
import shutil
import subprocess
import tempfile
import textwrap
import unittest


REPO = Path(__file__).resolve().parents[1]


HARNESS = r"""
#include "bm1368_pll.h"

#include <cassert>
#include <cmath>
#include <cstdint>

using BM1368Pll::LowVcoPreset;

static void check(float nominal, float actual, uint8_t feedbackDivider)
{
    LowVcoPreset preset{};
    assert(BM1368Pll::findLowVcoPreset(nominal, &preset));
    assert(std::fabs(preset.actualMhz - actual) < 0.0001f);
    assert(preset.feedbackDivider == feedbackDivider);

    uint8_t payload[6]{};
    BM1368Pll::encodeLowVcoPayload(preset, payload);
    assert(payload[0] == 0x00);
    assert(payload[1] == 0x08);
    assert(payload[2] == 0x40);
    assert(payload[3] == feedbackDivider);
    assert(payload[4] == 0x02);
    assert(payload[5] == 0x30);

    const float physicalMhz = 25.0f * payload[3] / (payload[4] * 4.0f * 1.0f);
    assert(std::fabs(physicalMhz - actual) < 0.0001f);
}

int main()
{
    check(528.0f, 528.125f, 0xA9);
    check(531.0f, 531.250f, 0xAA);
    check(534.0f, 534.375f, 0xAB);
    check(537.0f, 537.500f, 0xAC);
    check(540.0f, 540.625f, 0xAD);

    LowVcoPreset preset{};
    assert(!BM1368Pll::findLowVcoPreset(525.0f, &preset));
    assert(!BM1368Pll::findLowVcoPreset(550.0f, &preset));
    assert(!BM1368Pll::findLowVcoPreset(528.01f, &preset));
    assert(!BM1368Pll::findLowVcoPreset(531.0f, nullptr));
    return 0;
}
"""


class Bm1368LowVcoPllTest(unittest.TestCase):
    def test_low_vco_presets_encode_expected_register_bytes(self) -> None:
        compiler = shutil.which("c++") or shutil.which("g++") or shutil.which("clang++")
        self.assertIsNotNone(compiler, "A C++ compiler is required for this test")

        with tempfile.TemporaryDirectory(prefix="bm1368-low-vco-") as temp_dir:
            temp = Path(temp_dir)
            harness = temp / "bm1368_low_vco_harness.cpp"
            binary = temp / "bm1368_low_vco_harness"
            harness.write_text(textwrap.dedent(HARNESS))

            build = subprocess.run(
                [
                    compiler,
                    "-std=c++11",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-I",
                    str(REPO / "components/bm1397/include"),
                    str(harness),
                    "-o",
                    str(binary),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)

            run = subprocess.run([str(binary)], capture_output=True, text=True, check=False)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)


if __name__ == "__main__":
    unittest.main()
