#!/usr/bin/env bash
# scaffold.sh — Create AgentRadar monorepo structure in the current directory.
# Safe to run multiple times: skips files/dirs that already exist.

set -euo pipefail

# Sanity check: warn if we're not in a git repo (you said you ran `git init`)
if [[ ! -d .git ]]; then
  echo "Warning: no .git/ found in $(pwd). Continue? [y/N]"
  read -r reply
  [[ "$reply" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 1; }
fi

# Helper: create file only if missing
mkfile() {
  if [[ -e "$1" ]]; then
    echo "  skip  $1 (exists)"
  else
    mkdir -p "$(dirname "$1")"
    touch "$1"
    echo "  new   $1"
  fi
}

echo "Scaffolding AgentRadar in: $(pwd)"
echo

# ---------- top-level ----------
echo "[top-level]"
for f in pyproject.toml .python-version README.md .env.example .gitignore docker-compose.yml; do
  mkfile "$f"
done

# ---------- infra ----------
echo "[infra]"
mkfile infra/neo4j/init/01_schema.cypher
mkfile infra/postgres/init/01_schema.sql
mkfile infra/init-runner/Dockerfile
mkfile infra/init-runner/run-init.sh
[[ -f infra/init-runner/run-init.sh ]] && chmod +x infra/init-runner/run-init.sh

# ---------- packages/agentradar-core ----------
echo "[packages/agentradar-core]"
mkfile packages/agentradar-core/pyproject.toml
mkfile packages/agentradar-core/src/agentradar_core/__init__.py
mkfile packages/agentradar-core/src/agentradar_core/config.py
mkfile packages/agentradar-core/src/agentradar_core/types.py
mkfile packages/agentradar-core/src/agentradar_core/logging.py

# ---------- packages/agentradar-store ----------
echo "[packages/agentradar-store]"
mkfile packages/agentradar-store/pyproject.toml
mkfile packages/agentradar-store/src/agentradar_store/__init__.py
mkfile packages/agentradar-store/src/agentradar_store/neo4j_client.py
mkfile packages/agentradar-store/src/agentradar_store/pg_client.py
mkfile packages/agentradar-store/src/agentradar_store/s3_client.py
mkfile packages/agentradar-store/src/agentradar_store/embeddings.py

# ---------- services/mcp-server ----------
echo "[services/mcp-server]"
mkfile services/mcp-server/pyproject.toml
mkfile services/mcp-server/src/agentradar_mcp/__init__.py
mkfile services/mcp-server/src/agentradar_mcp/server.py
mkfile services/mcp-server/src/agentradar_mcp/__main__.py

# ---------- services/supervisor ----------
echo "[services/supervisor]"
mkfile services/supervisor/pyproject.toml
mkfile services/supervisor/src/agentradar_supervisor/__init__.py
mkfile services/supervisor/src/agentradar_supervisor/graph.py
mkfile services/supervisor/src/agentradar_supervisor/agents/__init__.py
mkfile services/supervisor/src/agentradar_supervisor/agents/scout.py
mkfile services/supervisor/src/agentradar_supervisor/agents/extractor.py
mkfile services/supervisor/src/agentradar_supervisor/agents/novelty.py
mkfile services/supervisor/src/agentradar_supervisor/agents/critic.py
mkfile services/supervisor/src/agentradar_supervisor/agents/forecaster.py
mkfile services/supervisor/src/agentradar_supervisor/agents/calibrator.py

# ---------- services/api ----------
echo "[services/api]"
mkfile services/api/pyproject.toml
mkfile services/api/src/agentradar_api/__init__.py
mkfile services/api/src/agentradar_api/main.py

# ---------- apps/dashboard (placeholder) ----------
echo "[apps/dashboard]"
mkfile apps/dashboard/.gitkeep

# ---------- tests ----------
echo "[tests]"
mkfile tests/integration/.gitkeep
mkfile tests/unit/.gitkeep

echo
echo "Tree:"
if command -v tree >/dev/null 2>&1; then
  tree -a -I '.git|node_modules|__pycache__|.venv' --dirsfirst
else
  find . -not -path '*/\.git/*' -not -path '*/__pycache__/*' -not -path '*/.venv/*' | sort
fi

echo
echo "Done."