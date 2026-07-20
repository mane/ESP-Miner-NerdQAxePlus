#pragma once

#include <stddef.h>
#include <stdint.h>
#include <sys/types.h>
#include <sys/socket.h>

#include "esp_transport.h"


class StratumTransport {
public:
    explicit StratumTransport(bool use_tls);
    virtual ~StratumTransport();

    virtual bool connect(const char* host, const char* ip, uint16_t port);
    virtual int send(const void* data, size_t len);
    virtual int recv(void* buf, size_t len);
    virtual bool isConnected();
    virtual void close();

private:
    bool m_use_tls;

    // esp_transport_*_set_keep_alive() retains this pointer and reads it
    // during connect, so the configuration must live as long as the
    // transport object rather than on applyKeepAlive_()'s stack.
    esp_transport_keep_alive_t m_keepAlive = {};

    void applyKeepAlive_();
    void setNoDelay_();

protected:
    esp_transport_handle_t m_t;
};

class TcpStratumTransport : public StratumTransport {
public:
    TcpStratumTransport() : StratumTransport(false) {}
};

class TlsStratumTransport : public StratumTransport {
public:
    TlsStratumTransport() : StratumTransport(true) {}
};
