/******************************************************************************
 *  *
 * References:
 *  1. Stratum Protocol - https://reference.cash/mining/stratum-protocol
 *****************************************************************************/

#include <cstddef>

#include "stratum_api.h" // Assumes that types like StratumApiV1Message,
                         // mining_notify, STRATUM_ID_SUBSCRIBE, etc., are defined here.
#include "ArduinoJson.h"
#include "psram_allocator.h"

#include "esp_log.h"
#include "esp_ota_ops.h"
#include "esp_timer.h"
#include "lwip/sockets.h"
#include <ctype.h>
#include <errno.h>
#include <limits.h>
#include <math.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>


#include "macros.h"

// The logging tag for ESP logging.
static const char *TAG = "stratum_api";

static bool isHexString(const char *value, size_t exact_length = 0, size_t max_length = SIZE_MAX)
{
    if (!value) {
        return false;
    }

    size_t len = strlen(value);
    if ((exact_length && len != exact_length) || len > max_length || (len & 1U) != 0) {
        return false;
    }

    for (size_t i = 0; i < len; i++) {
        if (!isxdigit(static_cast<unsigned char>(value[i]))) {
            return false;
        }
    }
    return true;
}

static bool parseHexUint32(const char *value, uint32_t *out)
{
    if (!out || !value) {
        return false;
    }

    size_t len = strlen(value);
    if (len == 0 || len > 8) {
        return false;
    }
    for (size_t i = 0; i < len; i++) {
        if (!isxdigit(static_cast<unsigned char>(value[i]))) {
            return false;
        }
    }

    char *end = nullptr;
    unsigned long parsed = strtoul(value, &end, 16);
    if (!end || *end != '\0' || parsed > UINT32_MAX) {
        return false;
    }
    *out = static_cast<uint32_t>(parsed);
    return true;
}

static bool appendBytes(char *buffer, size_t capacity, size_t *length,
                        const char *data, size_t data_length)
{
    if (!buffer || !length || !data || *length >= capacity ||
        data_length >= capacity - *length) {
        return false;
    }
    memcpy(buffer + *length, data, data_length);
    *length += data_length;
    buffer[*length] = '\0';
    return true;
}

static bool appendLiteral(char *buffer, size_t capacity, size_t *length,
                          const char *literal)
{
    return literal && appendBytes(buffer, capacity, length, literal, strlen(literal));
}

static bool appendFormatted(char *buffer, size_t capacity, size_t *length,
                            const char *format, ...)
{
    if (!buffer || !length || !format || *length >= capacity) {
        return false;
    }

    va_list args;
    va_start(args, format);
    int written = vsnprintf(buffer + *length, capacity - *length, format, args);
    va_end(args);
    if (written < 0 || static_cast<size_t>(written) >= capacity - *length) {
        buffer[capacity - 1] = '\0';
        return false;
    }
    *length += static_cast<size_t>(written);
    return true;
}

static bool appendJsonEscapedContent(char *buffer, size_t capacity, size_t *length,
                                     const char *value)
{
    if (!value) {
        return false;
    }

    static const char hex[] = "0123456789abcdef";
    for (const unsigned char *p = reinterpret_cast<const unsigned char *>(value); *p; ++p) {
        const char *escape = nullptr;
        switch (*p) {
        case '"': escape = "\\\""; break;
        case '\\': escape = "\\\\"; break;
        case '\b': escape = "\\b"; break;
        case '\f': escape = "\\f"; break;
        case '\n': escape = "\\n"; break;
        case '\r': escape = "\\r"; break;
        case '\t': escape = "\\t"; break;
        default: break;
        }

        if (escape) {
            if (!appendLiteral(buffer, capacity, length, escape)) {
                return false;
            }
        } else if (*p < 0x20) {
            char unicode_escape[7] = {'\\', 'u', '0', '0', hex[*p >> 4], hex[*p & 0x0f], '\0'};
            if (!appendLiteral(buffer, capacity, length, unicode_escape)) {
                return false;
            }
        } else {
            char c = static_cast<char>(*p);
            if (!appendBytes(buffer, capacity, length, &c, 1)) {
                return false;
            }
        }
    }
    return true;
}

