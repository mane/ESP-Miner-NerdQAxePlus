#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>

#include "esp_crt_bundle.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_ota_ops.h"
#include "esp_partition.h"
#include "esp_spiffs.h"

#include "global_state.h"

#include "guards.h"
#include "handler_file.h"
#include "handler_ota.h"
#include "http_cors.h"
#include "http_utils.h"
#include "macros.h"
#include "psram_allocator.h"

#define GITHUB_RELEASE_DOWNLOAD_PREFIX "https://github.com/mane/ESP-Miner-NerdQAxePlus/releases/download/"
#define GITHUB_RELEASE_ASSET_PREFIX "https://release-assets.githubusercontent.com/"
#define FACTORY_ASSET_NAME_PREFIX "esp-miner-factory-NerdQAxePlus-LTS-"

#define FW_START 0x10000
#define FW_LEN_MB 4
#define FW_LEN_BYTES (FW_LEN_MB * 1024 * 1024)

#define WWW_START 0x410000
#define WWW_LEN_MB 3
#define WWW_LEN_BYTES (WWW_LEN_MB * 1024 * 1024)

// merge_bin output ends after the 8 KiB ota_data_initial image at 0xf10000.
#define FACTORY_IMAGE_SIZE 0xf12000

#define CHUNK_SIZE 2048
#define URL_SIZE 4096 // make this big to avoid truncation

#define CHECK_ALLOC(p)                                                                                                             \
    do {                                                                                                                           \
        if (!(p)) {                                                                                                                \
            ESP_LOGE(TAG, "alloc failed: %s", #p);                                                                                 \
            return ESP_ERR_NO_MEM;                                                                                                 \
        }                                                                                                                          \
    } while (0)

static const char *TAG = "http_ota";

extern bool enter_recovery;

static bool is_safe_github_url(const char *url);

static bool is_safe_redirect_url(const char *url)
{
    if (!url)
        return false;
    if (is_safe_github_url(url))
        return true;
    if (strncasecmp(url, GITHUB_RELEASE_ASSET_PREFIX, strlen(GITHUB_RELEASE_ASSET_PREFIX)) != 0)
        return false;
    if (strchr(url, '\\') || strstr(url, "../") || strstr(url, "/..") || strstr(url, "%2e%2e") || strstr(url, "%2E%2E"))
        return false;
    for (const char *p = url; *p; ++p) {
        const unsigned char c = (unsigned char) *p;
        if (c < 0x20 || c == 0x7f)
            return false;
    }
    return true;
}

// --- HTTP event: capture Location header when requested (per-client via user_data)
esp_err_t http_evt_capture_location(esp_http_client_event_t *evt)
{
    if (!evt || !evt->user_data)
        return ESP_OK;
    redirect_ctx_t *ctx = (redirect_ctx_t *) evt->user_data;
    if (evt->event_id == HTTP_EVENT_ON_HEADER && ctx->capture) {
        const char *k = evt->header_key ? evt->header_key : "";
        const char *v = evt->header_value ? evt->header_value : "";
        if (strcasecmp(k, "Location") == 0) {
            strlcpy(ctx->loc, v, sizeof(ctx->loc)); // full value incl. query if present
            ESP_LOGI(TAG, "Location: %s", ctx->loc);
        }
    }
    return ESP_OK;
}

// Read exactly 'len' bytes or fail (premature EOF == error).
// Returns ESP_OK on success; ESP_ERR_INVALID_SIZE on premature EOF; ESP_FAIL on read error.
esp_err_t FactoryOTAUpdate::http_read_exact(esp_http_client_handle_t &client, uint8_t *dst, int len)
{
    uint8_t *p = (uint8_t *) dst;
    size_t left = len;
    while (left) {
        int got = esp_http_client_read(client, (char *) p, (int) left);
        if (got < 0)
            return ESP_FAIL; // read error
        if (got == 0)
            return ESP_ERR_INVALID_SIZE; // premature EOF
        p += got;
        left -= (size_t) got;
    }
    return ESP_OK;
}

// Reads one chunk
// Returns ESP_OK on success; ESP_ERR_INVALID_SIZE on premature EOF; ESP_FAIL on read error.
esp_err_t FactoryOTAUpdate::http_read_chunk(esp_http_client_handle_t &client, uint8_t *dst)
{
    return http_read_exact(client, dst, CHUNK_SIZE);
}

// Reads multiple chunks
// Returns ESP_OK on success; ESP_ERR_INVALID_SIZE on premature EOF; ESP_FAIL on read error.
esp_err_t FactoryOTAUpdate::http_read_chunks(esp_http_client_handle_t &client, uint8_t *dst, int chunks)
{
    for (int i = 0; i < chunks; i++) {
        esp_err_t err = http_read_chunk(client, &dst[i * CHUNK_SIZE]);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "reading chunks failed");
            return err;
        }
    }
    return ESP_OK;
}

