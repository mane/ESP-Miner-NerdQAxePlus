# NerdQAxe REST API

All endpoints require access from the local network (RFC 1918 private IPs, localhost, `.local` hostnames).
Endpoints that modify state require OTP authentication via `X-TOTP` or `X-OTP-Session` header when OTP is enabled.

Responses are `application/json` unless noted otherwise.

---

## V2 Endpoints

### Identify

#### `GET /api/v2/identify`

Lightweight endpoint for device identification (no polling).

```json
{
  "deviceModel": "NerdQAxe+",
  "defaultTheme": "default",
  "otp": true,
  "can": {
    "enabled": false
  }
}
```

---

### System

#### `GET /api/v2/system`

Device info, network status and memory. Used by the System page (polled every 5s).

```json
{
  "deviceModel": "NerdQAxe+",
  "asicModel": "BM1368",
  "version": "1.2.3",
  "uptimeSeconds": 86400,
  "lastResetReason": "RESET_POWERON",
  "network": {
    "hostname": "nerdqaxe",
    "ssid": "MyWiFi",
    "macAddr": "AA:BB:CC:DD:EE:FF",
    "ipAddr": "192.168.1.42",
    "wifiStatus": "SYSTEM.WIFI_CONNECTED",
    "wifiRSSI": -55
  },
  "memory": {
    "freeHeap": 4000000,
    "freeHeapInt": 120000
  }
}
```

---

### Dashboard

#### `GET /api/v2/dashboard`

Real-time mining telemetry. Used by the Home page (polled every 2s).

**Query parameters** (all optional):

| Param | Type | Description |
|---|---|---|
| `ts` | uint64 | History start timestamp (ms since epoch). Presence enables `history` in response. |
| `cur` | uint64 | Client's current timestamp for clock-drift compensation. |
| `limit` | uint32 | Max history data points (capped at 1000). |
| `historySpan` | uint64 | History window in ms (default 1h, max 3h). |

```json
{
  "system": {
    "uptime": 86400,
    "shutdown": false,
    "boardError": 0,
    "overheatTemp": 75
  },
  "performance": {
    "hashRateTimestamp": 1716700000000,
    "hashRate": 1200.5,
    "hashRate1m": 1195.0,
    "hashRate10m": 1198.3,
    "hashRate1h": 1200.0,
    "hashRate1d": 1199.7,
    "bestDiff": 1234567.89,
    "bestSessionDiff": 234567.89,
    "sharesAccepted": 4200,
    "sharesRejected": 3,
    "duplicateHWNonces": 0,
    "shareQueueDrops": 0,
    "frequency": 525,
    "configuredFrequency": 490,
    "actualFrequency": 524.99,
    "asicCount": 4,
    "smallCoreCount": 1276,
    "hashrateGovernor": {
      "enabled": true,
      "targetFrequency": 525,
      "lastStableFrequency": 525,
      "utilization": 0.998,
      "state": "observe",
      "lastReason": "at_frequency_cap"
    }
  },
  "power": {
    "watts": 15.2,
    "min": 0,
    "max": 25,
    "voltage": 5.1,
    "voltageMin": 4.8,
    "voltageMax": 5.5,
    "currentA": 2.98,
    "currentAMin": 0,
    "currentAMax": 5.0,
    "coreVoltageActual": 1.15
  },
  "thermal": {
    "asicTemp": 58.5,
    "vrTemp": 45.2,
    "vrTempInt": 52.0,
    "asicTemps": [57.5, 58.0, 59.0, 58.5],
    "fans": [
      { "speed": 72, "rpm": 3200 },
      { "speed": 65, "rpm": 2800 }
    ]
  },
  "stratum": {
    "poolMode": 0,
    "activePoolMode": 0,
    "usingFallback": false,
    "totalBestDiff": 1234567.89,
    "poolBalance": 0,
    "pools": [
      {
        "active": true,
        "host": "solo.ckpool.org",
        "port": 3333,
        "user": "bc1q...",
        "connected": true,
        "activeProtocol": 0,
        "encrypted": false,
        "accepted": 4200,
        "rejected": 3,
        "bestDiff": 1234567.89,
        "pingRtt": 42,
        "pingLoss": 0,
        "poolDifficulty": 10000,
        "networkDifficulty": 88000000000000
      },
      {
        "active": false,
        "host": "fallback.pool.org",
        "port": 3333,
        "user": "bc1q...",
        "connected": false,
        "activeProtocol": 0,
        "encrypted": false,
        "pingRtt": 0,
        "pingLoss": 0
      }
    ]
  },
  "can": {
    "hasExtension": false,
    "enabled": false
  },
  "coinbase": {
    "blockHeaders": [
      {
        "pool": 0,
        "blockHeight": 850000,
        "networkDifficulty": 88000000000000,
        "scriptSig": "...",
        "coinbaseValueTotalSatoshis": 312500000,
        "coinbaseValueUserSatoshis": 303125000,
        "verificationOk": true,
        "verificationFailCount": 0,
        "verificationCheckCount": 42
      }
    ],
    "pools": [
      { "mode": 1, "maxFee": 3.0, "force": false },
      { "mode": 0, "maxFee": 5.0, "force": false }
    ]
  },
  "history": { }
}
```

