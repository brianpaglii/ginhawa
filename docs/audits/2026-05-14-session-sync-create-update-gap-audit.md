# Session sync: create-vs-update propagation gap

Date: 2026-05-14
Scope: read-only. No code changed.

Sixth audit in the cross-system data-integrity series. The first
five (scale prefiring, BP stale, scale stale, DB lock, SpO2 stale)
all live at the kiosk's **input** boundary — sensor data
contaminating sessions. This one lives at the kiosk-cloud **output**
boundary — kiosk mutations failing to propagate.

## Bench evidence (2026-05-14)

Session `29e697f9-5e1f-40e6-8707-c9817dd044c7`:

| Side  | status        | ended_at                           | updated_at                         |
| ----- | ------------- | ---------------------------------- | ---------------------------------- |
| Kiosk | `completed`   | `2026-05-14T02:45:05.053774+00:00` | `2026-05-14T02:45:05.053774+00:00` |
| Cloud | `in_progress` | empty                              | `2026-05-14T02:43:50.652827+00:00` |

The kiosk's `updated_at` is 75 s after the cloud's. Both rows
show `synced=1` from the kiosk's view. 58 of 58 completed kiosk
sessions show the same pattern — the kiosk uploaded each session
_once_, at creation, and never again, even though every one was
later updated to `status='completed'`.

---

## Section 1 — The sync daemon's row-selection

All three pending-row queries gate on `synced = 0` and nothing
else:

```python
# kiosk/src/ginhawa_kiosk/sync/daemon.py:142-178
def _fetch_unsynced_citizens(session: Session, limit: int) -> list[Citizen]:
    return list(
        session.execute(
            select(Citizen)
            .where(Citizen.synced == 0)
            .order_by(Citizen.registered_at)
            .limit(limit)
        )
        .scalars()
        .all()
    )


def _fetch_unsynced_sessions(session: Session, limit: int) -> list[SessionModel]:
    return list(
        session.execute(
            select(SessionModel)
            .where(SessionModel.synced == 0)
            .order_by(SessionModel.started_at)
            .limit(limit)
        )
        .scalars()
        .all()
    )


def _fetch_unsynced_measurements(session: Session, limit: int) -> list[Measurement]:
    return list(
        session.execute(
            select(Measurement)
            .where(Measurement.synced == 0)
            .order_by(Measurement.measured_at)
            .limit(limit)
        )
        .scalars()
        .all()
    )
```

**No `updated_at` watermark. No dirty flag. No
needs_resync.** Once `synced=1` is stamped, the row is invisible
to the daemon for the rest of the kiosk's uptime.

On successful sync, the daemon flips `synced=1`:

```python
# kiosk/src/ginhawa_kiosk/sync/daemon.py:208-209 (inside _apply_results)
if result.status in _TERMINAL_OK_STATUSES:
    row.synced = 1
else:
    # conflict_constraint or rejected — leave synced=0 ...
```