// skips multiple chunks
esp_err_t FactoryOTAUpdate::http_skip_chunks(esp_http_client_handle_t &client, int chunks)
{
    uint8_t *dummy = (uint8_t *) MALLOC(CHUNK_SIZE);
    CHECK_ALLOC(dummy);
    for (int i = 0; i < chunks; i++) {
        esp_err_t err = http_read_chunk(client, dummy);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "skipping chunks failed");
            free(dummy);
            return err;
        }
    }
    free(dummy);
    return ESP_OK;
}

esp_err_t FactoryOTAUpdate::follow_redirect(esp_http_client_handle_t client, char *url, int url_len, redirect_ctx_t *ctx)
{
    const int max_hops = 8;
    for (int hop = 0; hop < max_hops; ++hop) {
        if (!is_safe_redirect_url(url)) {
            ESP_LOGE(TAG, "unsafe OTA redirect target rejected");
            return ESP_ERR_INVALID_ARG;
        }
        esp_err_t set_url_err = esp_http_client_set_url(client, url);
        if (set_url_err != ESP_OK) {
            ESP_LOGE(TAG, "set URL failed: %s", esp_err_to_name(set_url_err));
            return set_url_err;
        }

        ctx->loc[0] = '\0';
        ctx->capture = true;

        esp_err_t err = esp_http_client_open(client, 0);
        if (err != ESP_OK) {
            ctx->capture = false;
            ESP_LOGE(TAG, "open failed: %s", esp_err_to_name(err));
            return err;
        }

        (void) esp_http_client_fetch_headers(client);
        int status = esp_http_client_get_status_code(client);
        ctx->capture = false;

        if (status == 301 || status == 302 || status == 303 || status == 307 || status == 308) {
            if (!ctx->loc[0]) {
                ESP_LOGE(TAG, "Location header missing");
                esp_http_client_close(client);
                return ESP_ERR_INVALID_RESPONSE;
            }
            if (strlen(ctx->loc) >= (size_t) url_len || !is_safe_redirect_url(ctx->loc)) {
                ESP_LOGE(TAG, "unsafe or overlong redirect rejected");
                esp_http_client_close(client);
                return ESP_ERR_INVALID_RESPONSE;
            }
            // GitHub release assets use absolute HTTPS redirect URLs.
            strlcpy(url, ctx->loc, (size_t) url_len);

            esp_http_client_close(client);
            continue; // next hop
        }

        if (status != 200 && status != 206) {
            ESP_LOGE(TAG, "unexpected status: %d", status);
            esp_http_client_close(client);
            return ESP_FAIL;
        }

        // OK: keep connection open for body streaming
        return ESP_OK;
    }

    ESP_LOGE(TAG, "too many redirects");
    return ESP_ERR_INVALID_RESPONSE;
}

