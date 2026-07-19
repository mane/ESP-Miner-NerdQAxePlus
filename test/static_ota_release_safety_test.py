from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text()


class OtaReleaseSafetyContractTest(unittest.TestCase):
    def test_manual_ota_validates_before_hardware_shutdown_and_has_bounded_receives(self) -> None:
        source = _read("main/http_server/handler_ota.cpp")

        self.assertIn("MAX_CONSECUTIVE_RECV_TIMEOUTS", source)
        self.assertIn("UPLOAD_DEADLINE_US", source)
        self.assertIn("ESP_CHIP_ID_ESP32S3", source)
        self.assertIn("ESP_IMAGE_FLASH_SIZE_16MB", source)
        self.assertIn("ESP_APP_DESC_MAGIC_WORD", source)
        self.assertIn('strstr(candidate->version, "nqa-lts")', source)
        self.assertIn("candidate->secure_version < running->secure_version", source)
        self.assertIn("esp_ota_begin(ota_partition, image_size", source)

        validation = source.index("if (!validate_nerdqaxeplus_firmware_prefix(buf, prefix_received))")
        shutdown = source.index("POWER_MANAGEMENT_MODULE.shutdown()", validation)
        self.assertLess(validation, shutdown)


    def test_www_is_fully_buffered_before_safe_filesystem_mutation(self) -> None:
        source = _read("main/http_server/handler_ota.cpp")
        handler = source[source.index("esp_err_t POST_WWW_update") : source.index("esp_err_t POST_OTA_update")]

        self.assertIn("req->content_len != WWW_IMAGE_SIZE", handler)
        self.assertLess(handler.index("MALLOC(WWW_IMAGE_SIZE)"), handler.index("esp_vfs_spiffs_unregister"))
        self.assertLess(handler.index("received_total < WWW_IMAGE_SIZE"), handler.index("esp_vfs_spiffs_unregister"))
        self.assertLess(handler.index("esp_vfs_spiffs_unregister"), handler.index("esp_partition_erase_range"))
        self.assertLess(handler.index("esp_partition_write"), handler.index("init_fs()"))
        self.assertIn("POWER_MANAGEMENT_MODULE.restart()", handler)


    def test_factory_activation_is_last_and_download_source_is_pinned(self) -> None:
        source = _read("main/http_server/handler_ota_factory.cpp")

        self.assertIn("GITHUB_RELEASE_ASSET_PREFIX", source)
        self.assertIn("FACTORY_ASSET_NAME_PREFIX", source)
        self.assertIn("strchr(release_path, '?')", source)
        self.assertIn("is_safe_redirect_url(ctx->loc)", source)
        self.assertIn("clen != FACTORY_IMAGE_SIZE", source)

        update = source[source.index("esp_err_t FactoryOTAUpdate::ota_update_from_factory") : source.index("FactoryOTAUpdate::FactoryOTAUpdate")]
        self.assertLess(update.index("do_firmware_update(client, firmware_prefix, firmware_prefix_len)"),
                        update.index("do_www_update(wwwData)"))
        self.assertLess(update.index("do_www_update(wwwData)"), update.index("esp_ota_set_boot_partition"))

        flash = source[source.index("esp_err_t FactoryOTAUpdate::do_firmware_update") : source.index("esp_err_t FactoryOTAUpdate::erase_nvs_partition")]
        self.assertIn("esp_ota_end", flash)
        self.assertNotIn("esp_ota_set_boot_partition", flash)


    def test_factory_worker_is_not_gated_by_mining_credentials(self) -> None:
        source = _read("main/main.cpp")
        setup = source.index("setup_network(board->hasEthernet())")
        worker = source.index("xTaskCreate(FACTORY_OTA_UPDATER.taskWrapper", setup)
        username = source.index("Config::cfgGetStrAlloc(NVS_CONFIG_STRATUM_USER", setup)

        self.assertLess(setup, worker)
        self.assertLess(worker, username)
        self.assertEqual(source.count("xTaskCreate(FACTORY_OTA_UPDATER.taskWrapper"), 1)


    def test_one_click_ui_recovers_from_backend_and_transport_errors(self) -> None:
        source = _read("main/http_server/axe-os/src/app/pages/settings/settings.component.ts")

        self.assertIn("catchError((err)", source)
        self.assertIn("consecutiveStatusErrors >= 5", source)
        self.assertIn("status.step === 'error'", source)
        self.assertIn("failOneClickUpdate", source)
        self.assertIn("this.isOneClickUpdate = false", source)


    def test_cors_echoes_only_validated_device_origins(self) -> None:
        source = _read("main/http_server/http_cors.cpp")

        self.assertIn("origin_ip_matches_device", source)
        self.assertIn("hostname_matches_device", source)
        self.assertIn("captive_portal_same_origin", source)
        self.assertIn("authorities_match(origin, request_host)", source)
        self.assertIn("network_allowed(req, false)", source)
        self.assertIn('\"Access-Control-Allow-Origin\", origin', source)
        self.assertNotIn('\"Access-Control-Allow-Origin\", \"*\"', source)
        self.assertNotIn("is_localhost", source)
        self.assertNotIn("is_local(", source)


if __name__ == "__main__":
    unittest.main()
