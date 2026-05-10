// Forehead-ROI peak extraction from a 24×32 MLX90640 frame.
//
// CLAUDE.md "centre-ROI peak detection at 25–30 cm working distance,
// emissivity 0.98": the imager is mounted on the kiosk console
// pointing at the citizen's forehead. The Adafruit_MLX90640 library
// applies the emissivity correction inside getFrame(); this function
// then picks the peak temperature within the configured rectangular
// ROI. Out-of-ROI pixels are ignored even if hotter (e.g., the wall
// behind the citizen).
//
// Lives in include/ for the same reason as pulse_math.h: the desktop
// unity test links it without the Arduino / Adafruit dependency
// chain.
#pragma once

#include "config.h"

// frame is row-major, 24 rows × 32 cols = 768 floats in °C. Returns
// the maximum value within [MLX_ROI_ROW_MIN..MLX_ROI_ROW_MAX] ×
// [MLX_ROI_COL_MIN..MLX_ROI_COL_MAX]. Physiological-range checking
// is the caller's job; this function only does spatial selection.
inline float mlx90640_extract_forehead_peak(const float frame[768]) {
    float peak = -1000.0f;
    for (int row = MLX_ROI_ROW_MIN; row <= MLX_ROI_ROW_MAX; ++row) {
        for (int col = MLX_ROI_COL_MIN; col <= MLX_ROI_COL_MAX; ++col) {
            float pixel = frame[row * MLX_FRAME_COLS + col];
            if (pixel > peak) peak = pixel;
        }
    }
    return peak;
}
