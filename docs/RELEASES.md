# Fork release and updater procedure

This fork's installed web updater is configured to use releases from:

https://github.com/mane/ESP-Miner-NerdQAxePlus/releases

The Angular updater fetches GitHub release metadata from:

https://api.github.com/repos/mane/ESP-Miner-NerdQAxePlus/releases

The device backend accepts one-click OTA URLs only when they start with:

https://github.com/mane/ESP-Miner-NerdQAxePlus/releases/download/

This keeps installed fork builds on the fork's update channel instead of the upstream `shufps` channel.

## Build/release flow

The repository already has a manual GitHub Actions workflow:

`.github/workflows/build.yml`

It builds the full board matrix and uploads assets to an existing GitHub release whose tag matches the latest reachable git tag.

Important behavior:

- The workflow is `workflow_dispatch` only.
- It computes `VERSION_TAG` with `git describe --tags --abbrev=0`.
- If a GitHub release for `VERSION_TAG` already exists, it uploads the firmware assets to that release.
- If the release does not already exist, it only stores short-lived workflow artifacts and skips release upload.

## Creating a public fork release

Use a fork-owned tag that is newer than the installed version, for example:

```bash
cd /Users/mane/Development/ESP-Miner-NerdQAxePlus
git checkout develop
git pull --ff-only origin develop

TAG=v1.1.1-mane.1
git tag -a "$TAG" -m "$TAG"
git push origin "$TAG"

gh release create "$TAG" \
  --repo mane/ESP-Miner-NerdQAxePlus \
  --title "$TAG" \
  --notes "Fork release with fork update channel and accumulated fixes."

gh workflow run build.yml \
  --repo mane/ESP-Miner-NerdQAxePlus \
  --ref "$TAG"
```

Then monitor the run:

```bash
gh run list --repo mane/ESP-Miner-NerdQAxePlus --workflow build.yml --limit 3
gh run watch --repo mane/ESP-Miner-NerdQAxePlus <RUN_ID>
```

Expected release assets include one factory image per board:

```text
esp-miner-factory-NerdQAxe+-<TAG>.bin
esp-miner-factory-NerdOCTAXE+-<TAG>.bin
esp-miner-factory-NerdQAxe++-<TAG>.bin
esp-miner-factory-NerdAxe-<TAG>.bin
esp-miner-factory-NerdOCTAXE-Gamma-<TAG>.bin
esp-miner-factory-NerdAxeGamma-<TAG>.bin
esp-miner-factory-NerdHaxe-Gamma-<TAG>.bin
esp-miner-factory-NerdEKO-<TAG>.bin
esp-miner-factory-NerdQX-<TAG>.bin
esp-miner-factory-Q1370-<TAG>.bin
esp-miner-factory-Q1373-<TAG>.bin
```

and OTA firmware binaries named:

```text
esp-miner-<BoardLabel>.bin
www.bin
```

## Installing and future updates

Install the matching `esp-miner-factory-<BoardLabel>-<TAG>.bin` image first.

After that, the web UI updater embedded in that image will query `mane/ESP-Miner-NerdQAxePlus` releases and submit fork release asset URLs to the backend OTA endpoint. Because the backend allowlist also points to the fork release download prefix, one-click updates from the fork channel are accepted.

## Notes

- Do not create/publish a release until the tag, notes, and public naming are intentional.
- The current local machine does not have `idf.py`; the release build should be done via GitHub Actions unless Docker/ESP-IDF is configured locally.
- If a release is marked as GitHub prerelease, the UI may require enabling prereleases in the update dropdown.