static bool appendJsonString(char *buffer, size_t capacity, size_t *length,
                             const char *value)
{
    return appendLiteral(buffer, capacity, length, "\"") &&
           appendJsonEscapedContent(buffer, capacity, length, value) &&
           appendLiteral(buffer, capacity, length, "\"");
}

StratumApi::StratumApi() : m_len(0), m_send_uid(1)
{
    m_buffer = (char *) MALLOC(BIG_BUFFER_SIZE);
    m_requestBuffer = (char *) MALLOC(BUFFER_SIZE);
    if (!m_buffer || !m_requestBuffer) {
        ESP_LOGE(TAG, "Failed to allocate Stratum buffers");
        safe_free(m_buffer);
        safe_free(m_requestBuffer);
        return;
    }
    clearBuffer();
}

StratumApi::~StratumApi()
{
    safe_free(m_buffer);
    safe_free(m_requestBuffer);
}

uint8_t StratumApi::hex2val(char c)
{
    if (c >= '0' && c <= '9') {
        return c - '0';
    } else if (c >= 'a' && c <= 'f') {
        return c - 'a' + 10;
    } else if (c >= 'A' && c <= 'F') {
        return c - 'A' + 10;
    } else {
        return 0;
    }
}

size_t StratumApi::hex2bin(const char *hex, uint8_t *bin, size_t bin_len)
{
    size_t len = 0;
    while (*hex && len < bin_len) {
        bin[len] = hex2val(*hex++) << 4;
        if (!*hex) {
            len++;
            break;
        }
        bin[len++] |= hex2val(*hex++);
    }
    return len;
}

void StratumApi::debugTx(const char *msg)
{
    if (strstr(msg, "\"method\": \"mining.authorize\"") != nullptr) {
        ESP_LOGI(TAG, "tx: mining.authorize (credentials redacted)");
        return;
    }
    const char *newline = strchr(msg, '\n');
    if (newline != NULL) {
        ESP_LOGI(TAG, "tx: %.*s", (int) (newline - msg), msg);
    } else {
        ESP_LOGI(TAG, "tx: %s", msg);
    }
}

//--------------------------------------------------------------------
// receiveJsonRpcLine()
//--------------------------------------------------------------------
// Accumulates data from the given socket until a newline is found.
// Returns a dynamically allocated line (caller must free the returned memory).
//--------------------------------------------------------------------
void StratumApi::resetBuffer()
{
    m_len = 0;
    if (m_buffer) {
        m_buffer[0] = '\0';
    }
}

char *StratumApi::receiveJsonRpcLine(StratumTransport *transport)
{
    // This function blocks until either:
    // - a full line (terminated by '\n') is available and returned, or
    // - an error/EOF occurs and NULL is returned.

    if (!transport || !m_buffer) {
        ESP_LOGE(TAG, "Stratum receive buffer is unavailable");
        return nullptr;
    }

    for (;;) {
        // Check if we already have a complete line in the buffer.
        char *newline_ptr = strchr(m_buffer, '\n');
        if (newline_ptr != NULL) {
            // Compute line length up to '\n'.
            size_t line_length = static_cast<size_t>(newline_ptr - m_buffer);

            // Handle optional '\r' before '\n' (CRLF).
            if (line_length > 0 && m_buffer[line_length - 1] == '\r') {
                line_length--;
            }

            // Allocate memory for the line (without newline / CR).
            char *line = (char *)MALLOC(line_length + 1);
            if (line == NULL) {
                ESP_LOGE(TAG, "Failed to allocate memory for line. Flushing buffer.");
                resetBuffer();
                return NULL;
            }

            // Copy the line and null-terminate it.
            if (line_length > 0) {
                memcpy(line, m_buffer, line_length);
            }
            line[line_length] = '\0';

            // Remove the consumed line (including the '\n') from the buffer.
            size_t consumed = static_cast<size_t>(newline_ptr - m_buffer) + 1; // include '\n'
            size_t remaining = m_len - consumed;
            if (remaining > 0) {
                memmove(m_buffer, m_buffer + consumed, remaining);
            }

            m_len = remaining;
            m_buffer[m_len] = '\0';

            return line;
        }

        // No newline in buffer yet → need to read more data.
        if (m_len >= BIG_BUFFER_SIZE - 1) {
            ESP_LOGE(TAG, "Buffer full without newline. Flushing buffer.");
            resetBuffer();
            return NULL;
        }

        int available = BIG_BUFFER_SIZE - static_cast<int>(m_len) - 1; // reserve space for '\0'
        int nbytes = transport->recv(m_buffer + m_len, available);

        if (nbytes < 0) {
            // Error on recv
            if (errno == EWOULDBLOCK || errno == EAGAIN) {
                // Timeout / no data right now.
                if (!transport->isConnected()) {
                    ESP_LOGE(TAG, "Socket is not connected anymore.");
                    resetBuffer();
                    return NULL;
                }

                ESP_LOGD(TAG, "No data available yet, socket still connected.");
                // Avoid busy-looping and burning CPU.
                vTaskDelay(pdMS_TO_TICKS(10));
                continue;
            } else {
                ESP_LOGE(TAG, "Error in recv: %s", strerror(errno));
                resetBuffer();
                return NULL;
            }
        } else if (nbytes == 0) {
            // Remote side closed the connection.
            ESP_LOGI(TAG, "Remote closed the connection.");
            resetBuffer();
            return NULL;
        }

        // We received some data; append it to the buffer.
        m_len += static_cast<size_t>(nbytes);
        m_buffer[m_len] = '\0';
    }
}


