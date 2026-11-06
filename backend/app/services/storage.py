"""Object storage adapters for original PDF files.

Local storage is the default for development. The S3 adapter implements the same
small interface so the ingestion pipeline never knows where bytes live.
"""

from __future__ import annotations

import os
import tempfile
import uuid
from io import BytesIO
from pathlib import Path
from typing import BinaryIO, Protocol, cast

from app.core.config import get_settings


class StorageError(RuntimeError):
    """Raised when an object cannot be read or written."""


class Storage(Protocol):
    def put(self, key: str, data: bytes) -> str: ...

    def open(self, key: str) -> BinaryIO: ...

    def delete(self, key: str) -> None: ...

    def exists(self, key: str) -> bool: ...

    def signed_url(self, key: str, ttl_seconds: int = 300) -> str: ...


def document_key(course_id: uuid.UUID, document_id: uuid.UUID) -> str:
    return f"documents/{course_id}/{document_id}.pdf"


class LocalStorage:
    """Filesystem-backed storage rooted at ``UPLOAD_DIR``."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        candidate = (self.root / key).resolve()
        if not candidate.is_relative_to(self.root):
            raise StorageError("Invalid storage key")
        return candidate

    def put(self, key: str, data: bytes) -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=path.parent, prefix=".upload-", delete=False
            ) as handle:
                temporary = handle.name
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except OSError as exc:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)
            raise StorageError("Could not write the uploaded file") from exc
        return key

    def open(self, key: str) -> BinaryIO:
        try:
            return self._path(key).open("rb")
        except OSError as exc:
            raise StorageError("Could not read the stored file") from exc

    def delete(self, key: str) -> None:
        try:
            self._path(key).unlink(missing_ok=True)
        except OSError as exc:
            raise StorageError("Could not delete the stored file") from exc

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def signed_url(self, key: str, ttl_seconds: int = 300) -> str:
        del ttl_seconds
        return self._path(key).as_uri()


class S3Storage:
    """S3-compatible storage using presigned URLs for direct downloads."""

    def __init__(self, *, bucket: str, region: str | None = None) -> None:
        if not bucket:
            raise StorageError("S3_BUCKET is required when STORAGE_BACKEND=s3")
        import boto3

        self.bucket = bucket
        self._client = boto3.client("s3", region_name=region or None)

    def put(self, key: str, data: bytes) -> str:
        try:
            self._client.upload_fileobj(
                BytesIO(data),
                self.bucket,
                key,
                ExtraArgs={"ContentType": "application/pdf"},
            )
        except Exception as exc:  # boto3 has several transport-specific errors
            raise StorageError("Could not upload the file to object storage") from exc
        return key

    def open(self, key: str) -> BinaryIO:
        try:
            response = self._client.get_object(Bucket=self.bucket, Key=key)
            return cast(BinaryIO, response["Body"])
        except Exception as exc:
            raise StorageError("Could not read the stored file") from exc

    def delete(self, key: str) -> None:
        try:
            self._client.delete_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            raise StorageError("Could not delete the stored file") from exc

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:
            return False

    def signed_url(self, key: str, ttl_seconds: int = 300) -> str:
        try:
            return cast(
                str,
                self._client.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": self.bucket, "Key": key},
                    ExpiresIn=ttl_seconds,
                ),
            )
        except Exception as exc:
            raise StorageError("Could not create a download URL") from exc


def get_storage() -> Storage:
    settings = get_settings()
    if settings.storage_backend == "s3":
        return S3Storage(bucket=settings.s3_bucket, region=settings.s3_region or None)
    return LocalStorage(settings.upload_dir)
