#include <stdio.h>

#include "ping_task.h"
#include "esp_log.h"
#include "global_state.h"
#include "lwip/inet.h"
#include "lwip/ip_addr.h"
#include "lwip/sockets.h"
#include "macros.h"
#include "nvs_config.h"
#include "ping/ping_sock.h"
#include "stratum/stratum_manager.h"

// Configuration for ping intervals and history
#define PING_COUNT 7                                   // number of pings per round
#define PING_INTERVAL_MS 1000                          // delay between individual pings in ms
#define PING_TIMEOUT_MS 1000                           // timeout per ping request in ms
#define PING_DELAY 60                                  // delay between ping rounds in seconds
#define HISTORY_WINDOW_SEC 900                         // total window to calculate recent packet loss (900s = 15min)
#define HISTORY_SIZE (HISTORY_WINDOW_SEC / PING_DELAY) // number of historical entries based on ping interval

void PingTask::record_ping_result(uint16_t sent, uint16_t received)
{
    m_ping_history[m_history_index].sent = sent;
    m_ping_history[m_history_index].received = received;

    m_history_index = (m_history_index + 1) % HISTORY_SIZE;
    if (m_history_count < HISTORY_SIZE) {
        m_history_count++;
    }
}

int PingTask::init_ping_history()
{
    m_ping_history = (PingHistory*)MALLOC(HISTORY_SIZE * sizeof(PingHistory));
    if (!m_ping_history) {
        ESP_LOGE(m_tag, "Failed to allocate m_ping_history in PSRAM");
        return -1; // allocation failed
    }
    m_history_index = 0;
    m_history_count = 0;
    return 0;
}

double PingTask::get_recent_packet_loss()
{
    uint32_t total_sent = 0;
    uint32_t total_recv = 0;

    for (int i = 0; i < m_history_count; i++) {
        total_sent += m_ping_history[i].sent;
        total_recv += m_ping_history[i].received;
    }

    if (total_sent == 0)
        return 0.0;
    return (total_sent - total_recv) / (double) total_sent;
}

static void on_ping_task_success(esp_ping_handle_t hdl, void *args)
{
    PingStats *s = static_cast<PingStats*>(args);

    uint32_t time_ms = 0, seq = 0, ttl = 0, size = 0;
    ip_addr_t addr;

    bool ok_time = esp_ping_get_profile(hdl, ESP_PING_PROF_TIMEGAP, &time_ms, sizeof(time_ms)) == ESP_OK;
    bool ok_seq = esp_ping_get_profile(hdl, ESP_PING_PROF_SEQNO, &seq, sizeof(seq)) == ESP_OK;
    bool ok_ttl = esp_ping_get_profile(hdl, ESP_PING_PROF_TTL, &ttl, sizeof(ttl)) == ESP_OK;
    bool ok_size = esp_ping_get_profile(hdl, ESP_PING_PROF_SIZE, &size, sizeof(size)) == ESP_OK;
    bool ok_ip = esp_ping_get_profile(hdl, ESP_PING_PROF_IPADDR, &addr, sizeof(addr)) == ESP_OK;

    if (ok_time) {
        const char *ip_str = ok_ip ? ipaddr_ntoa(&addr) : "?.?.?.?";
        if (!s->header_shown && ok_ip && ok_size) {
            ESP_LOGI(s->tag, "PING %s (%s): %lu data bytes", s->hostname, ip_str, (unsigned long) size);
            s->header_shown = true;
        }

        s->total_time_ms += time_ms;
        s->replies++;
        if (time_ms < s->min_rtt)
            s->min_rtt = time_ms;
        if (time_ms > s->max_rtt)
            s->max_rtt = time_ms;

        uint32_t icmp_seq = ok_seq ? seq : s->replies;
        uint32_t pkt_size = ok_size ? size : 64;

        ESP_LOGI(s->tag, "%lu bytes from %s: icmp_seq=%lu ttl=%lu time=%lu ms", (unsigned long) pkt_size, ip_str,
                 (unsigned long) icmp_seq, (unsigned long) (ok_ttl ? ttl : 64), (unsigned long) time_ms);
    }
}

