#!/usr/bin/env bash
set -euo pipefail

ROOT=$(git rev-parse --show-toplevel)
cd "$ROOT"

git diff --quiet
git diff --cached --quiet
if [[ -n $(git status --porcelain --untracked-files=normal) ]]; then
  echo "qualification requires a clean tracked and untracked working tree" >&2
  exit 1
fi

COMMIT=$(git rev-parse HEAD)
PYTHON=${PYTHON:-"$ROOT/.venv/bin/python"}
[[ -x "$PYTHON" ]] || {
  echo "qualification Python is unavailable: $PYTHON" >&2
  exit 1
}

TEMP_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/heddle-public-qualification.XXXXXX")
trap 'rm -rf "$TEMP_ROOT"' EXIT

ARCHIVE="$TEMP_ROOT/source.tar"
SOURCE="$TEMP_ROOT/source"
DIRECT_DIST="$TEMP_ROOT/direct-dist"
REBUILT_ROOT="$TEMP_ROOT/rebuilt-source"
REBUILT_DIST="$TEMP_ROOT/rebuilt-dist"
INSTALL_ENV="$TEMP_ROOT/install-env"
HOST="$TEMP_ROOT/host"
mkdir -p "$SOURCE" "$DIRECT_DIST" "$REBUILT_ROOT" "$REBUILT_DIST" "$HOST"

git archive --format=tar "$COMMIT" -o "$ARCHIVE"
tar -xf "$ARCHIVE" -C "$SOURCE"

(
  cd "$SOURCE"
  git init -q
  git add -f .
)

for check in repository references surfaces tests; do
  "$PYTHON" "$SOURCE/scripts/check-public-repository.py" "$check" \
    --root "$SOURCE" --policy "$SOURCE/release/public-repository.toml"
done

"$PYTHON" -m build --no-isolation --sdist --wheel \
  --outdir "$DIRECT_DIST" "$SOURCE"

DIRECT_SDIST=$(find "$DIRECT_DIST" -maxdepth 1 -type f -name '*.tar.gz' -print -quit)
DIRECT_WHEEL=$(find "$DIRECT_DIST" -maxdepth 1 -type f -name '*.whl' -print -quit)
[[ -n "$DIRECT_SDIST" && -n "$DIRECT_WHEEL" ]]

"$PYTHON" "$SOURCE/scripts/check-public-repository.py" compare-sdist \
  --sdist "$DIRECT_SDIST" --source "$SOURCE" \
  --root "$SOURCE" \
  --policy "$SOURCE/release/public-repository.toml" --wheel "$DIRECT_WHEEL"

tar -xf "$DIRECT_SDIST" -C "$REBUILT_ROOT"
SDIST_SOURCE=$(find "$REBUILT_ROOT" -mindepth 1 -maxdepth 1 -type d -print -quit)
[[ -n "$SDIST_SOURCE" ]]
"$PYTHON" -m build --no-isolation --wheel \
  --outdir "$REBUILT_DIST" "$SDIST_SOURCE"
REBUILT_WHEEL=$(find "$REBUILT_DIST" -maxdepth 1 -type f -name '*.whl' -print -quit)
[[ -n "$REBUILT_WHEEL" ]]

"$PYTHON" "$SOURCE/scripts/check-public-repository.py" compare-wheel \
  --left "$DIRECT_WHEEL" --right "$REBUILT_WHEEL" \
  --root "$SOURCE" --policy "$SOURCE/release/public-repository.toml"

"$PYTHON" -m venv "$INSTALL_ENV"
"$INSTALL_ENV/bin/python" -m pip install --no-index --no-deps "$REBUILT_WHEEL"
DEPENDENCY_SITE=$(
  "$PYTHON" -c 'import site; print(site.getsitepackages()[0])'
)

(
cd "$TEMP_ROOT"
PYTHONPATH="$DEPENDENCY_SITE" "$INSTALL_ENV/bin/python" - \
  "$INSTALL_ENV" "$REBUILT_WHEEL" <<'PY'
import hashlib
import json
from pathlib import Path
import sys
from urllib.parse import unquote, urlparse
import heddle

package = Path(heddle.__file__).resolve()
environment = Path(sys.argv[1]).resolve()
wheel = Path(sys.argv[2]).resolve()
assert package.is_relative_to(environment), (package, environment)
receipts = list(
    environment.glob("lib/python*/site-packages/heddle-*.dist-info/direct_url.json")
)
assert len(receipts) == 1, receipts
receipt = json.loads(receipts[0].read_text())
installed_from = Path(unquote(urlparse(receipt["url"]).path)).resolve()
assert installed_from == wheel, (installed_from, wheel)
expected_hash = hashlib.sha256(wheel.read_bytes()).hexdigest()
assert receipt["archive_info"]["hashes"]["sha256"] == expected_hash
PY
)

(
  cd "$TEMP_ROOT"
  PYTHONPATH="$DEPENDENCY_SITE" "$INSTALL_ENV/bin/heddle" help --json \
    >"$TEMP_ROOT/help.json"
)

(
  cd "$HOST"
  git init -q
  PYTHONPATH="$DEPENDENCY_SITE" "$INSTALL_ENV/bin/heddle" init
)
"$PYTHON" - "$HOST/docs/workflow/engineering-principles.md" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()
marker = "title: Engineering Principles\n"
updated = text.replace(marker, f"{marker}status: ratified\n", 1)
assert updated != text, "generated principles did not contain expected frontmatter"
path.write_text(updated)
PY
(
  cd "$HOST"
  PYTHONPATH="$DEPENDENCY_SITE" "$INSTALL_ENV/bin/heddle" doctor --json \
    >"$TEMP_ROOT/doctor.json"
)

echo "qualified commit $COMMIT"
