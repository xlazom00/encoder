#!/usr/bin/env bash
cd "$(dirname "$0")"

../live_CT1_HD/CT1_HD.sh &
../live_CT2_HD/CT2_HD.sh &
../live_CTD_ART_HD/CTD_ART_HD.sh &
#../live_CT_SPORT_HD/CT_SPORT_HD.sh &
../live_NOVA_CINEMA_SD/NOVA_CINEMA_SD.sh &
../live_NOVA_FUN_SD/NOVA_FUN_SD.sh &
../live_NOVA_SD/NOVA_SD.sh &
../live_PRIMA_COOL_SD/PRIMA_COOL_SD.sh &
../live_PRIMA_MAX_SD/PRIMA_MAX_SD.sh &
../live_PRIMA_SD/PRIMA_SD.sh &
../live_PRIMA_ZOOM_SD/PRIMA_ZOOM_SD.sh &
../live_WARNER_SD/WARNER_SD.sh &
