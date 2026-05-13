// Stack-only JSON encoder for the kiosk's measurement payload shape:
//
//     {"value":<float>,"unit":"<unit>","captured_at":"<iso8601>"}
//
// When `iso8601_ts` is `nullptr` or empty, the captured_at field is
// omitted entirely:
//
//     {"value":<float>,"unit":"<unit>"}
//
// The kiosk's mqtt_sensors subscriber stamps a UTC timestamp on
// receipt when the field is absent, so the firmware can skip the
// ESP32-side NTP dependency and not require internet at all.
//
// Identical to the F2 (esp32-b-anthro) encoder. Hand-rolled with
// snprintf so it compiles on both targets without an ArduinoJson
// dependency on the native test build (ArduinoJson is in lib_deps
// for esp32dev only). The kiosk's mqtt_sensors parser only requires
// value + unit and ignores any others; captured_at is optional in
// the contract.
//
// Buffer rule (CLAUDE.md "no heap allocation in main loop"): caller
// provides a stack buffer. Returns false when the buffer is too
// small to hold the formatted string plus the terminating NUL.
#pragma once

#include <stddef.h>
#include <stdio.h>

inline bool json_encode_measurement(float value, const char* unit,
                                    const char* iso8601_ts,
                                    char* buf, size_t buf_size) {
    if (buf == nullptr || buf_size == 0) return false;
    int written;
    if (iso8601_ts == nullptr || iso8601_ts[0] == '\0') {
        written = snprintf(
            buf, buf_size, "{\"value\":%g,\"unit\":\"%s\"}",
            static_cast<double>(value), unit);
    } else {
        written = snprintf(
            buf, buf_size,
            "{\"value\":%g,\"unit\":\"%s\",\"captured_at\":\"%s\"}",
            static_cast<double>(value), unit, iso8601_ts);
    }
    if (written < 0) return false;
    return static_cast<size_t>(written) < buf_size;
}
