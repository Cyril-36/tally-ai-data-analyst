"""Run the backend over loopback HTTP with a disposable metadata store."""

import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
PYTHON = BACKEND / ".venv" / "bin" / "python"
PASSWORD = "temporary-demo-password-123"  # noqa: S105 - disposable local fixture
CSV = b"region,revenue\nWest,10\nEast,20\nWest,5\n"
SPEC = {
    "base_dataset": "sales",
    "metrics": [
        {
            "name": "revenue",
            "agg": "sum",
            "expr": {"kind": "col", "ref": {"dataset": "sales", "column": "revenue"}},
        }
    ],
    "dimensions": [{"dataset": "sales", "column": "region"}],
    "sort": [{"by": "region", "descending": False}],
}


def _port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _check(base: str) -> None:
    clients = {
        name: httpx.Client(base_url=base, headers={"X-Requested-With": "fetch"})
        for name in ("analyst1", "analyst2", "admin")
    }
    try:
        for name, client in clients.items():
            response = client.post(
                "/api/auth/login", json={"email": f"{name}@example.com", "password": PASSWORD}
            )
            assert response.status_code == 200, (name, response.text)
        owner, other, admin = (clients[name] for name in ("analyst1", "analyst2", "admin"))
        upload = owner.post("/api/datasets", files={"files": ("sales.csv", CSV, "text/csv")})
        assert upload.status_code == 201, upload.text
        dataset_id = upload.json()[0]["id"]
        for client in (other, admin):
            assert client.get(f"/api/datasets/{dataset_id}").status_code == 404
        assert owner.get(f"/api/datasets/{dataset_id}/quality").status_code == 200
        assert owner.get("/api/admin/settings").status_code == 403
        assert admin.get("/api/admin/settings").status_code == 200

        created = owner.post(
            "/api/conversations", json={"title": "Smoke", "dataset_ids": [dataset_id]}
        )
        assert created.status_code == 201, created.text
        conversation_id = created.json()["id"]
        analyzed = owner.post(
            f"/api/conversations/{conversation_id}/analyze",
            json={"question": "Revenue by region", "spec": SPEC, "request_key": "smoke-analysis"},
        )
        assert analyzed.status_code == 200, analyzed.text
        result = analyzed.json()
        assert result["table"]["rows"] == [["East", 20.0], ["West", 15.0]]
        assert result["answer_sentences"] and result["meta"]["sql"]
        assert owner.get(f"/api/results/{result['id']}/export?format=csv").status_code == 200
        assert other.get(f"/api/results/{result['id']}/export?format=html").status_code == 404
        assert owner.get(f"/api/results/{result['id']}").headers["cache-control"] == "no-store"
    finally:
        for client in clients.values():
            client.close()


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="analyst-http-smoke-") as data_dir:
        env = dict(os.environ, DATA_DIR=data_dir, DEMO_PASSWORD=PASSWORD)
        subprocess.run(  # noqa: S603 - fixed executable and argument list
            [str(PYTHON), "-m", "app.cli", "seed-demo"],
            cwd=BACKEND,
            env=env,
            check=True,
            capture_output=True,
        )
        port = _port()
        base = f"http://127.0.0.1:{port}"
        with (Path(data_dir) / "server.log").open("w+") as log:
            server = subprocess.Popen(  # noqa: S603 - fixed executable and argument list
                [
                    str(PYTHON),
                    "-m",
                    "uvicorn",
                    "app.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                ],
                cwd=BACKEND,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            try:
                for _ in range(40):
                    try:
                        if httpx.get(base + "/api/ready", timeout=1).status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.25)
                else:
                    raise RuntimeError("The disposable HTTP server did not become ready")
                _check(base)
                log.flush()
                assert '"status": 500' not in (Path(data_dir) / "server.log").read_text()
                print("HTTP smoke passed: auth, upload, quality, analysis, export, isolation")
            finally:
                server.terminate()
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait()


if __name__ == "__main__":
    sys.exit(main())
