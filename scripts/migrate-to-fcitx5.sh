#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FCITX_CONFIG_DIR="${HOME}/.config/fcitx5"
FCITX_PINYIN_DIR="${HOME}/.local/share/fcitx5/pinyin"
FCITX_DICT_DIR="${FCITX_PINYIN_DIR}/dictionaries"
AI_DB="${AI_PINYIN_DB:-${HOME}/.config/ibus-ai-pinyin/cache.sqlite3}"
TEXT_DICT="${FCITX_DICT_DIR}/ai-pinyin.txt"
BIN_DICT="${FCITX_DICT_DIR}/ai-pinyin.dict"
STAMP="$(date +%Y%m%d-%H%M%S)"

need_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

need_cmd python3
need_cmd libime_pinyindict

mkdir -p "${FCITX_CONFIG_DIR}" "${FCITX_DICT_DIR}"

if [[ -e "${BIN_DICT}" ]]; then
  cp "${BIN_DICT}" "${BIN_DICT}.bak.${STAMP}"
fi
if [[ -e "${TEXT_DICT}" ]]; then
  cp "${TEXT_DICT}" "${TEXT_DICT}.bak.${STAMP}"
fi
if [[ -e "${FCITX_CONFIG_DIR}/profile" ]]; then
  cp "${FCITX_CONFIG_DIR}/profile" "${FCITX_CONFIG_DIR}/profile.bak.${STAMP}"
fi

python3 "${ROOT_DIR}/scripts/export-fcitx5-pinyin-dict.py" --db "${AI_DB}" "${TEXT_DICT}"
libime_pinyindict "${TEXT_DICT}" "${BIN_DICT}"

cat > "${FCITX_CONFIG_DIR}/profile" <<'EOF'
[Groups/0]
# Group Name
Name=默认
# Layout
Default Layout=cn
# Default Input Method
DefaultIM=pinyin

[Groups/0/Items/0]
# Name
Name=keyboard-cn
# Layout
Layout=

[Groups/0/Items/1]
# Name
Name=pinyin
# Layout
Layout=

[GroupOrder]
0=默认
EOF

echo "Migrated ai-pinyin data to fcitx5:"
echo "  source: ${AI_DB}"
echo "  text:   ${TEXT_DICT}"
echo "  dict:   ${BIN_DICT}"
echo
echo "Next steps:"
echo "  im-config -n fcitx5"
echo "  fcitx5 -r"
echo "Then log out and back in if GTK/QT/XMODIFIERS still point to ibus."
