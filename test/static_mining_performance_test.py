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
    def test_bm1368_540_uses_isolated_low_vco_pll_preset(self) -> None:
        body = _function_body(_read("components/bm1397/asic.cpp"),
                              "bool Asic::sendHashFrequency")

        preset_start = body.index('if (strcmp(getName(), "BM1368") == 0')
        solver_start = body.index("float min_diff = 2.0;")
        preset = body[preset_start:solver_start]

        self.assertLess(preset_start, solver_start)
        self.assertIn("BM1368_540_NOMINAL_MHZ = 540.0f", body)
        self.assertIn("BM1368_540_ACTUAL_MHZ = 540.625f", body)
        self.assertIn("PLL_TARGET_EPSILON_MHZ = 0.001f", body)
        self.assertIn(
            "fabsf(target_freq - BM1368_540_NOMINAL_MHZ) <= PLL_TARGET_EPSILON_MHZ",
            preset,
        )
        self.assertIn("{0x00, 0x08, 0x40, 0xAD, 0x02, 0x30}", preset)
        self.assertEqual(body.count("{0x00, 0x08, 0x40, 0xAD, 0x02, 0x30}"), 1)

        send_guard = preset.index("if (!send(CMD_WRITE_ALL, freqbuf, sizeof(freqbuf)))")
        failure_return = preset.index("return false;", send_guard)
        nominal_cache = preset.index("m_current_frequency = BM1368_540_NOMINAL_MHZ;")
        actual_cache = preset.index("m_actual_current_frequency = BM1368_540_ACTUAL_MHZ;")
        success_return = preset.index("return true;", actual_cache)
        self.assertLess(send_guard, failure_return)
        self.assertLess(failure_return, nominal_cache)
        self.assertLess(nominal_cache, actual_cache)
        self.assertLess(actual_cache, success_return)

    def test_asic_send_reports_uart_write_failures(self) -> None:
        header = _read("components/bm1397/include/asic.h")
        body = _function_body(_read("components/bm1397/asic.cpp"),
                              "bool Asic::send(uint8_t header")

        self.assertIn("bool send(uint8_t header", header)
        self.assertIn("const int written = SERIAL_send(buf, total_length);", body)
        self.assertIn("written < 0", body)
        self.assertIn("written != total_length", body)
        self.assertIn("ASIC UART TX failed", body)
        self.assertIn("ASIC UART TX short write", body)
        self.assertIn("return true;", body)

    def test_frequency_cache_updates_only_after_successful_uart_write(self) -> None:
        body = _function_body(_read("components/bm1397/asic.cpp"),
                              "bool Asic::sendHashFrequency")

        send_guard = body.index("if (!send(CMD_WRITE_ALL, freqbuf, sizeof(freqbuf)))")
        failure_return = body.index("return false;", send_guard)
        cached_update = body.index("m_current_frequency = target_freq;")
        actual_update = body.index("m_actual_current_frequency = best_newf;")

        self.assertLess(send_guard, failure_return)
        self.assertLess(failure_return, cached_update)
        self.assertLess(cached_update, actual_update)

    def test_bm1368_init_propagates_frequency_transition_failure(self) -> None:
        body = _function_body(_read("components/bm1397/bm1368.cpp"),
                              "uint8_t BM1368::init")

        transition_guard = body.index("if (!doFrequencyTransition(frequency))")
        failure_log = body.index("Failed to initialize ASIC frequency", transition_guard)
        failure_return = body.index("return 0;", failure_log)
        vr_setup = body.index("setVrFrequency(vrFrequency);")

        self.assertLess(transition_guard, failure_log)
        self.assertLess(failure_log, failure_return)
        self.assertLess(failure_return, vr_setup)

    def test_blocking_pll_ramp_settles_after_its_final_command(self) -> None:
        body = _function_body(_read("components/bm1397/asic.cpp"),
                              "bool Asic::doFrequencyTransition")

        final_settle = body.index("if (pllCommandSent)")
        final_delay = body.index("vTaskDelay(pdMS_TO_TICKS(100));", final_settle)
        loop_end = body.index("// BM1368 initialization writes")

        self.assertLess(loop_end, final_settle)
        self.assertLess(final_settle, final_delay)
        self.assertIn("Runtime governor steps use stepAsicFrequency() directly", body)
        self.assertIn("cached/reported correctly", body)
        self.assertNotIn("reads back correctly", body)

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