static void on_ping_task_end(esp_ping_handle_t, void *args)
{
    PingStats *s = static_cast<PingStats*>(args);
    if (s && s->done) {
        xSemaphoreGive(s->done);
    }
}

// Run a ping session using resolved IP from STRATUM_MANAGER
PingResult PingTask::perform_ping(const char *ip_str, const char *hostname_str)
{
    PingResult result{};
    result.min_rtt_ms = 1e6;

    ip_addr_t target_addr;
    if (!ip_str || !ipaddr_aton(ip_str, &target_addr)) {
        ESP_LOGE(m_tag, "Invalid IP string passed to perform_ping (%s)", ip_str ? ip_str : "null");
        return result;
    }

    // The ping worker can still finish an in-flight callback after stop is
    // requested. Use object-owned callback state, including a hostname copy,
    // so the callback never observes stack storage that has gone out of scope.
    // A completed previous session may have left the binary semaphore set.
    // Drain it before associating it with this session.
    while (xSemaphoreTake(m_ping_done, 0) == pdTRUE) {
    }

    m_stats = {};
    snprintf(m_stats.hostname, sizeof(m_stats.hostname), "%s", hostname_str ? hostname_str : "?");
    m_stats.min_rtt = 1e6;
    m_stats.tag = m_tag;
    m_stats.done = m_ping_done;

    // Configure ping session
    esp_ping_config_t config = ESP_PING_DEFAULT_CONFIG();
    config.count = PING_COUNT;
    config.interval_ms = PING_INTERVAL_MS;
    config.timeout_ms = PING_TIMEOUT_MS;
    config.target_addr = target_addr;

    // Set callback to accumulate successful replies
    esp_ping_callbacks_t cbs = {};
    cbs.cb_args = &m_stats;
    cbs.on_ping_success = on_ping_task_success;
    cbs.on_ping_end = on_ping_task_end;

    // Configure and start ping session
    esp_ping_handle_t ping = NULL;
    if (esp_ping_new_session(&config, &cbs, &ping) != ESP_OK) {
        ESP_LOGE(m_tag, "Error creating ping session");
        if (ping)
            esp_ping_delete_session(ping); // Ensure memory is freed in case of prior allocation
        return result;
    }

    if (esp_ping_start(ping) != ESP_OK) {
        ESP_LOGE(m_tag, "Error starting ping session");
        esp_ping_delete_session(ping); // Free memory if start fails
        return result;
    }

    // Wait until all replies are received or timeout expires
    const int max_wait_ms = PING_COUNT * (PING_TIMEOUT_MS + 100);
    int wait = 0;
    uint32_t replies = 0, sent = 0;

    while (wait < max_wait_ms) {
        esp_ping_get_profile(ping, ESP_PING_PROF_REPLY, &replies, sizeof(replies));
        esp_ping_get_profile(ping, ESP_PING_PROF_REQUEST, &sent, sizeof(sent));

        if (replies >= config.count) {
            vTaskDelay(pdMS_TO_TICKS(50)); // allow final callback to settle
            break;
        }

        vTaskDelay(pdMS_TO_TICKS(100));
        wait += 100;
    }

    // Stop session
    esp_ping_stop(ping);

    // on_ping_end runs after every success/timeout callback. Waiting for it is
    // the completion barrier that makes the callback-owned statistics stable
    // before they are read and the session is deleted.
    xSemaphoreTake(m_ping_done, portMAX_DELAY);

    // Final verification: check if any replies were missing
    esp_ping_get_profile(ping, ESP_PING_PROF_REPLY, &replies, sizeof(replies));
    esp_ping_get_profile(ping, ESP_PING_PROF_REQUEST, &sent, sizeof(sent));

    // Clean up session
    esp_ping_delete_session(ping);

    // Store results if valid replies exist
    if (m_stats.replies > 0) {
        double round_avg = (double) m_stats.total_time_ms / m_stats.replies;
        result.success = true;
        result.replies = m_stats.replies;
        result.avg_rtt_ms = round_avg;
        result.min_rtt_ms = m_stats.min_rtt;
        result.max_rtt_ms = m_stats.max_rtt;
    }

    return result;
}

