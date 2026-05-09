// Tests for json_encode_measurement (include/json_encode.h).
//
// The encoder is hand-rolled with snprintf so it compiles on both
// the ESP32 (Arduino + ArduinoJson available, but ArduinoJson is
// NOT used here so the desktop test target can link the same
// header) and on platform=native.
#include <unity.h>
#include <string.h>

#include "json_encode.h"

void test_encodes_height_payload(void) {
    char buf[128];
    bool ok = json_encode_measurement(165.0f, "cm",
                                      "2026-05-09T12:30:00+00:00",
                                      buf, sizeof(buf));
    TEST_ASSERT_TRUE(ok);
    // %g formats 165.0f as "165" (no trailing decimals when the
    // value is an integer in float clothing). The kiosk's parser
    // does float() on the value so either rendering parses
    // identically.
    TEST_ASSERT_NOT_NULL(strstr(buf, "\"value\":165"));
    TEST_ASSERT_NOT_NULL(strstr(buf, "\"unit\":\"cm\""));
    TEST_ASSERT_NOT_NULL(strstr(buf, "\"captured_at\":\"2026-05-09"));
}

void test_buffer_too_small_returns_false(void) {
    // 8 bytes can't hold even the opening braces + first key, so
    // snprintf truncates and the function reports false.
    char buf[8];
    bool ok = json_encode_measurement(165.0f, "cm",
                                      "2026-05-09T12:30:00+00:00",
                                      buf, sizeof(buf));
    TEST_ASSERT_FALSE(ok);
}
