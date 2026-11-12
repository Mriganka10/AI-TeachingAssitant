import json
import logging
import signal
from threading import Event

from app.api.routes import run_agent_job
from app.core.config import settings
from app.core.job_queue import sqs_client

logger = logging.getLogger(__name__)
shutdown = Event()


def process_message(body: str) -> None:
    payload = json.loads(body)
    job_id = str(payload.get("job_id") or "").strip()
    if not job_id:
        raise ValueError("Agent queue message is missing job_id.")
    run_agent_job(job_id)


def lambda_handler(event: dict, _context) -> dict:
    failures = []
    for record in event.get("Records", []):
        try:
            process_message(record.get("body", ""))
        except Exception:
            logger.exception("Agent queue record failed")
            failures.append({"itemIdentifier": record.get("messageId", "")})
    return {"batchItemFailures": failures}


def poll_forever() -> None:
    if not settings.agent_queue_url:
        raise RuntimeError("AGENT_QUEUE_URL is required for the worker process.")
    client = sqs_client()
    while not shutdown.is_set():
        response = client.receive_message(
            QueueUrl=settings.agent_queue_url,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=settings.agent_queue_wait_seconds,
            VisibilityTimeout=settings.agent_queue_visibility_timeout_seconds,
        )
        for message in response.get("Messages", []):
            try:
                process_message(message["Body"])
            except Exception:
                logger.exception("Agent job failed and will be retried")
                continue
            client.delete_message(
                QueueUrl=settings.agent_queue_url,
                ReceiptHandle=message["ReceiptHandle"],
            )


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    signal.signal(signal.SIGTERM, lambda *_: shutdown.set())
    signal.signal(signal.SIGINT, lambda *_: shutdown.set())
    poll_forever()


if __name__ == "__main__":
    main()
