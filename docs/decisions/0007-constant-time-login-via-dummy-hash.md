# ADR 0007: Constant-time login via dummy argon2 hash

- **Status:** Accepted
- **Date:** 2026-04-22
- **Deciders:** Brian Paglinawan, Carl Justin Gasco, Lyan Libunao, Mariane Bersamira

## Context

argon2id's deliberate slowness (~100-300ms) is what makes it secure
against brute force. It also makes the login endpoint's timing
observable. A naive implementation that short-circuits when the
username is unknown — skipping `verify_password` because there is no
hash to verify against — produces an ~1ms response for unknown
usernames and a ~200ms response for known usernames with wrong
passwords. This timing differential allows username enumeration over
the network.

## Decision

When the username lookup fails, the login handler still calls
`verify_password(payload.password, _DUMMY_HASH)` against a precomputed
constant argon2id hash, discards the result, and then raises the same
generic 401 used for the wrong-password branch. The same call shape is
used on the inactive-user branch.

The dummy hash is a module-level constant computed once and hardcoded
into `core/security.py`. It is not a secret — it is a hash of an
arbitrary known string with a known salt — and hardcoding it avoids
the cost of regenerating it on every test run.

## Alternatives considered

- _Defer to Phase 1.5:_ rejected. The protection is incomplete until
  the timing leak is fixed, and an attacker probing usernames over the
  network has a reliable signal during the deferral window.
- _Lazy-init the dummy hash on first call:_ rejected. Argon2id
  generation is expensive and the lazy approach slows tests
  unnecessarily.
- _Different argon2 call (e.g., a no-op):_ rejected. The wall-clock
  must match exactly, which means using the same `verify_password`
  function as the real path.

## Consequences

- All three login failure branches (unknown_user, bad_password,
  inactive_user) take the same wall-clock time and execute the same
  number of hash operations.
- The audit_log distinguishes the three failure types in its `details`
  column for admin investigation. This is a deliberate trade-off:
  audit-log readers can distinguish, but network observers cannot.
  Admin access to the audit log is restricted; rate limiting on
  /auth/login (Phase 1.5) further reduces the value of bulk audit-log
  mining.
- A test (`test_login_unknown_user_runs_password_verification`) asserts
  the call-count invariant via mocking, which catches regressions
  without flaky wall-clock timing assertions.
