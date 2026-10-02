from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app


def test_built_frontend_serves_deep_links_without_masking_api(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("<html>tally review</html>")
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "app.js").write_text("console.log('tally')")
    monkeypatch.setenv("FRONTEND_DIST", str(tmp_path))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as client:
            assert "tally review" in client.get("/").text
            assert "tally review" in client.get("/ask/example").text
            assert "console.log" in client.get("/assets/app.js").text
            assert client.get("/assets/missing.js").status_code == 404
            missing = client.get("/api/no-such-route")
            assert missing.status_code == 404
            assert missing.json()["outcome"] == "not_found"
    finally:
        get_settings.cache_clear()