void PingTask::ping_task_wrapper(void *pvParameters)
{
    PingTask *task = static_cast<PingTask *>(pvParameters);
    task->ping_task();
}

// Periodic task that pings stratum target every PING_DELAY using resolved IP
void PingTask::ping_task()
{
    if (!m_ping_history && init_ping_history() < 0) {
        ESP_LOGE(m_tag, "Failed to init ping history. Task exiting.");
        vTaskDelete(NULL);
        return;
    }

    StratumConfig* cfg = new StratumConfig(m_pool);
    while (true) {
        if (POWER_MANAGEMENT_MODULE.isShutdown()) {
            ESP_LOGW(m_tag, "suspended");
            // reset metrics in shutdown
            reset();
            vTaskSuspend(NULL);
        }

        // Never hold the ping-history mutex while calling StratumManager. The
        // settings path owns the manager mutex before resetting ping history,
        // so nesting in the opposite order here would create an ABBA deadlock.
        if (!m_manager || !m_manager->isConnected(m_pool)) {
            vTaskDelay(pdMS_TO_TICKS(10000));
            continue;
        }

        // get a guaranteed consistent copy of the config
        m_manager->copyConfigInto(m_pool, cfg);
        const char *hostname = cfg->getHost();
        const char *resolved_ip = m_manager->getResolvedIpForPool(m_pool);

        if (!hostname || !resolved_ip) {
            ESP_LOGE(m_tag, "No resolved IP for current hostname");
            vTaskDelay(pdMS_TO_TICKS(PING_DELAY * 1000));
            continue;
        }

        // The task owns cfg, but the resolved-IP buffer belongs to the stratum
        // task. Snapshot both before the blocking ping session.
        char hostname_snapshot[256]{};
        char ip_snapshot[INET_ADDRSTRLEN]{};
        strncpy(hostname_snapshot, hostname, sizeof(hostname_snapshot) - 1);
        strncpy(ip_snapshot, resolved_ip, sizeof(ip_snapshot) - 1);

        PingResult result = perform_ping(ip_snapshot, hostname_snapshot);

        ESP_LOGI(m_tag, "--- %s ping statistics ---", hostname_snapshot);
        double loss_current = 100.0 * (PING_COUNT - result.replies) / (double) PING_COUNT;
        ESP_LOGI(m_tag, "%u packets transmitted, %u packets received, %.1f%% packet loss", PING_COUNT, result.replies,
                loss_current);
        if (result.success) {
            ESP_LOGI(m_tag, "round-trip min/avg/max = %.2f/%.2f/%.2f ms", result.min_rtt_ms, result.avg_rtt_ms, result.max_rtt_ms);
        }

        double recent_loss = 0.0;
        {
            PThreadGuard history_lock(m_mutex);
            if (result.success) {
                m_last_ping_rtt_ms = result.avg_rtt_ms;
            }
            record_ping_result(PING_COUNT, result.replies);
            recent_loss = get_recent_packet_loss();
        }
        ESP_LOGI(m_tag, "Recent %d-min packet loss: %.1f%%", HISTORY_WINDOW_SEC / 60, 100.0 * recent_loss);
        vTaskDelay(pdMS_TO_TICKS(PING_DELAY * 1000));
    }
}

void PingTask::reset() {
    PThreadGuard g(m_mutex);

    if (m_ping_history) {
        memset(m_ping_history, 0, HISTORY_SIZE * sizeof(PingHistory));
    }
    m_history_index = 0;
    m_history_count = 0;
    m_last_ping_rtt_ms = 0;
}

// Provide latest average RTT to other modules
double PingTask::get_last_ping_rtt()
{
    PThreadGuard g(m_mutex);
    return m_last_ping_rtt_ms;
}

// Provide recent few minutes packet loss to other modules
double PingTask::get_recent_ping_loss()
{
    PThreadGuard g(m_mutex);
    return get_recent_packet_loss();
}