The `history` object is only present when `ts` query parameter is provided. It contains arrays of hashrate and temperature samples for charting.

`stratum.pools[]` always contains both configured pools in configuration order: index 0 is primary and index 1 is fallback in failover mode or secondary in dual-pool mode. `active` identifies the pool currently mining; failover has exactly one active entry, while both entries are active in dual-pool mode. Because failover session counters are shared, `accepted`, `rejected`, `bestDiff`, `poolDifficulty`, and `networkDifficulty` are reported only on its active entry.

---

### Settings

#### `GET /api/v2/settings`

Full device configuration. Used by the Settings page.

```json
{
  "asicModel": "BM1368",
  "deviceModel": "NerdQAxe+",
  "version": "1.2.3",
  "otp": true,
  "can": { "hasExtension": false, "enabled": false },

  "frequency": 490,
  "effectiveFrequency": 525,
  "coreVoltage": 1300,
  "vrFrequency": 500,
  "defaultFrequency": 490,
  "defaultCoreVoltage": 1150,
  "defaultVrFrequency": 500,
  "ecoFrequency": 400,
  "ecoCoreVoltage": 1100,
  "frequencyOptions": [400, 425, 450, 475, 490, 500, 525, 528, 531, 534, 537, 540, 550],
  "voltageOptions": [1100, 1150, 1200, 1250, 1300, 1350],
  "absMinCoreVoltage": 1050,
  "absMaxCoreVoltage": 1400,
  "hashrateGovernor": {
    "enabled": true,
    "maxFrequency": 525,
    "powerLimitW": 69.0,
    "effectiveFrequency": 525,
    "targetFrequency": 525,
    "lastStableFrequency": 525,
    "utilization": 0.998,
    "state": "observe",
    "lastReason": "at_frequency_cap"
  },

  "poolMode": 0,
  "poolBalance": 0,
  "stratumKeep": 1,
  "jobInterval": 3000,
  "stratumDifficulty": 1000,
  "pools": [
    {
      "url": "solo.ckpool.org",
      "port": 3333,
      "user": "bc1q...",
      "enonceSubscribe": false,
      "tls": false,
      "protocol": 0,
      "sv2AuthorityPubkey": "",
      "sv2ChannelType": 0,
      "coinbaseVerifyMode": 1,
      "coinbaseMaxFee": 3.0,
      "coinbaseVerifyForce": false
    },
    { }
  ],

  "fans": [
    {
      "label": "ASIC",
      "mode": 1,
      "manualSpeed": 75,
      "overheatTemp": 75,
      "pid": { "targetTemp": 55, "p": 5.0, "i": 0.1, "d": 1.0 }
    }
  ],
  "invertFanPolarity": 0,

  "hostname": "nerdqaxe",
  "ssid": "MyWiFi",

  "flipScreen": 0,
  "invertScreen": 0,
  "autoScreenOff": 0
}
```

#### `PATCH /api/v2/settings`

Update device configuration. Requires OTP. All fields are optional — only provided fields are updated.

Accepts the same structure as `GET /api/v2/settings`. Pool settings use the `pools[]` array format.

**Request body** (example with subset of fields):

```json
{
  "hostname": "nerdqaxe",
  "ssid": "MyWiFi",
  "wifiPass": "secret",

  "frequency": 525,
  "coreVoltage": 1300,
  "hashrateGovernor": {
    "enabled": true,
    "maxFrequency": 550,
    "powerLimitW": 69.0
  },
  "jobInterval": 1200,
  "stratumDifficulty": 1000,

  "poolMode": 0,
  "stratumKeep": 1,
  "pools": [
    {
      "url": "solo.ckpool.org",
      "port": 3333,
      "user": "bc1q...",
      "password": "x",
      "tls": false,
      "protocol": 0,
      "coinbaseVerifyMode": 1,
      "coinbaseMaxFee": 3.0,
      "coinbaseVerifyForce": false
    },
    {
      "url": "backup.pool.org",
      "port": 3333,
      "user": "bc1q..."
    }
  ],

  "fans": [
    {
      "mode": 1,
      "manualSpeed": 75,
      "overheatTemp": 75,
      "pid": { "targetTemp": 55, "p": 5.0, "i": 0.1, "d": 1.0 }
    }
  ],
  "invertFanPolarity": false,

  "flipScreen": false,
  "autoScreenOff": false,
  "canMaster": false
}
```

