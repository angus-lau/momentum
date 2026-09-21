#!/bin/bash
# Convenience wrapper. Usage:
#   ./run.sh                     # uses default base path, prompts for month folder
#   ./run.sh "26 05"             # processes <base>/26 05/
#   ./run.sh /full/path/to/dir   # processes whatever folder you point it at

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CC="/Users/angus/Library/CloudStorage/OneDrive-St.MoritzWatch/Accounting Docs/CC Expenses"

# FY ends July 31: "26 08" lives under "YE 2027", "26 07" under "YE 2026".
ye_for_month() {
  local yy="${1%% *}" mm="${1##* }"
  if [ "$((10#$mm))" -gt 7 ]; then echo "$((2000 + 10#$yy + 1))"; else echo "$((2000 + 10#$yy))"; fi
}

if [ $# -eq 0 ]; then
  echo "Available month folders:"
  ls -1d "$CC"/YE\ */Amex\ Sofia\ 4002/* | sed "s|$CC/||"
  echo
  read -p "Which month folder? (e.g. '26 08') " MONTH
  TARGET="$CC/YE $(ye_for_month "$MONTH")/Amex Sofia 4002/$MONTH"
elif [ -d "$1" ]; then
  TARGET="$1"
else
  TARGET="$CC/YE $(ye_for_month "$1")/Amex Sofia 4002/$1"
fi

if [ ! -d "$TARGET" ]; then
  echo "Folder not found: $TARGET" >&2
  exit 1
fi

python3 "$SCRIPT_DIR/dhl_reconcile.py" "$TARGET"
