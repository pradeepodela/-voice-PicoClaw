#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# PicoClaw setup script for Raspberry Pi / Linux / macOS
# Usage:  bash scripts/setup_picoclaw.sh
# ─────────────────────────────────────────────────────────────
set -euo pipefail

RELEASES_URL="https://github.com/sipeed/picoclaw/releases/latest/download"
PICOCLAW_DIR="$HOME/.picoclaw"
CONFIG_FILE="$PICOCLAW_DIR/config.json"

# ── 1. Detect platform ────────────────────────────────────────
OS="$(uname -s)"
ARCH="$(uname -m)"

case "$OS" in
  Linux)
    case "$ARCH" in
      aarch64)  PKG="picoclaw_aarch64.deb" ;;
      armv7l)   PKG="picoclaw_armv7.deb" ;;
      armv6l)   PKG="picoclaw_armv6.deb" ;;
      x86_64)   PKG="picoclaw_x86_64.deb" ;;
      *)        echo "Unsupported arch: $ARCH"; exit 1 ;;
    esac
    INSTALL_CMD="deb"
    ;;
  Darwin)
    case "$ARCH" in
      arm64)   PKG="picoclaw_Darwin_arm64.tar.gz" ;;
      x86_64)  PKG="picoclaw_Darwin_x86_64.tar.gz" ;;
      *)        echo "Unsupported arch: $ARCH"; exit 1 ;;
    esac
    INSTALL_CMD="tar"
    ;;
  *)
    echo "Unsupported OS: $OS"; exit 1 ;;
esac

echo "Detected: $OS / $ARCH  →  package: $PKG"

# ── 2. Check if already installed ────────────────────────────
if command -v picoclaw &>/dev/null; then
  echo "PicoClaw already installed: $(picoclaw version 2>/dev/null || echo 'unknown version')"
  read -rp "Reinstall/update? [y/N] " ANSWER
  [[ "${ANSWER,,}" == "y" ]] || { echo "Skipping install."; setup_config; exit 0; }
fi

# ── 3. Download ───────────────────────────────────────────────
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

echo "Downloading $PKG ..."
curl -fsSL "$RELEASES_URL/$PKG" -o "$TMP_DIR/$PKG"

# ── 4. Install ────────────────────────────────────────────────
case "$INSTALL_CMD" in
  deb)
    echo "Installing .deb (may ask for sudo) ..."
    sudo dpkg -i "$TMP_DIR/$PKG"
    ;;
  tar)
    echo "Extracting .tar.gz ..."
    tar -xzf "$TMP_DIR/$PKG" -C "$TMP_DIR"
    BINARY="$(find "$TMP_DIR" -name picoclaw -type f | head -1)"
    if [[ -z "$BINARY" ]]; then
      echo "Error: picoclaw binary not found in archive."; exit 1
    fi
    sudo mv "$BINARY" /usr/local/bin/picoclaw
    sudo chmod +x /usr/local/bin/picoclaw
    ;;
esac

echo "PicoClaw installed: $(picoclaw version 2>/dev/null || echo 'OK')"

# ── 5. Write config ───────────────────────────────────────────
setup_config() {
  mkdir -p "$PICOCLAW_DIR"

  # Load OPENROUTER_API_KEY from .env if it exists
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  ENV_FILE="$SCRIPT_DIR/.env"
  API_KEY="${OPENROUTER_API_KEY:-}"

  if [[ -z "$API_KEY" && -f "$ENV_FILE" ]]; then
    API_KEY="$(grep -E '^OPENROUTER_API_KEY=' "$ENV_FILE" | cut -d= -f2- | tr -d '"' | tr -d "'")"
  fi

  if [[ -z "$API_KEY" ]]; then
    read -rp "Enter your OpenRouter API key (sk-or-v1-...): " API_KEY
  fi

  # Read model from config.yaml if available
  CONFIG_YAML="$SCRIPT_DIR/config.yaml"
  MODEL="meta-llama/llama-3.2-3b-instruct:free"
  if command -v python3 &>/dev/null && [[ -f "$CONFIG_YAML" ]]; then
    MODEL="$(python3 -c "import yaml; c=yaml.safe_load(open('$CONFIG_YAML')); print(c['llm']['model'])" 2>/dev/null || echo "$MODEL")"
  fi

  cat > "$CONFIG_FILE" <<JSON
{
  "agents": {
    "defaults": {
      "model_name": "openrouter-default",
      "max_tokens": 2048,
      "temperature": 0.7,
      "max_tool_iterations": 10
    }
  },
  "model_list": [
    {
      "model_name": "openrouter-default",
      "model": "$MODEL"
    }
  ],
  "providers": {
    "openrouter": {
      "api_key": "$API_KEY",
      "api_base": "https://openrouter.ai/api/v1"
    }
  }
}
JSON

  echo "Config written to $CONFIG_FILE"
  echo "  Model: $MODEL"
  echo "  API key: ${API_KEY:0:12}..."
}

setup_config

echo ""
echo "✓ PicoClaw is ready."
echo "  Test it: picoclaw agent -m 'What is the weather in London?'"
echo "  Your RPI agent will use it automatically via the picoclaw_agent tool."
