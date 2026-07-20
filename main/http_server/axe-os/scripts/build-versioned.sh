#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WEB_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
if REPO_ROOT="$(git -C "$WEB_DIR" rev-parse --show-toplevel 2>/dev/null)"; then
  :
else
  REPO_ROOT="$(cd "$WEB_DIR/../../.." && pwd)"
fi

COMMIT_HASH="${COMMIT_HASH:-$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || true)}"
COMMIT_HASH="${COMMIT_HASH:-local}"
if [[ -z "${VERSION_TAG:-}" ]]; then
  VERSION_TAG="dev-${COMMIT_HASH}"
  if git -C "$REPO_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1 &&
     ! git -C "$REPO_ROOT" diff-index --quiet HEAD -- 2>/dev/null; then
    VERSION_TAG="${VERSION_TAG}-dirty"
  fi
fi
APP_MODULE="$WEB_DIR/src/app/app.module.ts"
BACKUP="$(mktemp)"

cp "$APP_MODULE" "$BACKUP"
restore_app_module() {
  cp "$BACKUP" "$APP_MODULE"
  rm -f "$BACKUP"
}
trap restore_app_module EXIT

python3 - "$APP_MODULE" "$VERSION_TAG" "$COMMIT_HASH" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
version = sys.argv[2]
commit = sys.argv[3]
text = path.read_text()
text = text.replace("__VERSION__", version).replace("__COMMIT__", commit)
path.write_text(text)
PY

cd "$WEB_DIR"
./node_modules/.bin/ng build --configuration=production

# app.module.ts uses commit-suffixed translation filenames when __COMMIT__ is stamped.
# Angular copies assets as <lang>.json, so duplicate them in dist before gzip/cleanup.
for lang in de en es fr it pl ro; do
  src="dist/axe-os/assets/i18n/${lang}.json"
  dst="dist/axe-os/assets/i18n/${lang}.${COMMIT_HASH}.json"
  if [[ -f "$src" ]]; then
    cp "$src" "$dst"
  fi
done

./node_modules/.bin/gzipper compress --verbose --gzip --gzip-level 9 ./dist/axe-os
node only-gzip.js

python3 "$REPO_ROOT/scripts/verify_web_release.py" \
  --version "$VERSION_TAG" \
  --commit "$COMMIT_HASH" \
  --dist "$WEB_DIR/dist/axe-os" \
  --write-identity

echo "Web UI stamped with VERSION_TAG=${VERSION_TAG} COMMIT_HASH=${COMMIT_HASH}"
