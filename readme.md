[![](https://dcbadge.vercel.app/api/server/3E8ca2dkcC)](https://discord.gg/3E8ca2dkcC)

# ESP-Miner NerdQAxe+ fork

> Community fork status: this repository is now maintained independently at
> https://github.com/mane/ESP-Miner-NerdQAxePlus. It starts from
> `shufps/ESP-Miner-NerdQAxePlus` and keeps the original credits, while allowing
> fixes and releases to proceed in this fork when upstream collaboration is not
> available.

| Supported MCU | ESP32-S3 |
| --- | --- |
| Required Platform | >= ESP-IDF v5.3.X |

This is a forked version from the NerdAxe miner that was modified for using on the [NerdQAxe+](https://github.com/shufps/qaxe) and related Nerd*/Q* boards.

Credits to the devs:
- BitAxe devs on OSMU: @skot/ESP-Miner, @ben and @jhonny
- NerdAxe dev @BitMaker

## Supported boards

The GitHub Actions release workflow currently builds the following targets:

| Board ID (`BOARD=...`) | Release asset label |
| --- | --- |
| `NERDQAXEPLUS` | `NerdQAxe+` |
| `NERDOCTAXEPLUS` | `NerdOCTAXE+` |
| `NERDQAXEPLUS2` | `NerdQAxe++` |
| `NERDAXE` | `NerdAxe` |
| `NERDOCTAXEGAMMA` | `NerdOCTAXE-Gamma` |
| `NERDAXEGAMMA` | `NerdAxeGamma` |
| `NERDHAXEGAMMA` | `NerdHaxe-Gamma` |
| `NERDEKO` | `NerdEKO` |
| `NERDQX` | `NerdQX` |
| `Q1370` | `Q1370` |
| `Q1373` | `Q1373` |

Factory images are named `esp-miner-factory-<Release asset label>-<TAG>.bin`, for example `esp-miner-factory-NerdQAxe+-v1.1.1-mane.5.bin`.


## How to flash/update firmware

The newest releases are always here:

https://github.com/mane/ESP-Miner-NerdQAxePlus/releases

For release/update details and the fork update channel, see [docs/RELEASES.md](docs/RELEASES.md).

### Recommended method: browser/serial flashing or built-in updater

The [Webflasher](https://shufps.github.io/nerdqaxe-web-flasher/) (modified fork of the great [Bitaxe Webflasher](https://github.com/bitaxeorg/bitaxe-web-flasher) by [Wantclue](https://github.com/WantClue)) is an easy browser/serial flashing option for Nerd*axe variants.

[<img src="https://github.com/user-attachments/assets/4168f23a-bfe7-4536-91e3-7af6df9a203a" style="border:5px solid red;width:200px">](https://shufps.github.io/nerdqaxe-web-flasher/)

When installing this fork, use the matching factory binary from the release page above. After a fork build is installed, the built-in web UI updater is configured to query and install releases from `mane/ESP-Miner-NerdQAxePlus`.

### Other Methods

#### Clone repository and prepare config

First you need to clone the repository and create a local copy of the config file:

```bash
# clone repository
git clone https://github.com/mane/ESP-Miner-NerdQAxePlus

# change into the cloned repository
cd ESP-Miner-NerdQAxePlus

# copy the example config
cp config.cvs.example config.cvs
```

Then you can edit the fields like `stratumurl` and so on.

#### Bitaxetool

After the changes on the `config.cvs` files are done, use `bitaxetool` to flash the matching factory binary and the config onto the device.

To switch it into bootload mode, reset the device with the `boot` button pressed.

```
TAG=v1.1.1-mane.5  # replace with the latest release tag
BOARD_LABEL=NerdQAxe+
curl -L -o "esp-miner-factory-${BOARD_LABEL}-${TAG}.bin" \
  "https://github.com/mane/ESP-Miner-NerdQAxePlus/releases/download/${TAG}/esp-miner-factory-${BOARD_LABEL}-${TAG}.bin"

bitaxetool --config ./config.cvs --firmware "esp-miner-factory-${BOARD_LABEL}-${TAG}.bin"

```


## How to build firmware

### Using Docker

Docker containers allow to use the toolchain without installing `esp-idf` or `Node 20.x` on the system.

#### 0. TL;DR - `esp-miner.bin`, `www.bin`
```bash

# only once
cd docker
./build_docker.sh
cd ..

export BOARD="NERDQAXEPLUS2"
./docker/idf.sh set-target esp32s3

# after each change on the source code
./docker/idf.sh build
```

Afterwards you will have a `esp-miner.bin` and `www.bin` in your `build` directory.


#### 1. First build the docker container

```bash
cd docker
./build_docker.sh
```

#### 2. How to use it

There are several scripts in the `docker` directory but what is most flexible is to just start the container as bash via

```bash
./docker/idf-shell.sh
```

You will get a new terminal that provides tools like:
- `idf.py`
- `bitaxetool`
- `esptool.py`
- `nvs_partition_gen.py`

The current repository will be mounted to `/home/builder/project`.

The default `builder` user has `uid:gid = 1000:1000` (like the main user on *buntu/Mint)

#### 3. Compiling & Flashing using the shell

#### 3.1. Just flashing with dockered `bitaxetool` with factory binary

(no `idf-shell.sh` version)

```bash
TAG=v1.1.1-mane.5  # replace with the latest release tag
BOARD_LABEL=NerdQAxe+
./docker/bitaxetool.sh --config config.cvs --firmware "esp-miner-factory-${BOARD_LABEL}-${TAG}.bin" -p /dev/ttyACM0
```

##### 3.2. Compiling & Flashing using BitAxe tool

(inside of `idf-shell.sh`)

```bash
# start idf-shell
./docker/idf-shell.sh

# set board
export BOARD="NERDQAXEPLUS2"

# set target and build the binaries
idf.py set-target esp32s3
idf.py build

# merge all partitions including config into a single binary
./merge_bin.sh nerdqaxe+.bin

bitaxetool --config config.cvs --firmware esp-miner-factory-nerdqaxe+.bin  -p /dev/ttyACM0
```

#### 3.3. All manual steps for building and flashing

(inside of `idf-shell.sh`)

```bash
# start idf-shell
./docker/idf-shell.sh

# set board
export BOARD="NERDQAXEPLUS2"

# set target and build the binaries
idf.py set-target esp32s3

# optional if you want to change the sdkconfig
idf.py menuconfig

# build the binaries
idf.py build

# creat config.bin nvm partition from config.cvs
nvs_partition_gen.py generate config.cvs config.bin 12288

# merge all partitions including config into a single binary
./merge_bin_with_config.sh nerdqaxe+.bin

# flash using esptool
esptool.py --chip esp32s3 -p /dev/ttyACM0 -b 460800 \
  --before=default_reset --after=hard_reset write_flash \
  --flash_mode dio --flash_freq 80m --flash_size 16MB 0x0 nerdqaxe+.bin
```


When done just `exit` the shell.


### Without Docker

Install bitaxetool from pip. pip is included with Python 3.4 but if you need to install it check <https://pip.pypa.io/en/stable/installation/>

```
pip install --upgrade bitaxetool
```

## Grafana Monitoring

<img src="https://github.com/user-attachments/assets/3c485428-5e48-4761-9717-bd88579a747d" width="600px">

The NerdQAxe+ firmware supports Influx and this repository provides a Grafana dashboard setup under [`monitoring/`](monitoring/).


