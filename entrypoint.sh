#!/bin/sh
# Entrypoint for all Helix agent containers.
#
# Priority order for the command to run:
#   1. Arguments passed directly (docker-compose `command:`) — exec "$@"
#   2. START_COMMAND environment variable                    — exec sh -c "$START_COMMAND"
#   3. Neither set — run all four agents in one container    (Railway single-service mode)

# If REDIS_URL points to localhost or 127.0.0.1, redirect to the Docker
# redis service. Inside a container, localhost refers to the container
# itself — not the Redis container on the Docker network.
if echo "${REDIS_URL:-}" | grep -qE "(localhost|127\.0\.0\.1)"; then
  export REDIS_URL="redis://redis:6379"
  echo "[helix] REDIS_URL pointed to localhost — redirected to redis://redis:6379"
fi

# docker-compose `command:` passes its value as arguments to this script.
if [ $# -gt 0 ]; then
  echo "[helix] start: $*"
  exec "$@"
fi

if [ -n "${START_COMMAND:-}" ]; then
  echo "[helix] start: ${START_COMMAND}"
  exec sh -c "$START_COMMAND"
fi

echo "[helix] START_COMMAND not set — starting all agents"
for cmd in \
  "uvicorn agents.crash_handler.main:app --host 0.0.0.0 --port ${PORT:-8000}" \
  "python -m agents.qa.main" \
  "python -m agents.dev.main" \
  "python -m agents.notifier.main"; do
  sh -c "$cmd" &
  echo "[helix] started (pid $!): $cmd"
done

wait -n 2>/dev/null || wait
echo "[helix] an agent exited — shutting down"
exit 1
