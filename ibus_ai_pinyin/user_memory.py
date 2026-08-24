import os
import json
import re
import sqlite3
import time

from ibus_ai_pinyin.pinyin_utils import compact_pinyin, normalize_pinyin, normalize_text


HAN_RE = re.compile(r"[\u3400-\u9fff]")


def count_han(text):
    return len(HAN_RE.findall(text or ""))


def pinyin_short(pinyin):
    normalized = normalize_pinyin(pinyin)
    parts = [part for part in normalized.split(" ") if part]
    if len(parts) <= 1:
        return ""
    return "".join(part[0] for part in parts if part)


class UserMemoryStore:
    def __init__(self, path):
        self.path = os.path.expanduser(path)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.init_db()

    def init_db(self):
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS user_memory_terms (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              term TEXT NOT NULL UNIQUE,
              normalized_term TEXT NOT NULL,
              pinyin TEXT NOT NULL,
              compact_pinyin TEXT NOT NULL,
              short TEXT,
              type TEXT NOT NULL DEFAULT 'user',
              weight INTEGER NOT NULL DEFAULT 80,
              source TEXT NOT NULL DEFAULT 'manual_correction',
              confirm_count INTEGER NOT NULL DEFAULT 1,
              enabled INTEGER NOT NULL DEFAULT 1,
              created_at INTEGER NOT NULL,
              updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS user_memory_corrections (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              pinyin TEXT NOT NULL,
              original_candidate TEXT NOT NULL,
              corrected_text TEXT NOT NULL,
              method TEXT NOT NULL,
              committed INTEGER NOT NULL DEFAULT 1,
              created_at INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_user_memory_terms_pinyin
            ON user_memory_terms (pinyin, weight DESC, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_user_memory_terms_compact
            ON user_memory_terms (compact_pinyin, weight DESC, updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_user_memory_terms_short
            ON user_memory_terms (short, weight DESC, updated_at DESC);
            """
        )
        self.conn.commit()

    def record_correction(self, pinyin, original_candidate, corrected_text, method="candidate_edit", committed=True):
        now = int(time.time())
        self.conn.execute(
            """
            INSERT INTO user_memory_corrections
            (pinyin, original_candidate, corrected_text, method, committed, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                normalize_pinyin(pinyin),
                original_candidate,
                corrected_text,
                method,
                1 if committed else 0,
                now,
            ),
        )
        self.conn.commit()

    def should_auto_learn(self, text, min_han=2, max_han=12):
        han_count = count_han(text)
        if han_count < min_han or han_count > max_han:
            return False
        return bool(normalize_text(text))

    def learn_term(self, term, pinyin, weight=80, max_weight=120):
        term = normalize_text(term)
        normalized_pinyin = normalize_pinyin(pinyin)
        if not term or not normalized_pinyin:
            return False

        now = int(time.time())
        compact = compact_pinyin(normalized_pinyin)
        short = pinyin_short(normalized_pinyin)
        self.conn.execute(
            """
            INSERT INTO user_memory_terms
            (term, normalized_term, pinyin, compact_pinyin, short, weight, source, confirm_count, enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'manual_correction', 1, 1, ?, ?)
            ON CONFLICT(term) DO UPDATE SET
              pinyin = excluded.pinyin,
              compact_pinyin = excluded.compact_pinyin,
              short = excluded.short,
              weight = MIN(?, user_memory_terms.weight + 10),
              confirm_count = user_memory_terms.confirm_count + 1,
              enabled = 1,
              updated_at = excluded.updated_at
            """,
            (term, term, normalized_pinyin, compact, short, weight, now, now, max_weight),
        )
        self.conn.commit()
        return True

    def get_exact_candidates(self, pinyin, limit=5):
        normalized = normalize_pinyin(pinyin)
        compact = compact_pinyin(normalized)
        short = pinyin_short(normalized) or compact
        rows = []
        for where_sql, value in (
            ("pinyin = ?", normalized),
            ("compact_pinyin = ?", compact),
            ("short = ?", short),
        ):
            if not value:
                continue
            rows.extend(self._query(where_sql, value, limit))
        return self._dedupe_terms(rows, limit)

    def get_context_items(self, pinyin, limit=8):
        compact_input = compact_pinyin(pinyin)
        if len(compact_input) < 4:
            return []

        cur = self.conn.execute(
            """
            SELECT term, type, weight, pinyin, compact_pinyin, short, source
            FROM user_memory_terms
            WHERE enabled = 1
              AND LENGTH(compact_pinyin) >= 4
              AND INSTR(?, compact_pinyin) > 0
            ORDER BY weight DESC, LENGTH(compact_pinyin) DESC, updated_at DESC
            LIMIT ?
            """
            ,
            (compact_input, max(limit * 2, limit)),
        )
        result = []
        seen = set()
        for row in cur.fetchall():
            if row["term"] in seen:
                continue
            seen.add(row["term"])
            result.append(
                {
                    "text": row["term"],
                    "pinyin": row["pinyin"],
                    "type": row["type"],
                    "weight": row["weight"],
                    "source": "user_memory",
                    "match_type": "contains_compact",
                }
            )
            if len(result) >= limit:
                break
        return result

    def list_terms(self, query="", include_disabled=False, limit=100):
        params = []
        where = []
        if not include_disabled:
            where.append("enabled = 1")
        if query:
            where.append("(term LIKE ? OR pinyin LIKE ? OR compact_pinyin LIKE ? OR short LIKE ?)")
            like = f"%{query}%"
            params.extend([like, like, like, like])
        where_sql = "WHERE " + " AND ".join(where) if where else ""
        params.append(limit)
        cur = self.conn.execute(
            f"""
            SELECT term, pinyin, short, type, weight, source, confirm_count, enabled, created_at, updated_at
            FROM user_memory_terms
            {where_sql}
            ORDER BY enabled DESC, weight DESC, updated_at DESC
            LIMIT ?
            """,
            params,
        )
        return [dict(row) for row in cur.fetchall()]

    def set_enabled(self, term, enabled):
        cur = self.conn.execute(
            "UPDATE user_memory_terms SET enabled = ?, updated_at = ? WHERE term = ?",
            (1 if enabled else 0, int(time.time()), term),
        )
        self.conn.commit()
        return cur.rowcount

    def delete_term(self, term):
        cur = self.conn.execute("DELETE FROM user_memory_terms WHERE term = ?", (term,))
        self.conn.commit()
        return cur.rowcount

    def clear_terms(self):
        cur = self.conn.execute("DELETE FROM user_memory_terms")
        self.conn.commit()
        return cur.rowcount

    def export_dictionary(self, include_disabled=False):
        entries = []
        for term in self.list_terms(include_disabled=include_disabled, limit=100000):
            entries.append(
                {
                    "term": term["term"],
                    "pinyin": [term["pinyin"]],
                    "short": [term["short"]] if term["short"] else [],
                    "type": term["type"],
                    "weight": term["weight"],
                    "enabled": bool(term["enabled"]),
                    "comment": f"user_memory:{term['source']};confirm_count:{term['confirm_count']}",
                }
            )
        return {
            "version": "1.0",
            "name": "用户动态词库",
            "description": "由候选词修改功能自动学习生成",
            "source": "user_memory",
            "entries": entries,
        }

    def export_dictionary_json(self, include_disabled=False):
        return json.dumps(self.export_dictionary(include_disabled=include_disabled), ensure_ascii=False, indent=2)

    def _query(self, where_sql, value, limit):
        cur = self.conn.execute(
            f"""
            SELECT term
            FROM user_memory_terms
            WHERE enabled = 1 AND {where_sql}
            ORDER BY weight DESC, updated_at DESC
            LIMIT ?
            """,
            (value, limit),
        )
        return cur.fetchall()

    def _dedupe_terms(self, rows, limit):
        result = []
        seen = set()
        for row in rows:
            term = row["term"]
            if term in seen:
                continue
            seen.add(term)
            result.append(term)
            if len(result) >= limit:
                break
        return result