esp_err_t FactoryOTAUpdate::do_www_update(uint8_t *data)
{
    // use internal RAM for the temp buffer (OTA writes must not come from PSRAM)
    uint8_t *buf = (uint8_t *) malloc(CHUNK_SIZE);
    CHECK_ALLOC(buf);

    // frees memory when out of scope
    MemoryGuard gc(buf);

    const esp_partition_t *www_partition =
        esp_partition_find_first(ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_DATA_SPIFFS, "www");
    if (www_partition == NULL) {
        return ESP_ERR_NOT_FOUND;
    }

    // The factory layout and updater slice must stay exactly aligned.
    if (WWW_LEN_BYTES != www_partition->size) {
        ESP_LOGE(TAG, "WWW image/partition size mismatch (%u != %u)", (unsigned) WWW_LEN_BYTES, (unsigned) www_partition->size);
        return ESP_ERR_INVALID_SIZE;
    }
    const uint32_t to_write = WWW_LEN_BYTES;

    if (!enter_recovery) {
        esp_err_t unmount_err = esp_vfs_spiffs_unregister(NULL);
        if (unmount_err != ESP_OK) {
            ESP_LOGE(TAG, "WWW filesystem unmount failed: %s", esp_err_to_name(unmount_err));
            return unmount_err;
        }
    }

    // Erase the entire WWW partition only after its replacement is buffered.
    ESP_LOGI(TAG, "erasing www partition ...");
    esp_err_t err = esp_partition_erase_range(www_partition, 0, www_partition->size);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "www partition erase failed: %s", esp_err_to_name(err));
        return err;
    }
    ESP_LOGI(TAG, "erasing done");

    for (uint32_t offset = 0; offset < to_write; offset += CHUNK_SIZE) {
        // copy from PSRAM to internal RAM
        memcpy(buf, &data[offset], CHUNK_SIZE);

        // print each 64kb
        if (!(offset & 0xffff)) {
            ESP_LOGI(TAG, "flashing to %08x", (unsigned) offset);
        }
        if (esp_partition_write(www_partition, offset, (const void *) buf, CHUNK_SIZE) != ESP_OK) {
            return ESP_FAIL;
        }
        addWwwBytes(CHUNK_SIZE);
    }

    if (init_fs() != ESP_OK) {
        enter_recovery = true;
        ESP_LOGE(TAG, "updated WWW image failed to mount");
        return ESP_FAIL;
    }
    enter_recovery = false;
    return ESP_OK;
}

/*
 * Handle OTA file upload
 */
esp_err_t FactoryOTAUpdate::do_firmware_update(esp_http_client_handle_t client, const uint8_t *prefix, size_t prefix_len)
{
    if (!prefix || prefix_len != ota_firmware_prefix_size() || prefix_len >= FW_LEN_BYTES) {
        return ESP_ERR_INVALID_ARG;
    }

    // use internal RAM for temp buffer
    uint8_t *buf = (uint8_t *) malloc(CHUNK_SIZE);
    CHECK_ALLOC(buf);

    // frees memory when out of scope
    MemoryGuard gc(buf);

    esp_ota_handle_t ota_handle;
    const esp_partition_t *ota_partition = esp_ota_get_next_update_partition(NULL);
    if (ota_partition == NULL) {
        ESP_LOGE(TAG, "OTA partition not found");
        return ESP_ERR_NOT_FOUND;
    }

    esp_err_t err = esp_ota_begin(ota_partition, FW_LEN_BYTES, &ota_handle);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "esp_ota_begin failed: %s", esp_err_to_name(err));
        return err;
    }

    err = esp_ota_write(ota_handle, prefix, prefix_len);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "initial esp_ota_write failed: %s", esp_err_to_name(err));
        esp_ota_abort(ota_handle);
        return err;
    }
    addFwBytes(static_cast<uint32_t>(prefix_len));

    for (size_t offset = prefix_len; offset < FW_LEN_BYTES;) {
        const size_t remaining = FW_LEN_BYTES - offset;
        const size_t to_read = remaining < CHUNK_SIZE ? remaining : CHUNK_SIZE;
        err = http_read_exact(client, buf, static_cast<int>(to_read));
        if (err != ESP_OK) {
            // ensure abort on any read error
            esp_ota_abort(ota_handle);
            return err;
        }

        // print each 64kb
        if (!(offset & 0xffff)) {
            ESP_LOGI(TAG, "flashing to %08x", (unsigned) offset);
        }

        err = esp_ota_write(ota_handle, (const void *) buf, to_read);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "esp_ota_write failed: %s", esp_err_to_name(err));
            esp_ota_abort(ota_handle);
            return err;
        }
        addFwBytes(static_cast<uint32_t>(to_read));
        offset += to_read;
    }

    // Validate the inactive image. Activation is intentionally deferred until
    // the matching WWW image has also been written and mounted successfully.
    err = esp_ota_end(ota_handle);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "esp_ota_end failed: %s", esp_err_to_name(err));
        return err;
    }

    return ESP_OK;
}

