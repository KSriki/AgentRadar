"""
Async S3/MinIO client. Used by Scouts to dump raw artifacts (paper PDFs,
README dumps, RSS payloads) and by every other component to fetch them by URI.

We use aioboto3 here instead of plain boto3 to keep the entire data layer async.
"""

from __future__ import annotations

from typing import Any

import aioboto3

from agentradar_core import S3Settings, get_logger, settings

log = get_logger(__name__)


class S3Client:
    def __init__(self, cfg: S3Settings) -> None:
        self._cfg = cfg
        self._session = aioboto3.Session(
            aws_access_key_id=cfg.access_key.get_secret_value(),
            aws_secret_access_key=cfg.secret_key.get_secret_value(),
            region_name=cfg.region,
        )

    def _client_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"service_name": "s3"}
        if self._cfg.endpoint_url:
            kwargs["endpoint_url"] = self._cfg.endpoint_url
        return kwargs

    async def put_artifact(
        self, key: str, body: bytes, content_type: str = "application/octet-stream"
    ) -> str:
        """Store bytes at key. Returns the s3:// URI for use as raw_artifact_uri."""
        async with self._session.client(**self._client_kwargs()) as s3:
            await s3.put_object(
                Bucket=self._cfg.bucket,
                Key=key,
                Body=body,
                ContentType=content_type,
            )
        uri = f"s3://{self._cfg.bucket}/{key}"
        log.info("s3.put", uri=uri, size=len(body))
        return uri

    async def get_artifact(self, key: str) -> bytes:
        async with self._session.client(**self._client_kwargs()) as s3:
            resp = await s3.get_object(Bucket=self._cfg.bucket, Key=key)
            async with resp["Body"] as stream:
                return await stream.read()

    async def healthcheck(self) -> bool:
        try:
            async with self._session.client(**self._client_kwargs()) as s3:
                await s3.head_bucket(Bucket=self._cfg.bucket)
            return True
        except Exception as exc:
            log.warning("s3.healthcheck_failed", error=str(exc))
            return False


_singleton: S3Client | None = None


def get_s3_client() -> S3Client:
    global _singleton
    if _singleton is None:
        _singleton = S3Client(settings.s3)
    return _singleton