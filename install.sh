#!/bin/bash

set -euo pipefail
umask 077

AGENT_TEAM_DOWNLOAD_BASE='https://agentteam.zengjice.com:7001/install'
# Release publishing rewrites these three pins in a staged installer.
AGENT_TEAM_VERSION='0.1.2'
AGENT_TEAM_WHEEL='agent_team-0.1.2-py3-none-any.whl'
AGENT_TEAM_WHEEL_SHA256='938e8f41823ed9cecd5a2348832b0af8f15756eeaf82eed51960f2ccb1802c40'
AGENT_TEAM_TEMP_DIR=''

fail() {
  echo "Error: $1" >&2
  exit 1
}

cleanup() {
  cleanup_status=$?
  trap - EXIT
  set +e

  if [ -n "$AGENT_TEAM_TEMP_DIR" ] && [ -d "$AGENT_TEAM_TEMP_DIR" ]; then
    rm -rf -- "$AGENT_TEAM_TEMP_DIR"
  fi

  exit "$cleanup_status"
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "missing required command: $1"
}

trap cleanup EXIT
trap 'exit 130' HUP INT TERM

[ "$(uname -s)" = 'Darwin' ] || fail 'this installer only supports macOS'

export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

require_command curl
require_command shasum
require_command uv
require_command git
require_command tmux

if ! command -v codex >/dev/null 2>&1 && ! command -v claude >/dev/null 2>&1; then
  fail 'Codex CLI and/or Claude Code CLI must already be installed'
fi

AGENT_TEAM_OWNER_DIR="$HOME/Library/Application Support/agent-team/workspaces"
if [ -d "$AGENT_TEAM_OWNER_DIR" ]; then
  for agent_team_owner in "$AGENT_TEAM_OWNER_DIR"/*; do
    if [ -f "$agent_team_owner" ]; then
      fail 'an Agent-Team Run owns a workspace; complete or recover it before upgrading'
    fi
  done
fi

AGENT_TEAM_TEMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/agent-team-install.XXXXXX")"
AGENT_TEAM_WHEEL_PATH="$AGENT_TEAM_TEMP_DIR/$AGENT_TEAM_WHEEL"

echo "Downloading Agent-Team $AGENT_TEAM_VERSION..."
curl --fail --silent --show-error --location \
  --proto '=https' --tlsv1.2 \
  --connect-timeout 10 --max-time 600 \
  --retry 3 --retry-delay 1 --retry-all-errors \
  "$AGENT_TEAM_DOWNLOAD_BASE/$AGENT_TEAM_WHEEL" \
  --output "$AGENT_TEAM_WHEEL_PATH"

AGENT_TEAM_ACTUAL_SHA256="$(
  shasum -a 256 "$AGENT_TEAM_WHEEL_PATH" | awk '{ print $1 }'
)"
[ "$AGENT_TEAM_ACTUAL_SHA256" = "$AGENT_TEAM_WHEEL_SHA256" ] || \
  fail 'downloaded wheel checksum mismatch'

echo "Installing Agent-Team $AGENT_TEAM_VERSION..."
uv tool install --force "$AGENT_TEAM_WHEEL_PATH"

AGENT_TEAM_BIN="$(command -v agent-team || true)"
[ -n "$AGENT_TEAM_BIN" ] || fail 'agent-team was not added to ~/.local/bin'

"$AGENT_TEAM_BIN" install

AGENT_TEAM_INSTALLED_VERSION="$("$AGENT_TEAM_BIN" --version)"
[ "$AGENT_TEAM_INSTALLED_VERSION" = "$AGENT_TEAM_VERSION" ] || \
  fail "installed version $AGENT_TEAM_INSTALLED_VERSION does not match $AGENT_TEAM_VERSION"

[ -f "$HOME/.codex/skills/agent-team/SKILL.md" ] || \
  fail 'Codex skill installation could not be verified'
[ -f "$HOME/Library/Application Support/agent-team/installed/claude-code-plugin/.claude-plugin/plugin.json" ] || \
  fail 'Claude Code plugin installation could not be verified'

echo
echo "Agent-Team $AGENT_TEAM_INSTALLED_VERSION installed successfully"
echo "CLI:           $AGENT_TEAM_BIN"
echo "Codex skill:   $HOME/.codex/skills/agent-team"
echo "Claude plugin: $HOME/Library/Application Support/agent-team/installed/claude-code-plugin"
echo 'Authenticate the harness CLIs before starting a Run.'
