#pragma once

#include <stddef.h>
#include <stdint.h>

#include "esp_http_server.h"

size_t ota_firmware_prefix_size();
bool validate_nerdqaxeplus_firmware_prefix(const uint8_t *prefix, size_t prefix_len);

esp_err_t POST_WWW_update(httpd_req_t *req);
esp_err_t POST_OTA_update(httpd_req_t *req);
