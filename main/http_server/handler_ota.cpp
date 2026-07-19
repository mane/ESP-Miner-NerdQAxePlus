#include <cstring>

#include "esp_app_desc.h"
#include "esp_app_format.h"
#include "esp_chip_info.h"
#include "esp_http_server.h"
#include "esp_log.h"
#include "esp_ota_ops.h"
#include "esp_spiffs.h"
#include "esp_timer.h"

#include "global_state.h"

#include "guards.h"
#include "handler_file.h"
#include "handler_ota.h"
#include "http_cors.h"
#include "http_utils.h"
#include "macros.h"

static const char *TAG = "http_ota";

static constexpr size_t IO_CHUNK_SIZE = 2048;
static constexpr size_t WWW_IMAGE_SIZE = 3U * 1024U * 1024U;
static constexpr int MAX_CONSECUTIVE_RECV_TIMEOUTS = 3;
static constexpr int64_t UPLOAD_DEADLINE_US = 5LL * 60LL * 1000000LL;
static constexpr size_t APP_DESC_OFFSET = sizeof(esp_image_header_t) + sizeof(esp_image_segment_header_t);
static constexpr size_t APP_PREFIX_SIZE = APP_DESC_OFFSET + sizeof(esp_app_desc_t);

extern bool enter_recovery;

static constexpr size_t min_size(size_t first, size_t second)
{
    return first < second ? first : second;
}

static int recv_with_limits(httpd_req_t *req, uint8_t *dst, size_t len, int &consecutive_timeouts, int64_t deadline_us)
{
    while (esp_timer_get_time() < deadline_us) {
        int received = httpd_req_recv(req, reinterpret_cast<char *>(dst), len);
        if (received == HTTPD_SOCK_ERR_TIMEOUT) {
            ++consecutive_timeouts;
            if (consecutive_timeouts >= MAX_CONSECUTIVE_RECV_TIMEOUTS) {
                return HTTPD_SOCK_ERR_TIMEOUT;
            }
            continue;
        }
        if (received > 0) {
            consecutive_timeouts = 0;
        }
        return received;
    }
    return HTTPD_SOCK_ERR_TIMEOUT;
}

static esp_err_t send_timeout_response(httpd_req_t *req)
{
    httpd_resp_set_status(req, "408 Request Timeout");
    (void) httpd_resp_sendstr(req, "Upload timed out");
    return ESP_FAIL;
}

static esp_err_t send_error_and_restart(httpd_req_t *req, httpd_err_code_t status, const char *message)
{
    (void) httpd_resp_send_err(req, status, message);
    vTaskDelay(pdMS_TO_TICKS(1000));
    POWER_MANAGEMENT_MODULE.restart();
    return ESP_FAIL; // unreachable
}

static esp_err_t send_timeout_and_restart(httpd_req_t *req)
{
    (void) send_timeout_response(req);
    vTaskDelay(pdMS_TO_TICKS(1000));
    POWER_MANAGEMENT_MODULE.restart();
    return ESP_FAIL; // unreachable
}

static bool is_nonempty_c_string(const char *value, size_t capacity)
{
    return value && value[0] != '\0' && memchr(value, '\0', capacity) != nullptr;
}

size_t ota_firmware_prefix_size()
{
    return APP_PREFIX_SIZE;
}

