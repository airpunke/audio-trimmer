#!/bin/bash
# Double-click this to start the audio trimmer.
cd "$(dirname "$0")" || exit 1

for PY in python3 python; do
  if command -v "$PY" >/dev/null 2>&1; then
    if "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3,7) else 1)' 2>/dev/null; then
      FOUND="$PY"; break
    fi
  fi
done

if [ -z "$FOUND" ]; then
  echo ""
  echo "  Python isn't installed, or this computer can't find it."
  echo ""
  echo "  Get it from https://www.python.org/downloads/ and run this again."
  echo "  If the installer offers \"Add Python to PATH\", tick it."
  echo ""
  read -r -p "  Press return to close. "
  exit 1
fi

clear
echo ""
echo "  Audio trimmer"
echo ""
echo "  Leave this window open while you work."
echo "  Closing it stops the trimmer."
echo ""
"$FOUND" trimmer.py
echo ""
read -r -p "  Stopped. Press return to close. "
