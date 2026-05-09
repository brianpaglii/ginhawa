// Stack-only JSON encoder for the kiosk's measurement payload shape:
//
//     {"value":<float>,"unit":"<unit>","captured_at":"<iso8601>"}
//
// Hand-rolled with snprintf so the function compiles on both the
// ESP32 (Arduino + ArduinoJson available) and on platform=native
// for the desktop unity tests (no Arduino, no ArduinoJson). The
// kiosk's mqtt_sensors parser only requires the three fields and
// ignores any others, so a compact hand-rolled emitter matches
// the contract without dragging in a JSON library on the test path.
//
// Buffer rule (CLAUDE.md "no heap allocation in main loop"): caller
// provides a stack buffer; this function never allocates. Returns
// false when the buffer is too small to hold the formatted string
// plus the terminating NUL.
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