bool validate_nerdqaxeplus_firmware_prefix(const uint8_t *prefix, size_t prefix_len)
{
    if (!prefix || prefix_len < APP_PREFIX_SIZE) {
        ESP_LOGE(TAG, "firmware prefix is too short");
        return false;
    }

    const auto *image_header = reinterpret_cast<const esp_image_header_t *>(prefix);
    if (image_header->magic != ESP_IMAGE_HEADER_MAGIC || image_header->segment_count == 0 ||
        image_header->segment_count > ESP_IMAGE_MAX_SEGMENTS) {
        ESP_LOGE(TAG, "invalid ESP application image header");
        return false;
    }
    if (image_header->chip_id != ESP_CHIP_ID_ESP32S3) {
        ESP_LOGE(TAG, "firmware targets chip id 0x%04x, expected ESP32-S3", (unsigned) image_header->chip_id);
        return false;
    }
    if (image_header->spi_size != ESP_IMAGE_FLASH_SIZE_16MB) {
        ESP_LOGE(TAG, "firmware flash size id %u is not the required 16 MiB layout", (unsigned) image_header->spi_size);
        return false;
    }

    esp_chip_info_t chip_info = {};
    esp_chip_info(&chip_info);
    if (chip_info.model != CHIP_ESP32S3 || chip_info.revision < image_header->min_chip_rev_full ||
        chip_info.revision > image_header->max_chip_rev_full) {
        ESP_LOGE(TAG, "firmware chip revision range %u..%u does not include device revision %u",
                 (unsigned) image_header->min_chip_rev_full, (unsigned) image_header->max_chip_rev_full,
                 (unsigned) chip_info.revision);
        return false;
    }

    const auto *segment_header = reinterpret_cast<const esp_image_segment_header_t *>(prefix + sizeof(esp_image_header_t));
    if (segment_header->data_len < sizeof(esp_app_desc_t)) {
        ESP_LOGE(TAG, "first firmware segment does not contain an application descriptor");
        return false;
    }

    const auto *candidate = reinterpret_cast<const esp_app_desc_t *>(prefix + APP_DESC_OFFSET);
    const esp_app_desc_t *running = esp_app_get_description();
    if (!running || candidate->magic_word != ESP_APP_DESC_MAGIC_WORD ||
        !is_nonempty_c_string(candidate->project_name, sizeof(candidate->project_name)) ||
        !is_nonempty_c_string(candidate->version, sizeof(candidate->version))) {
        ESP_LOGE(TAG, "invalid application descriptor");
        return false;
    }
    if (strncmp(candidate->project_name, running->project_name, sizeof(candidate->project_name)) != 0 ||
        strncmp(candidate->project_name, "esp-miner", sizeof(candidate->project_name)) != 0) {
        ESP_LOGE(TAG, "firmware project '%s' does not match '%s'", candidate->project_name, running->project_name);
        return false;
    }
    // This LTS branch is intentionally single-board. esp_app_desc_t has no board
    // field, so the release-channel marker is the strongest descriptor-level
    // guard available in addition to the ESP32-S3/project/layout checks above.
    if (strstr(candidate->version, "nqa-lts") == nullptr) {
        ESP_LOGE(TAG, "firmware version '%s' is not a NerdQAxe+ LTS build", candidate->version);
        return false;
    }
    if (candidate->secure_version < running->secure_version) {
        ESP_LOGE(TAG, "firmware secure version %lu is older than running secure version %lu",
                 (unsigned long) candidate->secure_version, (unsigned long) running->secure_version);
        return false;
    }

    ESP_LOGI(TAG, "validated firmware project=%s version=%s secure_version=%lu", candidate->project_name,
             candidate->version, (unsigned long) candidate->secure_version);
    return true;
}

