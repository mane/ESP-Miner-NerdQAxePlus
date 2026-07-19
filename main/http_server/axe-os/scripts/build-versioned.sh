#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WEB_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
if REPO_ROOT="$(git -C "$WEB_DIR" rev-parse --show-toplevel 2>/dev/null)"; then
  :
else
  REPO_ROOT="$(cd "$WEB_DIR/../../.." && pwd)"
fi

VERSION_TAG="${VERSION_TAG:-$(git -C "$REPO_ROOT" describe --tags --abbrev=0 --dirty --always 2>/dev/null || true)}"
COMMIT_HASH="${COMMIT_HASH:-$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || true)}"
VERSION_TAG="${VERSION_TAG:-local}"
COMMIT_HASH="${COMMIT_HASH:-local}"
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

echo "Web UI stamped with VERSION_TAG=${VERSION_TAG} COMMIT_HASH=${COMMIT_HASH}"
