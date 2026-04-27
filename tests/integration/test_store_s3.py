"""Integration tests against MinIO (S3-compatible). Run with: uv run pytest -m integration."""

from __future__ import annotations

import uuid

import pytest


pytestmark = pytest.mark.integration


@pytest.fixture
async def unique_key() -> str:
    """A unique key per test so tests don't collide if run in parallel."""
    return f"test/{uuid.uuid4().hex}.bin"


class TestS3Healthcheck:
    async def test_healthcheck_passes(self, s3_client) -> None:
        # Bucket must exist — the init-runner creates it on first compose up.
        assert await s3_client.healthcheck() is True


class TestPutAndGet:
    async def test_round_trip_bytes(self, s3_client, unique_key) -> None:
        payload = b"the rain in spain falls mainly on the plain"
        uri = await s3_client.put_artifact(
            unique_key, payload, content_type="text/plain"
        )
        assert uri == f"s3://{s3_client._cfg.bucket}/{unique_key}"

        got = await s3_client.get_artifact(unique_key)
        assert got == payload

    async def test_overwrite_replaces(self, s3_client, unique_key) -> None:
        await s3_client.put_artifact(unique_key, b"first")
        await s3_client.put_artifact(unique_key, b"second")
        assert await s3_client.get_artifact(unique_key) == b"second"

    async def test_binary_payload_unchanged(self, s3_client, unique_key) -> None:
        """No accidental encoding shenanigans on binary content."""
        payload = bytes(range(256))  # every byte value
        await s3_client.put_artifact(unique_key, payload)
        assert await s3_client.get_artifact(unique_key) == payload

    async def test_get_missing_key_raises(self, s3_client) -> None:
        from botocore.exceptions import ClientError
        with pytest.raises(ClientError) as exc_info:
            await s3_client.get_artifact(f"definitely-missing/{uuid.uuid4().hex}")
        assert exc_info.value.response["Error"]["Code"] == "NoSuchKey"