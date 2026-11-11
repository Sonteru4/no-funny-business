#!/usr/bin/env bash
# Usage: scripts/set-name.sh "Final Name"
# Replaces the {{PRODUCT_NAME}} token in tracked text files and sets the default in desk/brand.py.
set -euo pipefail
name="${1:?usage: scripts/set-name.sh \"Final Name\"}"
cd "$(dirname "$0")/.."
git ls-files | grep -Ev '\.(png|jpg|mp4|ico)$' | xargs grep -l '{{PRODUCT_NAME}}' 2>/dev/null | while read -r f; do
  NAME="$name" perl -pi -e 's/\{\{PRODUCT_NAME\}\}/$ENV{NAME}/g' "$f"
done
NAME="$name" perl -pi -e 's/or "Merchant Desk"/or "$ENV{NAME}"/' desk/brand.py
echo "Product name set to: $name"
