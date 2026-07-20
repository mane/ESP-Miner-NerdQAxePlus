#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repo_dir="$(cd -- "$script_dir/.." && pwd -P)"

commit_hash="${COMMIT_HASH:-$(git -C "$repo_dir" rev-parse --short HEAD 2>/dev/null || true)}"
commit_hash="${commit_hash:-local}"
version_tag="${VERSION_TAG:-dev-${commit_hash}}"
if [[ -z "${VERSION_TAG:-}" ]] &&
   git -C "$repo_dir" rev-parse --is-inside-work-tree >/dev/null 2>&1 &&
   ! git -C "$repo_dir" diff-index --quiet HEAD -- 2>/dev/null; then
    version_tag="${version_tag}-dirty"
fi

docker_options=(run --rm)
if [[ -t 0 && -t 1 ]]; then
    docker_options+=(-it)
fi

exec docker "${docker_options[@]}" \
    -v /dev:/dev \
    --privileged \
    -e BOARD="${BOARD:-NERDQAXEPLUS}" \
    -e VERSION_TAG="$version_tag" \
    -e COMMIT_HASH="$commit_hash" \
    -v "$repo_dir":/home/builder/project \
    esp-idf-builder idf.py "$@"