esp_err_t POST_WWW_update(httpd_req_t *req)
{
    ConGuard connection_guard(http_server, req);

    if (is_network_allowed(req) != ESP_OK) {
        return httpd_resp_send_err(req, HTTPD_401_UNAUTHORIZED, "Unauthorized");
    }
    // OTP is unavailable when the primary filesystem cannot mount, so the
    // embedded recovery page may restore the exact WWW image without it.
    if (!enter_recovery && validateOTP(req) != ESP_OK) {
        return ESP_FAIL;
    }

    if (req->content_len != WWW_IMAGE_SIZE) {
        return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "WWW image must be exactly 3 MiB");
    }

    const esp_partition_t *www_partition =
        esp_partition_find_first(ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_DATA_SPIFFS, "www");
    if (!www_partition) {
        return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "WWW partition not found");
    }
    if (www_partition->size != WWW_IMAGE_SIZE) {
        ESP_LOGE(TAG, "unexpected WWW partition size: %lu", (unsigned long) www_partition->size);
        return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "WWW partition layout mismatch");
    }

    // Receive the complete image before unmounting or erasing the live UI.
    // Flash writes require an internal-RAM source, hence the separate chunk.
    uint8_t *image = static_cast<uint8_t *>(MALLOC(WWW_IMAGE_SIZE));
    MemoryGuard image_guard(image);
    uint8_t *flash_buf = static_cast<uint8_t *>(malloc(IO_CHUNK_SIZE));
    MemoryGuard flash_buf_guard(flash_buf);
    if (!image || !flash_buf) {
        return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Out of memory");
    }

    size_t received_total = 0;
    int consecutive_timeouts = 0;
    const int64_t deadline_us = esp_timer_get_time() + UPLOAD_DEADLINE_US;
    while (received_total < WWW_IMAGE_SIZE) {
        const size_t want = min_size(IO_CHUNK_SIZE, WWW_IMAGE_SIZE - received_total);
        int received = recv_with_limits(req, image + received_total, want, consecutive_timeouts, deadline_us);
        if (received == HTTPD_SOCK_ERR_TIMEOUT) {
            return send_timeout_response(req);
        }
        if (received <= 0) {
            return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "Incomplete WWW upload");
        }
        received_total += static_cast<size_t>(received);
        taskYIELD();
    }

    if (!enter_recovery) {
        esp_err_t unmount_err = esp_vfs_spiffs_unregister(nullptr);
        if (unmount_err != ESP_OK) {
            ESP_LOGE(TAG, "failed to unmount WWW filesystem: %s", esp_err_to_name(unmount_err));
            return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "WWW unmount failed");
        }
    }

    LockGuard power_guard(POWER_MANAGEMENT_MODULE);
    POWER_MANAGEMENT_MODULE.shutdown();

    ESP_LOGI(TAG, "erasing WWW partition");
    esp_err_t err = esp_partition_erase_range(www_partition, 0, www_partition->size);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "WWW partition erase failed: %s", esp_err_to_name(err));
        return send_error_and_restart(req, HTTPD_500_INTERNAL_SERVER_ERROR, "WWW erase failed; rebooting");
    }

    for (size_t offset = 0; offset < WWW_IMAGE_SIZE; offset += IO_CHUNK_SIZE) {
        memcpy(flash_buf, image + offset, IO_CHUNK_SIZE);
        err = esp_partition_write(www_partition, offset, flash_buf, IO_CHUNK_SIZE);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "WWW write failed at 0x%lx: %s", (unsigned long) offset, esp_err_to_name(err));
            return send_error_and_restart(req, HTTPD_500_INTERNAL_SERVER_ERROR, "WWW write failed; rebooting");
        }
        taskYIELD();
    }

    if (init_fs() != ESP_OK) {
        enter_recovery = true;
        ESP_LOGE(TAG, "updated WWW image could not be mounted; rebooting into recovery");
        return send_error_and_restart(req, HTTPD_500_INTERNAL_SERVER_ERROR, "WWW validation failed; rebooting");
    }
    enter_recovery = false;

    (void) httpd_resp_sendstr(req, "WWW update complete, rebooting now\n");
    vTaskDelay(pdMS_TO_TICKS(1000));
    POWER_MANAGEMENT_MODULE.restart();
    return ESP_OK; // unreachable
}

