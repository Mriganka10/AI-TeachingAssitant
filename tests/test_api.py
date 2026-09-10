from pathlib import Path

from fastapi.testclient import TestClient

from app.api import routes
from app.core.auth import auth_service
from app.core.config import settings
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
        teaching_job = client.get(f"/api/jobs/{teaching.json()['job_id']}")
        assert teaching_job.status_code == 200
        assert teaching_job.json()["status"] == "completed"
        assert {a["type"] for a in teaching_job.json()["artifacts"]} == {"json", "docx", "pdf", "pptx"}
        research = client.post(
            "/api/agents/research",
            json={"research_topic": "Responsible AI adoption", "use_web_search": False},
        )
        assert research.status_code == 200
        research_job = client.get(f"/api/jobs/{research.json()['job_id']}")
        assert research_job.status_code == 200
        assert research_job.json()["result"]["research_gaps"]
        assert {a["type"] for a in research_job.json()["artifacts"]} == {
            "json",
            "docx",
            "pdf",
            "pptx",
        }
        jobs = client.get("/api/jobs")
        assert jobs.status_code == 200
        assert jobs.json()[0]["title"] == "Responsible AI adoption"
        assert Path("data").exists()


def test_new_user_email_verification_starts_ses_identity(monkeypatch) -> None:
    created: list[str] = []

    class FakeSesClient:
        def get_email_identity(self, **kwargs):
            return {"VerificationStatus": "NOT_STARTED"}

        def create_email_identity(self, **kwargs):
            created.append(kwargs["EmailIdentity"])
            return {}

    monkeypatch.setattr(settings, "email_provider", "ses")
    monkeypatch.setattr(settings, "otp_dev_mode", False)
    monkeypatch.setattr(settings, "ses_from", "no-reply@professoraihub.com")
    monkeypatch.setattr(auth_service, "_ses_client", lambda: FakeSesClient())

    with TestClient(app) as client:
        response = client.post("/api/auth/register-email", json={"email": "new-prof@example.edu"})

    assert response.status_code == 200
    assert response.json()["status"] == "verification_required"
    assert created == ["new-prof@example.edu"]


def test_unverified_ses_user_must_register_before_otp(monkeypatch) -> None:
    created: list[str] = []

    class FakeSesClient:
        def get_email_identity(self, **kwargs):
            return {"VerificationStatus": "NOT_STARTED"}

        def create_email_identity(self, **kwargs):
            created.append(kwargs["EmailIdentity"])
            return {}

    monkeypatch.setattr(settings, "email_provider", "ses")
    monkeypatch.setattr(settings, "otp_dev_mode", False)
    monkeypatch.setattr(settings, "ses_from", "no-reply@professoraihub.com")
    monkeypatch.setattr(auth_service, "_ses_client", lambda: FakeSesClient())

    with TestClient(app) as client:
        response = client.post("/api/auth/request-otp", json={"email": "pending-prof@example.edu"})

    assert response.status_code == 403
    assert "New User Registration" in response.json()["detail"]
    assert created == []


def test_verified_ses_user_receives_otp(monkeypatch) -> None:
    sent: list[dict] = []

    class FakeSesClient:
        def get_email_identity(self, **kwargs):
            return {"VerificationStatus": "SUCCESS"}

        def send_email(self, **kwargs):
            sent.append(kwargs)
            return {}

    monkeypatch.setattr(settings, "email_provider", "ses")
    monkeypatch.setattr(settings, "otp_dev_mode", False)
    monkeypatch.setattr(settings, "ses_from", "no-reply@professoraihub.com")
    monkeypatch.setattr(auth_service, "_ses_client", lambda: FakeSesClient())

    with TestClient(app) as client:
        response = client.post("/api/auth/request-otp", json={"email": "verified-prof@example.edu"})

    assert response.status_code == 200
    assert response.json()["status"] == "sent"
    assert response.json()["delivery"] == "email"
    assert sent[0]["FromEmailAddress"] == "no-reply@professoraihub.com"
    assert sent[0]["Destination"] == {"ToAddresses": ["verified-prof@example.edu"]}


def test_register_page_contains_first_time_verification_view() -> None:
    with TestClient(app) as client:
        response = client.get("/register")

    assert response.status_code == 200
    assert 'id="email-register"' in response.text
    assert "Send verification link" in response.text


def test_agent_failure_returns_json_detail(monkeypatch) -> None:
    def fail_generate(**kwargs):
        raise RuntimeError("simulated upstream failure")

    monkeypatch.setattr(routes.llm, "generate", fail_generate)

    with TestClient(app) as client:
        login(client)
        response = client.post(
            "/api/agents/teaching",
            json={"topic": "Responsible AI", "use_web_search": False},
        )

        failed_job = client.get(f"/api/jobs/{response.json()['job_id']}")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert failed_job.json()["status"] == "failed"
    assert failed_job.json()["error"] == "simulated upstream failure"


def test_frontend_handles_non_json_api_responses() -> None:
    script = Path("app/static/app.js").read_text()

    assert "parseApiResponse" in script
    assert "nonJsonApiMessage" in script
    assert "HTML page instead of agent data" in script
    assert "waitForJob" in script
    assert "/api/jobs/" in script


def test_sqs_backend_enqueues_existing_job_without_running_inline(monkeypatch) -> None:
    queued: list[str] = []

    monkeypatch.setattr(settings, "agent_execution_backend", "sqs")
    monkeypatch.setattr(routes, "enqueue_agent_job", queued.append)

    with TestClient(app) as client:
        login(client)
        response = client.post(
            "/api/agents/teaching",
            json={"topic": "Durable queues", "use_web_search": False},
        )
        job = client.get(f"/api/jobs/{response.json()['job_id']}")

    assert response.status_code == 200
    assert response.json()["status"] == "running"
    assert queued == [response.json()["job_id"]]
    assert job.json()["status"] == "running"
