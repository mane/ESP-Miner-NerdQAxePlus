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

It builds only the NerdQAxe+ LTS target and uploads assets to an existing GitHub release whose tag matches the latest reachable git tag.

Important behavior:

- The workflow is `workflow_dispatch` only.
- When dispatched from a tag, it uses that exact tag and verifies that the tag
  resolves to the checked-out commit.
- A branch dispatch is stamped `dev-<commit>` and can produce short-lived
  artifacts, but it is never uploaded to a GitHub release.
- If a GitHub release for `VERSION_TAG` already exists, it uploads the firmware assets to that release.
- If the release does not already exist, it only stores short-lived workflow artifacts and skips release upload.

## Creating a public fork release

Use a fork-owned tag that is newer than the installed version, for example:

```bash
cd /Users/mane/Development/ESP-Miner-NerdQAxePlus
git switch lts/nerdqaxeplus-only
git pull --ff-only origin lts/nerdqaxeplus-only

TAG=v1.1.1-mane.6-nqa-lts4
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

Expected release assets include the NerdQAxe+ LTS factory image:

```text
esp-miner-factory-NerdQAxePlus-LTS-<TAG>.bin
```

and OTA firmware binaries named:

```text
esp-miner-NerdQAxePlus-LTS.bin
www.bin
```

## Installing and future updates

Install the matching `esp-miner-factory-<BoardLabel>-<TAG>.bin` image first.

After that, the web UI updater embedded in that image will query `mane/ESP-Miner-NerdQAxePlus` releases and submit fork release asset URLs to the backend OTA endpoint. Because the backend allowlist also points to the fork release download prefix, one-click updates from the fork channel are accepted.

## Notes

- Do not create/publish a release until the tag, notes, and public naming are intentional.
- `VERSION_TAG` is forwarded to both ESP-IDF and the versioned Web UI build so
  their reported versions stay identical. The Docker wrappers derive it from
  Git automatically; release CI supplies the release tag.
- The current local machine does not have `idf.py`; the release build should be done via GitHub Actions unless Docker/ESP-IDF is configured locally.
- If a release is marked as GitHub prerelease, the UI may require enabling prereleases in the update dropdown.
