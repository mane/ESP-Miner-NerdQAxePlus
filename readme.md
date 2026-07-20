[![](https://dcbadge.vercel.app/api/server/3E8ca2dkcC)](https://discord.gg/3E8ca2dkcC)

# ESP-Miner NerdQAxe+ LTS

> This branch is a single-hardware LTS fork for the original NerdQAxe+ only.
> It intentionally removes build support for other Nerd*/Q* boards so releases,
> CI, and local builds cannot accidentally target unsupported hardware.

| Supported MCU | ESP32-S3 |
| --- | --- |
| Required Platform | >= ESP-IDF v5.3.X |
| Supported board | NerdQAxe+ (`BOARD=NERDQAXEPLUS`) |
| Supported ASIC | BM1368 |

This firmware is maintained for NerdQAxe+ stability and measured efficiency.
The LTS baseline is the proven `v1.1.1-mane.6` firmware lineage, with the
repository trimmed to NerdQAxe+ / BM1368 only.

Credits to the devs:
- BitAxe devs on OSMU: @skot/ESP-Miner, @ben and @jhonny
- NerdAxe dev @BitMaker
- Original NerdQAxe+ work from shufps/ESP-Miner-NerdQAxePlus

## Supported board

Only this target is supported in this LTS branch:

| Board ID (`BOARD=...`) | Release asset label |
| --- | --- |
| `NERDQAXEPLUS` | `NerdQAxePlus-LTS` |

Factory images are named like:

```text
esp-miner-factory-NerdQAxePlus-LTS-<TAG>.bin
```

The top-level CMake project rejects every non-`NERDQAXEPLUS` board value.
If `BOARD` is omitted locally, it defaults to `NERDQAXEPLUS`.

## How to flash/update firmware

The newest fork releases are here:

https://github.com/mane/ESP-Miner-NerdQAxePlus/releases

For release/update details and the fork update channel, see
[docs/RELEASES.md](docs/RELEASES.md).

### Recommended method: browser/serial flashing or built-in updater

Use the matching NerdQAxe+ LTS factory binary from the release page. After a
fork build is installed, the built-in web UI updater is configured to query and
install releases from `mane/ESP-Miner-NerdQAxePlus`.

### Clone repository and prepare config

```bash
git clone --branch lts/nerdqaxeplus-only --single-branch \
  https://github.com/mane/ESP-Miner-NerdQAxePlus
cd ESP-Miner-NerdQAxePlus
cp config.cvs.example config.cvs
```

Edit `config.cvs` with your pool/user/network settings.

### Flash with bitaxetool

To switch the board into bootload mode, reset the device with the `boot` button
pressed.

```bash
TAG=v1.1.1-mane.6-nqa-lts9  # replace with the latest LTS tag
BOARD_LABEL=NerdQAxePlus-LTS
curl -L -o "esp-miner-factory-${BOARD_LABEL}-${TAG}.bin" \
  "https://github.com/mane/ESP-Miner-NerdQAxePlus/releases/download/${TAG}/esp-miner-factory-${BOARD_LABEL}-${TAG}.bin"

bitaxetool --config ./config.cvs --firmware "esp-miner-factory-${BOARD_LABEL}-${TAG}.bin"
```

## How to build firmware

Docker is recommended so the ESP-IDF/Node toolchain does not have to be
installed on the host.

### TL;DR

```bash
cd docker
./build_docker.sh
cd ..

# BOARD is optional on this branch; it defaults to NERDQAXEPLUS.
export BOARD=NERDQAXEPLUS
./docker/idf.sh set-target esp32s3
./docker/idf.sh build
```

The build outputs `build/esp-miner.bin` and `build/www.bin`.

The Docker wrappers derive a fail-safe `dev-<commit>` identity and the commit on
the host and pass them to the container, so firmware and Web UI report the same
version even when the checkout is a Git worktree. Tracked local changes add a
`-dirty` suffix. For a release build, always override both values explicitly:

```bash
VERSION_TAG=v1.1.1-mane.6-nqa-lts9 COMMIT_HASH="$(git rev-parse --short HEAD)" \
  ./docker/idf.sh build
```

### Manual Docker build with the published builder image

```bash
COMMIT_HASH="$(git rev-parse --short HEAD)"
VERSION_TAG="dev-${COMMIT_HASH}"
if ! git diff-index --quiet HEAD --; then
  VERSION_TAG="${VERSION_TAG}-dirty"
fi

docker run --rm --user root -e BOARD=NERDQAXEPLUS \
  -e VERSION_TAG -e COMMIT_HASH \
  -v "$PWD":/home/builder/project \
  shufps/esp-idf-builder:0.0.1@sha256:08101278a9ab09568cda93cac5a4927a897cbe08b9008253f9472ca9d17d0917 idf.py set-target esp32s3

docker run --rm --user root -e BOARD=NERDQAXEPLUS \
  -e VERSION_TAG -e COMMIT_HASH \
  -v "$PWD":/home/builder/project \
  shufps/esp-idf-builder:0.0.1@sha256:08101278a9ab09568cda93cac5a4927a897cbe08b9008253f9472ca9d17d0917 idf.py build
```

### Merge a factory image

```bash
./merge_bin.sh esp-miner-factory-NerdQAxePlus-LTS.bin
```

### Flash from Docker shell

```bash
./docker/idf-shell.sh
export BOARD=NERDQAXEPLUS
idf.py set-target esp32s3
idf.py build
./merge_bin.sh esp-miner-factory-NerdQAxePlus-LTS.bin
bitaxetool --config config.cvs --firmware esp-miner-factory-NerdQAxePlus-LTS.bin -p /dev/ttyACM0
```

## Grafana Monitoring

The NerdQAxe+ firmware supports Influx and this repository provides a Grafana
dashboard setup under [`monitoring/`](monitoring/).

## Read-only device health check

After an update, the bundled script can verify the board identity, firmware,
mining state, pool connection, memory, temperatures, efficiency and fan
tachometers without changing device settings:

```bash
python3 scripts/device_health_check.py http://192.168.68.104/ \
  --expect-model 'NerdQAxe+' \
  --expect-version 'v1.1.1-mane.6-nqa-lts9'
```

Omit `--expect-version` when checking an unknown or development build. Use
`--json` for monitoring or automation. The check also downloads the served
`index.html` and its fingerprinted `main.*.js` bundle with cache-busting and
gzip support. It reports the Web-UI version and commit and treats a Web-UI /
firmware version mismatch as an error; this catches partial OTA updates where
only one of `esp-miner-*.bin` and `www.bin` was installed. An old or custom UI
whose build markers cannot be read produces a warning while the API health
checks continue normally. A missing, unreadable or corrupt index or main bundle
is an error. Use `--api-only` to skip Web-UI verification explicitly when only
the JSON API is expected to be available.

A fan commanded above 30% while reporting 0 RPM is shown as a warning;
shutdown, board faults, zero hashrate, over-temperature and loss of every
Stratum pool are reported as errors.
