// Tests for compute_median_of_three (include/height_math.h).
//
// This file also hosts the single Unity main() for the whole
// test_desktop suite. PlatformIO links every .cpp under
// test/test_desktop/ into one binary, so only ONE main() is
// allowed across the three test files; we declare the sibling
// tests as ``extern void`` and RUN_TEST against them here.
#include <unity.h>

#include "height_math.h"

void test_median_picks_middle_value(void) {
    TEST_ASSERT_EQUAL_FLOAT(170.0f, compute_median_of_three(165.0f, 170.0f, 220.0f));
}

void test_median_with_low_outlier(void) {
    TEST_ASSERT_EQUAL_FLOAT(170.0f, compute_median_of_three(100.0f, 170.0f, 175.0f));
}

void test_median_with_all_equal(void) {
    TEST_ASSERT_EQUAL_FLOAT(170.0f, compute_median_of_three(170.0f, 170.0f, 170.0f));
}

void test_median_with_two_equal(void) {
    TEST_ASSERT_EQUAL_FLOAT(170.0f, compute_median_of_three(170.0f, 170.0f, 200.0f));
}

void test_median_unordered_input(void) {
    TEST_ASSERT_EQUAL_FLOAT(170.0f, compute_median_of_three(220.0f, 165.0f, 170.0f));
}

// Tests defined in sibling files — linked into the same binary.
extern void test_encodes_height_payload(void);
extern void test_buffer_too_small_returns_false(void);
extern void test_iso8601_placeholder(void);

void setUp(void) {}
void tearDown(void) {}

int main(int, char**) {
    UNITY_BEGIN();
    // height_math.h
    RUN_TEST(test_median_picks_middle_value);
    RUN_TEST(test_median_with_low_outlier);
    RUN_TEST(test_median_with_all_equal);
    RUN_TEST(test_median_with_two_equal);
    RUN_TEST(test_median_unordered_input);
    // json_encode.h
    RUN_TEST(test_encodes_height_payload);
    RUN_TEST(test_buffer_too_small_returns_false);
    // iso8601 placeholder (strftime is host-bound; bench-validated)
    RUN_TEST(test_iso8601_placeholder);
    return UNITY_END();
}
