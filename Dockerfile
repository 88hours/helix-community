FROM python:3.12-slim

# ---------------------------------------------------------------------------
# System dependencies
# ---------------------------------------------------------------------------
# git       — Dev Agent clones the target repo before invoking claude CLI
# curl/ca   — NodeSource setup script + TLS
# nodejs    — required to run the Claude Code CLI (Dev Agent, claude-code provider)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    ca-certificates \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# Claude Code CLI
# ---------------------------------------------------------------------------
# Installed globally so `claude` is on PATH when the Dev Agent runs
# `claude -p "<prompt>"` inside the cloned repo.
RUN npm install -g @anthropic-ai/claude-code

# ---------------------------------------------------------------------------
# Non-root user
# ---------------------------------------------------------------------------
# Claude Code CLI refuses --dangerously-skip-permissions when run as root.
RUN useradd --create-home --shell /bin/bash helix

# ---------------------------------------------------------------------------
# Python application
# ---------------------------------------------------------------------------
WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir -e "."

COPY . .
RUN chown -R helix:helix /app && chmod +x /app/entrypoint.sh

USER helix

# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------
EXPOSE 8000

# entrypoint.sh reads START_COMMAND from the environment.
# Set START_COMMAND as a Railway env var per service, or leave unset to run
# all agents in one container.
# docker-compose overrides this via its own `command:` per service.
ENTRYPOINT ["/app/entrypoint.sh"]
