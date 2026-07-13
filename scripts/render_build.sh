#!/usr/bin/env bash
# =============================================================================
# Render.com build script
# Runs during every deploy. Installs Python deps, builds React frontend,
# and downloads the Litestream binary for DB restore at startup.
# =============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "── Python dependencies ──────────────────────────────────"
pip install --upgrade pip
pip install -e .

echo "── Node.js / React frontend ─────────────────────────────"
cd desktop-app
# Use a Node version manager shim if available, otherwise rely on system Node
NODE_BIN="$(command -v node 2>/dev/null || true)"
if [[ -z "$NODE_BIN" ]]; then
  echo "ERROR: Node.js not found. Render's Python runtime includes Node 20+."
  exit 1
fi
echo "  Node: $(node --version)  npm: $(npm --version)"
npm ci --prefer-offline 2>/dev/null || npm install
npm run build
echo "  Frontend built → desktop-app/dist/"
cd "$ROOT"

echo "── Litestream binary ────────────────────────────────────"
LITESTREAM_VERSION="v0.3.13"
LITESTREAM_BIN="$ROOT/.litestream/litestream"
if [[ ! -x "$LITESTREAM_BIN" ]]; then
  mkdir -p "$ROOT/.litestream"
  ARCH="$(uname -m)"
  if [[ "$ARCH" == "x86_64" ]]; then
    LS_ARCH="amd64"
  elif [[ "$ARCH" == "arm64" || "$ARCH" == "aarch64" ]]; then
    LS_ARCH="arm64"
  else
    LS_ARCH="amd64"  # fallback
  fi
  LS_URL="https://github.com/benbjohnson/litestream/releases/download/${LITESTREAM_VERSION}/litestream-${LITESTREAM_VERSION}-linux-${LS_ARCH}.tar.gz"
  echo "  Downloading Litestream ${LITESTREAM_VERSION} (${LS_ARCH})..."
  curl -fsSL "$LS_URL" | tar xz -C "$ROOT/.litestream"
  chmod +x "$LITESTREAM_BIN"
fi
echo "  Litestream: $("$LITESTREAM_BIN" version)"

echo "── Build complete ───────────────────────────────────────"
