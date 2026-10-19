#!/usr/bin/env bash
# get_rootfs.sh - download the latest Alpine x86_64 minirootfs into rootfs/.
# Usage: scripts/get_rootfs.sh      (do NOT run with sudo; it prints the
#                                    sudo command if extraction needs root)
set -u

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
ROOTFS_DIR="$ROOT_DIR/rootfs"
CACHE_DIR="$ROOT_DIR/.cache"
BASE_URL="https://dl-cdn.alpinelinux.org/alpine/latest-stable/releases/x86_64"

die() { echo "error: $1" >&2; exit 1; }

# fetch <url> <output-file>: use wget, else curl.
fetch() {
    if command -v wget >/dev/null 2>&1; then
        wget -q -O "$2" "$1"
    elif command -v curl >/dev/null 2>&1; then
        curl -fsSL -o "$2" "$1"
    else
        die "need wget or curl"
    fi
}

mkdir -p "$CACHE_DIR" "$ROOTFS_DIR" || die "cannot create directories"

# latest-releases.yaml lists the current minirootfs filename.
echo "Looking up latest Alpine minirootfs..."
fetch "$BASE_URL/latest-releases.yaml" "$CACHE_DIR/latest-releases.yaml" \
    || die "could not download latest-releases.yaml"
FILE=$(grep -o 'alpine-minirootfs-[0-9.]*-x86_64\.tar\.gz' "$CACHE_DIR/latest-releases.yaml" | head -n 1)
[ -n "$FILE" ] || die "could not find minirootfs name in latest-releases.yaml"
echo "Latest: $FILE"

if [ ! -s "$CACHE_DIR/$FILE" ]; then
    fetch "$BASE_URL/$FILE" "$CACHE_DIR/$FILE" || die "download of $FILE failed"
fi

# Verify integrity when a checksum file is published.
if fetch "$BASE_URL/$FILE.sha256" "$CACHE_DIR/$FILE.sha256" 2>/dev/null; then
    EXPECTED=$(awk '{print $1}' "$CACHE_DIR/$FILE.sha256")
    ACTUAL=$(sha256sum "$CACHE_DIR/$FILE" | awk '{print $1}')
    [ "$EXPECTED" = "$ACTUAL" ] || die "sha256 mismatch (delete .cache/ and retry)"
    echo "sha256 OK"
else
    echo "warning: no sha256 file available, skipping verification"
fi

# The rootfs must be owned by root (uid 0) so processes inside the container
# see correct ownership; that needs root to extract.
if [ "$(id -u)" -ne 0 ]; then
    echo
    echo "Extraction needs root to preserve file ownership. Run:"
    echo "  sudo tar -xzf '$CACHE_DIR/$FILE' -C '$ROOTFS_DIR'"
    exit 2
fi

tar -xzf "$CACHE_DIR/$FILE" -C "$ROOTFS_DIR" || die "extraction failed"
echo "Alpine rootfs extracted to $ROOTFS_DIR"
