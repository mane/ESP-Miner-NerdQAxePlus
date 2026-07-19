#include "http_cors.h"

#include <stdio.h>
#include <string.h>
#include <strings.h>

#include "esp_http_server.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "global_state.h"
#include "lwip/inet.h"
#include "lwip/sockets.h"
#include "network/connect.h"

static const char *CORS_TAG = "http_cors";

static constexpr size_t ORIGIN_BUFFER_SIZE = 160;
static constexpr size_t HOST_BUFFER_SIZE = 128;

struct ParsedAuthority {
    char host[HOST_BUFFER_SIZE];
    uint16_t port;
};

// Returns true if the IPv4 address (in network byte order) is in RFC1918 ranges.
static bool ip_in_private_range(uint32_t address_be)
{
    const uint32_t ip = ntohl(address_be);
    return (ip >= 0x0A000000 && ip <= 0x0AFFFFFF) ||
           (ip >= 0xAC100000 && ip <= 0xAC1FFFFF) ||
           (ip >= 0xC0A80000 && ip <= 0xC0A8FFFF);
}

static bool parse_ipv4(const char *host, uint32_t *out)
{
    return host && out && inet_pton(AF_INET, host, out) == 1;
}

static bool get_peer_ipv4(httpd_req_t *req, uint32_t *out)
{
    if (!req || !out)
        return false;

    const int sockfd = httpd_req_to_sockfd(req);
    if (sockfd < 0)
        return false;

    struct sockaddr_storage peer = {};
    socklen_t peer_size = sizeof(peer);
    if (getpeername(sockfd, reinterpret_cast<struct sockaddr *>(&peer), &peer_size) < 0) {
        ESP_LOGE(CORS_TAG, "Error getting client IP");
        return false;
    }

    if (peer.ss_family == AF_INET && peer_size >= sizeof(struct sockaddr_in)) {
        const auto *peer4 = reinterpret_cast<const struct sockaddr_in *>(&peer);
        *out = peer4->sin_addr.s_addr;
        return true;
    }

    if (peer.ss_family == AF_INET6 && peer_size >= sizeof(struct sockaddr_in6)) {
        const auto *peer6 = reinterpret_cast<const struct sockaddr_in6 *>(&peer);
        const uint8_t *bytes = reinterpret_cast<const uint8_t *>(&peer6->sin6_addr);
        static const uint8_t mapped_prefix[12] = {0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0xff, 0xff};
        if (memcmp(bytes, mapped_prefix, sizeof(mapped_prefix)) == 0) {
            memcpy(out, bytes + sizeof(mapped_prefix), sizeof(*out));
            return true;
        }
    }

    ESP_LOGW(CORS_TAG, "Client does not have a supported IPv4 address");
    return false;
}

static bool copy_request_header(httpd_req_t *req, const char *name, char *out, size_t out_size, bool *present)
{
    if (!req || !name || !out || out_size == 0 || !present)
        return false;

    const size_t len = httpd_req_get_hdr_value_len(req, name);
    *present = len > 0;
    if (!*present) {
        out[0] = '\0';
        return true;
    }
    if (len >= out_size || httpd_req_get_hdr_value_str(req, name, out, out_size) != ESP_OK) {
        ESP_LOGW(CORS_TAG, "%s header is invalid or too long", name);
        return false;
    }
    return true;
}

static bool parse_authority(const char *authority, ParsedAuthority *parsed)
{
    if (!authority || !parsed || authority[0] == '\0')
        return false;
    if (strchr(authority, '/') || strchr(authority, '\\') || strchr(authority, '@') ||
        strchr(authority, '?') || strchr(authority, '#')) {
        return false;
    }

    const char *colon = strchr(authority, ':');
    const size_t host_len = colon ? static_cast<size_t>(colon - authority) : strlen(authority);
    if (host_len == 0 || host_len >= sizeof(parsed->host))
        return false;

    uint32_t port = 80;
    if (colon) {
        const char *port_text = colon + 1;
        if (*port_text == '\0')
            return false;
        port = 0;
        for (; *port_text; ++port_text) {
            if (*port_text < '0' || *port_text > '9')
                return false;
            port = (port * 10U) + static_cast<uint32_t>(*port_text - '0');
            if (port > 65535U)
                return false;
        }
        if (port == 0)
            return false;
    }

    for (size_t i = 0; i < host_len; ++i) {
        const unsigned char c = static_cast<unsigned char>(authority[i]);
        if (c < 0x21 || c == 0x7f)
            return false;
    }
    memcpy(parsed->host, authority, host_len);
    parsed->host[host_len] = '\0';
    parsed->port = static_cast<uint16_t>(port);
    return true;
}

static bool parse_origin_authority(const char *origin, ParsedAuthority *parsed)
{
    static const char http_prefix[] = "http://";
    if (!origin || strncmp(origin, http_prefix, sizeof(http_prefix) - 1) != 0)
        return false;
    return parse_authority(origin + sizeof(http_prefix) - 1, parsed);
}

static bool authorities_match(const ParsedAuthority &first, const ParsedAuthority &second)
{
    return first.port == second.port && strcasecmp(first.host, second.host) == 0;
}

