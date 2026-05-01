# ADR 0006: argon2id for BHW portal password hashing

- **Status:** Accepted
- **Date:** 2026-04-22
- **Deciders:** Brian Paglinawan, Carl Justin Gasco, Lyan Libunao, Mariane Bersamira

## Context

The BHW portal authenticates users by password. Passwords must be
stored in a form that resists offline brute-force attacks if the
database is exfiltrated. The candidate hashing algorithms in current
use are bcrypt, scrypt, and argon2 (with three variants: argon2d,
argon2i, argon2id).

## Decision

Use **argon2id** via `passlib`'s `CryptContext`, with default cost
parameters and `deprecated="auto"` to allow future re-hashing if the
defaults change.

## Alternatives considered

- _bcrypt:_ still the most widely-deployed option and has a long track
  record. Rejected because bcrypt has no memory-hardness — modern GPU
  attacks scale linearly with bcrypt's compute cost, while argon2id's
  memory requirement defeats the GPU advantage. OWASP's current Password
  Storage Cheat Sheet recommends argon2id first for new deployments.
- _scrypt:_ memory-hard like argon2 but predates the Password Hashing
  Competition. Argon2id won that competition and has had more
  cryptographic review since.
- _PBKDF2:_ rejected — neither memory-hard nor side-channel-resistant.

## Consequences

- Login and verification take ~100-300ms per attempt by design. This
  is the property that defeats brute force; we accept the latency.
- The same latency creates a timing-leak risk on the login endpoint
  if the unknown-username branch is allowed to short-circuit. ADR-0007
  documents the mitigation (constant-time dummy hash on the
  unknown-user path).
- Migration to a different algorithm later is supported by passlib's
  `deprecated="auto"`: any login with a non-argon2id hash triggers
  re-hash on success.
