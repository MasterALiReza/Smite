"""
Integration tests verifying frontend static asset delivery, SPA routing, and API contracts.
"""
import pytest
from fastapi.testclient import TestClient
from main import app


def test_frontend_root_and_spa_routing():
    """Verify root and client routes serve index.html with SPA mount point."""
    with TestClient(app) as client:
        # Root route
        res = client.get("/")
        assert res.status_code == 200
        assert "<title>Smite - Tunneling Control Panel</title>" in res.text
        assert '<div id="root"></div>' in res.text

        # Client-side routes (SPA fallback)
        for route in ["/dashboard", "/nodes", "/servers", "/tunnels", "/logs", "/core-health", "/settings", "/login"]:
            res = client.get(route)
            assert res.status_code == 200
            assert '<div id="root"></div>' in res.text


def test_frontend_static_assets_and_security():
    """Verify favicon, robots.txt security rules, and static asset headers."""
    with TestClient(app) as client:
        # Favicon delivery
        res = client.get("/favicon.svg")
        assert res.status_code == 200
        assert "svg" in res.headers.get("content-type", "")

        # Robots.txt privacy / security barrier
        res = client.get("/robots.txt")
        assert res.status_code == 200
        assert "Disallow: /" in res.text


def test_backend_api_contracts():
    """Verify backend health, version, and auth contracts."""
    with TestClient(app) as client:
        # Health check
        res = client.get("/api/status/health")
        assert res.status_code == 200
        assert res.json() == {"status": "ok"}

        # Version endpoint
        res = client.get("/api/status/version")
        assert res.status_code == 200
        assert "version" in res.json()

        # Auth rejected contract
        res = client.post("/api/auth/login", json={"username": "invalid", "password": "wrong"})
        assert res.status_code == 401

        # Protected resource 401
        res = client.get("/api/nodes")
        assert res.status_code == 401