esp_err_t FactoryOTAUpdate::erase_nvs_partition()
{
    const esp_partition_t *nvs_partition =
        esp_partition_find_first(ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_DATA_NVS, "nvs");
    if (!nvs_partition) {
        ESP_LOGE(TAG, "NVS partition not found!");
        return ESP_ERR_NOT_FOUND;
    }
    ESP_LOGI(TAG, "erasing NVS partition (reset to factory defaults) ...");
    esp_err_t err = esp_partition_erase_range(nvs_partition, 0, nvs_partition->size);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "NVS erase failed: %s", esp_err_to_name(err));
    } else {
        ESP_LOGI(TAG, "NVS partition erased successfully");
    }
    return err;
}

esp_err_t FactoryOTAUpdate::ota_update_from_factory(const char *start_url, bool keep_config)
{
    // use PSRAM for the long URL and WWW buffer
    char *url = (char *) MALLOC(URL_SIZE);
    CHECK_ALLOC(url);
    MemoryGuard gcUrl(url);

    // PSRAM
    uint8_t *wwwData = (uint8_t *) MALLOC(WWW_LEN_BYTES);
    CHECK_ALLOC(wwwData);
    MemoryGuard gcwwwData(wwwData);

    // calloc initializes with 0
    // PSRAM
    redirect_ctx_t *redir_ctx = (redirect_ctx_t *) CALLOC(1, sizeof(redirect_ctx_t));
    CHECK_ALLOC(redir_ctx);
    MemoryGuard gcCtx(redir_ctx);

    strlcpy(url, start_url, URL_SIZE);

    esp_http_client_config_t cfg = {};
    cfg.url = url;
    cfg.timeout_ms = 60000;
    cfg.buffer_size = 8192;
    cfg.buffer_size_tx = 8192;
    cfg.keep_alive_enable = false;    // avoid keep-alive edge-cases
    cfg.disable_auto_redirect = true; // manual redirect loop
    cfg.user_agent = "ESP32-DemoDownloader/1.0";
    cfg.crt_bundle_attach = esp_crt_bundle_attach;
    cfg.event_handler = http_evt_capture_location;
    cfg.user_data = redir_ctx; // pass per-client redirect state

    esp_http_client_handle_t client = esp_http_client_init(&cfg);
    if (!client) {
        setStep(OtaStep::ERROR, "OTA: error");
        return ESP_FAIL;
    }

    // cleans up the client when out of scope
    ClientGuard gc(&client);

    esp_http_client_set_method(client, HTTP_METHOD_GET);
    // Avoid compression so Content-Length matches bytes we read (server may still use chunked)
    esp_http_client_set_header(client, "Accept-Encoding", "identity");
    // Also make the server close after response
    esp_http_client_set_header(client, "Connection", "close");

    resetProgress();
    setStep(OtaStep::RESOLVING_URL, "OTA: resolving/following redirects");

    esp_err_t err = follow_redirect(client, url, URL_SIZE, redir_ctx);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "following redirects failed");
        setStep(OtaStep::ERROR, "OTA: error");
        return err;
    }

    setStep(OtaStep::RESOLVING_URL, "OTA: final URL OK");

    // ---- Final response: stream body
    int64_t clen = esp_http_client_get_content_length(client);
    ESP_LOGI(TAG, "Final URL OK. Content-Length=%lld", (long long) clen);

    // GitHub release assets provide Content-Length. Require the exact merge_bin
    // output so a different layout cannot be sliced into the live partitions.
    if (clen != FACTORY_IMAGE_SIZE) {
        ESP_LOGE(TAG, "unexpected factory image size: %lld != %u", (long long) clen, (unsigned) FACTORY_IMAGE_SIZE);
        setStep(OtaStep::ERROR, "OTA: error");
        return ESP_ERR_INVALID_SIZE;
    }

    // skip first bytes to seek to the fw
    err = http_skip_chunks(client, FW_START / CHUNK_SIZE);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "error reading stream");
        setStep(OtaStep::ERROR, "OTA: error");
        return err;
    }

    // Authenticate the image identity while the current firmware, filesystem,
    // and power-management loop are still fully operational. The bytes are
    // passed into do_firmware_update(), so the streamed image remains exact.
    const size_t firmware_prefix_len = ota_firmware_prefix_size();
    uint8_t *firmware_prefix = static_cast<uint8_t *>(malloc(firmware_prefix_len));
    if (!firmware_prefix) {
        setStep(OtaStep::ERROR, "OTA: error");
        return ESP_ERR_NO_MEM;
    }
    MemoryGuard gcFirmwarePrefix(firmware_prefix);
    err = http_read_exact(client, firmware_prefix, static_cast<int>(firmware_prefix_len));
    if (err != ESP_OK || !validate_nerdqaxeplus_firmware_prefix(firmware_prefix, firmware_prefix_len)) {
        ESP_LOGE(TAG, "factory firmware identity validation failed");
        setStep(OtaStep::ERROR, "OTA: incompatible firmware");
        return err == ESP_OK ? ESP_ERR_INVALID_VERSION : err;
    }

    {
        // lock the power management module
        LockGuard g(POWER_MANAGEMENT_MODULE);

        // Shut down buck converter before starting OTA.
        // During OTA, I2C conflicts prevent the PID from working correctly.
        // Disabling hardware avoids any risk of overheating.
        // The device will reboot after the update anyway.
        POWER_MANAGEMENT_MODULE.shutdown();

        ESP_LOGI(TAG, "performing firmware update ...");

        setStep(OtaStep::UPDATING_FW, "OTA: updating firmware");

        err = do_firmware_update(client, firmware_prefix, firmware_prefix_len);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "firmware update failed");
            setStep(OtaStep::ERROR, "OTA: error");
            vTaskDelay(pdMS_TO_TICKS(2000));
            POWER_MANAGEMENT_MODULE.restart();
            return err; // unreachable; old app remains selected
        }
        ESP_LOGI(TAG, "firmware update successful!");

        // Buffer the entire WWW into PSRAM (safer single-partition strategy)
        ESP_LOGI(TAG, "download www binary ...");

        setStep(OtaStep::DOWNLOADING_WWW, "OTA: downloading www");

        int chunks = WWW_LEN_BYTES / CHUNK_SIZE;
        for (int i = 0; i < chunks; ++i) {
            esp_err_t e = http_read_chunk(client, &wwwData[i * CHUNK_SIZE]);
            if (e != ESP_OK) {
                ESP_LOGE(TAG, "error reading www binary");
                setStep(OtaStep::ERROR, "OTA: error");
                vTaskDelay(pdMS_TO_TICKS(2000));
                POWER_MANAGEMENT_MODULE.restart();
                return e; // unreachable; old app remains selected
            }
            addWwwRecvBytes(CHUNK_SIZE);
        }

        ESP_LOGI(TAG, "performing www update ...");

        setStep(OtaStep::UPDATING_WWW, "OTA: updating www");

        err = do_www_update(wwwData);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "www update failed");
            setStep(OtaStep::ERROR, "OTA: error");
            vTaskDelay(pdMS_TO_TICKS(2000));
            POWER_MANAGEMENT_MODULE.restart();
            return err; // unreachable; old app remains selected
        }
        ESP_LOGI(TAG, "www update successful!");

        // Activate only after both firmware and WWW have been validated. Since
        // the running partition has not changed, round-robin resolves the same
        // inactive slot that do_firmware_update() just wrote.
        const esp_partition_t *ota_partition = esp_ota_get_next_update_partition(NULL);
        if (!ota_partition) {
            ESP_LOGE(TAG, "OTA activation partition not found");
            setStep(OtaStep::ERROR, "OTA: error");
            vTaskDelay(pdMS_TO_TICKS(2000));
            POWER_MANAGEMENT_MODULE.restart();
            return ESP_ERR_NOT_FOUND;
        }
        err = esp_ota_set_boot_partition(ota_partition);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "esp_ota_set_boot_partition failed: %s", esp_err_to_name(err));
            setStep(OtaStep::ERROR, "OTA: error");
            vTaskDelay(pdMS_TO_TICKS(2000));
            POWER_MANAGEMENT_MODULE.restart();
            return err;
        }

        if (!keep_config) {
            setStep(OtaStep::ERASING_NVS, "OTA: erasing NVS (reset to factory defaults)");
            if (erase_nvs_partition() != ESP_OK) {
                // Non-fatal: log and continue — device will reboot anyway,
                // config reset is best-effort at this point.
                ESP_LOGW(TAG, "NVS erase failed, continuing with reboot");
            }
        }
    }

    ESP_LOGI(TAG, "Restarting System because of Firmware update complete");

    // we set the status to rebooting
    setStep(OtaStep::REBOOTING, "OTA: done, rebooting");

    // wait for 3 seconds, this makes sure the web UI saw this state
    vTaskDelay(pdMS_TO_TICKS(3000));


    POWER_MANAGEMENT_MODULE.restart();

    return ESP_OK;
}

