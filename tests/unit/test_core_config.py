"""Unit tests for agentradar_core.config — pure pydantic, no network."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agentradar_core import (
    EmbeddingSettings,
    Neo4jSettings,
    Settings,
)


class TestNeo4jSettings:
    def test_defaults_when_no_env(self, clean_env: pytest.MonkeyPatch) -> None:
        s = Neo4jSettings()
        assert s.uri == "bolt://localhost:7687"
        assert s.user == "neo4j"
        # Default password should still be set (it's a dev default).
        assert s.password.get_secret_value() == "agentradar_dev"

    def test_env_override(self, clean_env: pytest.MonkeyPatch) -> None:
        clean_env.setenv("NEO4J_URI", "bolt://prod:7687")
        clean_env.setenv("NEO4J_USER", "produser")
        clean_env.setenv("NEO4J_PASSWORD", "supersecret")
        s = Neo4jSettings()
        assert s.uri == "bolt://prod:7687"
        assert s.user == "produser"
        assert s.password.get_secret_value() == "supersecret"

    def test_password_is_secret(self, clean_env: pytest.MonkeyPatch) -> None:
        """SecretStr should never expose the raw value via repr or str."""
        clean_env.setenv("NEO4J_PASSWORD", "supersecret")
        s = Neo4jSettings()
        assert "supersecret" not in repr(s)
        assert "supersecret" not in str(s)
        # But explicit access works:
        assert s.password.get_secret_value() == "supersecret"


class TestEmbeddingSettings:
    def test_provider_validation_rejects_unknown(
        self, clean_env: pytest.MonkeyPatch
    ) -> None:
        clean_env.setenv("EMBEDDING_PROVIDER", "not_a_real_provider")
        with pytest.raises(ValidationError):
            EmbeddingSettings()

    def test_provider_accepts_known(self, clean_env: pytest.MonkeyPatch) -> None:
        for provider in ("bedrock", "local"):
            clean_env.setenv("EMBEDDING_PROVIDER", provider)
            s = EmbeddingSettings()
            assert s.provider == provider

    def test_dim_is_int(self, clean_env: pytest.MonkeyPatch) -> None:
        clean_env.setenv("EMBEDDING_DIM", "768")
        s = EmbeddingSettings()
        assert s.dim == 768
        assert isinstance(s.dim, int)


class TestTopLevelSettings:
    def test_environment_validation(self, clean_env: pytest.MonkeyPatch) -> None:
        clean_env.setenv("ENVIRONMENT", "production")  # not in the literal!
        with pytest.raises(ValidationError):
            Settings()

    def test_environment_accepts_docker(self, clean_env: pytest.MonkeyPatch) -> None:
        clean_env.setenv("ENVIRONMENT", "docker")
        s = Settings()
        assert s.environment == "docker"

    def test_nested_settings_compose(self, clean_env: pytest.MonkeyPatch) -> None:
        """settings.neo4j.uri should be reachable, not just settings.neo4j_uri."""
        clean_env.setenv("NEO4J_URI", "bolt://nested:7687")
        s = Settings()
        assert s.neo4j.uri == "bolt://nested:7687"