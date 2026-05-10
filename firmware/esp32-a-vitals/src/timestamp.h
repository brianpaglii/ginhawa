// NTP sync + ISO 8601 UTC timestamp formatting for the
// ``captured_at`` field of every published measurement.
//
// configTime() is bound to UTC at boot (configTime(0, 0, ...)) and
// timestamp_now_iso8601() formats with %Y-%m-%dT%H:%M:%S+00:00 so
// the kiosk-side parser sees a timezone-aware ISO 8601 string.
#pragma once

#include <stddef.h>

void timestamp_sync_ntp();

// Writes "YYYY-MM-DDTHH:MM:SS+00:00" into ``buf``. Caller-owned
// buffer (no heap allocation here, per CLAUDE.md "no heap in main
// loop"). 32 bytes is comfortably enough; 64 is the ergonomic
// default.
void timestamp_now_iso8601(char* buf, size_t buf_size);
