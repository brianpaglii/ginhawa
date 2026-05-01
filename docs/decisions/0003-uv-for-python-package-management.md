# ADR 0003: uv for Python package and project management

- **Status:** Accepted
- **Date:** 04-20-2026
- **Deciders:** Brian Paglinawan, Carl Justin Gasco, Lyan Libunao, Mariane Bersamira

## Context

The project has multiple Python components (cloud backend, kiosk
application, scripts) that need consistent dependency management,
reproducible installs across developer machines and the production
Raspberry Pi, and fast install times so developer iteration is not
slowed by environment setup.

## Decision

Use **`uv`** as the Python package and project manager across all
Python components. Each component has its own `pyproject.toml` and
lockfile; `uv sync` reproduces the environment from the lockfile.

## Alternatives considered

- _pip + requirements.txt:_ rejected. No proper lockfile for transitive
  pinning, slower installs, no virtualenv management.
- _Poetry:_ viable, but slower than `uv` (often 10x or more on cold
  installs) and the lockfile format has occasionally produced surprises
  during version upgrades.
- _Hatch:_ less mature than Poetry at the time of decision; smaller
  community.
- _PDM:_ viable, but `uv` has more momentum in the ecosystem currently.

## Consequences

- All install instructions in READMEs reference `uv sync` as the
  canonical setup command. Contributors must have `uv` installed.
- The Raspberry Pi's deployment image includes `uv` so production
  installs use the same tool as development.
- Lockfile updates are committed alongside dependency changes;
  reviewers verify the lockfile diff is consistent with the
  `pyproject.toml` change.
- If `uv` is later abandoned or has a major regression, migration to
  another tool is straightforward — `pyproject.toml` is the standard,
  lockfile formats are translatable.
