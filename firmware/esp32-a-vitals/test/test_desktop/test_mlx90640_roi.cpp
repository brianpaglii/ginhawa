// Tests for mlx90640_extract_forehead_peak (include/mlx90640_roi.h).
//
// The 24×32 frame is row-major. ROI per config.h: rows 8–15,
// cols 12–19 (8×8 block centred near the frame midpoint).
#include <unity.h>

#include "config.h"
#include "mlx90640_roi.h"

namespace {
constexpr int kFrameSize = MLX_FRAME_ROWS * MLX_FRAME_COLS;  // 768

void _fill_roi(float* frame, float value) {
    for (int row = MLX_ROI_ROW_MIN; row <= MLX_ROI_ROW_MAX; ++row) {
        for (int col = MLX_ROI_COL_MIN; col <= MLX_ROI_COL_MAX; ++col) {
            frame[row * MLX_FRAME_COLS + col] = value;
        }
    }
}
}  // namespace

void test_roi_peak_finds_max_in_central_block(void) {
    float frame[kFrameSize] = {};
    _fill_roi(frame, 36.5f);
    // Hotter pixel inside the ROI at (row=12, col=16) — frame
    // centre, well within [8..15] × [12..19].
    frame[12 * MLX_FRAME_COLS + 16] = 37.2f;
    float peak = mlx90640_extract_forehead_peak(frame);
    TEST_ASSERT_FLOAT_WITHIN(0.01f, 37.2f, peak);
}

void test_roi_ignores_pixels_outside_roi(void) {
    float frame[kFrameSize] = {};
    // Set a hot pixel OUTSIDE the ROI (top-left corner). The
    // extractor must not see this.
    frame[0] = 99.0f;
    // Body-temp values inside the ROI.
    _fill_roi(frame, 36.5f);
    float peak = mlx90640_extract_forehead_peak(frame);
    TEST_ASSERT_FLOAT_WITHIN(0.01f, 36.5f, peak);
    // Sanity belt-and-braces: not the 99°C corner.
    TEST_ASSERT_TRUE(peak < 50.0f);
}
