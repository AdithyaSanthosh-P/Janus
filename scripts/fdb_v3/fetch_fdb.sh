#!/usr/bin/env bash
# Clones Full-Duplex-Bench v3 at the pinned commit and downloads +
# sha256-verifies the benchmark data (docs/fdb_v3_implementation_plan.md
# §8, §11, §14). FDB is never vendored into this repo's git history --
# this script fetches it into a local, gitignored directory instead,
# every time, so it can't silently drift from the pinned commit/hash.
#
# Usage:
#   scripts/fdb_v3/fetch_fdb.sh [DEST_DIR]
# DEST_DIR defaults to $FDB_V3_ROOT's parent, or ./.fdb_cache.

set -euo pipefail

FDB_COMMIT="3e799c45a045256f47d5f1c9cda90157e2d2ec9e"
DATA_SHA256="37545bd896f81718136598cf5be25d42ea9aa22efcd91f58370938d05d7d672f"
DATA_FILE_ID="1SO_4MTazWQ_jvCx0dtmpQ-t40bdd07yz"
FDB_REPO_URL="https://github.com/DanielLin94144/Full-Duplex-Bench.git"

DEST_ROOT="${1:-${FDB_CACHE_DIR:-.fdb_cache}}"
mkdir -p "$DEST_ROOT"

REPO_DIR="$DEST_ROOT/Full-Duplex-Bench"
if [ -d "$REPO_DIR/.git" ]; then
    echo "FDB already cloned at $REPO_DIR -- checking commit"
else
    echo "Cloning FDB into $REPO_DIR ..."
    git clone "$FDB_REPO_URL" "$REPO_DIR"
fi

cd "$REPO_DIR"
CURRENT_COMMIT="$(git rev-parse HEAD)"
if [ "$CURRENT_COMMIT" != "$FDB_COMMIT" ]; then
    echo "Checking out pinned commit $FDB_COMMIT (was $CURRENT_COMMIT) ..."
    git fetch origin "$FDB_COMMIT" || true
    git checkout "$FDB_COMMIT"
fi
echo "FDB at commit: $(git rev-parse HEAD)"

DATA_DIR="$REPO_DIR/v3/fdb_v3_data_released"
ZIP_PATH="$DEST_ROOT/fdb_v3_data_released.zip"

if [ -d "$DATA_DIR" ] && [ "$(ls -A "$DATA_DIR" 2>/dev/null | wc -l)" -ge 100 ]; then
    echo "Benchmark data already present at $DATA_DIR (100+ entries) -- skipping download"
else
    if [ ! -f "$ZIP_PATH" ]; then
        echo "Downloading benchmark data ..."
        command -v gdown >/dev/null 2>&1 || pip install --quiet gdown
        gdown "$DATA_FILE_ID" -O "$ZIP_PATH"
    fi

    echo "Verifying sha256 ..."
    ACTUAL_SHA256="$(sha256sum "$ZIP_PATH" | cut -d' ' -f1)"
    if [ "$ACTUAL_SHA256" != "$DATA_SHA256" ]; then
        echo "sha256 MISMATCH: expected $DATA_SHA256, got $ACTUAL_SHA256" >&2
        echo "Refusing to extract an unverified data file." >&2
        exit 1
    fi
    echo "sha256 verified: $ACTUAL_SHA256"

    echo "Extracting into $REPO_DIR/v3/ ..."
    unzip -q -o "$ZIP_PATH" -d "$REPO_DIR/v3/"
fi

echo ""
echo "FDB_V3_ROOT=$REPO_DIR/v3"
echo "FDB_V3_DATA_ROOT=$DATA_DIR"