bool StratumApi::parseMethods(JsonDocument &doc, const char *method_str, StratumApiV1Message *message)
{
    message->method = STRATUM_UNKNOWN;

    if (strcmp(method_str, "mining.notify") == 0) {
        message->method = MINING_NOTIFY;
    } else if (strcmp(method_str, "mining.set_difficulty") == 0) {
        message->method = MINING_SET_DIFFICULTY;
    } else if (strcmp(method_str, "mining.set_version_mask") == 0) {
        message->method = MINING_SET_VERSION_MASK;
    } else if (strcmp(method_str, "client.reconnect") == 0) {
        message->method = CLIENT_RECONNECT;
    } else if (strcmp(method_str, "mining.set_extranonce") == 0) {
        message->method = MINING_SET_EXTRANONCE;
    } else {
        ESP_LOGI(TAG, "Unhandled method in stratum message: %s", method_str);
        return false;
    }

    switch (message->method) {
    case MINING_NOTIFY: {
        ESP_LOGI(TAG, "mining notify");
        JsonArray params = doc["params"].as<JsonArray>();
        if (params.isNull() || params.size() < 9) {
            ESP_LOGE(TAG, "Invalid mining.notify parameter count");
            return false;
        }

        const char *job_id = params[0].as<const char *>();
        const char *prev_block_hash = params[1].as<const char *>();
        const char *coinbase_1 = params[2].as<const char *>();
        const char *coinbase_2 = params[3].as<const char *>();
        const char *version = params[5].as<const char *>();
        const char *target = params[6].as<const char *>();
        const char *ntime = params[7].as<const char *>();

        // Empty coinbase fragments are valid Stratum components. Their final
        // concatenation is checked before hashing.
        if (!job_id || !isHexString(prev_block_hash, HASH_SIZE * 2) ||
            !isHexString(coinbase_1) || !isHexString(coinbase_2)) {
            ESP_LOGE(TAG, "Invalid mining.notify string or hash field");
            return false;
        }

        uint32_t parsed_version = 0;
        uint32_t parsed_target = 0;
        uint32_t parsed_ntime = 0;
        if (!parseHexUint32(version, &parsed_version) ||
            !parseHexUint32(target, &parsed_target) ||
            !parseHexUint32(ntime, &parsed_ntime)) {
            ESP_LOGE(TAG, "Invalid mining.notify numeric field");
            return false;
        }

        JsonArray merkle_branch = params[4].as<JsonArray>();
        if (merkle_branch.isNull() || merkle_branch.size() > MAX_MERKLE_BRANCHES) {
            ESP_LOGE(TAG, "Invalid number of Merkle branches");
            return false;
        }

        mining_notify *new_work = (mining_notify *) CALLOC(1, sizeof(mining_notify));
        if (!new_work) {
            ESP_LOGE(TAG, "Failed to allocate mining.notify");
            return false;
        }

        new_work->job_id = strdup(job_id);
        new_work->coinbase_1 = strdup(coinbase_1);
        new_work->coinbase_2 = strdup(coinbase_2);
        if (!new_work->job_id || !new_work->coinbase_1 || !new_work->coinbase_2) {
            ESP_LOGE(TAG, "Failed to copy mining.notify strings");
            freeMiningNotify(new_work);
            safe_free(new_work);
            return false;
        }

        hex2bin(prev_block_hash, new_work->_prev_block_hash, HASH_SIZE);
        new_work->n_merkle_branches = merkle_branch.size();

        for (size_t i = 0; i < new_work->n_merkle_branches; i++) {
            const char *branch = merkle_branch[i].as<const char *>();
            if (!isHexString(branch, HASH_SIZE * 2)) {
                ESP_LOGE(TAG, "Invalid Merkle branch at index %zu", i);
                freeMiningNotify(new_work);
                safe_free(new_work);
                return false;
            }
            hex2bin(branch, new_work->_merkle_branches[i], HASH_SIZE);
        }

        new_work->version = parsed_version;
        new_work->target = parsed_target;
        new_work->ntime = parsed_ntime;

        message->mining_notification = new_work;
        message->should_abandon_work = params[8].as<bool>();
        break;
    }
    case MINING_SET_DIFFICULTY: {
        JsonVariant difficulty_value = doc["params"][0];
        if (!difficulty_value.is<float>() && !difficulty_value.is<double>() &&
            !difficulty_value.is<uint32_t>()) {
            ESP_LOGE(TAG, "Invalid mining difficulty");
            return false;
        }
        double difficulty = difficulty_value.as<double>();
        if (!isfinite(difficulty) || difficulty < 1.0 || difficulty > UINT32_MAX) {
            ESP_LOGE(TAG, "Mining difficulty out of range");
            return false;
        }
        message->new_difficulty = static_cast<uint32_t>(difficulty);
        break;
    }
    case MINING_SET_VERSION_MASK: {
        if (!parseHexUint32(doc["params"][0].as<const char *>(), &message->version_mask)) {
            ESP_LOGE(TAG, "Invalid version rolling mask");
            return false;
        }
        break;
    }
    case MINING_SET_EXTRANONCE: {
        ESP_LOGI(TAG, "mining.set_extranonce");

        // format:
        // {"id": null, "method": "mining.set_extranonce", "params": ["<new_extranonce1>", <extranonce2_size>]}
        JsonArray params = doc["params"].as<JsonArray>();

        if (params.size() < 2) {
            ESP_LOGE(TAG, "Invalid result array for subscribe.");
            return false;
        }
        int extranonce_2_len = params[1].as<int>();

        const char *extranonce_str = params[0].as<const char *>();
        // An empty extranonce1 prefix is valid; extranonce2 still has a
        // mandatory positive size.
        if (!isHexString(extranonce_str, 0, MAX_EXTRANONCE_SIZE * 2) ||
            extranonce_2_len <= 0 || extranonce_2_len > MAX_EXTRANONCE_SIZE) {
            ESP_LOGE(TAG, "Invalid extranonce parameters");
            return false;
        }
        message->extranonce_2_len = extranonce_2_len;
        message->extranonce_str = strdup(extranonce_str);
        if (!message->extranonce_str) {
            ESP_LOGE(TAG, "Failed to copy extranonce");
            return false;
        }

        ESP_LOGI(TAG, "extranonce_str: %s", message->extranonce_str);
        ESP_LOGI(TAG, "extranonce_2_len: %d", message->extranonce_2_len);
        break;
    }
    default:
        break;
    }

    // ESP_LOGI(TAG, "allocs: %d, deallocs: %d, reallocs: %d", allocs, deallocs, reallocs);
    return true;
}