static bool hostname_matches_device(const char *host)
{
    const char *hostname = SYSTEM_MODULE.getHostname();
    if (!host || !hostname || hostname[0] == '\0')
        return false;
    if (strcasecmp(host, hostname) == 0)
        return true;

    char mdns_name[HOST_BUFFER_SIZE];
    const int written = snprintf(mdns_name, sizeof(mdns_name), "%s.local", hostname);
    return written > 0 && static_cast<size_t>(written) < sizeof(mdns_name) && strcasecmp(host, mdns_name) == 0;
}

static bool get_netif_ip_info(esp_netif_t *netif, esp_netif_ip_info_t *info)
{
    return netif && info && esp_netif_get_ip_info(netif, info) == ESP_OK && info->ip.addr != 0;
}

static bool ip_matches_netif(uint32_t origin_ip, esp_netif_t *netif)
{
    esp_netif_ip_info_t info = {};
    return get_netif_ip_info(netif, &info) && origin_ip == info.ip.addr;
}

static bool peer_is_on_netif(uint32_t peer_ip, esp_netif_t *netif)
{
    esp_netif_ip_info_t info = {};
    return get_netif_ip_info(netif, &info) && info.netmask.addr != 0 &&
           (peer_ip & info.netmask.addr) == (info.ip.addr & info.netmask.addr);
}

static bool origin_ip_matches_device(uint32_t origin_ip)
{
    return ip_matches_netif(origin_ip, NETWORK.getWifiStaNetif()) ||
           ip_matches_netif(origin_ip, NETWORK.getEthNetif()) ||
           ip_matches_netif(origin_ip, wifi_get_ap_netif());
}

static bool captive_portal_same_origin(uint32_t peer_ip)
{
    // Captive-portal probes may use their probe hostname. The caller has
    // already required Origin and Host to be the exact same HTTP authority.
    return SYSTEM_MODULE.getAPState() && peer_is_on_netif(peer_ip, wifi_get_ap_netif());
}

static bool request_origin_is_allowed(httpd_req_t *req, uint32_t peer_ip, char *origin_value, size_t origin_size,
                                      bool *origin_present)
{
    if (!copy_request_header(req, "Origin", origin_value, origin_size, origin_present))
        return false;
    if (!*origin_present)
        return true; // private, direct CLI/app client; peer validation is separate

    ParsedAuthority origin = {};
    if (!parse_origin_authority(origin_value, &origin)) {
        ESP_LOGW(CORS_TAG, "Invalid Origin header");
        return false;
    }

    char host_value[HOST_BUFFER_SIZE];
    bool host_present = false;
    ParsedAuthority request_host = {};
    if (!copy_request_header(req, "Host", host_value, sizeof(host_value), &host_present) || !host_present ||
        !parse_authority(host_value, &request_host)) {
        ESP_LOGW(CORS_TAG, "Missing or invalid Host header for Origin request");
        return false;
    }
    if (!authorities_match(origin, request_host)) {
        ESP_LOGW(CORS_TAG, "Denied request whose Origin authority differs from Host");
        return false;
    }

    if (hostname_matches_device(origin.host))
        return true;

    uint32_t origin_ip = 0;
    if (parse_ipv4(origin.host, &origin_ip) && origin_ip_matches_device(origin_ip))
        return true;

    if (captive_portal_same_origin(peer_ip))
        return true;

    ESP_LOGW(CORS_TAG, "Denied Origin for non-device host: %s", origin.host);
    return false;
}

static esp_err_t network_allowed(httpd_req_t *req, bool log_allowed)
{
    uint32_t peer_ip = 0;
    if (!get_peer_ipv4(req, &peer_ip))
        return ESP_FAIL;

    char peer_text[INET_ADDRSTRLEN];
    if (!inet_ntop(AF_INET, &peer_ip, peer_text, sizeof(peer_text)))
        return ESP_FAIL;
    if (!ip_in_private_range(peer_ip)) {
        ESP_LOGW(CORS_TAG, "Denied non-private client: %s", peer_text);
        return ESP_FAIL;
    }

    char origin[ORIGIN_BUFFER_SIZE];
    bool origin_present = false;
    if (!request_origin_is_allowed(req, peer_ip, origin, sizeof(origin), &origin_present))
        return ESP_FAIL;

    if (log_allowed) {
        ESP_LOGI(CORS_TAG, "Allowed private client %s%s", peer_text,
                 origin_present ? " with same-device Origin" : " without Origin");
    }
    return ESP_OK;
}

esp_err_t is_network_allowed(httpd_req_t *req)
{
    return network_allowed(req, true);
}

esp_err_t set_cors_headers(httpd_req_t *req)
{
    char origin[ORIGIN_BUFFER_SIZE];
    bool origin_present = false;
    if (!copy_request_header(req, "Origin", origin, sizeof(origin), &origin_present))
        return ESP_FAIL;

    // Never emit a wildcard for control endpoints. Echo only an Origin that was
    // validated against this miner by the same access-control boundary.
    if (origin_present) {
        if (network_allowed(req, false) != ESP_OK ||
            httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", origin) != ESP_OK ||
            httpd_resp_set_hdr(req, "Vary", "Origin") != ESP_OK) {
            return ESP_FAIL;
        }
    }

    return (httpd_resp_set_hdr(req, "Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS") == ESP_OK &&
            httpd_resp_set_hdr(req, "Access-Control-Allow-Headers",
                               "Content-Type, Authorization, X-TOTP, X-OTP-Session, X-OTP-Session-TTL") == ESP_OK)
               ? ESP_OK
               : ESP_FAIL;
}
