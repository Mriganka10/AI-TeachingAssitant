import json
from functools import lru_cache

from app.core.config import settings


@lru_cache
def sqs_client():
    import boto3

    return boto3.client("sqs", region_name=settings.aws_region)


def enqueue_agent_job(job_id: str) -> None:
    if not settings.agent_queue_url:
        raise RuntimeError("AGENT_QUEUE_URL is required when AGENT_EXECUTION_BACKEND=sqs.")
    sqs_client().send_message(
        QueueUrl=settings.agent_queue_url,
        MessageBody=json.dumps({"job_id": job_id}, separators=(",", ":")),
    )
