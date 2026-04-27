"""
Typed configuration loaded from environment variables (and .env file).

Usage:
    from agentradar_core import settings
    print(settings.neo4j.uri)

Validation happens at import time — if a required var is missing or has the
wrong type, the process fails fast with a clear error. This is what we want
in an autonomous system: never start running with a broken config.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Neo4jSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NEO4J_", extra="ignore")

    uri: str = "bolt://localhost:7687"
    user: str = "neo4j"
    password: SecretStr = SecretStr("agentradar_dev")


class PostgresSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="POSTGRES_", extra="ignore")

    dsn: SecretStr = SecretStr(
        "postgresql://agentradar:agentradar_dev@localhost:5432/agentradar"
    )


class S3Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="S3_", extra="ignore")

    endpoint_url: str | None = "http://localhost:9000"
    access_key: SecretStr = SecretStr("agentradar")
    secret_key: SecretStr = SecretStr("agentradar_dev")
    bucket: str = "agentradar-artifacts"
    region: str = "us-east-1"


class BedrockSettings(BaseSettings):
    """Settings for Claude on AWS Bedrock + Titan embeddings."""

    model_config = SettingsConfigDict(extra="ignore")

    aws_region: str = Field(default="us-east-1", alias="AWS_REGION")
    model_id: str = Field(
        default="anthropic.claude-sonnet-4-20250514-v1:0",
        alias="BEDROCK_MODEL_ID",
    )
    critic_model_id: str = Field(
        default="anthropic.claude-opus-4-20250514-v1:0",
        alias="BEDROCK_CRITIC_MODEL_ID",
    )


class EmbeddingSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EMBEDDING_", extra="ignore")

    provider: Literal["bedrock", "local"] = "bedrock"
    model_id: str = "amazon.titan-embed-text-v2:0"
    dim: int = 1024


class Settings(BaseSettings):
    """
    Top-level settings. Composed of nested groups so callers can
    pass settings.neo4j to a Neo4j client constructor without
    plumbing every individual field.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    environment: Literal["local", "docker", "dev", "staging", "prod"] = Field(
        default="local", alias="ENVIRONMENT"
    )
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(
        default="INFO", alias="LOG_LEVEL"
    )

    neo4j: Neo4jSettings = Field(default_factory=Neo4jSettings)
    postgres: PostgresSettings = Field(default_factory=PostgresSettings)
    s3: S3Settings = Field(default_factory=S3Settings)
    bedrock: BedrockSettings = Field(default_factory=BedrockSettings)
    embedding: EmbeddingSettings = Field(default_factory=EmbeddingSettings)


@lru_cache(maxsize=1)
def _load() -> Settings:
    """Cached so we don't re-parse .env on every import."""
    return Settings()


# Module-level singleton. Importers do: from agentradar_core import settings
settings: Settings = _load()