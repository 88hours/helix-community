#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# railway-deploy.sh — build, push, and deploy Helix to Railway
#
# Usage:
#   ./railway-deploy.sh                    # build + push + redeploy
#   ./railway-deploy.sh --env-file .env    # sync .env vars, then deploy
#   ./railway-deploy.sh --no-build         # skip docker build, just redeploy
#   ./railway-deploy.sh --env-only         # sync .env vars only, no deploy
#
# First-time setup (run once in Railway dashboard):
#   Service → Settings → Source → Docker Image → set to $HELIX_IMAGE
#
# Prerequisites:
#   - HELIX_IMAGE exported (or set in .env), e.g. yourname/helix-community
#   - docker login
#   - railway login && railway link
# ---------------------------------------------------------------------------
set -euo pipefail

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------

ENV_FILE=""
ENV_ONLY=false
NO_BUILD=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file)
      shift
      ENV_FILE="${1:-}"
      [[ -z "$ENV_FILE" ]] && { echo "error: --env-file requires a path" >&2; exit 1; }
      shift
      ;;
    --env-only) ENV_ONLY=true; shift ;;
    --no-build) NO_BUILD=true; shift ;;
    --*) echo "error: unknown flag '$1'" >&2; exit 1 ;;
    *) echo "error: unexpected argument '$1'" >&2; exit 1 ;;
  esac
done

# Load .env file into the local shell environment so variables like HELIX_IMAGE
# are available for the preflight check below, before they are synced to Railway.
if [[ -n "$ENV_FILE" ]]; then
  [[ ! -f "$ENV_FILE" ]] && { echo "error: file not found: $ENV_FILE" >&2; exit 1; }
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
    line="${line#export }"
    [[ "$line" != *=* ]] && continue
    key="${line%%=*}"
    key="${key#"${key%%[![:space:]]*}"}"  # trim leading whitespace
    key="${key%"${key##*[![:space:]]}"}"  # trim trailing whitespace
    val="${line#*=}"
    [[ "$val" =~ ^\"(.*)\"$ ]] && val="${BASH_REMATCH[1]}"
    [[ "$val" =~ ^\'(.*)\'$ ]] && val="${BASH_REMATCH[1]}"
    val="${val%%#*}"                      # strip inline comments
    val="${val%"${val##*[![:space:]]}"}"  # trim trailing whitespace
    [[ -n "$val" ]] && export "$key=$val"
  done < "$ENV_FILE"
fi

IMAGE="${HELIX_IMAGE:-}"
TAG="${HELIX_IMAGE_TAG:-latest}"
SERVICE="${HELIX_SERVICE:-helix}"

# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------

if [[ -z "$IMAGE" ]]; then
  echo "error: HELIX_IMAGE is not set." >&2
  echo "  export HELIX_IMAGE=yourname/helix-community" >&2
  exit 1
fi

if ! railway status &>/dev/null; then
  echo "error: not linked to a Railway project — run 'railway link' first" >&2
  exit 1
fi

PROJECT=$(railway status | awk '/Project:/ {print $2}')
ENV=$(railway status | awk '/Environment:/ {print $2}')
echo "Project:     $PROJECT"
echo "Environment: $ENV"
echo "Image:       ${IMAGE}:${TAG}"
echo "Service:     $SERVICE"
echo ""

# ---------------------------------------------------------------------------
# Build and push
# ---------------------------------------------------------------------------

if [[ "$NO_BUILD" == false && "$ENV_ONLY" == false ]]; then
  echo "──────────────────────────────────────"
  echo "Building ${IMAGE}:${TAG}"
  echo ""
  docker buildx build --platform linux/amd64 -t "${IMAGE}:${TAG}" --push .
  echo "✓ built and pushed"
  echo ""
fi

# ---------------------------------------------------------------------------
# Ensure service exists
# ---------------------------------------------------------------------------

add_output=$(railway add --service "$SERVICE" 2>&1 || true)
if echo "$add_output" | grep -qi "already exists"; then
  echo "Service $SERVICE already exists"
else
  echo "Service $SERVICE created"
fi
echo ""

# ---------------------------------------------------------------------------
# Sync .env → Railway variables
# ---------------------------------------------------------------------------

if [[ -n "$ENV_FILE" ]]; then
  echo "──────────────────────────────────────"
  echo "Syncing ${ENV_FILE} → Railway"
  echo ""

  [[ ! -f "$ENV_FILE" ]] && { echo "error: file not found: $ENV_FILE" >&2; exit 1; }

  pairs=()
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
    line="${line#export }"
    [[ "$line" != *=* ]] && continue
    key="${line%%=*}"
    key="${key#"${key%%[![:space:]]*}"}"  # trim leading whitespace
    key="${key%"${key##*[![:space:]]}"}"  # trim trailing whitespace
    val="${line#*=}"
    [[ "$val" =~ ^\"(.*)\"$ ]] && val="${BASH_REMATCH[1]}"
    [[ "$val" =~ ^\'(.*)\'$ ]] && val="${BASH_REMATCH[1]}"
    val="${val%%#*}"                      # strip inline comments
    val="${val%"${val##*[![:space:]]}"}"  # trim trailing whitespace
    [[ -z "$val" ]] && continue
    pairs+=("${key}=${val}")
  done < "$ENV_FILE"

  if [[ ${#pairs[@]} -gt 0 ]]; then
    railway variable set --service "$SERVICE" --skip-deploys "${pairs[@]}"
    echo "  ✓ ${#pairs[@]} variable(s) synced"
  else
    echo "  no variables found"
  fi
  echo ""
fi

if [[ "$ENV_ONLY" == true ]]; then
  echo "Done (--env-only)."
  exit 0
fi

# ---------------------------------------------------------------------------
# Redeploy
# ---------------------------------------------------------------------------

echo "──────────────────────────────────────"
echo "Redeploying $SERVICE..."
redeploy_output=$(railway redeploy --service "$SERVICE" --yes 2>&1) && {
  echo "✓ redeployment triggered"
} || {
  if echo "$redeploy_output" | grep -qi "no deployment found"; then
    echo ""
    echo "No deployment found — first-time setup required."
    echo ""
    echo "In the Railway dashboard:"
    echo "  1. Open the '$SERVICE' service → Settings → Source"
    echo "  2. Switch to Docker Image"
    echo "  3. Enter: ${IMAGE}:${TAG}"
    echo "  4. Click Deploy"
    echo ""
    echo "After that, re-run this script to deploy future updates."
  else
    echo "$redeploy_output" >&2
    exit 1
  fi
}
echo ""

domain=$(railway domain --service "$SERVICE" 2>/dev/null | grep -oE '[a-zA-Z0-9.-]+\.up\.railway\.app' | head -1)
if [[ -n "$domain" ]]; then
  echo "Webhook URLs:"
  echo "  Rollbar → https://${domain}/webhook/rollbar"
  echo "  Sentry  → https://${domain}/webhook/sentry"
  echo ""
fi

echo "Logs:      railway logs --service $SERVICE"
echo "Dashboard: railway open"
