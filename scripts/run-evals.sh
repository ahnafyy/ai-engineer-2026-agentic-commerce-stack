#!/usr/bin/env zsh

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$ROOT/.venv"
ENV_FILE="$ROOT/.env"
SUITE="${1:-all}"

# ── Colors ────────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'

log()  { echo -e "${CYAN}[evals]${NC} $1"; }
ok()   { echo -e "${GREEN}[ok]${NC}    $1"; }
warn() { echo -e "${YELLOW}[warn]${NC}  $1"; }
err()  { echo -e "${RED}[error]${NC} $1"; exit 1; }

# ── Load .env ─────────────────────────────────────────────────────────────────
if [[ -f "$ENV_FILE" ]]; then
  set -a; source "$ENV_FILE"; set +a
  ok "Loaded $ENV_FILE"
else
  warn ".env not found — CEREBRAS_API_KEY may be unset (quality suite will fail)"
fi

if [[ -z "$CEREBRAS_API_KEY" ]]; then
  warn "CEREBRAS_API_KEY is not set — quality suite will fail"
fi

# ── Check stack is up ─────────────────────────────────────────────────────────
log "Checking stack is running..."
curl -sf http://localhost:8002/status  > /dev/null 2>&1 || err "catalog-sync not reachable on :8002 — run ./scripts/app-start.sh first"
curl -sf http://localhost:8001/        > /dev/null 2>&1 || err "mcp-server not reachable on :8001 — run ./scripts/app-start.sh first"
curl -sf http://localhost:10999/.well-known/agent-card.json > /dev/null 2>&1 || err "merchant-agent not reachable on :10999 — run ./scripts/app-start.sh first"
ok "All services reachable"

# ── Install eval deps into shared venv ────────────────────────────────────────
if [[ ! -f "$VENV/bin/python" ]]; then
  log "Creating Python venv..."
  python3 -m venv "$VENV"
fi

log "Installing eval dependencies..."
"$VENV/bin/pip" install -q pytest httpx "openai>=1.30.0" rich "typer>=0.12.0"
ok "Eval deps ready"

# ── Run evals ─────────────────────────────────────────────────────────────────
log "Running suite: $SUITE"
cd "$ROOT/evals"
AGENT_URL=http://localhost:10999 \
MCP_URL=http://localhost:8001 \
CATALOG_SYNC_URL=http://localhost:8002 \
CEREBRAS_API_KEY="$CEREBRAS_API_KEY" \
CEREBRAS_MODEL="${CEREBRAS_MODEL:-gpt-oss-120b}" \
  "$VENV/bin/python" run_evals.py --suite "$SUITE"