`_TERMINAL_OK_STATUSES` includes `{"created", "updated",
"conflict_stale"}`
([daemon.py:73-75](kiosk/src/ginhawa_kiosk/sync/daemon.py#L73-L75)).
A row marked synced=1 needs an external trigger to ever flip
back to 0.

---

## Section 2 — The kiosk's UPDATE statements on `sessions`

Four sites mutate `current_session` on the kiosk:

### 2.1 `_after_aborted`

```python
# kiosk/src/ginhawa_kiosk/fsm/session_fsm.py:613-616
def _after_aborted(self) -> None:
    if self.current_session is not None:
        self.current_session.status = "aborted"
        self.current_session.updated_at = _utc_now_iso()
```

Bumps `status` and `updated_at`. **No `synced` flip.**

### 2.2 `_after_path_selected`

```python
# kiosk/src/ginhawa_kiosk/fsm/session_fsm.py:628-638
def _after_path_selected(self, path: _PathChoice) -> None:
    self._pending_path = path
    if self.current_session is not None:
        self.current_session.measurement_path = (...)
        self.current_session.updated_at = _utc_now_iso()
```

Bumps `measurement_path` and `updated_at`. **No `synced` flip.**

### 2.3 `_after_error`

```python
# kiosk/src/ginhawa_kiosk/fsm/session_fsm.py:720-725
def _after_error(self, reason: str) -> None:
    self._error_reason = reason
    if self.current_session is not None:
        self.current_session.status = "error"
        self.current_session.error_reason = reason
        self.current_session.updated_at = _utc_now_iso()
```

Bumps `status`, `error_reason`, `updated_at`. **No `synced` flip.**

### 2.4 `_finalise_session_completed`

```python
# kiosk/src/ginhawa_kiosk/fsm/session_fsm.py:827-834
def _finalise_session_completed(self, *, printed_status: str) -> None:
    if self.current_session is None:
        return
    now = _utc_now_iso()
    self.current_session.status = "completed"
    self.current_session.ended_at = now
    self.current_session.printed_status = printed_status
    self.current_session.updated_at = now
```

Bumps `status`, `ended_at`, `printed_status`, `updated_at`.
**No `synced` flip.** This is the path that produced the bench
evidence row.

### Other tables

- **citizens**: no post-creation mutations exist on the kiosk
  (no `citizen.<field> =` assignments in `src/ginhawa_kiosk/`
  outside the constructor call in
  [main_window.py:733-745](kiosk/src/ginhawa_kiosk/gui/main_window.py#L733-L745)
  for new registrations). Citizens are append-only on the kiosk.
- **measurements**: no post-creation mutations exist on the
  kiosk. The duplicate-drop guard at
  [main_window.py:1033-1040](kiosk/src/ginhawa_kiosk/gui/main_window.py#L1033-L1040)
  enforces "one real reading per type per session" — the row is
  written once and never UPDATEd.
- **audit_log**: append-only by convention (ADR-0016), enforced
  by the application's
  [services/audit.py:80-110](kiosk/src/ginhawa_kiosk/services/audit.py#L80-L110).
  No UPDATEs.

So **sessions is the only table with a current mutation pattern
that loses data to the gap.** Citizens and measurements would
have the same gap _if_ a future kiosk-side edit path appeared;
they don't have one today.

---

## Section 3 — The cloud endpoint's update semantics

The cloud endpoint **already supports UPSERT-by-id with
`conflict_stale` rejection by `updated_at`**. The kiosk daemon's
HTTP-method assumption (always POST) is fine — the cloud's
`POST /api/v1/sync/sessions` is the upsert path:

```python
# cloud/src/ginhawa_cloud/api/sync_sessions.py:123-139
existing = db.get(SessionModel, record.id)
if existing is None:
    return _apply_create(record, kiosk, db)

# ISO 8601 UTC strings sort lexicographically the same as
# chronologically, so direct string comparison is correct.
if record.updated_at <= existing.updated_at:
    return BatchSyncRecordResult(
        id=record.id,
        status="conflict_stale",
        error=(
            f"incoming updated_at {record.updated_at} is not newer "
            f"than stored {existing.updated_at}"
        ),
    )

return _apply_update(record, existing, kiosk, db)
```

`_apply_update` replaces every mutable column verbatim:

```python
# cloud/src/ginhawa_cloud/api/sync_sessions.py:67-96
existing.citizen_id = record.citizen_id
existing.device_id = record.device_id
existing.started_at = record.started_at
existing.ended_at = record.ended_at
existing.status = record.status
existing.error_reason = record.error_reason
existing.measurement_path = record.measurement_path
existing.printed_status = record.printed_status
existing.synced = 1
existing.updated_at = record.updated_at
```

Citizens and measurements share the same shape — upsert + stale
guard:

- [`sync_citizens.py:166-201`](cloud/src/ginhawa_cloud/api/sync_citizens.py)
- [`sync_measurements.py:165-179`](cloud/src/ginhawa_cloud/api/sync_measurements.py)

The kiosk HTTP client picks the right method:

```python
# kiosk/src/ginhawa_kiosk/sync/client.py:115-122
async def _post_batch(self, path, records):
    body = [r.model_dump(mode="json") for r in records]
    try:
        response = await self._client.post(path, json=body)
    ...
```

POST is correct against the upsert endpoint. **The cloud-side
upsert path is not the gap.** If the kiosk re-sent a session
row with a newer `updated_at`, the cloud would happily update it.

---

## Section 4 — Confirmation that the gap is structural

The bench evidence is fully explained by the asymmetry between
Section 1 (daemon row-selection on `synced=0` only) and
Section 2 (UPDATE sites that don't flip `synced=0`). Concretely:

- Cloud `updated_at = 2026-05-14T02:43:50.652827+00:00` →
  the kiosk last _sent_ the row at this timestamp, which the
  cloud took as the row's `updated_at` (per the upsert apply
  above). This was the daemon's create-time push.
- Kiosk `updated_at = 2026-05-14T02:45:05.053774+00:00` →
  75 s later, `_finalise_session_completed` mutated the row
  but **left synced=1**. The daemon's next `_fetch_unsynced_*`
  cycle saw an empty result-set and never re-sent the row.

This is **not** a one-off bug in `_finalise_session_completed`
— it's a structural property of the daemon's row-selection
contract. The same gap fires for every aborted, errored, and
path-selected session (the three other UPDATE sites in Section 2).

The portal is a passthrough — its `StatusPill`
([components/StatusPill.tsx:4-9](portal/src/components/StatusPill.tsx))
renders the cloud's `status` field literally. There's no portal-
side bug compounding the symptom; the cloud genuinely stores
`status='in_progress'` for every completed kiosk session.

---

## Section 5 — Other affected tables

| Table           | Current kiosk mutation paths    | Cloud upsert support  | Affected today?      |
| --------------- | ------------------------------- | --------------------- | -------------------- |
| `sessions`      | 4 UPDATE sites (Section 2)      | Yes                   | **Yes (this audit)** |
| `citizens`      | None (append-only on kiosk)     | Yes                   | Not affected today   |
| `measurements`  | None (append-only on kiosk)     | Yes                   | Not affected today   |
| `audit_log`     | None (append-only, ADR-0016)    | N/A (separate stream) | Not applicable       |
| `device_config` | Provisioning only (kiosk-local) | N/A (not synced)      | Not applicable       |

The bug class — "kiosk UPDATE doesn't flag for resync" — is
**only sessions today**, but the surface for citizens and
measurements is one-line away: any future kiosk-side edit handler
(e.g. "BHW corrects a citizen's phone number on the kiosk", or
"admin invalidates a misattributed measurement") would inherit
the gap unless it explicitly flips `synced=0`. The fix scope
matters: it's not just three lines of patching, it's an
architectural rule about how kiosk mutations interact with the
daemon.

User-visible symptoms (today):

- **`status` stays `in_progress`** for every completed,
  aborted, or errored session in the cloud → BHW portal's
  session list, dashboard counts, and per-citizen history all
  display stale status.
- **`ended_at` stays empty** in the cloud → cloud-side session
  duration metrics and "session timeline" UIs (if/when they
  appear) read NULL instead of the citizen's real end time.
- **`measurement_path` stays whatever it was at creation**.
  Today `_after_path_selected` runs before the first daemon
  cycle picks the row up (the row is created in `_ensure_session_row`
  at line 796-825 on PATH*CHOICE entry, \_path is selected on
  the same transition*), so the path value is normally captured
  in the create payload. But re-paths (rare, currently
  impossible via the FSM) wouldn't propagate.
- **`error_reason` stays empty** in the cloud for sessions that
  errored after creation. Forensic gap when investigating
  failures.
- **`printed_status` stays at the default `not_requested`** in
  the cloud forever. Cloud-side reporting on print success-rate
  is broken.

---

## Section 6 — Failure-mode trace

For the bench-evidence session (vitals_only path, completed
normally):

1. **PATH_CHOICE entry.** `_ensure_session_row`
   ([session_fsm.py:796-825](kiosk/src/ginhawa_kiosk/fsm/session_fsm.py#L796-L825))
   `INSERT`s the row with `synced=0`.
2. **PATH_CHOICE → MEASURING_VITALS via `path_selected`.**
   `_after_path_selected`
   ([session_fsm.py:628-638](kiosk/src/ginhawa_kiosk/fsm/session_fsm.py#L628-L638))
   mutates `measurement_path` and bumps `updated_at`. **synced
   stays 0** (it was 0 already from step 1).
3. **First sync cycle after step 2 or 3.** Daemon's
   `_fetch_unsynced_sessions` returns the row.
   `sync_sessions` POSTs the batch. Cloud's
   `_process_record` runs `db.get(SessionModel, record.id)`
   → `None` → `_apply_create`. Cloud now has the row with
   `status='in_progress'`, `ended_at=NULL`,
   `measurement_path='vitals'`, etc. Daemon receives
   `status='created'`, flips local `synced=1`
   ([daemon.py:208-209](kiosk/src/ginhawa_kiosk/sync/daemon.py#L208-L209)).
   **This is the only time the row goes over the wire.**
4. **Citizen captures BP / SpO2 / temperature. FSM advances
   through MEASURING_VITALS → REPORT.** No `sessions` UPDATE
   happens during measurement capture — only `measurements`
   inserts.
5. **Citizen taps Finish / Print, or REPORT auto-timeout
   fires.** `_finalise_session_completed`
   ([session_fsm.py:827-834](kiosk/src/ginhawa_kiosk/fsm/session_fsm.py#L827-L834))
   sets `status='completed'`, `ended_at=now`,
   `printed_status=<chosen>`, `updated_at=now`. **`synced`
   stays at 1** — the row was synced at step 3, the UPDATE
   doesn't touch the flag.
6. **Next sync cycle after step 5.** Daemon's
   `_fetch_unsynced_sessions` sees `synced=0` rows only. The
   row from step 5 has `synced=1` → invisible. **Daemon does
   not POST anything for this row.** Cloud row stays at
   `status='in_progress'` from step 3.
7. **Forever after.** No subsequent UPDATE will flag the row
   for resync (no kiosk code flips synced back to 0). The
   cloud's view of this session is permanently frozen at
   creation time.

The trace produces exactly the bench evidence: cloud
`updated_at` = creation-time push, kiosk `updated_at` =
finalisation-time mutation, 75 s apart, both with `synced=1`
locally.

---

## Section 7 — Root cause

**The sync daemon implements one-shot INSERT semantics, but the
data model assumes UPSERT semantics on both sides.** Three layers
were considered, and the diagnosis lands squarely on Layer 1:

| Layer                                    | What it does                                | Bug?                                           |
| ---------------------------------------- | ------------------------------------------- | ---------------------------------------------- |
| Kiosk row-selection (daemon.py:142-178)  | `WHERE synced=0` only                       | **Yes — this is the gap.**                     |
| Kiosk UPDATE sites (session_fsm.py × 4)  | Mutate row, bump updated_at, leave synced=1 | **Yes — compound cause.**                      |
| Cloud endpoint (sync_sessions.py:99-139) | Upsert-by-id with conflict_stale            | No — correct                                   |
| Kiosk HTTP method (client.py:122)        | POST                                        | No — correct (cloud's POST is the upsert path) |

The cloud is innocent; the kiosk daemon's `_fetch_unsynced_*`
contract is the structural gap. There are two equivalent ways
to read the bug:

- **From the daemon's view:** "I only sync rows whose `synced`
  flag I haven't flipped yet." That's INSERT-once, not
  eventual-consistency.
- **From the UPDATE site's view:** "I mutate the row and trust
  the daemon to notice." That's eventual-consistency, not
  INSERT-once.

Neither side enforces a contract that bridges the two views.

---

## Section 8 — Recommended fix sketch

**No code in this section.** Comparison of the four paths the
prompt outlined.

### Path A — `updated_at` watermark

Add `last_synced_at: datetime | None` per row (or per-table
global watermark). Daemon selects
`WHERE updated_at > coalesce(last_synced_at, '0001-01-01')`.

- **Schema change:** one new column on each synced table; mild
  migration.
- **Daemon refactor:** non-trivial — `_apply_results` stamps
  `last_synced_at`, all three `_fetch_unsynced_*` queries
  change.
- **Cloud:** unchanged.
- **Robustness:** future UPDATE sites need nothing extra; any
  mutation that bumps `updated_at` (already the FSM's
  convention) is automatically eligible for resync.
- **Demo-readiness:** worst — too invasive for tonight.

### Path B — `dirty` flag

Add `dirty: bool` per synced table. Every kiosk UPDATE sets
`dirty=1`. Daemon selects `WHERE dirty=1`. On successful sync,
daemon resets `dirty=0` (or only resets dirty if `dirty` hasn't
been re-set during the round-trip — needs care).

- **Schema change:** one new column; mild migration.
- **Daemon refactor:** moderate — query change, result-apply
  change.
- **Cloud:** unchanged.
- **Robustness:** same as Path C below — future UPDATE sites
  still need to remember to flip `dirty=1`. Marginal advantage
  over C: a fresh `dirty` column makes "is this row pending
  resync?" lexically distinct from "is this row known to the
  cloud?" (currently both meanings overload `synced`).
- **Demo-readiness:** medium.

### Path C — flip `synced=0` on every UPDATE (surgical)

Every UPDATE site in
[session_fsm.py](kiosk/src/ginhawa_kiosk/fsm/session_fsm.py)
adds `self.current_session.synced = 0` alongside the existing
`updated_at` bump. Three sites today
(`_after_aborted` at 613-616, `_after_error` at 720-725,
`_finalise_session_completed` at 827-834) plus
`_after_path_selected` at 628-638 — four total. Each site is
already touching `updated_at`; adding `synced=0` is one extra
line per site.

- **Schema change:** none.
- **Daemon refactor:** none.
- **Cloud:** none (already upsert).
- **Robustness:** brittle — future UPDATE sites must remember
  the rule. Citizens and measurements have no current sites
  but the rule should extend to them too if they ever get
  one.
- **Demo-readiness:** best — four lines, no migration, no
  test rewiring (the daemon's existing query immediately
  picks up resync-flagged rows on the next cycle).

### Path D — cloud-side UPSERT only

Already done. Not a stand-alone fix.

### Recommended posture for the user's demo tomorrow

**Path C, tonight.** Four lines of code, surgical, no schema
change, no migration concern, no daemon refactor, and the
existing cloud upsert path immediately accepts the resync. The
bench can verify within minutes by running one new session and
checking the cloud row.

The brittleness concern is real but **mitigable** by adding a
short ADR (suggested: ADR-0024 — "Kiosk UPDATE sites must flip
`synced=0`") that codifies the rule for future UPDATE sites,
plus a regression test that constructs a Session, calls each
FSM transition, and asserts `synced == 0` on the row afterwards.
That gives Path C 90% of Path B's robustness at 10% of the
shipping cost.

Path B becomes a clean refactor for Phase 3 once the demo is
behind us; the `dirty` column is a better long-term name and
separates concerns more cleanly. Path A is over-engineering
for the four current mutation sites.

---

## Section 9 — Cross-references and pattern

This is the **first** sync-layer bug in the audit series.

| #     | Audit                                                                                | Class                               | Surface                  |
| ----- | ------------------------------------------------------------------------------------ | ----------------------------------- | ------------------------ |
| 1     | [scale-prefiring](2026-05-13-scale-prefiring-audit.md)                               | Receipt boundary                    | Kiosk input              |
| 2     | [bp-stale-readings](2026-05-13-bp-stale-readings-audit.md) (ADR-0020)                | Receipt boundary                    | Kiosk input              |
| 3     | [scale-stale-readings](2026-05-13-scale-stale-readings-audit.md)                     | Receipt boundary                    | Kiosk input              |
| 4     | [db-lock-contention](2026-05-14-db-lock-contention-audit.md) (ADR-0021)              | Concurrency / log noise             | Kiosk infrastructure     |
| 5     | [spo2-stale-readings](2026-05-14-spo2-stale-readings-audit.md) (ADR-0022 + ADR-0023) | Receipt boundary                    | Firmware + kiosk input   |
| **6** | **this audit**                                                                       | **Cross-boundary sync correctness** | **Kiosk output → cloud** |

Audits 1–5 share a principle: _every always-on or store-and-
forward sensor that can deliver data outside the citizen's
capture window needs a session-relative gate at the kiosk's
receipt boundary._ Defended by per-sensor ADRs and one umbrella
suggestion.

Audit 6 lives on a different axis: _every kiosk-side mutation
to a synced row must explicitly re-flag the row for resync._ The
data flows in the opposite direction (kiosk → cloud, not sensor
→ kiosk), the failure mode is silent **propagation gap** rather
than silent **misattribution**, and the fix surface is the
mutation site rather than the receipt boundary.

Both axes deserve their own paper-section treatment:

- **Receipt-boundary defence for sensor freshness** (audits 1–3, 5) — a documented architectural principle, four ADRs across
  six fixes.
- **Eventual consistency for partial-connectivity deployments**
  (this audit, future portal-driven invalidate flows, future
  citizen edit flows) — currently undocumented; this audit + a
  subsequent ADR-0024 would seed the family.

---

## Section 10 — Impact and urgency

### Demo readiness — **HIGH visibility**

The BHW portal is the natural second screen after the kiosk in
any panel demo. Every session that has been completed on the
kiosk currently shows in the portal's session list as
**"In progress"** with no end time
([StatusPill.tsx:5](portal/src/components/StatusPill.tsx#L5)).
58 of 58 completed sessions are affected by the bench evidence.
A panellist navigating to the session list will see what looks
like an empty deployment with zero finished work.

The DashboardPage's funnel counts
([portal/src/pages/DashboardPage.tsx:176](portal/src/pages/DashboardPage.tsx#L176))
sum `completed + aborted + in_progress + error` — those still
add up correctly, but the breakdown is misleading (everything
in the `in_progress` bucket).

### Data integrity

The cloud's local `audit_log` records `sync_create` events but
no `sync_update`s for these sessions (the
`_apply_update` path at
[sync_sessions.py:67-96](cloud/src/ginhawa_cloud/api/sync_sessions.py#L67-L96)
is the audit-emitter, and it's never been hit). The forensic
trail is silently incomplete — a future analysis trying to
reconstruct "when did citizen X finish their visit" from cloud
data alone would see only creation events.

Analytics, dashboard charts that rely on `ended_at`, and any
future "show me all completed sessions today" portal query are
all impacted.

### Other tables

Currently only `sessions` has mutation sites that hit the gap.
Citizens and measurements would inherit the gap on the first
post-creation kiosk-side edit. **The architectural rule needs
to be agreed before the next edit-feature lands**, regardless of
which fix path the user picks for the demo.

### Suggested priority

**Fix tonight.** Path C is four lines of additions to
[session_fsm.py](kiosk/src/ginhawa_kiosk/fsm/session_fsm.py)
(`_after_aborted` line 615, `_after_path_selected` line 631,
`_after_error` line 723, `_finalise_session_completed` line 831) plus a one-paragraph ADR-0024 codifying the rule. The
cloud already supports the resulting upserts; no migration; no
daemon refactor; bench verification is "run one new session,
check the cloud row updates."

After the demo, Path B is the clean Phase 3 refactor; the
`dirty` column separates "known to cloud" from "needs resync"
and removes the brittle "every UPDATE site remembers to flip"
constraint. Path A is excessive for the current mutation
surface.
