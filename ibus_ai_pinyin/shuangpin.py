"""Shuangpin (双拼) scheme definitions and conversion logic.

Each shuangpin scheme maps a 2-keystroke syllable to pinyin.
Position 1 = initial consonant (shengmu), Position 2 = final/vowel (yunmu).
"""

# ── Scheme definitions ────────────────────────────────────────────────
# Each scheme provides:
#   initials : dict  -- first keystroke → pinyin initial ("" = zero-initial marker)
#   finals   : dict  -- second keystroke → pinyin final

SHUANGPIN_SCHEMES = {
    "xiaohe": {  # 小鹤双拼
        "initials": {
            "b": "b", "c": "c", "d": "d", "f": "f", "g": "g",
            "h": "h", "j": "j", "k": "k", "l": "l", "m": "m",
            "n": "n", "p": "p", "q": "q", "r": "r", "s": "s",
            "t": "t", "w": "w", "x": "x", "y": "y", "z": "z",
            "i": "ch", "u": "sh", "v": "zh",
            "a": "", "e": "", "o": "",  # zero-initial markers
        },
        "finals": {
            "a": "a", "b": "ou", "c": "ao", "d": "ei", "e": "e",
            "f": "en", "g": "eng", "h": "ang", "i": "i", "j": "an",
            "k": "ai", "l": "ing", "m": "ian", "n": "in", "o": "uo",
            "p": "ie", "q": "iu", "r": "uan", "s": "ong", "t": "ue",
            "u": "u", "v": "ui", "w": "ei", "x": "ia", "y": "un",
            "z": "ou",
        },
    },
    "ziranma": {  # 自然码
        "initials": {
            "b": "b", "c": "c", "d": "d", "f": "f", "g": "g",
            "h": "h", "j": "j", "k": "k", "l": "l", "m": "m",
            "n": "n", "p": "p", "q": "q", "r": "r", "s": "s",
            "t": "t", "w": "w", "x": "x", "y": "y", "z": "z",
            "i": "ch", "u": "sh", "v": "zh",
            "a": "", "e": "", "o": "",  # zero-initial markers
        },
        "finals": {
            "a": "a", "b": "ou", "c": "ao", "d": "ei", "e": "e",
            "f": "en", "g": "eng", "h": "ang", "i": "i", "j": "an",
            "k": "ao", "l": "ai", "m": "ian", "n": "in", "o": "uo",
            "p": "un", "q": "iu", "r": "uan", "s": "ie", "t": "ue",
            "u": "u", "v": "ui", "w": "ia", "x": "ua", "y": "ing",
            "z": "ei",
        },
    },
    "microsoft": {  # 微软双拼
        "initials": {
            "b": "b", "c": "c", "d": "d", "f": "f", "g": "g",
            "h": "h", "j": "j", "k": "k", "l": "l", "m": "m",
            "n": "n", "p": "p", "q": "q", "r": "r", "s": "s",
            "t": "t", "w": "w", "x": "x", "y": "y", "z": "z",
            "i": "ch", "u": "sh", "v": "zh",
            "a": "", "e": "", "o": "",  # zero-initial markers
        },
        "finals": {
            "a": "a", "b": "ou", "c": "iao", "d": "iang", "e": "e",
            "f": "en", "g": "eng", "h": "ang", "i": "i", "j": "ian",
            "k": "ao", "l": "in", "m": "ian", "n": "iu", "o": "uo",
            "p": "un", "q": "er", "r": "uan", "s": "ong", "t": "ue",
            "u": "u", "v": "ui", "w": "ia", "x": "ie", "y": "ing",
            "z": "ei",
        },
    },
    "sogou": {  # 搜狗双拼
        "initials": {
            "b": "b", "c": "c", "d": "d", "f": "f", "g": "g",
            "h": "h", "j": "j", "k": "k", "l": "l", "m": "m",
            "n": "n", "p": "p", "q": "q", "r": "r", "s": "s",
            "t": "t", "w": "w", "x": "x", "y": "y", "z": "z",
            "i": "ch", "u": "sh", "v": "zh",
            "a": "", "e": "", "o": "",  # zero-initial markers
        },
        "finals": {
            "a": "a", "b": "ou", "c": "ao", "d": "ie", "e": "e",
            "f": "en", "g": "eng", "h": "ang", "i": "i", "j": "an",
            "k": "ao", "l": "ai", "m": "ian", "n": "in", "o": "uo",
            "p": "ing", "q": "ei", "r": "er", "s": "ong", "t": "ue",
            "u": "u", "v": "zh", "w": "ei", "x": "ia", "y": "iao",
            "z": "un",
        },
    },
}


# ── Conversion ─────────────────────────────────────────────────────────

def shuangpin_to_pinyin(buffer: str, scheme_name: str) -> str:
    """Convert a shuangpin keystroke buffer to a full pinyin string.

    Every 2 characters form one syllable: initial_key + final_key.
    A trailing odd character (incomplete syllable) is silently dropped.

    Returns space-separated pinyin, e.g. ``"ni hao"``.
    """
    scheme = SHUANGPIN_SCHEMES.get(scheme_name)
    if scheme is None:
        raise ValueError(
            f"Unknown shuangpin scheme: {scheme_name!r}. "
            f"Available: {', '.join(SHUANGPIN_SCHEMES)}"
        )

    initials = scheme["initials"]
    finals = scheme["finals"]

    syllables = []
    step = 2
    for i in range(0, len(buffer) - 1, step):
        ik = buffer[i].lower()
        fk = buffer[i + 1].lower()

        init = initials.get(ik)
        fin = finals.get(fk)

        if init is None or fin is None:
            # Unknown key combination — pass through raw
            syllables.append(buffer[i:i + 2].lower())
            continue

        if not init:
            syllable = fin
        else:
            syllable = init + fin

        syllables.append(syllable)

    return " ".join(syllables)


def available_schemes():
    """Return a list of available shuangpin scheme names."""
    return list(SHUANGPIN_SCHEMES.keys())
