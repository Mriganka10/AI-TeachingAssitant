import hashlib
import shutil
from pathlib import Path

from app.core.config import settings


def safe_part(value: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "-" for c in value).strip("-") or "item"


class StorageService:
    def save(self, source: Path, *, tenant_id: str, category: str, object_id: str) -> str:
        filename = safe_part(source.name)
        key = (
            f"{settings.s3_prefix.strip('/')}/tenants/{safe_part(tenant_id)}/"
            f"{safe_part(category)}/{safe_part(object_id)}/{filename}"
        )
        if settings.storage_provider.lower() == "s3":
            if not settings.s3_bucket:
                raise RuntimeError("S3_BUCKET is required when STORAGE_PROVIDER=s3.")
            import boto3

            extra = {"ServerSideEncryption": "AES256"}
            if settings.s3_kms_key_id:
                extra = {
                    "ServerSideEncryption": "aws:kms",
                    "SSEKMSKeyId": settings.s3_kms_key_id,
                }
            boto3.client("s3", region_name=settings.aws_region).upload_file(
                str(source), settings.s3_bucket, key, ExtraArgs=extra
            )
            return f"s3://{settings.s3_bucket}/{key}"
        destination = settings.data_dir / key
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        return str(destination.resolve())

    def materialize(self, uri: str) -> Path:
        if not uri.startswith("s3://"):
            return Path(uri)
        bucket, key = uri[5:].split("/", 1)
        destination = settings.data_dir / "downloads" / safe_part(Path(key).name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        import boto3

        boto3.client("s3", region_name=settings.aws_region).download_file(
            bucket, key, str(destination)
        )
        return destination


def checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


storage = StorageService()
