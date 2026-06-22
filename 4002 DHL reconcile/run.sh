#!/bin/bash
# Convenience wrapper. Usage:
#   ./run.sh                     # uses default base path, prompts for month folder
#   ./run.sh "26 05"             # processes <base>/26 05/
#   ./run.sh /full/path/to/dir   # processes whatever folder you point it at

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BASE="/Users/angus/Library/CloudStorage/OneDrive-St.MoritzWatch/Accounting Docs/CC Expenses/YE 2026/Amex Sofia 4002"

if [ $# -eq 0 ]; then
  echo "Available month folders in $BASE :"
  ls -1 "$BASE"
  echo
  read -p "Which month folder? (e.g. '26 05') " MONTH
  TARGET="$BASE/$MONTH"
elif [ -d "$1" ]; then
  TARGET="$1"
else
  TARGET="$BASE/$1"
fi

if [ ! -d "$TARGET" ]; then
  echo "Folder not found: $TARGET" >&2
  exit 1
fi

python3 "$SCRIPT_DIR/dhl_reconcile.py" "$TARGET"
