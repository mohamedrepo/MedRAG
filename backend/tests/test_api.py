"""API smoke tests via TestClient (no network)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_login_bad(client):
    r = client.post("/api/v1/auth/login", json={"username": "nobody", "password": "x"})
    assert r.status_code == 401


def test_login_ok(client):
    r = client.post("/api/v1/auth/login", json={"username": "admin", "password": "medrag-admin"})
    assert r.status_code == 200
    body = r.json()
    assert body["access_token"]
    assert body["role"] == "admin"


def test_search_cbc(client):
    r = client.get("/api/v1/search", params={"q": "CBC"})
    assert r.status_code == 200
    j = r.json()
    assert j["interpretation"]["canonical_code"] == "LAB-000001"
    assert j["interpretation"]["confidence"] >= 0.99


def test_search_grouped_prices(client):
    r = client.get("/api/v1/search", params={"q": "CT Brain", "group": True})
    assert r.status_code == 200
    j = r.json()
    assert j["interpretation"]["canonical_code"] == "RAD-000001"
    assert isinstance(j.get("groups"), list)


def test_resolve_batch(client):
    r = client.post("/api/v1/search/resolve", json={"names": ["C.B.C", "c-b-c", "صورة دم كاملة"]})
    assert r.status_code == 200
    res = r.json()["results"]
    assert len(res) == 3
    codes = {x["canonical_code"] for x in res}
    assert codes == {"LAB-000001"}


def test_suggest(client):
    r = client.get("/api/v1/search/suggest", params={"q": "compl"})
    assert r.status_code == 200
    assert len(r.json()) >= 1


def test_stats(client):
    r = client.get("/api/v1/stats")
    assert r.status_code == 200
    assert r.json()["examinations"] > 100


def test_admin_requires_auth(client):
    r = client.get("/api/v1/admin/reviews")
    assert r.status_code in (401, 403)


def test_upload_rejects_bad_ext(client):
    # login
    tok = client.post("/api/v1/auth/login", json={"username": "admin", "password": "medrag-admin"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {tok}"}
    r = client.post("/api/v1/documents/upload", files={"file": ("evil.exe", b"MZ...", "application/x-exe")}, headers=headers)
    assert r.status_code == 400
