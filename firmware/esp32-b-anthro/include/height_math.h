// Pure-function median used by the smoothed VL53L0X reader.
// Lives in include/ rather than src/ so the desktop unit tests can
// link against it under platform=native (which doesn't compile
// project sources by default and has no Arduino/Pololu libraries).
//
// Inline definition: the body lands in any TU that includes this
// header — both sensor_vl53l0x.cpp on the device and the unity
// test on the desktop pull the same compiled body.
#pragma once

inline float compute_median_of_three(float a, float b, float c) {
    if ((a >= b && a <= c) || (a <= b && a >= c)) return a;
    if ((b >= a && b <= c) || (b <= a && b >= c)) return b;
    return c;
}
