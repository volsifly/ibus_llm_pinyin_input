#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="${ROOT_DIR}/build/fcitx5-ai-pinyin"
DEV_ROOT="${FCITX5_DEV_ROOT:-/tmp/fcitx5-dev-root}"
INSTALL_LIB_DIR="${HOME}/.local/lib/fcitx5"
INSTALL_DATA_DIR="${HOME}/.local/share/fcitx5"
INSTALL_RUNTIME_DIR="${HOME}/.local/share/ibus-ai-pinyin"
SYSTEM_LIB_DIR="/usr/lib/x86_64-linux-gnu/fcitx5"
SYSTEM_ADDON_DIR="/usr/share/fcitx5/addon"
SYSTEM_INPUTMETHOD_DIR="/usr/share/fcitx5/inputmethod"

if [[ ! -d "${DEV_ROOT}/usr/include/Fcitx5" ]]; then
  echo "Missing fcitx5 dev headers at ${DEV_ROOT}." >&2
  echo "Run: mkdir -p /tmp/fcitx5-dev-debs /tmp/fcitx5-dev-root && cd /tmp/fcitx5-dev-debs && apt download libfcitx5core-dev libfcitx5config-dev libfcitx5utils-dev fcitx5-modules-dev && for deb in *.deb; do dpkg-deb -x \"\$deb\" /tmp/fcitx5-dev-root; done" >&2
  exit 1
fi

mkdir -p "${BUILD_DIR}" "${INSTALL_LIB_DIR}" "${INSTALL_DATA_DIR}/addon" "${INSTALL_DATA_DIR}/inputmethod" "${INSTALL_RUNTIME_DIR}/ibus_ai_pinyin"

cmake -S "${ROOT_DIR}/fcitx5_ai_pinyin" -B "${BUILD_DIR}" \
  -DFCITX5_DEV_ROOT="${DEV_ROOT}" \
  -DCMAKE_INSTALL_PREFIX="${HOME}/.local" \
  -DCMAKE_INSTALL_LIBDIR=lib \
  -DCMAKE_INSTALL_DATADIR=share
cmake --build "${BUILD_DIR}"
cmake --install "${BUILD_DIR}"

cp "${ROOT_DIR}/fcitx5_backend.py" "${INSTALL_RUNTIME_DIR}/fcitx5_backend.py"
cp "${ROOT_DIR}/ibus_ai_pinyin/"*.py "${INSTALL_RUNTIME_DIR}/ibus_ai_pinyin/"
chmod +x "${INSTALL_RUNTIME_DIR}/fcitx5_backend.py"

python3 -m py_compile "${INSTALL_RUNTIME_DIR}/fcitx5_backend.py" "${INSTALL_RUNTIME_DIR}/ibus_ai_pinyin/"*.py

install_system_file() {
  local src="$1"
  local dest="$2"
  if [[ -w "$(dirname "${dest}")" ]]; then
    cp "${src}" "${dest}"
  elif command -v pkexec >/dev/null 2>&1; then
    pkexec cp "${src}" "${dest}"
  else
    echo "Need permission to install ${dest}" >&2
    return 1
  fi
}

# Fcitx5 5.1 on this Ubuntu install only discovers shared-library addon
# metadata from the system addon directory. Keep the user copy for development,
# but install the active addon registration system-wide.
install_system_file "${INSTALL_LIB_DIR}/ai-pinyin.so" "${SYSTEM_LIB_DIR}/ai-pinyin.so"
install_system_file "${INSTALL_DATA_DIR}/addon/ai-pinyin.conf" "${SYSTEM_ADDON_DIR}/ai-pinyin.conf"
install_system_file "${INSTALL_DATA_DIR}/inputmethod/ai-pinyin.conf" "${SYSTEM_INPUTMETHOD_DIR}/ai-pinyin.conf"

python3 - <<'PY'
from pathlib import Path
profile = Path.home() / ".config/fcitx5/profile"
profile.parent.mkdir(parents=True, exist_ok=True)
text = profile.read_text() if profile.exists() else ""
if "Name=ai-pinyin" not in text:
    if "[Groups/0/Items/1]" in text:
        text = text.replace("[GroupOrder]", "[Groups/0/Items/2]\n# Name\nName=ai-pinyin\n# Layout\nLayout=\n\n[GroupOrder]")
    else:
        text += "\n[Groups/0/Items/1]\n# Name\nName=ai-pinyin\n# Layout\nLayout=\n"
if "DefaultIM=" in text:
    text = "\n".join("DefaultIM=ai-pinyin" if line.startswith("DefaultIM=") else line for line in text.splitlines()) + "\n"
else:
    text = text.replace("[Groups/0]\n", "[Groups/0]\nDefaultIM=ai-pinyin\n", 1)
profile.write_text(text)
PY

echo "Installed fcitx5 ai-pinyin addon."
echo "Restart: fcitx5 -d -r"
echo "Switch:  fcitx5-remote -s ai-pinyin"
