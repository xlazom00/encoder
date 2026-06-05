#!/usr/bin/env bash
cd "$(dirname "$0")"

while true; do
    python3 PRIMA_ZOOM_SD.py
    sleep 60s
done
