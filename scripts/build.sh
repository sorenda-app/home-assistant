#!/usr/bin/env bash
# Builds dist/sorenda.zip: the integration's files at the archive root.
# The Marketplace (hacs.json: zip_release) unpacks it into
# custom_components/sorenda/, and so can a person installing by hand.
set -euo pipefail
cd "$(dirname "$0")/.."
version=$(python3 -c 'import json; print(json.load(open("custom_components/sorenda/manifest.json"))["version"])')
mkdir -p dist
rm -f dist/sorenda.zip
(
  cd custom_components/sorenda
  zip -qr -X ../../dist/sorenda.zip . -x '*__pycache__*' '*.pyc' '.DS_Store'
)
echo "dist/sorenda.zip (version $version)"
unzip -l dist/sorenda.zip | tail -1
