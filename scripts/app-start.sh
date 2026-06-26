#!/usr/bin/env zsh

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$ROOT/.venv"
ENV_FILE="$ROOT/.env"

# ── Colors ────────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'

log()  { echo -e "${CYAN}[start]${NC} $1"; }
ok()   { echo -e "${GREEN}[ok]${NC}    $1"; }
warn() { echo -e "${YELLOW}[warn]${NC}  $1"; }
err()  { echo -e "${RED}[error]${NC} $1"; }

# ── Load .env ─────────────────────────────────────────────────────────────────
if [[ -f "$ENV_FILE" ]]; then
  set -a; source "$ENV_FILE"; set +a
  ok "Loaded $ENV_FILE"
else
  warn ".env not found — CEREBRAS_API_KEY may be unset (agent LLM calls will fail)"
fi

if [[ -z "$CEREBRAS_API_KEY" ]]; then
  warn "CEREBRAS_API_KEY is not set — the merchant agent won't be able to call the model"
fi

# ── Python venv ───────────────────────────────────────────────────────────────
if [[ ! -f "$VENV/bin/python" ]]; then
  log "Creating Python venv..."
  python3 -m venv "$VENV"
fi

log "Installing Python dependencies..."
"$VENV/bin/pip" install -q fastapi "uvicorn[standard]" httpx "openai>=1.30.0" "apscheduler>=3.10.0" "websockets>=12.0" || true
ok "Python deps ready"

# ── Cleanup on exit ───────────────────────────────────────────────────────────
PIDS=()
cleanup() {
  echo ""
  log "Shutting down..."
  for pid in "${PIDS[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
}
trap cleanup EXIT INT TERM

# ── Start catalog sync (port 8002) ───────────────────────────────
log "Starting catalog sync on :8002..."
cd "$ROOT/demo/catalog-sync"
  "$VENV/bin/uvicorn" main:app --port 8002 --reload --log-level warning &
PIDS+=($!)
ok "Catalog sync PID ${PIDS[-1]}"

# ── Start MCP server (port 8001) ───────────────────────────────────
log "Starting MCP server on :8001..."
cd "$ROOT/demo/mcp-server"
MCP_SERVER_URL=http://localhost:8001 \
CATALOG_SYNC_URL=http://localhost:8002 \
  "$VENV/bin/uvicorn" main:app --port 8001 --reload --log-level warning &
PIDS+=($!)
ok "MCP server PID ${PIDS[-1]}"

# ── Start merchant agent (port 10999) ─────────────────────────────────────────
log "Starting merchant agent on :10999..."
cd "$ROOT/demo/merchant-agent"
MCP_SERVER_URL=http://localhost:8001 \
AGENT_BASE_URL=http://localhost:10999 \
CEREBRAS_API_KEY="$CEREBRAS_API_KEY" \
CEREBRAS_MODEL="${CEREBRAS_MODEL:-gpt-oss-120b}" \
  "$VENV/bin/uvicorn" main:app --port 10999 --reload --log-level warning &
PIDS+=($!)
ok "Merchant agent PID ${PIDS[-1]}"

# ── Wait for backends ─────────────────────────────────────────────────────────
log "Waiting for backends to be ready..."
for i in {1..20}; do
  catalog_up=$(curl -sf http://localhost:8002/status > /dev/null 2>&1 && echo "yes" || echo "no")
  mcp_up=$(curl -sf http://localhost:8001/ > /dev/null 2>&1 && echo "yes" || echo "no")
  agent_up=$(curl -sf http://localhost:10999/.well-known/agent-card.json > /dev/null 2>&1 && echo "yes" || echo "no")
  [[ "$catalog_up" == "yes" && "$mcp_up" == "yes" && "$agent_up" == "yes" ]] && break
  sleep 0.5
done

if [[ "$catalog_up" == "yes" ]]; then ok "Catalog sync → http://localhost:8002"; else err "Catalog sync failed to start"; fi
if [[ "$mcp_up" == "yes" ]]; then ok "MCP server  → http://localhost:8001"; else err "MCP server failed to start"; fi
if [[ "$agent_up" == "yes" ]]; then ok "Agent       → http://localhost:10999"; else err "Merchant agent failed to start"; fi

# ── Start React dev server (port 3000) ────────────────────────────────────────
log "Starting React dev server on :3000..."
cd "$ROOT/demo/chat-client"

if [[ ! -d node_modules ]]; then
  log "Installing npm dependencies (first run)..."
  npm install --silent
fi

REACT_APP_AGENT_URL=http://localhost:10999 npm start &
PIDS+=($!)
ok "React app PID ${PIDS[-1]}"

echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  Purrfect Bites — all services running${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "  Chat UI      →  ${CYAN}http://localhost:3000${NC}"
echo -e "  Agent        →  ${CYAN}http://localhost:10999${NC}"
echo -e "  MCP server   →  ${CYAN}http://localhost:8001${NC}"
echo -e "  Catalog sync →  ${CYAN}http://localhost:8002${NC}"
echo -e "  Product feed →  ${CYAN}http://localhost:8002/feed/acp${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "  Press ${YELLOW}Ctrl+C${NC} to stop all services"
echo ""

# ── Keep alive ────────────────────────────────────────────────────────────────
wait
