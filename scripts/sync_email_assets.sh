#!/bin/bash
# Copy the brand email images from ahavah-web into this repo and regenerate
# the manifest the tests read.
#
# Why a copy: the API attaches these images to the message itself, as inline
# `cid:` parts, so an Ahavah email still looks like Ahavah in a client that
# blocks remote images. The API can only attach what ships inside its own
# container image, and fetching them over HTTP at send time would put a
# network call in front of every email. Two copies plus a drift test is the
# cheaper trade.
#
# Run from anywhere:
#     ./scripts/sync_email_assets.sh
#     ./scripts/sync_email_assets.sh /path/to/ahavah-web/public/email

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE_DIR="${1:-$REPO_ROOT/../ahavah-web/public/email}"
TARGET_DIR="$REPO_ROOT/emails/assets"
MANIFEST="$REPO_ROOT/tests/email_assets_manifest.txt"

if [ ! -d "$SOURCE_DIR" ]; then
  echo "Source directory not found: $SOURCE_DIR" >&2
  echo "Pass the path to ahavah-web/public/email as the first argument." >&2
  exit 1
fi

mkdir -p "$TARGET_DIR"

# Drop images that are gone from the source so a deleted asset cannot linger
# here and keep a stale template passing.
for existing in "$TARGET_DIR"/*.png; do
  [ -e "$existing" ] || continue
  name="$(basename "$existing")"
  if [ ! -f "$SOURCE_DIR/$name" ]; then
    echo "removing stale $name"
    rm -f "$existing"
  fi
done

copied=0
for source in "$SOURCE_DIR"/*.png; do
  [ -e "$source" ] || continue
  cp -f "$source" "$TARGET_DIR/"
  copied=$((copied + 1))
done

# The manifest names the files, one per line, sorted, so the drift test and
# the title-image test read the same list in CI where only this repo exists.
ls "$TARGET_DIR"/*.png | xargs -n1 basename | LC_ALL=C sort > "$MANIFEST"

echo "synced $copied png into emails/assets"
echo "manifest: $(wc -l < "$MANIFEST" | tr -d ' ') entries"