Mining setpoints are validated before anything is saved: `frequency` and the
governor ceiling must be entries from `frequencyOptions`, the governor ceiling
must not be below the persistent base while enabled, `powerLimitW` must be
between 30 W and 69 W, and `jobInterval` must be between 100 ms and 5000 ms.
When the governor is enabled, a ceiling above 500 MHz requires at least
1300 mV core voltage; this is checked both when enabling/upclocking and when
lowering the voltage of an already governed miner. The 69 W comparison is
strict, while the board's independent 70 W and 5.9 A guards remain active.
The adaptive governor is opt-in; when disabled, the persistent `frequency`
continues to be applied exactly as before.

On NerdQAxe+, the nominal 528, 531, 534, and 537 MHz options are explicit BM1368
low-VCO presets. They use register-0x08 feedback dividers `A9`, `AA`, `AB`, and `AC`
and produce 528.125, 531.250, 534.375, and 537.500 MHz physically; the existing 540 MHz
preset remains `AD` (540.625 MHz physical). Settings keep reporting the nominal
integer target in `effectiveFrequency`; the dashboard exposes the physical clock
separately in `actualFrequency`.

**Response**: `200 OK` (empty body). Device subsystems are automatically reloaded after saving.

---

### Alerts (Webhook)

#### `GET /api/v2/alert`

Returns webhook alert configuration. The webhook URL is **not** returned for security.

```json
{
  "watchdogEnable": 1,
  "blockFoundEnable": 1,
  "bestDiffEnable": 0,
  "coinbaseVerifyEnable": 0,
  "showBlockFoundScreen": 1
}
```

#### `PATCH /api/v2/alert`

Update alert configuration. Requires OTP.

**Request body** (all fields optional):

```json
{
  "webhookUrl": "https://example.com/webhook/alerts",
  "watchdogEnable": true,
  "blockFoundEnable": true,
  "bestDiffEnable": false,
  "coinbaseVerifyEnable": false,
  "showBlockFoundScreen": true
}
```

**Response**: `200 OK` (empty body)

#### `POST /api/v2/alert/test`

Send a test message to the configured webhook. Requires OTP.

**Request body**: empty

**Response**: `"ok"` (plain text) on success, `500` on failure.

---

### InfluxDB

#### `GET /api/v2/influx`

Returns InfluxDB configuration. The token is **not** returned for security.

```json
{
  "url": "http://influxdb.local",
  "port": 8086,
  "bucket": "mining",
  "org": "myorg",
  "prefix": "nerdqaxe",
  "enabled": 1
}
```

#### `PATCH /api/v2/influx`

Update InfluxDB configuration. Requires OTP. Device restart needed for changes to take effect.

**Request body** (all fields optional):

```json
{
  "enabled": true,
  "url": "http://influxdb.local",
  "port": 8086,
  "token": "my-secret-token",
  "bucket": "mining",
  "org": "myorg",
  "prefix": "nerdqaxe"
}
```

**Response**: `200 OK` (empty body)

---

### OTP / Security

#### `GET /api/v2/otp/status`

```json
{ "enabled": true }
```

#### `POST /api/v2/otp`

Start OTP enrollment. Displays a QR code on the device screen for scanning with an authenticator app. Only allowed when OTP is **not** yet enabled.

**Request body**: empty

**Response**: `200 OK` (empty body). Returns `405` if OTP is already enabled.

#### `PATCH /api/v2/otp`

Complete enrollment or disable OTP. Requires `X-TOTP` header with a valid 6-digit code.

**Request body**:

```json
{ "enabled": true }
```

**Response**: `200 OK` (empty body). Returns `401` if TOTP is invalid.

#### `POST /api/v2/otp/session`

Create a session token to avoid repeated OTP prompts. Requires `X-TOTP` header. Only works when OTP is enabled.

**Request headers**:
- `X-TOTP` (required): 6-digit TOTP code

**Response**:

