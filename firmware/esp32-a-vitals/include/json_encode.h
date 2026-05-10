// Stack-only JSON encoder for the kiosk's measurement payload shape:
//
//     {"value":<float>,"unit":"<unit>","captured_at":"<iso8601>"}
//
// Identical to the F2 (esp32-b-anthro) encoder. Hand-rolled with
// snprintf so it compiles on both targets without an ArduinoJson
// dependency on the native test build (ArduinoJson is in lib_deps
// for esp32dev only). The kiosk's mqtt_sensors parser only requires
// the three fields and ignores any others.
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
    int written = snprintf(
        buf, buf_size,
        "{\"value\":%g,\"unit\":\"%s\",\"captured_at\":\"%s\"}",
        static_cast<double>(value), unit, iso8601_ts);
    if (written < 0) return false;
    return static_cast<size_t>(written) < buf_size;
}