FactoryOTAUpdate::FactoryOTAUpdate()
{
    pthread_mutex_init(&m_mutex, nullptr);
    pthread_cond_init(&m_cond, nullptr);

    m_running = false;
    m_pending = false;
    m_update_url = nullptr;
    m_fw_written = 0;
    m_www_written = 0;
    m_www_recv = 0;
    m_step = OtaStep::IDLE;
}

void FactoryOTAUpdate::taskWrapper(void *pvParameters)
{
    FactoryOTAUpdate *ota = static_cast<FactoryOTAUpdate *>(pvParameters);
    if (!ota) {
        return;
    }
    ota->task();
}

// FreeRTOS task function
void FactoryOTAUpdate::task()
{
    ESP_LOGI(TAG, "OTA task started...");

    for (;;) {
        // Wait for a pending request (use while for spurious wakeups)
        pthread_mutex_lock(&m_mutex);
        while (!m_pending) {
            pthread_cond_wait(&m_cond, &m_mutex);
        }

        // Consume the request and mark running
        char *url = m_update_url; // take ownership
        bool keep_config = m_keep_config;
        m_update_url = NULL;
        m_pending = false;
        m_running = true;
        pthread_mutex_unlock(&m_mutex);

        ESP_LOGI(TAG, "OTA update triggered.");

        if (!url) {
            ESP_LOGE(TAG, "update url is null!");
            // Clear running state and continue
            pthread_mutex_lock(&m_mutex);
            m_running = false;
            pthread_mutex_unlock(&m_mutex);
            continue;
        }

        esp_err_t err = ota_update_from_factory(url, keep_config);
        free(url);

        if (err != ESP_OK) { // NOTE: fix typo: ESP_OK (not EPS_OK)
            ESP_LOGE(TAG, "OTA update failed! Please check the log");
        }

        // Mark not running so a new trigger can be accepted
        pthread_mutex_lock(&m_mutex);
        m_running = false;
        pthread_mutex_unlock(&m_mutex);
    }
}

