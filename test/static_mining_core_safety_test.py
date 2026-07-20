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


class MiningCoreSafetyContractTest(unittest.TestCase):
    def test_binary_helpers_are_bounds_and_alignment_safe(self) -> None:
        source = _read("components/bm1397/mining_utils.cpp")
        bin2hex = _function_body(source, "size_t bin2hex")
        le256 = _function_body(source, "double le256todouble")

        self.assertIn("buflen > (SIZE_MAX - 1) / 2", bin2hex)
        self.assertIn("hexlen < buflen * 2 + 1", bin2hex)
        self.assertIn("hex[2 * buflen] = '\\0';", bin2hex)
        self.assertIn("const uint8_t *bytes", le256)
        self.assertGreaterEqual(le256.count("memcpy(&data64"), 4)
        self.assertNotIn("(uint64_t *)", le256)

    def test_merkle_builder_rejects_untrusted_hex_and_branch_counts(self) -> None:
        body = _function_body(_read("components/bm1397/mining.cpp"),
                              "bool calculate_merkle_root_hash")

        self.assertIn("num_merkle_branches > MAX_MERKLE_BRANCHES", body)
        self.assertIn("coinbase_hex_len == 0", body)
        self.assertIn("(coinbase_hex_len & 1U) != 0", body)
        self.assertIn("c >= '0' && c <= '9'", body)
        self.assertIn("return bin2hex", body)

    def test_uart_receive_requires_a_complete_asic_frame(self) -> None:
        source = _read("components/bm1397/serial.cpp")
        receive = _function_body(source, "int16_t SERIAL_rx")
        asic_receive = _function_body(_read("components/bm1397/asic.cpp"),
                                      "bool Asic::receiveWork")

        self.assertIn("while (total_read < size)", receive)
        self.assertIn("buf + total_read", receive)
        self.assertIn("size - total_read", receive)
        self.assertIn("received != static_cast<int>(sizeof(*result))", asic_receive)

    def test_stratum_v1_bounds_messages_and_serializes_writes(self) -> None:
        source = _read("main/stratum/stratum_api.cpp")
        header = _read("main/stratum/stratum_api.h")
        parse_methods = _function_body(source, "bool StratumApi::parseMethods")

        self.assertIn("#define MAX_EXTRANONCE_SIZE 32", header)
        self.assertIn("params.size() < 9", parse_methods)
        self.assertIn("merkle_branch.size() > MAX_MERKLE_BRANCHES", parse_methods)
        self.assertIn("difficulty < 1.0", parse_methods)
        self.assertIn("isHexString(coinbase_1)", parse_methods)
        self.assertIn("isHexString(coinbase_2)", parse_methods)
        self.assertIn("isHexString(extranonce_str, 0, MAX_EXTRANONCE_SIZE * 2)", parse_methods)
        self.assertIn("extranonce_2_len > MAX_EXTRANONCE_SIZE", source)
        self.assertIn("pthread_mutex_t m_sendMutex", header)
        for signature in (
            "bool StratumApi::subscribe",
            "bool StratumApi::authenticate",
            "bool StratumApi::submitShare",
        ):
            self.assertIn("PThreadGuard lock(m_sendMutex)",
                          _function_body(source, signature), signature)

    def test_stratum_v1_escapes_json_redacts_secrets_and_tracks_setup_ids(self) -> None:
        source = _read("main/stratum/stratum_api.cpp")
        task = _read("main/stratum/stratum_task.cpp")
        manager = _read("main/stratum/stratum_manager.cpp")
        escape = _function_body(source, "static bool appendJsonEscapedContent")
        debug = _function_body(source, "void StratumApi::debugTx")

        self.assertIn("case '\"': escape = \"\\\\\\\"\"", escape)
        self.assertIn("case '\\\\': escape = \"\\\\\\\\\"", escape)
        self.assertIn("*p < 0x20", escape)
        for signature in (
            "bool StratumApi::subscribe",
            "bool StratumApi::authenticate",
            "bool StratumApi::submitShare",
        ):
            self.assertIn("appendJson", _function_body(source, signature), signature)
        self.assertIn("mining.authorize (credentials redacted)", debug)
        self.assertNotIn("debugTx(message);", debug)

        self.assertIn("m_lastSetupMessageId = STRATUM_ID_EXTRANONCE_SUBSCRIBE", task)
        self.assertIn("m_lastSetupMessageId = STRATUM_ID_SUGGEST_DIFFICULTY", task)
        self.assertIn("selected->m_lastSetupMessageId", manager)
        self.assertIn("message->message_id <= last_setup_message_id", source)

    def test_v1_submit_owns_config_snapshot_and_close_is_serialized(self) -> None:
        source = _read("main/stratum/stratum_task.cpp")
        header = _read("main/stratum/stratum_task.h")
        submit = _function_body(source, "void StratumTaskV1::submitShare")

        self.assertIn("pthread_mutex_t m_configMutex", header)
        self.assertIn("pthread_mutex_t m_ioMutex", header)
        self.assertLess(submit.index("PThreadGuard config_lock"), submit.index("strdup(configured_user)"))
        self.assertIn("MemoryGuard username_guard", submit)
        self.assertLess(submit.index("PThreadGuard io_lock"), submit.index("m_stratumAPI.submitShare"))

    def test_sv2_reconnect_releases_pending_jobs_and_validates_frames(self) -> None:
        source = _read("main/stratum/stratum_task_v2.cpp")
        reset = _function_body(source, "void StratumTaskV2::resetConnectionState")
        loop = _function_body(source, "void StratumTaskV2::protocolLoop")

        self.assertIn("sv2_ext_job_free", reset)
        self.assertIn("memset(&m_sv2_conn", reset)
        self.assertIn("hdr.msg_length != static_cast<uint32_t>(payload_len)", loop)
        self.assertIn("extranonce_size == 0 || extranonce_size > 32", source)
        self.assertIn("channel_id != m_sv2_conn.channel_id", source)

    def test_sv2_serializes_noise_sends_and_activates_only_referenced_job(self) -> None:
        source = _read("main/stratum/stratum_task_v2.cpp")
        submit = _function_body(source, "void StratumTaskV2::submitShare")
        prevhash = _function_body(source, "void StratumTaskV2::handleSetNewPrevHash")

        lock_pos = submit.index("PThreadGuard io_lock(m_ioMutex)")
        sequence_pos = submit.index("m_sv2_conn.sequence_number")
        send_pos = submit.index("sv2_noise_send")
        self.assertLess(lock_pos, sequence_pos)
        self.assertLess(sequence_pos, send_pos)
        for signature in ("bool StratumTaskV2::sendSetupConnection",
                          "bool StratumTaskV2::sendOpenChannel"):
            body = _function_body(source, signature)
            self.assertLess(body.index("PThreadGuard io_lock"), body.index("sv2_noise_send"))

        self.assertIn("bool activated_job = false", prevhash)
        self.assertIn("m_validNotify = activated_job", prevhash)
        self.assertIn("sv2_ext_job_free", prevhash)
        self.assertNotIn("first_prev_hash", prevhash)
        self.assertNotIn("job_id != job_id", prevhash)

    def test_sv2_honors_version_and_extranonce_updates(self) -> None:
        source = _read("main/stratum/stratum_task_v2.cpp")
        protocol_h = _read("components/stratum_v2/include/sv2_protocol.h")
        protocol_c = _read("components/stratum_v2/sv2_protocol.c")
        extended = _function_body(source, "bool StratumTaskV2::enqueueExtendedJob")
        standard = _function_body(source, "bool StratumTaskV2::enqueueStandardJob")
        prefix = _function_body(source, "void StratumTaskV2::handleSetExtranoncePrefix")
        le256 = _function_body(protocol_c, "static double s_le256todouble")

        self.assertIn("SV2_MSG_SET_EXTRANONCE_PREFIX", protocol_h)
        self.assertIn("sv2_parse_set_extranonce_prefix", protocol_h)
        self.assertIn("m_sv2_conn.requires_fixed_version", standard)
        self.assertIn("job->version_rolling_allowed", extended)
        self.assertIn("create_job_invalidate(m_index)", prefix)
        self.assertIn("sv2_ext_job_free", prefix)
        self.assertIn("read_u64_le", le256)
        self.assertNotIn("uint64_t *", le256)

    def test_sv2_manager_state_is_locked_without_inverting_job_lock(self) -> None:
        source = _read("main/stratum/stratum_task_v2.cpp")
        open_channel = _function_body(source, "bool StratumTaskV2::receiveOpenChannelSuccess")
        prevhash = _function_body(source, "void StratumTaskV2::handleSetNewPrevHash")
        target = _function_body(source, "void StratumTaskV2::handleSetTarget")
        extended = _function_body(source, "bool StratumTaskV2::enqueueExtendedJob")

        for body, mutation in (
            (open_channel, "m_manager->setPoolDifficulty"),
            (prevhash, "m_manager->setNetworkDifficulty"),
            (target, "m_manager->setPoolDifficulty"),
            (extended, "m_manager->processCoinbase"),
        ):
            self.assertLess(body.index("PThreadGuard manager_lock(m_manager->m_mutex)"),
                            body.index(mutation), mutation)

        self.assertLess(prevhash.index("m_manager->setNetworkDifficulty"),
                        prevhash.index("enqueueStandardJob"))
        self.assertLess(target.index("m_manager->setPoolDifficulty"),
                        target.index("create_job_sv2_set_difficulty"))
        self.assertLess(extended.index("m_manager->processCoinbase"),
                        extended.index("bool enqueued = create_job_sv2_extended"))
        self.assertLess(prevhash.rindex("PThreadGuard manager_lock(m_manager->m_mutex)"),
                        prevhash.index("m_validNotify = activated_job"))

    def test_ping_history_lock_never_nests_manager_calls(self) -> None:
        source = _read("main/tasks/ping_task.cpp")
        ping = _function_body(source, "void PingTask::ping_task()")
        history_lock = ping.index("PThreadGuard history_lock(m_mutex)")

        self.assertNotIn("PThreadGuard g(m_mutex)", ping[:history_lock])
        self.assertLess(ping.index("m_manager->copyConfigInto"), history_lock)
        self.assertLess(ping.index("m_manager->getResolvedIpForPool"), history_lock)
        self.assertLess(ping.index("perform_ping"), history_lock)
        self.assertIn("PThreadGuard g(m_mutex)",
                      _function_body(source, "double PingTask::get_last_ping_rtt"))
        self.assertIn("PThreadGuard g(m_mutex)",
                      _function_body(source, "double PingTask::get_recent_ping_loss"))
        self.assertIn("if (m_ping_history)",
                      _function_body(source, "void PingTask::reset"))

    def test_transport_keepalive_and_ping_callback_state_outlive_async_users(self) -> None:
        transport_h = _read("main/stratum/stratum_transport.h")
        transport = _function_body(_read("main/stratum/stratum_transport.cpp"),
                                   "void StratumTransport::applyKeepAlive_()")
        ping_h = _read("main/tasks/ping_task.h")
        ping = _function_body(_read("main/tasks/ping_task.cpp"),
                              "PingResult PingTask::perform_ping")

        self.assertIn("esp_transport_keep_alive_t m_keepAlive", transport_h)
        self.assertNotIn("esp_transport_keep_alive_t ka", transport)
        self.assertIn("&m_keepAlive", transport)

        self.assertIn("char hostname[256]", ping_h)
        self.assertIn("PingStats m_stats", ping_h)
        self.assertIn("StaticSemaphore_t m_ping_done_storage", ping_h)
        self.assertIn("cbs.cb_args = &m_stats", ping)
        self.assertIn("snprintf(m_stats.hostname", ping)
        self.assertIn("cbs.on_ping_end = on_ping_task_end", ping)
        stop = ping.index("esp_ping_stop(ping)")
        quiesce = ping.index("xSemaphoreTake(m_ping_done, portMAX_DELAY)", stop)
        delete = ping.index("esp_ping_delete_session(ping)", quiesce)
        self.assertNotIn("PING_TIMEOUT_MS + 200", ping)
        self.assertLess(stop, quiesce)
        self.assertLess(quiesce, delete)

    def test_pool_endpoint_display_uses_locked_value_snapshots(self) -> None:
        manager = _read("main/stratum/stratum_manager.cpp")
        manager_h = _read("main/stratum/stratum_manager.h")
        fallback = _read("main/stratum/stratum_manager_fallback.cpp")
        display = _function_body(_read("main/displays/displayDriver.cpp"),
                                 "void DisplayDriver::updateCurrentSettings")
        all_source = manager_h + _read("main/stratum/stratum_manager_dual_pool.h") + \
            _read("main/stratum/stratum_manager_fallback.h")

        snapshot = _function_body(manager, "bool StratumManager::copyPoolEndpoint(")
        self.assertIn("PThreadGuard lock(m_mutex)", snapshot)
        self.assertIn("copyPoolEndpointLocked", snapshot)
        current = _function_body(fallback, "bool StratumManagerFallback::copyCurrentPoolEndpoint")
        self.assertIn("PThreadGuard lock(m_mutex)", current)
        self.assertIn("copyPoolEndpointLocked", current)
        self.assertIn("char poolHost[128]", display)
        self.assertIn("copyPoolEndpoint", display)
        self.assertIn("copyCurrentPoolEndpoint", display)
        self.assertNotIn("getPoolHost", all_source)
        self.assertNotIn("getCurrentPoolHost", all_source)

    def test_fan_patches_are_validated_atomically_and_reloaded_under_lock(self) -> None:
        safety_h = _read("main/fan_config_safety.h")
        safety = _read("main/fan_config_safety.cpp")
        patch_parser = _read("main/http_server/fan_settings_patch.cpp")
        legacy = _function_body(_read("main/http_server/handler_system.cpp"),
                                "esp_err_t PATCH_update_settings")
        v2 = _function_body(_read("main/http_server/v2/handler_v2_settings.cpp"),
                            "esp_err_t PATCH_V2_settings")
        can = _function_body(_read("main/tasks/can_slave_task.cpp"),
                             "static void handle_settings_cmd")
        controller = _function_body(_read("main/fan_controller.cpp"),
                                    "void FanController::loadSettings")

        self.assertIn("MIN_MANUAL_SPEED = 30", safety_h)
        self.assertIn("MIN_TEMP_MARGIN = 5", safety_h)
        self.assertIn("settings.mode != MODE_MANUAL", safety)
        self.assertIn("settings.manualSpeed < MIN_MANUAL_SPEED", safety)
        self.assertIn("settings.targetTemp + MIN_TEMP_MARGIN", safety)
        self.assertIn("!isfinite(value)", safety)
        for field in ("overheat_temp", "autofanspeed", "manualFanSpeed",
                      "pidTargetTemp", "pidP", "pidI", "pidD"):
            self.assertIn(field, patch_parser)

        for body in (legacy, v2):
            validate_pos = body.index("parseFanSettingsPatch")
            first_write = body.index("Config::set")
            self.assertLess(validate_pos, first_write)
            lock_pos = body.index("POWER_MANAGEMENT_MODULE.lock()")
            reload_pos = body.index("getFanController().loadSettings()")
            unlock_pos = body.index("POWER_MANAGEMENT_MODULE.unlock()")
            self.assertLess(lock_pos, reload_pos)
            self.assertLess(reload_pos, unlock_pos)

        self.assertIn("ch >= FanConfigSafety::MAX_CHANNELS", can)
        self.assertIn("FanConfigSafety::validate", can)
        self.assertLess(can.index("POWER_MANAGEMENT_MODULE.lock()"),
                        can.index("getFanController().loadSettings()"))
        self.assertIn("sanitizePersisted", controller)
        self.assertIn("Config::flush()", controller)

    def test_startup_failures_keep_power_safe_and_gate_mining(self) -> None:
        main = _function_body(_read("main/main.cpp"), 'extern "C" void app_main')
        board_h = _read("main/boards/board.h")
        dual_h = _read("main/stratum/stratum_manager_dual_pool.h")
        fallback_h = _read("main/stratum/stratum_manager_fallback.h")
        nerd = _read("main/boards/nerdqaxeplus.cpp")
        init_board = _function_body(nerd, "bool NerdQaxePlus::initBoard")
        shutdown = _function_body(nerd, "void NerdQaxePlus::shutdown")
        system_init = _function_body(_read("main/system.cpp"), "void System::init()")

        self.assertIn("BOARD_INIT_FAULT", board_h)
        self.assertIn("ASIC_INIT_FAULT", board_h)
        self.assertLess(init_board.index("configureSafeOutput(BM1368_RST_PIN"),
                        init_board.index("i2c_master_init"))
        self.assertIn("EMC2302_set_fan_speed(channel, 1.0f)", init_board)
        self.assertIn("return false", init_board)
        self.assertLess(shutdown.index("setAsicReset(0)"), shutdown.index("VREG_disable()"))

        self.assertIn("const bool boardInitOk = board->initBoard()", main)
        self.assertIn("const bool canSlave = boardInitOk && board->isCanSlave()", main)
        self.assertIn("bool asicInitOk = false", main)
        mining_gate = main.rindex("if (asicInitOk)")
        self.assertGreater(main.index('xTaskCreatePSRAM(create_jobs_task'), mining_gate)
        self.assertGreater(main.index('xTaskCreatePSRAM(ASIC_result_task'), mining_gate)
        self.assertGreater(main.index('StratumManager::taskWrapper'), mining_gate)
        self.assertIn("network and HTTP remain available for recovery", main)
        self.assertNotIn("m_boardError = Board::Error::NONE", system_init)
        # In recovery mode the manager/config objects exist, but its Stratum and
        # ping tasks are deliberately not started. Telemetry and HTTP getters
        # must therefore tolerate empty task slots.
        for manager_header in (dual_h, fallback_h):
            pool_errors = _function_body(manager_header, "virtual int getPoolErrors()")
            self.assertIn("if (m_stratumTasks[i])", pool_errors)

    def test_v1_job_creation_uses_fixed_extranonce_buffer(self) -> None:
        source = _read("main/tasks/create_jobs_task.cpp")
        build = _function_body(source, "bm_job* buildBmJob")

        self.assertIn("char extranonce_2_str[MAX_EXTRANONCE_SIZE * 2 + 1]", build)
        self.assertIn("extranonce_2_len > MAX_EXTRANONCE_SIZE", build)
        self.assertIn("bool merkle_ok = calculate_merkle_root_hash", build)
        self.assertNotIn("char extranonce_2_str[extranonce_2_len", build)

    def test_clean_work_generation_is_rechecked_before_publish(self) -> None:
        source = _read("main/tasks/create_jobs_task.cpp")
        header = _read("main/tasks/create_jobs_task.h")
        sv2 = _read("main/tasks/create_jobs_sv2.cpp")

        self.assertIn("extern uint64_t miningJobGeneration[2]", header)
        self.assertIn("next_job_generation = miningJobGeneration[active_pool]", source)
        self.assertIn("miningJobGeneration[active_pool] != next_job_generation", source)
        self.assertIn("PThreadGuard publish_lock(current_stratum_job_mutex)", source)
        self.assertGreaterEqual(source.count("miningJobGeneration[pool]++"), 2)
        self.assertGreaterEqual(sv2.count("miningJobGeneration[pool]++"), 2)
        clean_all = _function_body(source, "void clean_asic_jobs_for_pool_locked")
        self.assertIn("asicJobs.cleanJobs(pool)", clean_all)
        self.assertIn("slaveAsicJobs[slave].cleanJobs(pool)", clean_all)
        self.assertGreaterEqual(sv2.count("clean_asic_jobs_for_pool_locked(pool)"), 2)

    def test_nerdqaxeplus_fan_and_power_fail_safe_guards(self) -> None:
        driver = _read("main/boards/drivers/EMC2302.cpp")
        set_fan = _function_body(driver, "esp_err_t EMC2302_set_fan_speed")
        get_fan = _function_body(driver, "esp_err_t EMC2302_get_fan_speed")
        board = _read("main/boards/nerdqaxeplus.cpp")
        init_board = _function_body(board, "bool NerdQaxePlus::initBoard")
        init_asics = _function_body(board, "bool NerdQaxePlus::initAsics")

        self.assertIn("!isfinite(percent)", set_fan)
        self.assertIn("percent < 0.0f", set_fan)
        self.assertIn("percent > 1.0f", set_fan)
        zero_guard = get_fan.index("rpm_raw == 0")
        divide = get_fan.index("/ rpm_raw")
        self.assertLess(zero_guard, divide)
        self.assertIn("EMC2302_set_fan_speed(channel, 1.0f)", init_board)
        self.assertIn("Fan controller initialization failed", init_board)
        self.assertIn("VREG_disable();", init_asics)
        self.assertIn("LDO_disable();", init_asics)
        self.assertIn("setAsicReset(0);", init_asics)


if __name__ == "__main__":
    unittest.main()
