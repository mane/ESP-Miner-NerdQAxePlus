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

    def test_asic_send_wrappers_propagate_uart_status(self) -> None:
        header = _read("components/bm1397/include/asic.h")
        source = _read("components/bm1397/asic.cpp")
        send2 = _function_body(source, "bool Asic::send2")
        send6 = _function_body(source, "bool Asic::send6")

        self.assertIn("bool send2(", header)
        self.assertIn("bool send6(", header)
        self.assertIn("return send(header, buf, sizeof(buf));", send2)
        self.assertIn("return send(header, buf, sizeof(buf));", send6)

    def test_bm1368_init_guards_every_critical_uart_write(self) -> None:
        body = _function_body(_read("components/bm1397/bm1368.cpp"),
                              "uint8_t BM1368::init")
        critical_calls = (
            "send6",
            "sendChainInactive",
            "setChipAddress",
            "setJobDifficultyMask",
            "doFrequencyTransition",
            "setVrFrequency",
            "setVersionMask",
        )

        for name in critical_calls:
            self.assertIn(f"!{name}(", body)
            self.assertNotRegex(body, rf"(?<!\!)\b{name}\(")

        self.assertIn("Failed initial version-mask sequence", body)
        self.assertIn("Failed broadcast pre-address initialization", body)
        self.assertIn("Failed broadcast core initialization", body)
        self.assertIn("Failed register initialization for ASIC", body)
        self.assertIn("Failed final version-rolling configuration", body)

    def test_max_baud_failure_aborts_board_initialization(self) -> None:
        asic_body = _function_body(_read("components/bm1397/asic.cpp"),
                                   "int Asic::setMaxBaud")
        board_body = _function_body(_read("main/boards/nerdqaxeplus.cpp"),
                                    "bool NerdQaxePlus::initAsics")

        self.assertIn("if (!send6(CMD_WRITE_ALL", asic_body)
        self.assertIn("return 0;", asic_body)
        baud_call = board_body.index("int maxBaud = m_asics->setMaxBaud();")
        baud_guard = board_body.index("if (maxBaud <= 0)", baud_call)
        baud_switch = board_body.index("SERIAL_set_baud(maxBaud);", baud_guard)
        failure_return = board_body.index("return false;", baud_guard)
        self.assertLess(baud_call, baud_guard)
        self.assertLess(baud_guard, failure_return)
        self.assertLess(failure_return, baud_switch)

    def test_difficulty_cache_updates_only_after_successful_uart_write(self) -> None:
        body = _function_body(_read("components/bm1397/asic.cpp"),
                              "bool Asic::setJobDifficultyMask")

        send_guard = body.index("if (!send((CMD_WRITE_ALL), job_difficulty_mask, 6))")
        failure_return = body.index("return false;", send_guard)
        cached_update = body.index("m_asicDifficulty = difficulty;")
        self.assertLess(send_guard, failure_return)
        self.assertLess(failure_return, cached_update)

    def test_work_tx_uses_unambiguous_bool_and_out_job_id(self) -> None:
        header = _read("components/bm1397/include/asic.h")
        source = _read("components/bm1397/asic.cpp")
        send_work = _function_body(source, "bool Asic::sendWork")
        send_raw = _function_body(source, "bool Asic::sendRawJob")

        self.assertIn("bool sendWork(uint32_t job_id, bm_job *next_bm_job, uint8_t &asic_job_id);", header)
        self.assertIn("bool sendRawJob(BM1368_job *job);", header)
        tx_guard = send_work.index("if (!send((TYPE_JOB | GROUP_SINGLE | CMD_WRITE)")
        failure_return = send_work.index("return false;", tx_guard)
        out_assignment = send_work.index("asic_job_id = job.job_id;")
        self.assertLess(tx_guard, failure_return)
        self.assertLess(failure_return, out_assignment)
        self.assertIn("return send((TYPE_JOB | GROUP_SINGLE | CMD_WRITE)", send_raw)

    def test_create_jobs_publishes_only_successful_asic_and_can_tx(self) -> None:
        body = _function_body(_read("main/tasks/create_jobs_task.cpp"),
                              "void create_jobs_task")

        difficulty_guard = body.index("if (!asics->setJobDifficultyMask")
        version_guard = body.index("if (!asics->setVersionMask", difficulty_guard)
        active_mask_update = body.index("active_version_mask =", version_guard)
        work_guard = body.index("if (!asics->sendWork", active_mask_update)
        local_store = body.index("asicJobs.storeJob", work_guard)
        extranonce_increment = body.index("extranonce_2++;", local_store)
        self.assertLess(difficulty_guard, version_guard)
        self.assertLess(version_guard, active_mask_update)
        self.assertLess(active_mask_update, work_guard)
        self.assertLess(work_guard, local_store)
        self.assertLess(local_store, extranonce_increment)

        slave_counter_read = body.index("can_make_extranonce2(slave, slave_counters[slave])")
        can_work_guard = body.index("if (!can_send_raw_job", slave_counter_read)
        slave_store = body.index("slaveAsicJobs[slave].storeJob", can_work_guard)
        slave_counter_increment = body.index("slave_counters[slave]++;", slave_store)
        self.assertLess(slave_counter_read, can_work_guard)
        self.assertLess(can_work_guard, slave_store)
        self.assertLess(slave_store, slave_counter_increment)

    def test_vr_frequency_cache_retries_after_uart_failure(self) -> None:
        board_header = _read("main/boards/board.h")
        board_body = _function_body(_read("main/boards/board.cpp"),
                                    "bool Board::setVrFrequency")
        power_body = _function_body(_read("main/tasks/power_management_task.cpp"),
                                    "void PowerManagementTask::checkVrFrequencyChanged")

        self.assertIn("bool setVrFrequency(uint32_t freq);", board_header)
        self.assertIn("return m_asics->setVrFrequency(freq);", board_body)
        tx_guard = power_body.index("if (m_board->setVrFrequency(vrFrequency))")
        cache_update = power_body.index("lastVrFrequency = vrFrequency;", tx_guard)
        retry_log = power_body.index("will retry", cache_update)
        self.assertLess(tx_guard, cache_update)
        self.assertLess(cache_update, retry_log)

    def test_can_slave_publishes_mask_and_job_only_after_asic_tx(self) -> None:
        source = _read("main/tasks/can_slave_task.cpp")
        settings = _function_body(source, "static void handle_settings_cmd")
        task = _function_body(source, "void can_slave_task")

        mask_guard = settings.index("if (asics->setVersionMask(version_mask))")
        applied_log = settings.index("→ applied", mask_guard)
        failure_log = settings.index("ASIC UART TX error", applied_log)
        self.assertLess(mask_guard, applied_log)
        self.assertLess(applied_log, failure_log)

        invalidation = task.index("s_job_valid[job->job_id] = false;")
        raw_guard = task.index("if (!asics->sendRawJob(job))", invalidation)
        job_store = task.index("s_jobs[job->job_id]", raw_guard)
        valid_publish = task.index("s_job_valid[job->job_id]  = true;", job_store)
        timeout_refresh = task.index("last_job = now;", valid_publish)
        self.assertLess(invalidation, raw_guard)
        self.assertLess(raw_guard, job_store)
        self.assertLess(job_store, valid_publish)
        self.assertLess(valid_publish, timeout_refresh)

    def test_can_multiframe_failure_prevents_job_publication(self) -> None:
        sender = _read("main/tasks/can_sender.cpp")
        header = _read("main/tasks/can_sender.h")
        multiframe = _function_body(sender, "static bool send_multiframe")
        raw_job = _function_body(sender, "bool can_send_raw_job")

        self.assertIn("bool can_send_settings_cmd", header)
        self.assertIn("bool can_send_raw_job", header)
        self.assertIn("can_transmit_with_recovery(&frame) != ESP_OK", multiframe)
        self.assertIn("return false;", multiframe)
        self.assertIn("bool sent = send_multiframe", raw_job)
        self.assertIn("return sent;", raw_job)

    def test_post_detection_failures_clear_board_asic_state(self) -> None:
        body = _function_body(_read("main/boards/nerdqaxeplus.cpp"),
                              "bool NerdQaxePlus::initAsics")

        self.assertIn("m_chipsDetected = 0;", body[:body.index("// disable buck")])
        for marker in (
            "error initializing asics!",
            "error setting ASIC UART baud",
            "error setting final ASIC voltage",
        ):
            branch_start = body.index(marker)
            branch_end = body.index("return false;", branch_start)
            failure_branch = body[branch_start:branch_end]
            self.assertIn("m_chipsDetected = 0;", failure_branch, marker)
            self.assertIn("m_isInitialized = false;", failure_branch, marker)

    def test_frequency_cache_updates_only_after_successful_uart_write(self) -> None:
        body = _function_body(_read("components/bm1397/asic.cpp"),
                              "bool Asic::sendHashFrequency")
        solver = body[body.index("float min_diff = 2.0;"):]

        send_guard = solver.index("if (!send(CMD_WRITE_ALL, freqbuf, sizeof(freqbuf)))")
        failure_return = solver.index("return false;", send_guard)
        cached_update = solver.index("m_current_frequency = target_freq;")
        actual_update = solver.index("m_actual_current_frequency = best_newf;")

        self.assertLess(send_guard, failure_return)
        self.assertLess(failure_return, cached_update)
        self.assertLess(cached_update, actual_update)

    def test_bm1368_init_propagates_frequency_transition_failure(self) -> None:
        body = _function_body(_read("components/bm1397/bm1368.cpp"),
                              "uint8_t BM1368::init")

        transition_guard = body.index("if (!doFrequencyTransition(frequency))")
        failure_log = body.index("Failed to initialize ASIC frequency", transition_guard)
        failure_return = body.index("return 0;", failure_log)
        vr_setup = body.index("setVrFrequency(vrFrequency)")

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
