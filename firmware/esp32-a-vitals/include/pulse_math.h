// Pure median over a small float buffer used to smooth the
// MAX30100 library's per-tick SpO2 / heart-rate readings before
// publishing one stable value per ~30 s.
//
// Lives in include/ rather than src/ so the desktop unity tests
// can link against it under platform=native without dragging in
// Arduino or the oxullo library. The on-device implementation
// includes the same header from sensor_max30100.cpp.
#pragma once

#include <stddef.h>

// Compute the median of ``count`` floats in ``samples``. Caller-
// owned scratch buffer; this function does an in-place sort on a
// stack-allocated copy capped at MAX30100_SAMPLE_BUFFER (64), so it
// is safe in the loop without heap allocation. Returns 0.0f when
// count is 0 (caller should pre-check).
inline float compute_pulse_median(const float* samples, int count) {
    if (count <= 0) return 0.0f;
    // 64 mirrors MAX30100_SAMPLE_BUFFER; we clamp here so a future
    // bigger producer doesn't silently overrun.
    constexpr int kMax = 64;
    if (count > kMax) count = kMax;
    float copy[kMax];
    for (int i = 0; i < count; ++i) copy[i] = samples[i];
    // Insertion sort — small, branch-friendly, fast for ≤64 floats.
    for (int i = 1; i < count; ++i) {
        float v = copy[i];
        int j = i - 1;
        while (j >= 0 && copy[j] > v) {
            copy[j + 1] = copy[j];
            --j;
        }
        copy[j + 1] = v;
    }
    if (count % 2 == 1) {
        return copy[count / 2];
    }
    return (copy[count / 2 - 1] + copy[count / 2]) * 0.5f;
}
