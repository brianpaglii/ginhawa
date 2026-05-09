#!/usr/bin/env bash
# Simulate an ESP32 publish so we can verify the kiosk's MQTT
# subscriber routes the message and the cloud-side audit picks it up.
# Useful before any real ESP32 firmware exists — round-trips the
# broker, ACL, kiosk routing, and event-bus publish in one shot.
#
# Usage:
#   ./scripts/bench_mqtt_publish.sh spo2 97.0 "%"
#   ./scripts/bench_mqtt_publish.sh height 167.5 cm
#
# Required environment (set via scripts/.bench_mqtt_secrets, which is
# gitignored — copy from .bench_mqtt_secrets.example):
#   ESP32_A_PASS — Mosquitto password for the esp32_a role
#   ESP32_B_PASS — Mosquitto password for the esp32_b role
# Optional:
#   MQTT_HOST            — defaults to 127.0.0.1 (run the script on the Pi)
#   KIOSK_DEVICE_ID      — UUID of this kiosk; defaults to the bench Pi
set -euo pipefail

SUFFIX="${1:?suffix required: spo2|heart_rate|temperature|height}"
VALUE="${2:?value required (number)}"
UNIT="${3:?unit required (e.g. %, bpm, C, cm)}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SECRETS_FILE="$SCRIPT_DIR/.bench_mqtt_secrets"
if [ ! -f "$SECRETS_FILE" ]; then
  echo "ERROR: $SECRETS_FILE not found." >&2
  echo "       Copy scripts/.bench_mqtt_secrets.example and fill in" >&2
  echo "       the passwords issued by mosquitto_passwd on the Pi." >&2
  exit 2
fi
# shellcheck source=/dev/null
source "$SECRETS_FILE"

MQTT_HOST="${MQTT_HOST:-127.0.0.1}"
DEVICE_ID="${KIOSK_DEVICE_ID:-00000000-0000-0000-0000-000000000401}"

# ESP32-A owns spo2/heart_rate/temperature (per ADR-0018 — the
# MLX90640 moved off ESP32-B). ESP32-B owns height only.
case "$SUFFIX" in
  spo2|heart_rate|temperature)
    MQTT_USER="esp32_a"
    PASS_VAR="ESP32_A_PASS"
    ;;
  height)
    MQTT_USER="esp32_b"
    PASS_VAR="ESP32_B_PASS"
    ;;
  *)
    echo "ERROR: unknown suffix '$SUFFIX'." >&2
    echo "       Expected one of: spo2 heart_rate temperature height" >&2
    exit 2
    ;;
esac
MQTT_PASS="${!PASS_VAR:-}"
if [ -z "$MQTT_PASS" ]; then
  echo "ERROR: $PASS_VAR is empty in $SECRETS_FILE." >&2
  exit 2
fi

NOW=$(date -u +%Y-%m-%dT%H:%M:%S+00:00)
PAYLOAD=$(printf '{"value":%s,"unit":"%s","captured_at":"%s"}' \
  "$VALUE" "$UNIT" "$NOW")
TOPIC="ginhawa/kiosk/$DEVICE_ID/sensors/$SUFFIX"

mosquitto_pub \
  -h "$MQTT_HOST" -p 1883 \
  -u "$MQTT_USER" -P "$MQTT_PASS" \
  -t "$TOPIC" \
  -q 1 \
  -m "$PAYLOAD"

echo "Published to $TOPIC"
echo "$PAYLOAD"
