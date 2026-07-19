from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text()


def _function_body(source: str, signature: str) -> str:
    start = source.index(signature)
    brace_start = source.index("{", start)
    depth = 0
    for index in range(brace_start, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[brace_start : index + 1]
    raise AssertionError(f"Could not find body for {signature}")


def test_static_file_handler_bounds_extension_and_rejects_traversal() -> None:
    header = _read("main/http_server/http_utils.h")
    source = _read("main/http_server/handler_file.cpp")

    assert "CHECK_FILE_EXTENSION" not in header
    extension_body = _function_body(source, "static bool has_file_extension")
    assert "filename_len >= extension_len" in extension_body

    path_body = _function_body(source, "static bool is_safe_uri_path")
    assert "segment[0] == '.'" in path_body
    assert "segment[1] == '.'" in path_body
    assert "b == 'e' || b == 'f'" in path_body
    assert "b == 'c'" in path_body

    handler_body = _function_body(source, "esp_err_t rest_common_get_handler")
    assert "strlcpy(uri_clean, req->uri" in handler_body
    assert '"URI too long"' in handler_body
    assert handler_body.index("is_safe_uri_path(uri_clean)") < handler_body.index("open(filepath")


def test_network_allowlist_parses_peer_family_and_rejects_oversized_origin() -> None:
    source = _read("main/http_server/http_cors.cpp")
    peer_body = _function_body(source, "static bool get_peer_ipv4")
    assert "struct sockaddr_storage" in peer_body
    assert "peer.ss_family == AF_INET" in peer_body
    assert "peer.ss_family == AF_INET6" in peer_body
    assert "mapped_prefix" in peer_body

    header_body = _function_body(source, "static bool copy_request_header")
    assert "httpd_req_get_hdr_value_len(req, name)" in header_body
    assert "len >= out_size" in header_body

    origin_body = _function_body(source, "static bool request_origin_is_allowed")
    assert 'copy_request_header(req, "Origin"' in origin_body
    assert 'copy_request_header(req, "Host"' in origin_body
    assert "authorities_match(origin, request_host)" in origin_body

    allowed_body = _function_body(source, "static esp_err_t network_allowed")
    peer_private = allowed_body.index("!ip_in_private_range(peer_ip)")
    origin_validation = allowed_body.index("request_origin_is_allowed")
    assert peer_private < origin_validation

    public_body = _function_body(source, "esp_err_t is_network_allowed")
    assert "return network_allowed(req, true)" in public_body


def test_otp_session_mint_uses_shared_rate_limited_validator() -> None:
    source = _read("main/http_server/handler_otp.cpp")
    session_body = _function_body(source, "esp_err_t POST_create_otp_session")
    assert "validateOTP(req, true)" in session_body
    assert "otp.validate(" not in session_body

    update_body = _function_body(source, "esp_err_t PATCH_update_otp")
    validation_branch = update_body[
        update_body.index("if (validateOTP(req, true)") : update_body.index("otp.disableEnrollment")
    ]
    assert "httpd_resp_send_err" not in validation_branch


def test_post_body_size_uses_size_t_and_validates_context() -> None:
    source = _read("main/http_server/http_utils.cpp")
    body = _function_body(source, "esp_err_t getPostData")
    assert "!req || !req->user_ctx" in body
    assert "const size_t total_len" in body
    assert "size_t cur_len" in body
    assert "sizeof(context->scratch)" in body


def test_factory_ota_failure_paths_release_buffers_and_reset_all_progress() -> None:
    source = _read("main/http_server/handler_ota_factory.cpp")
    update_body = _function_body(source, "esp_err_t FactoryOTAUpdate::ota_update_from_factory")
    assert update_body.index("MemoryGuard gcUrl(url)") < update_body.index("MALLOC(WWW_LEN_BYTES)")
    assert update_body.index("MemoryGuard gcwwwData(wwwData)") < update_body.index("CALLOC(1, sizeof(redirect_ctx_t))")

    reset_body = _function_body(source, "void FactoryOTAUpdate::resetProgress")
    assert "m_fw_written = 0" in reset_body
    assert "m_www_written = 0" in reset_body
    assert "m_www_recv = 0" in reset_body

    status_body = _function_body(source, "esp_err_t GET_OTA_status")
    assert "is_network_allowed(req)" in status_body
    assert "set_cors_headers(req)" in status_body


def test_websocket_startup_and_frame_receive_fail_closed() -> None:
    source = _read("main/http_server/http_websocket.cpp")
    log_body = _function_body(source, "static int log_to_queue")
    assert "formatted_size < 0" in log_body
    assert "!log_queue ||" in log_body

    handler_body = _function_body(source, "esp_err_t echo_handler")
    assert "if (!log_queue)" in handler_body
    assert handler_body.count("httpd_ws_recv_frame") >= 2
    assert "ws_pkt.len > 125" in handler_body

    start_body = _function_body(source, "void websocket_start")
    assert "if (!log_queue)" in start_body
    assert "xTaskCreatePSRAM" in start_body
    assert "vQueueDelete(log_queue)" in start_body


def test_wifi_event_loop_does_not_block_and_scan_timeout_stops_driver_scan() -> None:
    source = _read("main/network/connect.cpp")
    event_body = _function_body(source, "static void event_handler")
    assert "vTaskDelay" not in event_body

    init_body = _function_body(source, "esp_netif_t *wifi_init(")
    start_failure = init_body[init_body.index("esp_wifi_start()") : init_body.index("esp_wifi_set_ps")]
    assert "err != ESP_OK" in start_failure
    assert "return nullptr" in start_failure
    assert "ESP_ERR_WIFI_CONN" not in start_failure

    scan_body = _function_body(source, "esp_err_t wifi_scan")
    timeout = scan_body.index('ESP_LOGE(TAG, "WiFi scan timeout")')
    timeout_return = scan_body.index("return ESP_ERR_TIMEOUT", timeout)
    assert "esp_wifi_scan_stop()" in scan_body[timeout:timeout_return]

    manager = _read("main/network/network_manager.cpp")
    eth_ip = _function_body(manager, "void NetworkManager::onEthGotIp")
    assert eth_ip.index("if (err == ESP_OK)") < eth_ip.index("m_wifiDisabledBecauseEth = true")
    assert "xEventGroupClearBits(m_eg, NET_WIFI_IP)" in eth_ip

    link_down = _function_body(manager, "void NetworkManager::onEthLinkDown")
    assert "err == ESP_OK" in link_down
    assert "ESP_ERR_WIFI_CONN" not in link_down


def test_w5500_init_is_idempotent_and_propagates_setup_errors() -> None:
    source = _read("main/network/w5500.cpp")
    early_body = _function_body(source, "esp_err_t W5500::earlySpiInit")
    assert "if (m_prepared)" in early_body
    assert early_body.index("esp_eth_driver_install") < early_body.index("esp_netif_new")
    assert "esp_eth_del_netif_glue(glue)" in early_body

    cleanup_body = _function_body(source, "static esp_err_t uninstallDriverAndDeleteObjects")
    assert cleanup_body.index("esp_eth_driver_uninstall") < cleanup_body.index("phy->del")
    uninstall_failure = cleanup_body[
        cleanup_body.index("if (err != ESP_OK)") : cleanup_body.index("handle = nullptr")
    ]
    assert "phy->del" not in uninstall_failure
    assert "mac->del" not in uninstall_failure

    init_body = _function_body(source, "esp_err_t W5500::init")
    assert "if (m_inited)" in init_body
    assert "earlySpiInit()" in init_body
    assert "ESP_ERROR_CHECK" not in init_body
    assert "esp_event_handler_unregister" in init_body


def test_mdns_setup_rolls_back_partial_initialization() -> None:
    source = _read("main/network/mdns_service.cpp")
    body = _function_body(source, "void mdns_service_start")
    assert "mdns_hostname_set" in body
    assert "mdns_instance_name_set" in body
    assert body.count("mdns_free()") >= 3
