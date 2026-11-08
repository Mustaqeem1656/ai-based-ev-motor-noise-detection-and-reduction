from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_sessions_listing_works():
    r = client.get("/api/anc/sessions")
    assert r.status_code == 200 and isinstance(r.json()["sessions"], list)


def test_rejects_bad_paths_and_files():
    assert client.post("/api/anc/run/data/..%2F..%2Fetc").status_code == 404
    assert client.post("/api/anc/run/nope/x").status_code == 404
    assert client.get("/api/anc/file/data/x/secret.txt").status_code == 404
    assert client.get("/api/anc/file/data_fake/does_not_exist/anc_result.png").status_code == 404
