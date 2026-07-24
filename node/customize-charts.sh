#!/bin/bash
set -euo pipefail
shopt -s nullglob
CHARTS_DIR="$1"
PATCHES_DIR="${2:-/opt/rke2/manifests/patches}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
[ -d "$PATCHES_DIR" ] || { echo "no patches dir $PATCHES_DIR"; exit 0; }
ls "$CHARTS_DIR"
matched=0
for patch in "$PATCHES_DIR"/*; do
  patch_name="$(basename "$patch")"
  chart="$CHARTS_DIR/$patch_name"
  [ -f "$chart" ] || continue
  len="$(yq e 'length' "$chart")" || { echo "yq failed on $chart" >&2; exit 1; }
  [ "$len" != "0" ] || continue
  "$SCRIPT_DIR/customize-chart.sh" "$chart" "$patch"
  matched=$((matched + 1))
done
[ "$matched" -gt 0 ] || echo "warning: no patches applied to $CHARTS_DIR"
