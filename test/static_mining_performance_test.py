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
    def test_q1370_and_q1373_refresh_vr_frequency_after_replacing_asic_model(self) -> None:
        for relative_path, signature, asic_type in (
            ("main/boards/q1370.cpp", "Q1370B::Q1370B()", "BM1370"),
            ("main/boards/q1373.cpp", "Q1373B::Q1373B()", "BM1373"),
        ):
            body = _function_body(_read(relative_path), signature)
            replace_pos = body.index(f"m_asics = new {asic_type}();")
            refresh_pos = body.index("m_vrFrequency = m_defaultVrFrequency = m_asics->getDefaultVrFrequency();")
            self.assertLess(replace_pos, refresh_pos, relative_path)

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
