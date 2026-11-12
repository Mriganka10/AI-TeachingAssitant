import json

import pytest

from app import worker


def test_process_message_runs_named_job(monkeypatch) -> None:
    processed: list[str] = []
    monkeypatch.setattr(worker, "run_agent_job", processed.append)

    worker.process_message(json.dumps({"job_id": "job-123"}))

    assert processed == ["job-123"]


def test_process_message_rejects_missing_job_id() -> None:
    with pytest.raises(ValueError, match="missing job_id"):
        worker.process_message("{}")


def test_lambda_handler_reports_only_failed_records(monkeypatch) -> None:
    def process(body: str) -> None:
        if body == "bad":
            raise RuntimeError("retry")

    monkeypatch.setattr(worker, "process_message", process)
    event = {
        "Records": [
            {"messageId": "ok-id", "body": "ok"},
            {"messageId": "bad-id", "body": "bad"},
        ]
    }

    assert worker.lambda_handler(event, None) == {
        "batchItemFailures": [{"itemIdentifier": "bad-id"}]
    }
