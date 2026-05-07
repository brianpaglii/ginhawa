"""CORS preflight handling for the BHW portal.

The browser sends an ``OPTIONS`` preflight before any cross-origin POST
that carries a JSON body (Content-Type is not CORS-safe). Pre-fix the
cloud had no CORS middleware, so the preflight hit the router and got
405 Method Not Allowed — login from the portal at http://localhost:5173
never reached the handler.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from ginhawa_cloud import app


client = TestClient(app)


# Verifies that an OPTIONS preflight from a Vite-dev-server origin is
# answered with the ACAO header echoed back, which is what the browser
# requires before letting the actual POST go out.
# Mortality: would fail if CORSMiddleware were dropped or if the origin
# allowlist regressed to omit the dev-server URL.
def test_cors_preflight_from_dev_origin_succeeds() -> None:
    response = client.options(
        "/api/v1/auth/login",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    # Starlette's CORSMiddleware answers preflights with 200; FastAPI's
    # router would return 405 here pre-fix.
    assert response.status_code == 200
    assert (
        response.headers.get("access-control-allow-origin") == "http://localhost:5173"
    )
    allow_methods = response.headers.get("access-control-allow-methods", "")
    assert "POST" in allow_methods
    assert "OPTIONS" in allow_methods


# Verifies the 127.0.0.1 dev origin is also allowed — Vite picks one
# or the other depending on how the user navigates, and we want both.
def test_cors_preflight_from_127_origin_succeeds() -> None:
    response = client.options(
        "/api/v1/auth/login",
        headers={
            "Origin": "http://127.0.0.1:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert response.status_code == 200
    assert (
        response.headers.get("access-control-allow-origin") == "http://127.0.0.1:5173"
    )


# Verifies an unallowed origin does NOT receive the ACAO header — the
# browser interprets that absence as "deny" and aborts the request.
# Important so a future deployment that forgets to lock down origins
# fails noisily rather than silently allowing any caller through.
def test_cors_preflight_rejects_unknown_origin() -> None:
    response = client.options(
        "/api/v1/auth/login",
        headers={
            "Origin": "http://evil.example.com",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    # Starlette returns 400 for a preflight whose origin isn't on the
    # allowlist (or no ACAO header at all on a permissive policy).
    assert response.headers.get("access-control-allow-origin") is None


# Verifies CORS headers appear on actual responses (not just preflight).
# A successful simple GET from an allowed origin must come back with
# the ACAO header so the fetch promise resolves in the browser.
def test_cors_actual_request_carries_allow_origin() -> None:
    response = client.get(
        "/health",
        headers={"Origin": "http://localhost:5173"},
    )
    assert response.status_code == 200
    assert (
        response.headers.get("access-control-allow-origin") == "http://localhost:5173"
    )
