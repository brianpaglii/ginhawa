// Tests for compute_pulse_median (include/pulse_math.h).
//
// This file also hosts the single Unity main() for the whole
// test_desktop suite. PlatformIO links every .cpp under
// test/test_desktop/ into one binary, so only ONE main() is
// allowed across the three test files; we declare the sibling
// tests as ``extern void`` and RUN_TEST against them here.
#include <unity.h>

#include "pulse_math.h"

void test_median_picks_middle_for_odd(void) {
    float samples[5] = {95.0f, 96.0f, 97.0f, 98.0f, 99.0f};
    TEST_ASSERT_EQUAL_FLOAT(97.0f, compute_pulse_median(samples, 5));
}

void test_median_averages_for_even(void) {
    float samples[4] = {95.0f, 96.0f, 97.0f, 98.0f};
    TEST_ASSERT_EQUAL_FLOAT(96.5f, compute_pulse_median(samples, 4));
}

void test_median_rejects_outlier_via_position(void) {
    // Sorted: 95, 95, 96, 97, 200 → median = 96. The outlier sits
    // at the top of the order rather than displacing the centre.
    float samples[5] = {95.0f, 95.0f, 96.0f, 97.0f, 200.0f};
    TEST_ASSERT_EQUAL_FLOAT(96.0f, compute_pulse_median(samples, 5));
}

void test_median_handles_unsorted_input(void) {
    // Sorted: 95, 96, 97, 98, 99 → median = 97.
    float samples[5] = {99.0f, 95.0f, 97.0f, 96.0f, 98.0f};
    TEST_ASSERT_EQUAL_FLOAT(97.0f, compute_pulse_median(samples, 5));
}

void test_median_returns_zero_for_empty_input(void) {
    // The on-device call site guards against this with
    // MAX30100_MIN_BUFFERED_SAMPLES, but the function should
    // still return a defined value rather than UB.
    float samples[1] = {0.0f};
    TEST_ASSERT_EQUAL_FLOAT(0.0f, compute_pulse_median(samples, 0));
}

// Tests defined in sibling files — linked into the same binary.
extern void test_roi_peak_finds_max_in_central_block(void);
extern void test_roi_ignores_pixels_outside_roi(void);
extern void test_encodes_vitals_payload(void);
extern void test_buffer_too_small_returns_false(void);

void setUp(void) {}
void tearDown(void) {}

int main(int, char**) {
    UNITY_BEGIN();
    // pulse_math.h
    RUN_TEST(test_median_picks_middle_for_odd);
    RUN_TEST(test_median_averages_for_even);
    RUN_TEST(test_median_rejects_outlier_via_position);
    RUN_TEST(test_median_handles_unsorted_input);
    RUN_TEST(test_median_returns_zero_for_empty_input);
    // mlx90640_roi.h
    RUN_TEST(test_roi_peak_finds_max_in_central_block);
    RUN_TEST(test_roi_ignores_pixels_outside_roi);
    // json_encode.h
    RUN_TEST(test_encodes_vitals_payload);
    RUN_TEST(test_buffer_too_small_returns_false);
    return UNITY_END();
}
