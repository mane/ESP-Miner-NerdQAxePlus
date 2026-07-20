#pragma once

#include <stddef.h>
#include <stdint.h>

#include "esp_http_server.h"

size_t ota_firmware_prefix_size();
bool validate_nerdqaxeplus_firmware_prefix(const uint8_t *prefix, size_t prefix_len);
// Copy esp_app_desc_t::version from an already buffered firmware prefix.
// The output is cleared on failure and is always NUL-terminated on success.
bool copy_nerdqaxeplus_firmware_version(const uint8_t *prefix, size_t prefix_len, char *version,
                                        size_t version_capacity);

esp_err_t POST_WWW_update(httpd_req_t *req);
esp_err_t POST_OTA_update(httpd_req_t *req);
