# ADR 0014: Kiosk supports self-service first-time registration (Option A)

- **Status:** Accepted
- **Date:** 04-30-2026
- **Deciders:** Brian Paglinawan, Carl Justin Gasco, Lyan Libunao, Mariane Bersamira, and Adviser Engr. Elizier Obamos

## Context

Phase 1's `POST /api/v1/citizens` was conceived for BHW-driven
registration via the portal. The kiosk's REGISTER state (Figure 3.8 of
the paper) describes a flow where an unregistered citizen taps a new
RFID card and proceeds through the registration screens. Whether this
constitutes "self-service" registration (the citizen registers
themselves, mediated by the kiosk UI) or "BHW-supervised registration
at the kiosk" (a BHW must be present and authenticated before
registration can proceed) is a deployment-context choice with real
implications for the API contract, the audit attribution, and the
consent-verification mechanism.

The schema's `citizens.registered_by` column was deliberately nullable
to leave the choice open until this decision was made.

## Decision

**Option A: the kiosk supports first-time self-service registration.**
An unregistered RFID card triggers the REGISTER state. The citizen
taps through the consent flow on the kiosk touchscreen and provides
their own demographics. The kiosk POSTs to the cloud sync endpoint
with `registered_by=NULL`. The audit_log entry recording the create
uses `actor_type='kiosk'` and `actor_id=device_id`, with
`details.registration_type='self_service'`.

## Alternatives considered

- _Option B (deferred):_ leave the question open until a later phase.
  Rejected because the kiosk's REGISTER state implementation cannot
  ship without a contract, and deferring would block Phase 2.
- _Option C (BHW supervision required):_ require BHW authentication
  at the kiosk before registration can proceed. Rejected because it
  would significantly complicate the kiosk UI (a BHW-login subflow
  embedded in the citizen-facing flow), and the deployment context
  (a barangay health center where a BHW is typically already present
  but may not be at the kiosk at the moment a citizen taps in) doesn't
  require it. The DPA's consent-verification requirement is satisfied
  by the kiosk's tap-to-consent flow, which captures the citizen's
  active consent action with timestamp.

## Consequences

- The kiosk-to-cloud sync endpoint accepts `registered_by=NULL` and
  attributes the audit row to the kiosk principal. This was
  implemented in Phase 1.5 and verified in smoke test scenario PS3
  (2026-05-01).
- Section 3.6 of the paper requires a paragraph addition explaining
  the consent verification mechanism for self-service registration:
  the citizen's tap on the consent screen with timestamp captures
  active consent; consent_version is recorded; the consent screen text
  is the version-controlled privacy notice required under NPC Circular
  2023-04.
- Future analytics may benefit from being able to distinguish
  self-service from BHW-assisted registrations; the
  `details.registration_type` field in audit_log supports this.
- If a future deployment context (e.g., a hospital outpatient clinic
  rather than a barangay health center) requires BHW supervision,
  the API supports it: a kiosk can be configured to refuse self-service
  by always requiring `registered_by` to be non-NULL. This is a
  deployment-config choice, not a code change.
