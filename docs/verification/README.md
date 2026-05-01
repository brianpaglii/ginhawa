# Verification artifacts

Outputs of one-time, structured verification passes against the cloud
package — smoke tests, mechanical-verification audits, and coverage
triages. Kept under version control so the empirical evidence behind
a release decision is traceable, even after the runtime artifacts in
`/tmp/` are wiped.

These files are reports, not source. Don't edit them post-hoc; if a
finding is later resolved, link the resolving commit from the
finding's section (see `phase1_smoke_test_path_a.md`'s "Findings"
for the existing pattern) rather than rewriting history.

## Files

| File                                                                               | What                                                                                                                                    |
| ---------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| [`phase1_smoke_test_path_a.md`](phase1_smoke_test_path_a.md)                       | First Phase 1 functional smoke test against seeded data. Path A means kiosk-sync scenarios are deferred (sync endpoints not built yet). |
| [`2026-04-30-phase1-coverage-snapshot.md`](2026-04-30-phase1-coverage-snapshot.md) | Phase 1 final coverage snapshot — 97% project coverage with per-module breakdown and explanations for every module under 95%.           |
| [`2026-05-01-phase1-5-smoke-test.md`](2026-05-01-phase1-5-smoke-test.md)           | Phase 1.5 smoke test covering the kiosk-sync paths that were N/A in Path A. PS1–PS10 all PASS.                                          |

## When to add to this directory

- Mechanical-verification passes (`pre-commit`, coverage, schema dumps,
  `pytest` results) where the **structured output** is itself the
  artifact, not just the test result.
- Smoke-test scenarios run against a seeded environment, captured as a
  per-scenario PASS/FAIL/N/A table with concrete evidence.
- Coverage triages that classify gaps as BEHAVIOUR / DEFENSIVE /
  FRAMEWORK and feed downstream test-writing work.

Routine pytest output, ad-hoc one-off curls, and grep findings do not
belong here. Those stay in chat / pull request bodies.