bool StratumApi::parseResult(JsonDocument &doc) {
    JsonVariant result_json = doc["result"];
    JsonVariant error_json = doc["error"];

    if (!error_json.isNull()) {
        return false;
    }

    if (!result_json.isNull()) {
        return result_json.is<bool>() ? result_json.as<bool>() : false;
    }

    return false;
}

bool StratumApi::parseResponses(JsonDocument &doc, StratumApiV1Message *message)
{
    message->method = STRATUM_RESULT;
    message->response_success = parseResult(doc);
    return true;
}

bool StratumApi::parseSetupResponses(JsonDocument &doc, StratumApiV1Message *message)
{
    // first messages are responses to our mining setup requests
    message->method = STRATUM_UNKNOWN;

    JsonVariant result_json = doc["result"];

    switch (message->message_id) {
    case STRATUM_ID_SUBSCRIBE: {
        message->method = STRATUM_RESULT_SUBSCRIBE;

        JsonArray result_arr = result_json.as<JsonArray>();
        if (result_arr.size() < 3) {
            ESP_LOGE(TAG, "Invalid result array for subscribe.");
            return false;
        }
        int extranonce_2_len = result_arr[2].as<int>();

        const char *extranonce_str = result_arr[1].as<const char *>();
        if (!isHexString(extranonce_str, 0, MAX_EXTRANONCE_SIZE * 2) ||
            extranonce_2_len <= 0 || extranonce_2_len > MAX_EXTRANONCE_SIZE) {
            ESP_LOGE(TAG, "Invalid subscribe extranonce parameters");
            return false;
        }
        message->extranonce_2_len = extranonce_2_len;
        message->extranonce_str = strdup(extranonce_str);
        if (!message->extranonce_str) {
            ESP_LOGE(TAG, "Failed to copy subscribe extranonce");
            return false;
        }

        ESP_LOGI(TAG, "extranonce_str: %s", message->extranonce_str);
        ESP_LOGI(TAG, "extranonce_2_len: %d", message->extranonce_2_len);
        break;
    }
    case STRATUM_ID_CONFIGURE: {
        message->method = STRATUM_RESULT_VERSION_MASK;

        const char *mask = result_json["version-rolling.mask"].as<const char *>();
        if (!parseHexUint32(mask, &message->version_mask)) {
            ESP_LOGE(TAG, "Invalid configure version mask");
            return false;
        }
        ESP_LOGI(TAG, "Set version mask: %08lx", message->version_mask);
        break;
    }
    case STRATUM_ID_AUTHORIZE: {
        message->method = STRATUM_RESULT_SETUP;
        message->response_success = parseResult(doc);
        break;
    }
    case STRATUM_ID_SUGGEST_DIFFICULTY: {
        message->method = STRATUM_RESULT_SETUP;
        message->response_success = parseResult(doc);
        break;
    }
    case STRATUM_ID_EXTRANONCE_SUBSCRIBE: {
        message->method = STRATUM_RESULT_SETUP;
        message->response_success = parseResult(doc);
        break;
    }
    default:
        ESP_LOGW(TAG, "unhandled ID");
        return false;
    }
    return true;
}