// --- Helper: validate URL is a safe GitHub release asset link for this fork
static bool is_safe_github_url(const char *url)
{
    if (!url)
        return false;

    // Only allow factory update assets from this fork's GitHub releases.
    if (strncasecmp(url, GITHUB_RELEASE_DOWNLOAD_PREFIX, strlen(GITHUB_RELEASE_DOWNLOAD_PREFIX)) != 0)
        return false;

    // Bind the one-click updater to the release artifact for this exact board
    // and LTS channel. Query strings/fragments could obscure the final basename.
    const char *release_path = url + strlen(GITHUB_RELEASE_DOWNLOAD_PREFIX);
    if (strchr(release_path, '?') || strchr(release_path, '#') || strchr(release_path, '%'))
        return false;
    const char *asset_name = strchr(release_path, '/');
    if (!asset_name || asset_name == release_path || asset_name[1] == '\0')
        return false;
    ++asset_name;
    if (strchr(asset_name, '/'))
        return false;
    if (strncmp(asset_name, FACTORY_ASSET_NAME_PREFIX, strlen(FACTORY_ASSET_NAME_PREFIX)) != 0)
        return false;
    const size_t asset_len = strlen(asset_name);
    const size_t name_prefix_len = strlen(FACTORY_ASSET_NAME_PREFIX);
    static const char bin_suffix[] = ".bin";
    if (asset_len <= name_prefix_len + sizeof(bin_suffix) - 1 ||
        strcmp(asset_name + asset_len - (sizeof(bin_suffix) - 1), bin_suffix) != 0) {
        return false;
    }

    // Reject traversal and encoded traversal
    if (strstr(url, "../") || strstr(url, "/..") || strstr(url, "%2e%2e") || strstr(url, "%2E%2E"))
        return false;

    // Reject backslashes (avoid weird normalization)
    if (strchr(url, '\\'))
        return false;

    // Reject control chars
    for (const char *q = url; *q; ++q) {
        unsigned char c = (unsigned char) *q;
        if (c < 0x20 || c == 0x7F)
            return false;
    }

    return true;
}

