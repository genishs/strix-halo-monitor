#!/usr/bin/env bash
# Build a dependency-free single-file executable (halo-monitor.pyz) via stdlib zipapp.
# Deploy: scp dist/halo-monitor.pyz to the box, run with the system python3 (no venv/pip).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD="$(mktemp -d)"
trap 'rm -rf "$BUILD"' EXIT
cp -r "$ROOT/src/halo_monitor" "$BUILD/halo_monitor"
find "$BUILD" -name __pycache__ -type d -prune -exec rm -rf {} +
mkdir -p "$ROOT/dist"
python3 -m zipapp "$BUILD" \
  --main "halo_monitor.__main__:main" \
  --python "/usr/bin/env python3" \
  --compress \
  --output "$ROOT/dist/halo-monitor.pyz"
echo "built: $ROOT/dist/halo-monitor.pyz"
