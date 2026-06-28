#!/usr/bin/env zsh

# ── Colors ────────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'

log()  { echo -e "${CYAN}[setup]${NC} $1"; }
ok()   { echo -e "${GREEN}[ok]${NC}    $1"; }
warn() { echo -e "${YELLOW}[warn]${NC}  $1"; }
err()  { echo -e "${RED}[error]${NC} $1"; exit 1; }

# ── Require brew ──────────────────────────────────────────────────────────────
if ! command -v brew &>/dev/null; then
  err "Homebrew not found. Install it first: https://brew.sh"
fi
ok "Homebrew found"

# ── Docker Desktop ────────────────────────────────────────────────────────────
log "Installing Docker Desktop..."
if [[ -d /Applications/Docker.app ]] || command -v docker &>/dev/null; then
  ok "Docker Desktop already installed"
else
  brew install --cask docker || warn "Docker Desktop install failed — download manually: https://www.docker.com/products/docker-desktop/"
  [[ -d /Applications/Docker.app ]] && ok "Docker Desktop installed" || warn "Docker.app not found after install — see above"
fi

# ── Node.js 20 ────────────────────────────────────────────────────────────────
log "Installing Node.js 20..."
if command -v node &>/dev/null && [[ $(node --version 2>/dev/null) == v20* ]]; then
  ok "Node.js 20 already installed"
else
  brew install node@20
  command -v node &>/dev/null && ok "Node.js 20 installed" || warn "node not found on PATH after install — you may need: brew link node@20"
fi

# ── Python 3.13 ───────────────────────────────────────────────────────────────
log "Installing Python 3.13..."
if command -v python3.13 &>/dev/null; then
  ok "Python 3.13 already installed ($(python3.13 --version))"
else
  brew install python@3.13 || warn "brew install failed — download manually: https://www.python.org/ftp/python/3.13.4/python-3.13.4-macos11.pkg"
  command -v python3.13 &>/dev/null && ok "Python 3.13 installed ($(python3.13 --version))" || warn "python3.13 not found after install — if you installed via .pkg, this is fine"
fi

# ── Python 3.11 ───────────────────────────────────────────────────────────────
log "Installing Python 3.11..."
if command -v python3.11 &>/dev/null; then
  ok "Python 3.11 already installed ($(python3.11 --version))"
else
  brew install python@3.11 || warn "brew install failed — download manually: https://www.python.org/ftp/python/3.11.9/python-3.11.9-macos11.pkg"
  command -v python3.11 &>/dev/null && ok "Python 3.11 installed ($(python3.11 --version))" || warn "python3.11 not found after install — if you installed via .pkg, this is fine"
fi

# ── .env reminder ─────────────────────────────────────────────────────────────
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
echo ""
if [[ ! -f "$ROOT/.env" ]]; then
  warn "No .env file found. Create one before running the stack:"
  echo  "      echo 'CEREBRAS_API_KEY=your_key_here' > $ROOT/.env"
  warn "Get a free API key at: https://cloud.cerebras.ai"
else
  ok ".env file already exists"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
ok "All dependencies installed. Open Docker.app if it isn't running, then:"
echo "      cd $ROOT && docker compose up --build"