// Map step to readable string (for logging or JSON)
static const char *stepToStr(FactoryOTAUpdate::OtaStep s)
{
    switch (s) {
    case FactoryOTAUpdate::OtaStep::IDLE:
        return "idle";
    case FactoryOTAUpdate::OtaStep::RESOLVING_URL:
        return "resolving_url";
    case FactoryOTAUpdate::OtaStep::UPDATING_FW:
        return "updating_fw";
    case FactoryOTAUpdate::OtaStep::DOWNLOADING_WWW:
        return "downloading_www";
    case FactoryOTAUpdate::OtaStep::UPDATING_WWW:
        return "updating_www";
    case FactoryOTAUpdate::OtaStep::ERASING_NVS:
        return "erasing_nvs";
    case FactoryOTAUpdate::OtaStep::REBOOTING:
        return "rebooting";
    case FactoryOTAUpdate::OtaStep::ERROR:
        return "error";
    default:
        return "unknown";
    }
}

void FactoryOTAUpdate::setStep(OtaStep s, const char *logmsg)
{
    pthread_mutex_lock(&m_mutex);
    m_step = s;
    pthread_mutex_unlock(&m_mutex);
    if (logmsg)
        ESP_LOGI(TAG, "%s", logmsg); // optional
}

void FactoryOTAUpdate::resetProgress()
{
    pthread_mutex_lock(&m_mutex);
    m_fw_written = 0;
    m_www_written = 0;
    m_www_recv = 0;
    m_step = OtaStep::IDLE;
    pthread_mutex_unlock(&m_mutex);
}

void FactoryOTAUpdate::addFwBytes(uint32_t n)
{
    pthread_mutex_lock(&m_mutex);
    m_fw_written += n;
    pthread_mutex_unlock(&m_mutex);
}

void FactoryOTAUpdate::addWwwBytes(uint32_t n)
{
    pthread_mutex_lock(&m_mutex);
    m_www_written += n;
    pthread_mutex_unlock(&m_mutex);
}

void FactoryOTAUpdate::addWwwRecvBytes(uint32_t n)
{
    pthread_mutex_lock(&m_mutex);
    m_www_recv += n;
    pthread_mutex_unlock(&m_mutex);
}

int FactoryOTAUpdate::calcProgress() const
{
    const uint64_t total = (uint64_t) FW_LEN_BYTES + (uint64_t) (2 * WWW_LEN_BYTES);
    uint64_t done = (uint64_t) m_fw_written + m_www_written + m_www_recv;
    if (done > total)
        done = total;
    int p = (total == 0) ? 0 : (int) ((done * 100ull) / total);
    if (p < 0)
        p = 0;
    if (p > 100)
        p = 100;
    return p;
}

void FactoryOTAUpdate::getStatus(OtaStatus *out)
{
    if (!out)
        return;
    pthread_mutex_lock(&m_mutex);
    out->pending = m_pending;
    out->running = m_running;
    out->step = m_step;
    out->progress = calcProgress();
    pthread_mutex_unlock(&m_mutex);
}

bool FactoryOTAUpdate::trigger(const char *url, bool keep_config)
{
    if (!url || !*url)
        return false;
    pthread_mutex_lock(&m_mutex);
    if (m_running || m_pending) {
        pthread_mutex_unlock(&m_mutex);
        return false;
    }

    char *copy = strdup(url);
    if (!copy) {
        pthread_mutex_unlock(&m_mutex);
        return false;
    }

    free(m_update_url);
    m_update_url = copy;
    m_keep_config = keep_config;
    m_pending = true;

    pthread_cond_signal(&m_cond);
    pthread_mutex_unlock(&m_mutex);
    return true;
}

