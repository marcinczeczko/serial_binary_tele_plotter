#!/usr/bin/env bash
set -euo pipefail

OUTPUT="${1:-/dev/stdout}"
ROOT="$(pwd)"

find . \
  -type f \
  -name "*.py" \
  ! -path "./.venv/*" \
  ! -path "./venv/*" \
  ! -path "./.git/*" \
  ! -path "*/__pycache__/*" \
  | sort \
  | while read -r file; do
      rel="${file#./}"

      {
        echo "################################################################################"
        echo "# FILE: $rel"
        echo "################################################################################"
        echo
        cat "$file"
        echo
        echo
      } >> "$OUTPUT"
    done
