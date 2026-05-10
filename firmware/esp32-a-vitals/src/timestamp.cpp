// NTP sync + UTC ISO 8601 formatter.
#include "timestamp.h"

#include <Arduino.h>
#include <time.h>

void timestamp_sync_ntp() {
    // configTime(gmtOffset, dstOffset, server1, server2). UTC for
    // both offsets — the kiosk's freshness checks reduce both
    // sides to a UTC instant, so no local-tz awareness is needed
    // on the firmware side.
    configTime(0, 0, "pool.ntp.org", "time.nist.gov");
    Serial.print("NTP syncing");
    unsigned long start = millis();
    time_t now = 0;
    while ((millis() - start) < 15000) {
        now = time(nullptr);
        // 1700000000 is 2023-11-14; before that we definitely
        // haven't synced (the ESP32 boots with epoch ≈ 1970).
        if (now > 1700000000) break;
        delay(250);
        Serial.print(".");
    }
    Serial.println();
    if (now > 1700000000) {
        char buf[64];
        timestamp_now_iso8601(buf, sizeof(buf));
        Serial.printf("NTP synced: %s\n", buf);
    } else {
        Serial.println("WARN: NTP sync timeout — timestamps will be wrong");
    }
}

void timestamp_now_iso8601(char* buf, size_t buf_size) {
    time_t now = time(nullptr);
    struct tm tm_utc;
    gmtime_r(&now, &tm_utc);
    strftime(buf, buf_size, "%Y-%m-%dT%H:%M:%S+00:00", &tm_utc);
}
