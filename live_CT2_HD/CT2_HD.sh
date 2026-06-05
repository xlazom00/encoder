#!/usr/bin/env bash
cd "$(dirname "$0")"

while true; do
    python3 CT2_HD.py
    sleep 60s
done
