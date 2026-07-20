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


class MiningPerformanceContractTest(unittest.TestCase):
    def test_blocking_pll_ramp_settles_after_its_final_command(self) -> None:
        body = _function_body(_read("components/bm1397/asic.cpp"),
                              "bool Asic::doFrequencyTransition")

        final_settle = body.index("if (pllCommandSent)")
        final_delay = body.index("vTaskDelay(pdMS_TO_TICKS(100));", final_settle)
        loop_end = body.index("// BM1368 initialization writes")

        self.assertLess(loop_end, final_settle)
        self.assertLess(final_settle, final_delay)
        self.assertIn("Runtime governor steps use stepAsicFrequency() directly", body)

    def test_nerdqaxeplus_uses_bm1368_and_refreshes_vr_frequency(self) -> None:
        body = _function_body(_read("main/boards/nerdqaxeplus.cpp"), "NerdQaxePlus::NerdQaxePlus()")
        asic_pos = body.index("m_asics = new BM1368();")
        refresh_pos = body.index("m_vrFrequency = m_defaultVrFrequency = m_asics->getDefaultVrFrequency();")
        self.assertLess(asic_pos, refresh_pos)

    def test_can_slave_job_timeout_runs_even_when_bus_is_quiet(self) -> None:
        body = _function_body(_read("main/tasks/can_slave_task.cpp"), "void can_slave_task(void *pvParameters)")

        timeout_pos = body.index("No job for 10s → re-negotiating")
        receive_pos = body.index("twai_receive(&msg")
        receive_timeout_pos = body.index("if (err == ESP_ERR_TIMEOUT) continue;")

        self.assertLess(timeout_pos, receive_pos)
        self.assertLess(receive_pos, receive_timeout_pos)
        self.assertIn("state == SLAVE_ACTIVE", body[timeout_pos - 250:timeout_pos])

    def test_sv2_noise_send_all_retries_positive_partial_writes(self) -> None:
        body = _function_body(_read("components/stratum_v2/sv2_noise.c"), "static int noise_send_all")

        self.assertIn("while (sent < len)", body)
        self.assertIn("len - sent", body)
        self.assertIn("sent += ret", body)
        self.assertIn("ret <= 0", body)


if __name__ == "__main__":
    unittest.main()