bool StratumApi::parse(StratumApiV1Message *message, const char *stratum_json,
                       int last_setup_message_id)
{
    PSRAMAllocator allocator;
    JsonDocument doc(&allocator);

    // Deserialize JSON
    DeserializationError error = deserializeJson(doc, stratum_json);
    if (error) {
        ESP_LOGE(TAG, "Unable to parse JSON: %s", error.c_str());
        return false;
    }

    return parse(message, doc, last_setup_message_id);
}

bool StratumApi::parse(StratumApiV1Message *message, JsonDocument &doc,
                       int last_setup_message_id)
{
    // Extract message ID
    message->message_id = doc["id"].is<int>() ? doc["id"].as<int>() : -1;

    // Extract method
    const char *method_str = doc["method"].as<const char *>();

    if (method_str) {
        return parseMethods(doc, method_str, message);
    } else {
        if (message->message_id > 0 && message->message_id <= last_setup_message_id) {
            return parseSetupResponses(doc, message);
        }
        return parseResponses(doc, message);
    }
}

//--------------------------------------------------------------------
// freeMiningNotify()
//--------------------------------------------------------------------
void StratumApi::freeMiningNotify(mining_notify *params)
{
    // nothing to free
    if (!params) {
        return;
    }
    safe_free(params->job_id);
    safe_free(params->coinbase_1);
    safe_free(params->coinbase_2);
}