esp_err_t POST_OTA_update(httpd_req_t *req)
{
    ConGuard connection_guard(http_server, req);

    if (is_network_allowed(req) != ESP_OK) {
        return httpd_resp_send_err(req, HTTPD_401_UNAUTHORIZED, "Unauthorized");
    }
    if (validateOTP(req) != ESP_OK) {
        return ESP_FAIL;
    }
    if (req->content_len <= APP_PREFIX_SIZE) {
        return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "Firmware image is empty or too small");
    }

    const esp_partition_t *ota_partition = esp_ota_get_next_update_partition(nullptr);
    if (!ota_partition) {
        return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "OTA partition not found");
    }
    const size_t image_size = req->content_len;
    if (image_size > ota_partition->size) {
        return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "Firmware image exceeds OTA partition");
    }

    uint8_t *buf = static_cast<uint8_t *>(malloc(IO_CHUNK_SIZE));
    MemoryGuard buffer_guard(buf);
    if (!buf) {
        return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Out of memory");
    }

    size_t prefix_received = 0;
    size_t remaining = image_size;
    int consecutive_timeouts = 0;
    const int64_t deadline_us = esp_timer_get_time() + UPLOAD_DEADLINE_US;
    while (prefix_received < APP_PREFIX_SIZE) {
        const size_t want = min_size(APP_PREFIX_SIZE - prefix_received, remaining);
        int received = recv_with_limits(req, buf + prefix_received, want, consecutive_timeouts, deadline_us);
        if (received == HTTPD_SOCK_ERR_TIMEOUT) {
            return send_timeout_response(req);
        }
        if (received <= 0) {
            return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "Incomplete firmware header");
        }
        prefix_received += static_cast<size_t>(received);
        remaining -= static_cast<size_t>(received);
    }
    if (!validate_nerdqaxeplus_firmware_prefix(buf, prefix_received)) {
        return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "Incompatible firmware image");
    }

    LockGuard power_guard(POWER_MANAGEMENT_MODULE);
    POWER_MANAGEMENT_MODULE.shutdown();

    esp_ota_handle_t ota_handle = 0;
    esp_err_t ota_err = esp_ota_begin(ota_partition, image_size, &ota_handle);
    if (ota_err != ESP_OK) {
        ESP_LOGE(TAG, "esp_ota_begin failed: %s", esp_err_to_name(ota_err));
        return send_error_and_restart(req, HTTPD_500_INTERNAL_SERVER_ERROR, "OTA begin failed; rebooting");
    }

    ota_err = esp_ota_write(ota_handle, buf, prefix_received);
    if (ota_err != ESP_OK) {
        (void) esp_ota_abort(ota_handle);
        ESP_LOGE(TAG, "initial OTA write failed: %s", esp_err_to_name(ota_err));
        return send_error_and_restart(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Firmware write failed; rebooting");
    }

    size_t offset = prefix_received;
    while (remaining > 0) {
        const size_t want = min_size(IO_CHUNK_SIZE, remaining);
        int received = recv_with_limits(req, buf, want, consecutive_timeouts, deadline_us);
        if (received == HTTPD_SOCK_ERR_TIMEOUT) {
            (void) esp_ota_abort(ota_handle);
            return send_timeout_and_restart(req);
        }
        if (received <= 0) {
            (void) esp_ota_abort(ota_handle);
            return send_error_and_restart(req, HTTPD_400_BAD_REQUEST, "Incomplete firmware upload; rebooting");
        }

        ota_err = esp_ota_write(ota_handle, buf, received);
        if (ota_err != ESP_OK) {
            (void) esp_ota_abort(ota_handle);
            ESP_LOGE(TAG, "OTA write failed at 0x%lx: %s", (unsigned long) offset, esp_err_to_name(ota_err));
            return send_error_and_restart(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Firmware write failed; rebooting");
        }
        remaining -= static_cast<size_t>(received);
        offset += static_cast<size_t>(received);
        taskYIELD();
    }

    ota_err = esp_ota_end(ota_handle);
    if (ota_err != ESP_OK) {
        ESP_LOGE(TAG, "esp_ota_end failed: %s", esp_err_to_name(ota_err));
        return send_error_and_restart(req, HTTPD_400_BAD_REQUEST, "Firmware validation failed; rebooting");
    }

    ota_err = esp_ota_set_boot_partition(ota_partition);
    if (ota_err != ESP_OK) {
        ESP_LOGE(TAG, "esp_ota_set_boot_partition failed: %s", esp_err_to_name(ota_err));
        return send_error_and_restart(req, HTTPD_500_INTERNAL_SERVER_ERROR, "Firmware activation failed; rebooting");
    }

    (void) httpd_resp_sendstr(req, "Firmware update complete, rebooting now\n");
    vTaskDelay(pdMS_TO_TICKS(1000));
    POWER_MANAGEMENT_MODULE.restart();
    return ESP_OK; // unreachable
}