```json
{
  "token": "LGQ7I2GZ6L2WRCJX...",
  "ttlMs": 86400000,
  "expiresAt": 1716789000000
}
```

Use the returned token in subsequent requests via `X-OTP-Session` header.

---

### CAN Swarm

CAN bus extension for multi-board setups. The master board exposes these endpoints to manage connected nodes.

#### `GET /api/v2/can/nodes`

Returns fleet totals and an array of all CAN nodes. The master board is always `nodes[0]` with `isMaster: true`. Each node's `hashRate` is its **own** hashrate, not the combined fleet total.

```json
{
  "fleet": {
    "hashRate": 1200.0,
    "power": 30.0
  },
  "nodes": [
  {
    "id": 0,
    "mac": "B4:3A:45:A2:3E:44",
    "active": true,
    "foreign": false,
    "isMaster": true,
    "deviceModel": "NerdQAxe+",
    "version": "1.2.3",
    "hashRate": 600.0,
    "temp": 55.0,
    "vrTemp": 42.0,
    "power": 7.5,
    "current": 1500,
    "coreVoltageActual": 1150,
    "fanRpm": 3200,
    "fanRpm2": 2800,
    "fanSpeed": 72,
    "fanSpeed2": 65,
    "shutdown": false,
    "boardError": 0,
    "freeHeapInt": 120000,
    "frequency": 525,
    "coreVoltage": 1150,
    "flipScreen": false,
    "autoScreenOff": false,
    "asicTemps": [57.5, 58.0, 59.0, 58.5],
    "fans": [
      { "mode": 1, "manualSpeed": 75, "overheatTemp": 75, "targetTemp": 55, "rpm": 3200, "speedPerc": 72 }
    ]
  },
  {
    "id": 1,
    "mac": "AA:BB:CC:DD:EE:FF",
    "active": true,
    "foreign": false,
    "deviceModel": "NerdQAxe++",
    "version": "1.2.3",
    "hashRate": 600.0,
    "..."
  }
  ]
}
```

#### `PATCH /api/v2/can/nodes/{id}`

Update node configuration (slaves only, `id >= 1`). Requires OTP.

**Request body** (all fields optional):

```json
{
  "frequency": 525,
  "coreVoltage": 1150,
  "fans": [
    { "mode": 1, "manualSpeed": 75, "targetTemp": 55, "overheatTemp": 75 },
    { "mode": 3, "manualSpeed": 100, "targetTemp": 65, "overheatTemp": 80 }
  ],
  "flipScreen": false,
  "autoScreenOff": false
}
```

**Response**: `{"ok": true}`

#### `POST /api/v2/can/nodes/{id}/restart`

Restart a node (slaves only, `id >= 1`). Requires OTP.

**Response**: `{"ok": true}`

#### `POST /api/v2/can/nodes/{id}/shutdown`

Shutdown a node (slaves only, `id >= 1`). Stops mining until restarted. Requires OTP.

**Response**: `{"ok": true}`

#### `POST /api/v2/can/nodes/{id}/identify`

Trigger identification sequence on a node (slaves only, `id >= 1`). Requires OTP.

**Response**: `{"ok": true}`

#### `DELETE /api/v2/can/nodes/{id}`

Remove a node from the registry (slaves only, `id >= 1`). Requires OTP.

**Response**: `{"ok": true}`

---

## Legacy V1 Endpoints

These endpoints remain on v1 for backwards compatibility (Swarm discovery across devices with different firmware versions) or because they handle binary data.

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/system/info` | Full system info (used by Swarm page for remote device discovery) |
| `PATCH` | `/api/system` | Update system settings |
| `POST` | `/api/system/restart` | Restart device (requires OTP) |
| `POST` | `/api/system/shutdown` | Shutdown device (requires OTP) |
| `POST` | `/api/system/OTA` | Upload firmware binary |
| `POST` | `/api/system/OTAWWW` | Upload web UI binary |
| `POST` | `/api/system/OTA/github` | One-click GitHub OTA update |
| `GET` | `/api/system/OTA/github` | Get GitHub OTA update status |
| `GET` | `/api/swarm/info` | Get swarm device list |
| `PATCH` | `/api/swarm` | Update swarm configuration |

---

## Authentication

When OTP is enabled, write operations require one of:

- **`X-TOTP` header**: 6-digit TOTP code from authenticator app
- **`X-OTP-Session` header**: Session token obtained from `POST /api/v2/otp/session`

Session tokens are valid for 24 hours by default. Rate limiting applies: 5 failed TOTP attempts within 60 seconds triggers a 5-minute lockout.
