#!/usr/bin/env python3
import argparse
import os
import sqlite3
import sys


PINYIN_SYLLABLES = {
    "a", "ai", "an", "ang", "ao",
    "ba", "bai", "ban", "bang", "bao", "bei", "ben", "beng", "bi", "bian", "biao", "bie", "bin", "bing", "bo", "bu",
    "ca", "cai", "can", "cang", "cao", "ce", "cen", "ceng", "cha", "chai", "chan", "chang", "chao", "che", "chen", "cheng", "chi", "chong", "chou", "chu", "chua", "chuai", "chuan", "chuang", "chui", "chun", "chuo", "ci", "cong", "cou", "cu", "cuan", "cui", "cun", "cuo",
    "da", "dai", "dan", "dang", "dao", "de", "dei", "den", "deng", "di", "dia", "dian", "diao", "die", "ding", "diu", "dong", "dou", "du", "duan", "dui", "dun", "duo",
    "e", "ei", "en", "eng", "er",
    "fa", "fan", "fang", "fei", "fen", "feng", "fo", "fou", "fu",
    "ga", "gai", "gan", "gang", "gao", "ge", "gei", "gen", "geng", "gong", "gou", "gu", "gua", "guai", "guan", "guang", "gui", "gun", "guo",
    "ha", "hai", "han", "hang", "hao", "he", "hei", "hen", "heng", "hong", "hou", "hu", "hua", "huai", "huan", "huang", "hui", "hun", "huo",
    "ji", "jia", "jian", "jiang", "jiao", "jie", "jin", "jing", "jiong", "jiu", "ju", "juan", "jue", "jun",
    "ka", "kai", "kan", "kang", "kao", "ke", "ken", "keng", "kong", "kou", "ku", "kua", "kuai", "kuan", "kuang", "kui", "kun", "kuo",
    "la", "lai", "lan", "lang", "lao", "le", "lei", "leng", "li", "lia", "lian", "liang", "liao", "lie", "lin", "ling", "liu", "lo", "long", "lou", "lu", "luan", "lun", "luo", "lv", "lve",
    "m", "ma", "mai", "man", "mang", "mao", "me", "mei", "men", "meng", "mi", "mian", "miao", "mie", "min", "ming", "miu", "mo", "mou", "mu",
    "n", "na", "nai", "nan", "nang", "nao", "ne", "nei", "nen", "neng", "ng", "ni", "nian", "niang", "niao", "nie", "nin", "ning", "niu", "nong", "nou", "nu", "nuan", "nun", "nuo", "nv", "nve",
    "o", "ou",
    "pa", "pai", "pan", "pang", "pao", "pei", "pen", "peng", "pi", "pian", "piao", "pie", "pin", "ping", "po", "pou", "pu",
    "qi", "qia", "qian", "qiang", "qiao", "qie", "qin", "qing", "qiong", "qiu", "qu", "quan", "que", "qun",
    "ran", "rang", "rao", "re", "ren", "reng", "ri", "rong", "rou", "ru", "ruan", "rui", "run", "ruo",
    "sa", "sai", "san", "sang", "sao", "se", "sen", "seng", "sha", "shai", "shan", "shang", "shao", "she", "shei", "shen", "sheng", "shi", "shou", "shu", "shua", "shuai", "shuan", "shuang", "shui", "shun", "shuo", "si", "song", "sou", "su", "suan", "sui", "sun", "suo",
    "ta", "tai", "tan", "tang", "tao", "te", "teng", "ti", "tian", "tiao", "tie", "ting", "tong", "tou", "tu", "tuan", "tui", "tun", "tuo",
    "wa", "wai", "wan", "wang", "wei", "wen", "weng", "wo", "wu",
    "xi", "xia", "xian", "xiang", "xiao", "xie", "xin", "xing", "xiong", "xiu", "xu", "xuan", "xue", "xun",
    "ya", "yan", "yang", "yao", "ye", "yi", "yin", "ying", "yo", "yong", "you", "yu", "yuan", "yue", "yun",
    "za", "zai", "zan", "zang", "zao", "ze", "zei", "zen", "zeng", "zha", "zhai", "zhan", "zhang", "zhao", "zhe", "zhei", "zhen", "zheng", "zhi", "zhong", "zhou", "zhu", "zhua", "zhuai", "zhuan", "zhuang", "zhui", "zhun", "zhuo", "zi", "zong", "zou", "zu", "zuan", "zui", "zun", "zuo",
}


def has_han(text):
    return any("\u3400" <= ch <= "\u9fff" for ch in text)


def fcitx_pinyin(pinyin):
    parts = [part for part in str(pinyin or "").strip().lower().split() if part]
    if not parts or any(part not in PINYIN_SYLLABLES for part in parts):
        return ""
    return "'".join(parts)


def clamp_score(weight):
    try:
        value = float(weight)
    except (TypeError, ValueError):
        value = 50
    # libime accepts floating scores. Keep imported words neutral but let
    # stronger ai-pinyin memory terms sort a little higher.
    return max(-10.0, min(10.0, (value - 50.0) / 10.0))


def collect_rows(conn):
    rows = []
    if table_exists(conn, "user_memory_terms"):
        rows.extend(
            conn.execute(
                """
                SELECT term, pinyin, weight, 'user_memory' AS source
                FROM user_memory_terms
                WHERE enabled = 1 AND term <> '' AND pinyin <> ''
                """
            ).fetchall()
        )

    if table_exists(conn, "domain_terms") and table_exists(conn, "domain_term_pinyin"):
        rows.extend(
            conn.execute(
                """
                SELECT t.term, p.pinyin, MAX(t.weight, p.weight) AS weight, 'domain_dictionary' AS source
                FROM domain_terms t
                JOIN domain_term_pinyin p ON p.term_id = t.id
                WHERE t.enabled = 1 AND t.term <> '' AND p.pinyin <> ''
                """
            ).fetchall()
        )
    return rows


def table_exists(conn, name):
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone()
    return bool(row)


def main():
    parser = argparse.ArgumentParser(
        description="Export ai-pinyin SQLite memory/dictionaries as libime pinyin text."
    )
    parser.add_argument(
        "--db",
        default="~/.config/ibus-ai-pinyin/cache.sqlite3",
        help="ai-pinyin SQLite cache path",
    )
    parser.add_argument("output", help="output text dictionary path")
    args = parser.parse_args()

    db_path = os.path.expanduser(args.db)
    if not os.path.exists(db_path):
        print(f"ai-pinyin database not found: {db_path}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    merged = {}
    for row in collect_rows(conn):
        term = str(row["term"] or "").strip()
        if not has_han(term):
            continue
        pinyin = fcitx_pinyin(row["pinyin"])
        if not term or not pinyin:
            continue
        key = (term, pinyin)
        score = clamp_score(row["weight"])
        if key not in merged or score > merged[key]:
            merged[key] = score

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        for (term, pinyin), score in sorted(merged.items(), key=lambda item: (item[0][1], item[0][0])):
            f.write(f"{term} {pinyin} {score:.3f}\n")

    print(f"exported={len(merged)} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
