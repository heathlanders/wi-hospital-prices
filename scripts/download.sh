#!/usr/bin/env bash
# Download each hospital's machine-readable file listed in config/sources.csv
# into data/raw/<slug>/. Zip files are extracted next to the download.
# Usage: scripts/download.sh [slug ...]   (default: all)
set -euo pipefail
cd "$(dirname "$0")/.."

UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"

tail -n +2 config/sources.csv | while IFS=, read -r slug _ _ _ url; do
  if [ $# -gt 0 ] && [[ ! " $* " =~ " $slug " ]]; then continue; fi
  dir="data/raw/$slug"
  mkdir -p "$dir"
  echo "== $slug"
  curl -fsSL -A "$UA" -o "$dir/download.tmp" "$url"
  if [ "$(head -c 2 "$dir/download.tmp")" = "PK" ]; then
    mv "$dir/download.tmp" "$dir/$slug.zip"
    unzip -o -q "$dir/$slug.zip" -d "$dir"
  else
    ext=csv; [ "$(head -c 1 "$dir/download.tmp")" = "{" ] && ext=json
    mv "$dir/download.tmp" "$dir/$slug.$ext"
  fi
  ls -la "$dir"
done
