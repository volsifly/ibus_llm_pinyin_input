#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_DIR="${HOME}/.local/share/ibus-ai-pinyin"
COMPONENT_DIR="${HOME}/.local/share/ibus/component"
CONFIG_DIR="${HOME}/.config/ibus-ai-pinyin"

mkdir -p "${INSTALL_DIR}/ibus_ai_pinyin" "${COMPONENT_DIR}" "${CONFIG_DIR}"

cp "${ROOT_DIR}/engine.py" "${INSTALL_DIR}/engine.py"
cp "${ROOT_DIR}/settings.py" "${INSTALL_DIR}/settings.py"
cp "${ROOT_DIR}/run-engine.sh" "${INSTALL_DIR}/run-engine.sh"
cp "${ROOT_DIR}/ibus_ai_pinyin/"*.py "${INSTALL_DIR}/ibus_ai_pinyin/"
chmod +x "${INSTALL_DIR}/engine.py"
chmod +x "${INSTALL_DIR}/settings.py"
chmod +x "${INSTALL_DIR}/run-engine.sh"

sed "s#__INSTALL_DIR__#${INSTALL_DIR}#g" \
  "${ROOT_DIR}/ai-pinyin.xml.in" > "${COMPONENT_DIR}/ai-pinyin.xml"

python3 -m py_compile "${INSTALL_DIR}/engine.py" "${INSTALL_DIR}/ibus_ai_pinyin/"*.py
ibus engine ai-pinyin >/dev/null 2>&1 || true

echo "Installed to ${INSTALL_DIR}"
echo "Component: ${COMPONENT_DIR}/ai-pinyin.xml"
echo "Run: ibus restart"
echo "Then select input source: Chinese -> AI 拼音输入法"