//--------------------------------------------------------------------
// send()
//--------------------------------------------------------------------
bool StratumApi::send(StratumTransport *transport, const char *message)
{
    if (!transport || !message) {
        return false;
    }
    debugTx(message);

    if (!transport->isConnected()) {
        ESP_LOGI(TAG, "Socket not connected. Cannot send message.");
        return false;
    }

    const char *p = message;
    size_t remaining = strlen(message);
    int64_t deadline = esp_timer_get_time() + 35LL * 1000 * 1000;

    while (remaining > 0) {
        int n = transport->send(p, remaining);
        if (n > 0) {
            p += n;
            remaining -= (size_t)n;
            continue;
        }

        // n == 0 means "no progress"; treat like a retryable condition.
        if (n == 0 || (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK))) {
            if (esp_timer_get_time() >= deadline || !transport->isConnected()) {
                ESP_LOGE(TAG, "Timed out writing Stratum message");
                return false;
            }
            vTaskDelay(pdMS_TO_TICKS(10));
            continue;
        }

        ESP_LOGE(TAG, "Error writing to socket: %s", strerror(errno));
        return false;
    }
    return true;
}

//--------------------------------------------------------------------
// subscribe()
//--------------------------------------------------------------------
bool StratumApi::subscribe(StratumTransport *transport, const char *device, const char *asic)
{
    PThreadGuard lock(m_sendMutex);
    if (!m_requestBuffer || !device || !asic) {
        return false;
    }
    const esp_app_desc_t *app_desc = esp_app_get_description();
    const char *version = app_desc->version;
    size_t len = 0;
    if (!appendFormatted(m_requestBuffer, BUFFER_SIZE, &len,
                         "{\"id\": %d, \"method\": \"mining.subscribe\", \"params\": [\"", m_send_uid) ||
        !appendJsonEscapedContent(m_requestBuffer, BUFFER_SIZE, &len, device) ||
        !appendLiteral(m_requestBuffer, BUFFER_SIZE, &len, "/") ||
        !appendJsonEscapedContent(m_requestBuffer, BUFFER_SIZE, &len, asic) ||
        !appendLiteral(m_requestBuffer, BUFFER_SIZE, &len, "/") ||
        !appendJsonEscapedContent(m_requestBuffer, BUFFER_SIZE, &len, version) ||
        !appendLiteral(m_requestBuffer, BUFFER_SIZE, &len, "\"]}\n")) {
        ESP_LOGE(TAG, "Subscribe request exceeds buffer");
        return false;
    }
    m_send_uid++;

    return send(transport, m_requestBuffer);
}

//--------------------------------------------------------------------
// subscribe()
//--------------------------------------------------------------------
bool StratumApi::entranonceSubscribe(StratumTransport *transport)
{
    PThreadGuard lock(m_sendMutex);
    if (!m_requestBuffer) {
        return false;
    }
    int len = snprintf(m_requestBuffer, BUFFER_SIZE,
                       "{\"id\": %d, \"method\": \"mining.extranonce.subscribe\", \"params\": []}\n",
                       m_send_uid++);
    if (len < 0 || len >= BUFFER_SIZE) {
        ESP_LOGE(TAG, "Extranonce subscribe request exceeds buffer");
        return false;
    }

    return send(transport, m_requestBuffer);
}

//--------------------------------------------------------------------
// suggestDifficulty()
//--------------------------------------------------------------------
bool StratumApi::suggestDifficulty(StratumTransport *transport, uint32_t difficulty)
{
    PThreadGuard lock(m_sendMutex);
    if (!m_requestBuffer) {
        return false;
    }
    int len = snprintf(m_requestBuffer, BUFFER_SIZE,
                       "{\"id\": %d, \"method\": \"mining.suggest_difficulty\", \"params\": [%lu]}\n",
                       m_send_uid++, (unsigned long)difficulty);
    if (len < 0 || len >= BUFFER_SIZE) {
        ESP_LOGE(TAG, "Difficulty request exceeds buffer");
        return false;
    }

    return send(transport, m_requestBuffer);
}

