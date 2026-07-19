#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repo_dir="$(cd -- "$script_dir/.." && pwd -P)"

version_tag="${VERSION_TAG:-$(git -C "$repo_dir" describe --tags --abbrev=0 --dirty --always 2>/dev/null || true)}"
commit_hash="${COMMIT_HASH:-$(git -C "$repo_dir" rev-parse --short HEAD 2>/dev/null || true)}"
version_tag="${version_tag:-local}"
commit_hash="${commit_hash:-local}"

exec docker run --rm -it \
    -v /dev:/dev \
    --privileged \
    -e BOARD="${BOARD:-NERDQAXEPLUS}" \
    -e VERSION_TAG="$version_tag" \
    -e COMMIT_HASH="$commit_hash" \
    -v "$repo_dir":/home/builder/project \
    esp-idf-builder /bin/bash
