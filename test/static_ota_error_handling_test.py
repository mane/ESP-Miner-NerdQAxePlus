from pathlib import Path
import re
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


class OtaErrorHandlingContractTest(unittest.TestCase):
    def test_direct_ota_handlers_return_errors_instead_of_aborting(self):
        source = _read("main/http_server/handler_ota.cpp")
        self.assertIsNone(re.search(r"\bESP_ERROR_CHECK\s*\(", source))

        www_body = _function_body(source, "esp_err_t POST_WWW_update")
        self.assertIn("req->content_len != WWW_IMAGE_SIZE", www_body)
        self.assertLess(www_body.index("MALLOC(WWW_IMAGE_SIZE)"), www_body.index("esp_partition_erase_range"))
        self.assertLess(www_body.index("received_total < WWW_IMAGE_SIZE"), www_body.index("esp_vfs_spiffs_unregister"))
        self.assertIn("esp_partition_erase_range", www_body)
        self.assertRegex(www_body, r"esp_err_t\s+err\s*=\s*esp_partition_erase_range")
        self.assertIn("if (!image || !flash_buf)", www_body)
        self.assertIn("init_fs()", www_body)
        self.assertIn("POWER_MANAGEMENT_MODULE.restart()", www_body)

        fw_body = _function_body(source, "esp_err_t POST_OTA_update")
        self.assertIn("req->content_len <= APP_PREFIX_SIZE", fw_body)
        self.assertIn("image_size > ota_partition->size", fw_body)
        self.assertLess(fw_body.index("validate_nerdqaxeplus_firmware_prefix"), fw_body.index("POWER_MANAGEMENT_MODULE.shutdown"))
        self.assertIn("if (!ota_partition)", fw_body)
        self.assertRegex(fw_body, r"esp_err_t\s+ota_err\s*=\s*esp_ota_begin\(ota_partition, image_size,")
        self.assertIn("if (!buf)", fw_body)
        self.assertIn("esp_ota_abort(ota_handle)", fw_body)
        self.assertIn("send_error_and_restart", fw_body)
        self.assertIn("send_timeout_and_restart", fw_body)
        self.assertRegex(fw_body, r"ota_err\s*=\s*esp_ota_end")
        self.assertRegex(fw_body, r"ota_err\s*=\s*esp_ota_set_boot_partition")

    def test_factory_ota_returns_esp_errors_instead_of_aborting(self):
        source = _read("main/http_server/handler_ota_factory.cpp")
        self.assertIsNone(re.search(r"\bESP_ERROR_CHECK\s*\(", source))

        www_body = _function_body(source, "esp_err_t FactoryOTAUpdate::do_www_update")
        self.assertRegex(www_body, r"esp_err_t\s+err\s*=\s*esp_partition_erase_range")

        fw_body = _function_body(source, "esp_err_t FactoryOTAUpdate::do_firmware_update")
        self.assertIn("if (ota_partition == NULL)", fw_body)
        self.assertRegex(fw_body, r"esp_err_t\s+err\s*=\s*esp_ota_begin")
        self.assertIn("esp_ota_write(ota_handle, prefix, prefix_len)", fw_body)
        self.assertRegex(fw_body, r"err\s*=\s*esp_ota_end")
        self.assertNotIn("esp_ota_set_boot_partition", fw_body)

        update_body = _function_body(source, "esp_err_t FactoryOTAUpdate::ota_update_from_factory")
        self.assertLess(update_body.index("validate_nerdqaxeplus_firmware_prefix"), update_body.index("POWER_MANAGEMENT_MODULE.shutdown"))
        self.assertLess(update_body.index("do_www_update(wwwData)"), update_body.index("esp_ota_set_boot_partition"))
        self.assertIn("clen != FACTORY_IMAGE_SIZE", update_body)


if __name__ == "__main__":
    unittest.main()
