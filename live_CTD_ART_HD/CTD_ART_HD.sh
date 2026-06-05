#!/usr/bin/env bash
cd "$(dirname "$0")"

while true; do
    python3 CTD_ART_HD.py
    sleep 60s
done
