#!/usr/bin/env bash
# Desinstala Movris Vault. No borra las copias del iPhone ni los ajustes.
set -euo pipefail

APP_ID=io.github.jaca101.MovrisVault
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
desktop_dir="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"

rm -rf "$DATA_HOME/movris-vault"
rm -f "$HOME/.local/bin/movris-vault" \
      "$DATA_HOME/applications/$APP_ID.desktop" \
      "$DATA_HOME/icons/hicolor/scalable/apps/$APP_ID.svg" \
      "$desktop_dir/$APP_ID.desktop"
update-desktop-database -q "$DATA_HOME/applications" 2>/dev/null || true

echo "Movris Vault desinstalada. Tus copias siguen donde estaban (por defecto en ~/iPhoneBackup)."
