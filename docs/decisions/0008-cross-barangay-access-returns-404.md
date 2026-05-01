# ADR 0008: Cross-barangay access returns 404, not 403

- **Status:** Accepted
- **Date:** 2026-04-22
- **Deciders:** Brian Paglinawan, Carl Justin Gasco, Lyan Libunao, Mariane Bersamira

## Context

BHWs in the GINHAWA portal are scoped to their assigned barangay; they
should not be able to view records from other barangays. When a BHW
issues `GET /api/v1/citizens/{uuid}` for a citizen in a different
barangay, the API must reject the request. There are two reasonable
HTTP responses: 403 Forbidden (the citizen exists; you may not see
them) or 404 Not Found (the citizen, as far as you are concerned, does
not exist).

## Decision

Cross-barangay access returns **404 Not Found**, not 403. List
endpoints filter to the BHW's barangay and never reveal counts from
other barangays. Detail endpoints return 404 if the resource is in a
different barangay.

## Alternatives considered

- _403 Forbidden:_ rejected because 403 leaks the existence of records
  outside the requestor's scope. A BHW probing for a co-worker's
  citizens, or an attacker who has compromised a BHW account, can
  enumerate records they shouldn't see by counting 403s versus 404s.
  The DPA's data-minimization principle argues against this leakage.
- _Return 200 with an empty result:_ would work for list endpoints but
  not for detail endpoints, where an empty 200 is a different kind of
  semantic confusion.

## Consequences

- BHW UI cannot distinguish "no such citizen exists" from "this
  citizen is in another barangay." This is acceptable; the BHW has no
  legitimate need to make that distinction.
- Admin role is unscoped; admins see 200 for any existing citizen.
- The test
  `test_bhw_cross_barangay_get_returns_404_not_403` enforces this
  contract explicitly.
- The same 404-not-403 pattern extends to soft-deleted citizens
  (per a follow-up fix during smoke testing): a soft-deleted citizen
  returns 404 indistinguishable from a never-existed citizen, with a
  byte-equivalent message.