//--------------------------------------------------------------------
// authenticate()
//--------------------------------------------------------------------
bool StratumApi::authenticate(StratumTransport *transport, const char *username, const char *pass)
{
    PThreadGuard lock(m_sendMutex);
    if (!m_requestBuffer || !username || !pass) {
        return false;
    }
    size_t len = 0;
    if (!appendFormatted(m_requestBuffer, BUFFER_SIZE, &len,
                         "{\"id\": %d, \"method\": \"mining.authorize\", \"params\": [", m_send_uid) ||
        !appendJsonString(m_requestBuffer, BUFFER_SIZE, &len, username) ||
        !appendLiteral(m_requestBuffer, BUFFER_SIZE, &len, ", ") ||
        !appendJsonString(m_requestBuffer, BUFFER_SIZE, &len, pass) ||
        !appendLiteral(m_requestBuffer, BUFFER_SIZE, &len, "]}\n")) {
        ESP_LOGE(TAG, "Authorize request exceeds buffer");
        return false;
    }
    m_send_uid++;

    return send(transport, m_requestBuffer);
}

//--------------------------------------------------------------------
// submitShare()
//--------------------------------------------------------------------
bool StratumApi::submitShare(StratumTransport *transport, const char *username, const char *jobid, const char *extranonce_2, uint32_t ntime,
                             uint32_t nonce, uint32_t version)
{
    PThreadGuard lock(m_sendMutex);
    if (!m_requestBuffer || !username || !jobid || !extranonce_2) {
        return false;
    }
    size_t len = 0;
    if (!appendFormatted(m_requestBuffer, BUFFER_SIZE, &len,
                         "{\"id\": %d, \"method\": \"mining.submit\", \"params\": [", m_send_uid) ||
        !appendJsonString(m_requestBuffer, BUFFER_SIZE, &len, username) ||
        !appendLiteral(m_requestBuffer, BUFFER_SIZE, &len, ", ") ||
        !appendJsonString(m_requestBuffer, BUFFER_SIZE, &len, jobid) ||
        !appendLiteral(m_requestBuffer, BUFFER_SIZE, &len, ", ") ||
        !appendJsonString(m_requestBuffer, BUFFER_SIZE, &len, extranonce_2) ||
        !appendFormatted(m_requestBuffer, BUFFER_SIZE, &len,
                         ", \"%08lx\", \"%08lx\", \"%08lx\"]}\n",
                         (unsigned long)ntime, (unsigned long)nonce, (unsigned long)version)) {
        ESP_LOGE(TAG, "Share request exceeds buffer");
        return false;
    }
    m_send_uid++;

    return send(transport, m_requestBuffer);
}

//--------------------------------------------------------------------
// configureVersionRolling()
//--------------------------------------------------------------------
bool StratumApi::configureVersionRolling(StratumTransport *transport)
{
    PThreadGuard lock(m_sendMutex);
    if (!m_requestBuffer) {
        return false;
    }
    int len = snprintf(m_requestBuffer, BUFFER_SIZE,
                       "{\"id\": %d, \"method\": \"mining.configure\", \"params\": [[\"version-rolling\"], {\"version-rolling.mask\": "
                       "\"1fffe000\"}]}\n",
                       m_send_uid++);
    if (len < 0 || len >= BUFFER_SIZE) {
        ESP_LOGE(TAG, "Configure request exceeds buffer");
        return false;
    }

    return send(transport, m_requestBuffer);
}

//--------------------------------------------------------------------
// resetUid()
//--------------------------------------------------------------------
void StratumApi::resetUid()
{
    PThreadGuard lock(m_sendMutex);
    ESP_LOGI(TAG, "Resetting stratum uid");
    m_send_uid = 1;
}

//--------------------------------------------------------------------
// clearBuffer()
//--------------------------------------------------------------------
void StratumApi::clearBuffer()
{
    if (m_buffer) {
        memset(m_buffer, 0, BIG_BUFFER_SIZE);
    }
    m_len = 0;
}
