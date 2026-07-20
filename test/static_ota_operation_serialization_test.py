from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text()


def _function_body(source: str, signature: str) -> str:
    start = source.index(signature)
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[brace:index + 1]
    raise AssertionError(f"unterminated function: {signature}")


class OtaOperationSerializationContractTest(unittest.TestCase):
    def test_shared_lock_is_atomic_and_raii_owned(self) -> None:
        header = _read("main/http_server/ota_operation.h")
        source = _read("main/http_server/ota_operation.cpp")
        cmake = _read("main/CMakeLists.txt")

        self.assertIn("std::atomic<bool>", source)
        self.assertIn("compare_exchange_strong", source)
        self.assertIn("std::memory_order_acq_rel", source)
        self.assertIn("std::memory_order_release", source)
        self.assertIn("OtaOperationGuard::~OtaOperationGuard", source)
        self.assertIn("ota_operation_release();", source)
        self.assertIn("OtaOperationGuard(const OtaOperationGuard &) = delete", header)
        self.assertIn('"./http_server/ota_operation.cpp"', cmake)

    def test_manual_routes_reserve_before_receiving_or_mutating_flash(self) -> None:
        source = _read("main/http_server/handler_ota.cpp")
        www = _function_body(source, "esp_err_t POST_WWW_update")
        firmware = _function_body(source, "esp_err_t POST_OTA_update")

        for body in (www, firmware):
            self.assertIn("OtaOperationGuard ota_guard;", body)
            self.assertIn("if (!ota_guard.acquired())", body)
            self.assertIn("send_ota_busy_response(req)", body)

        self.assertLess(www.index("OtaOperationGuard ota_guard"), www.index("recv_with_limits"))
        self.assertLess(www.index("OtaOperationGuard ota_guard"), www.index("esp_vfs_spiffs_unregister"))
        self.assertLess(www.index("OtaOperationGuard ota_guard"), www.index("esp_partition_erase_range"))
        self.assertLess(firmware.index("OtaOperationGuard ota_guard"), firmware.index("recv_with_limits"))
        self.assertLess(firmware.index("OtaOperationGuard ota_guard"), firmware.index("esp_ota_begin"))
        self.assertLess(firmware.index("OtaOperationGuard ota_guard"), firmware.index("esp_ota_set_boot_partition"))

    def test_factory_route_reserves_before_queueing_and_worker_adopts(self) -> None:
        source = _read("main/http_server/handler_ota_factory.cpp")
        trigger = _function_body(source, "bool FactoryOTAUpdate::trigger")
        task = _function_body(source, "void FactoryOTAUpdate::task()")

        self.assertLess(trigger.index("ota_operation_try_acquire()"), trigger.index("m_pending = true"))
        self.assertGreaterEqual(trigger.count("ota_operation_release();"), 2)
        self.assertIn("m_ota_operation_reserved = true", trigger)
        self.assertLess(
            task.index("OtaOperationLockMode::ADOPT_ACQUIRED"),
            task.index("ota_update_from_factory(url, keep_config)"),
        )

    def test_frontend_entry_points_are_mutually_exclusive(self) -> None:
        source = _read("main/http_server/axe-os/src/app/pages/settings/settings.component.ts")
        template = _read("main/http_server/axe-os/src/app/pages/settings/settings.component.html")
        busy_guard = "this.isOneClickUpdate || this.isFirmwareUploading || this.isWebsiteUploading"

        for signature in (
            "public uploadFirmwareFile()",
            "public async uploadWebsiteFile()",
            "public directUpdateFromGithub()",
        ):
            body = _function_body(source, signature)
            self.assertIn(busy_guard, body)
            self.assertLess(body.index(busy_guard), body.index("ensureOtp$"))

        website = _function_body(source, "public async uploadWebsiteFile()")
        self.assertIn("await validateWebsiteImage", website)
        self.assertIn("TOAST.WWW_IDENTITY_INVALID", website)
        self.assertGreaterEqual(template.count("isOneClickUpdate || isFirmwareUploading || isWebsiteUploading"), 3)


if __name__ == "__main__":
    unittest.main()
