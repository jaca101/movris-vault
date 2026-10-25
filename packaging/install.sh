#!/usr/bin/env bash
# Instala Movris Vault para el usuario actual.
# Solo pide la contraseña si faltan paquetes del sistema; todo lo demás va dentro de tu carpeta personal.
#
#   packaging/install.sh                       instalar o actualizar
#   packaging/install.sh --desktop-shortcut    además, un icono en el escritorio
set -euo pipefail

APP_ID=io.github.jaca101.MovrisVault
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
PREFIX="$DATA_HOME/movris-vault"
VENV="$PREFIX/venv"
BINDIR="$HOME/.local/bin"
APT_PKGS=(python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 usbmuxd)

desktop_shortcut=0
for arg in "$@"; do
    case "$arg" in
        --desktop-shortcut) desktop_shortcut=1 ;;
        -h|--help) sed -n '2,7p' "$0"; exit 0 ;;
        *) echo "Opción desconocida: $arg" >&2; exit 2 ;;
    esac
done

say() { printf '\033[1m==> %s\033[0m\n' "$*"; }

# 1. Paquetes del sistema: GTK 4 para la ventana y usbmuxd para hablar con el iPhone
if command -v dpkg >/dev/null; then
    missing=()
    for pkg in "${APT_PKGS[@]}"; do
        dpkg -s "$pkg" >/dev/null 2>&1 || missing+=("$pkg")
    done
    if ((${#missing[@]})); then
        say "Instalando paquetes del sistema (te pedirá tu contraseña): ${missing[*]}"
        sudo apt-get install -y "${missing[@]}"
    fi
else
    say "No es Debian/Ubuntu/Mint: asegúrate de tener PyGObject, GTK 4, libadwaita y usbmuxd"
fi

# 2. Un entorno de Python propio, para no tocar el del sistema
if [ ! -x "$VENV/bin/pip" ]; then
    say "Preparando el entorno de Python en $VENV"
    mkdir -p "$PREFIX"
    if python3 -c "import ensurepip" 2>/dev/null; then
        python3 -m venv --system-site-packages "$VENV"
    else
        # Mint y Ubuntu no traen ensurepip sin python3-venv: pip desde su paquete oficial de PyPI
        python3 -m venv --without-pip --system-site-packages "$VENV"
        tmp="$(mktemp -d)"
        trap 'rm -rf "$tmp"' EXIT
        python3 - "$tmp" <<'EOF'
import hashlib, json, sys, urllib.request
data = json.load(urllib.request.urlopen("https://pypi.org/pypi/pip/json"))
wheel = next(f for f in data["urls"] if f["filename"].endswith(".whl"))
path = f"{sys.argv[1]}/{wheel['filename']}"
urllib.request.urlretrieve(wheel["url"], path)
if hashlib.sha256(open(path, "rb").read()).hexdigest() != wheel["digests"]["sha256"]:
    sys.exit("La descarga de pip no coincide con la de PyPI")
EOF
        whl=("$tmp"/pip-*.whl)
        "$VENV/bin/python" "${whl[0]}/pip" install -q "${whl[0]}"
    fi
fi

say "Instalando Movris Vault"
"$VENV/bin/pip" install -q --upgrade "$SRC"

# 3. La orden, el icono y el lanzador del menú
mkdir -p "$BINDIR" "$DATA_HOME/applications" "$DATA_HOME/icons/hicolor/scalable/apps"
ln -sf "$VENV/bin/movris-vault" "$BINDIR/movris-vault"
install -m644 "$SRC/data/$APP_ID.svg" "$DATA_HOME/icons/hicolor/scalable/apps/$APP_ID.svg"
sed "s|@BINDIR@|$BINDIR|" "$SRC/data/$APP_ID.desktop.in" > "$DATA_HOME/applications/$APP_ID.desktop"
update-desktop-database -q "$DATA_HOME/applications" 2>/dev/null || true
gtk-update-icon-cache -q -t "$DATA_HOME/icons/hicolor" 2>/dev/null || true

if ((desktop_shortcut)); then
    desktop_dir="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
    if [ -d "$desktop_dir" ]; then
        install -m755 "$DATA_HOME/applications/$APP_ID.desktop" "$desktop_dir/$APP_ID.desktop"
        # Sin esto, Cinnamon pregunta si te fías del lanzador la primera vez
        gio set "$desktop_dir/$APP_ID.desktop" metadata::trusted true 2>/dev/null || true
    fi
fi

say "Listo. Busca «Movris Vault» en el menú."
