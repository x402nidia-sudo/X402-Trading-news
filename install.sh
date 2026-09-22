#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
if ! command -v python3 >/dev/null; then
  if command -v apt-get >/dev/null; then
    sudo apt-get update
    sudo apt-get install -y python3 python3-venv python3-pip
  else
    echo 'Install Python 3.10 or later and run this file again.'
    exit 1
  fi
fi
exec python3 install.py
