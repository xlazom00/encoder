#!/usr/bin/env bash
cd "$(dirname "$0")"

while true; do
    python3 NOVA_FUN_SD.py
    sleep 60s
done
