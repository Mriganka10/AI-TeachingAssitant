from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


def login(client: TestClient) -> None:
    requested = client.post("/api/auth/request-otp", json={"email": "professor@example.edu"})
    assert requested.status_code == 200
    verified = client.post(
        "/api/auth/verify",
        json={"email": "professor@example.edu", "otp": requested.json()["dev_otp"]},
    )
    assert verified.status_code == 200


def test_health_and_auth() -> None:
    with TestClient(app) as client:
        assert client.get("/health").json()["status"] == "ok"
        login(client)
        assert client.get("/api/auth/me").json()["role"] == "professor"


def test_upload_and_both_agents() -> None:
    with TestClient(app) as client:
        login(client)
        upload = client.post(
            "/api/documents",
            data={"collection": "research_papers"},
            files={"file": ("paper.txt", b"Longitudinal evidence is limited.", "text/plain")},
        )
        assert upload.status_code == 200
        teaching = client.post(
            "/api/agents/teaching",
            json={"topic": "Responsible AI", "use_web_search": False},
        )
        assert teaching.status_code == 200
        assert {a["type"] for a in teaching.json()["artifacts"]} == {"json", "docx", "pdf", "pptx"}
        research = client.post(
            "/api/agents/research",
            json={"research_topic": "Responsible AI adoption", "use_web_search": False},
        )
        assert research.status_code == 200
        assert research.json()["result"]["research_gaps"]
        assert {a["type"] for a in research.json()["artifacts"]} == {
            "json",
            "docx",
            "pdf",
            "pptx",
        }
        jobs = client.get("/api/jobs")
        assert jobs.status_code == 200
        assert jobs.json()[0]["title"] == "Responsible AI adoption"
        assert Path("data").exists()
