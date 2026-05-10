// Tests for json_encode_measurement (include/json_encode.h).
//
// Same encoder shape as F2 (esp32-b-anthro): hand-rolled snprintf,
// no Arduino / ArduinoJson dependency, so the native test target
// links it cleanly.
#include <unity.h>
#include <string.h>

#include "json_encode.h"

void test_encodes_vitals_payload(void) {
    char buf[128];
    bool ok = json_encode_measurement(97.0f, "%",
                                      "2026-05-10T12:30:00+00:00",
                                      buf, sizeof(buf));
    TEST_ASSERT_TRUE(ok);
    TEST_ASSERT_NOT_NULL(strstr(buf, "\"value\":97"));
    TEST_ASSERT_NOT_NULL(strstr(buf, "\"unit\":\"%\""));
    TEST_ASSERT_NOT_NULL(strstr(buf, "\"captured_at\":\"2026-05-10"));
}

void test_buffer_too_small_returns_false(void) {
    char buf[8];
    bool ok = json_encode_measurement(97.0f, "%",
                                      "2026-05-10T12:30:00+00:00",
                                      buf, sizeof(buf));
    TEST_ASSERT_FALSE(ok);
}