/*
 * Handle OTA update from GitHub URL
 * Expects JSON body: {"url": "https://github.com/mane/ESP-Miner-NerdQAxePlus/releases/download/..."}
 */
esp_err_t POST_OTA_update_from_url(httpd_req_t *req)
{
    // close connection when out of scope
    ConGuard g(http_server, req);

    ESP_LOGI(TAG, "=== POST_OTA_update_from_url called ===");

    if (is_network_allowed(req) != ESP_OK) {
        ESP_LOGE(TAG, "Network not allowed");
        return httpd_resp_send_err(req, HTTPD_401_UNAUTHORIZED, "Unauthorized");
    }

    ESP_LOGI(TAG, "Network allowed, content_len=%d", req->content_len);

    if (validateOTP(req) != ESP_OK) {
        return ESP_FAIL;
    }

    // Read body into PSRAM or heap buffer
    if (req->content_len <= 0 || req->content_len > 4096) {
        return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "Body too large or empty");
    }

    size_t need = (size_t) req->content_len;
    char *buf = (char *) MALLOC(need + 1);
    if (!buf)
        return httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "OOM");

    size_t off = 0;
    while (off < need) {
        int n = httpd_req_recv(req, buf + off, (int) (need - off));
        if (n <= 0) {
            free(buf);
            return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "recv failed");
        }
        off += (size_t) n;
    }
    buf[off] = '\0';

    PSRAMAllocator allocator;
    JsonDocument doc(&allocator);

    // Parse the JSON payload
    DeserializationError error = deserializeJson(doc, buf);
    free(buf); // buffer no longer needed
    if (error) {
        ESP_LOGE(TAG, "JSON parsing failed: %s", error.c_str());
        return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "Invalid JSON");
    }

    const char *url = doc["url"] | (const char *) nullptr;
    if (!url) {
        return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "`url` missing");
    }

    // keep_config defaults to true (safe default: don't wipe config unless explicitly requested)
    bool keep_config = doc["keep_config"] | true;

    // Validate URL
    if (!is_safe_github_url(url)) {
        ESP_LOGE(TAG, "Rejected URL: %s", url);
        return httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "Invalid or unsafe URL");
    }

    // Start OTA (non-blocking)
    ESP_LOGI(TAG, "starting OTA update for URL: %s (keep_config=%d)", url, (int) keep_config);
    bool ok = FACTORY_OTA_UPDATER.trigger(url, keep_config);
    if (!ok) {
        httpd_resp_set_status(req, "409 Conflict");
        return httpd_resp_send(req, "{\"status\":\"busy\"}", HTTPD_RESP_USE_STRLEN);
    }

    httpd_resp_set_type(req, "application/json");
    httpd_resp_set_status(req, "202 Accepted");
    const char *resp = "{\"status\":\"started\"}";
    return httpd_resp_send(req, resp, HTTPD_RESP_USE_STRLEN);
}

esp_err_t GET_OTA_status(httpd_req_t *req)
{
    // close connection when out of scope
    ConGuard g(http_server, req);

    if (is_network_allowed(req) != ESP_OK) {
        return httpd_resp_send_err(req, HTTPD_401_UNAUTHORIZED, "Unauthorized");
    }

    if (set_cors_headers(req) != ESP_OK) {
        httpd_resp_send_500(req);
        return ESP_FAIL;
    }

    httpd_resp_set_hdr(req, "Cache-Control", "no-store, no-cache, must-revalidate");

    FactoryOTAUpdate::OtaStatus s = {};
    FACTORY_OTA_UPDATER.getStatus(&s);

    // step as string
    const char *step = stepToStr(s.step);

    char json[160];
    int n = snprintf(json, sizeof(json), "{\"pending\":%s,\"running\":%s,\"step\":\"%s\",\"progress\":%d}",
                     s.pending ? "true" : "false", s.running ? "true" : "false", step, s.progress);

    httpd_resp_set_type(req, "application/json");
    return httpd_resp_send(req, json, n);
}
