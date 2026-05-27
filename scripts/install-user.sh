#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Resolve the real user's home even when run with sudo
if [ -n "${SUDO_USER:-}" ]; then
    REAL_HOME=$(getent passwd "$SUDO_USER" | cut -d: -f6)
else
    REAL_HOME="$HOME"
fi

INSTALL_DIR="${REAL_HOME}/.local/share/ibus-ai-pinyin"
COMPONENT_DIR="/usr/share/ibus/component"
CONFIG_DIR="${REAL_HOME}/.config/ibus-ai-pinyin"

mkdir -p "${INSTALL_DIR}/ibus_ai_pinyin" "${CONFIG_DIR}"
sudo mkdir -p "${COMPONENT_DIR}"

cp "${ROOT_DIR}/engine.py" "${INSTALL_DIR}/engine.py"
cp "${ROOT_DIR}/run-engine.sh" "${INSTALL_DIR}/run-engine.sh"
cp "${ROOT_DIR}/ibus_ai_pinyin/"*.py "${INSTALL_DIR}/ibus_ai_pinyin/"
cp "${ROOT_DIR}/scripts/ibus-setup-ai-pinyin" "${INSTALL_DIR}/ibus-setup-ai-pinyin"
cp "${ROOT_DIR}/preferences.py" "${INSTALL_DIR}/preferences.py"
chmod +x "${INSTALL_DIR}/engine.py"
chmod +x "${INSTALL_DIR}/run-engine.sh"
chmod +x "${INSTALL_DIR}/ibus-setup-ai-pinyin"

sed "s#__INSTALL_DIR__#${INSTALL_DIR}#g" \
  "${ROOT_DIR}/ai-pinyin.xml.in" | sudo tee "${COMPONENT_DIR}/ai-pinyin.xml" > /dev/null

python3 -m py_compile "${INSTALL_DIR}/engine.py" "${INSTALL_DIR}/ibus_ai_pinyin/"*.py
ibus engine ai-pinyin >/dev/null 2>&1 || true

echo "Installed to ${INSTALL_DIR}"
echo "Component: ${COMPONENT_DIR}/ai-pinyin.xml"
echo "Run: ibus restart"
echo "Then select input source: Chinese -> AI 拼音输入法"
