import os
import sqlite3
import time

from ibus_ai_pinyin.pinyin_utils import compact_pinyin, normalize_pinyin, normalize_short


class DomainDictionaryStore:
    def __init__(self, path):
        self.path = os.path.expanduser(path)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.init_db()

    def init_db(self):
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS domain_dictionaries (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              name TEXT NOT NULL,
              version TEXT NOT NULL DEFAULT '1.0',
              description TEXT,
              source TEXT,
              locale TEXT DEFAULT 'zh-CN',
              file_path TEXT,
              file_hash TEXT,
              imported_at INTEGER NOT NULL,
              created_at INTEGER NOT NULL,
              updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS domain_terms (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              dictionary_id INTEGER,
              term TEXT NOT NULL,
              normalized_term TEXT NOT NULL,
              type TEXT DEFAULT 'other',
              weight INTEGER NOT NULL DEFAULT 50,
              enabled INTEGER NOT NULL DEFAULT 1,
              source TEXT NOT NULL DEFAULT 'import',
              comment TEXT,
              created_at INTEGER NOT NULL,
              updated_at INTEGER NOT NULL,
              UNIQUE(term, source),
              FOREIGN KEY(dictionary_id) REFERENCES domain_dictionaries(id)
            );

            CREATE TABLE IF NOT EXISTS domain_term_pinyin (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              term_id INTEGER NOT NULL,
              pinyin TEXT NOT NULL,
              compact_pinyin TEXT NOT NULL,
              short TEXT,
              weight INTEGER NOT NULL DEFAULT 50,
              created_at INTEGER NOT NULL,
              updated_at INTEGER NOT NULL,
              UNIQUE(term_id, pinyin),
              FOREIGN KEY(term_id) REFERENCES domain_terms(id)
            );

            CREATE TABLE IF NOT EXISTS domain_term_aliases (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              term_id INTEGER NOT NULL,
              alias TEXT NOT NULL,
              alias_pinyin TEXT,
              alias_compact_pinyin TEXT,
              alias_short TEXT,
              weight INTEGER NOT NULL DEFAULT 40,
              created_at INTEGER NOT NULL,
              updated_at INTEGER NOT NULL,
              UNIQUE(term_id, alias),
              FOREIGN KEY(term_id) REFERENCES domain_terms(id)
            );

            CREATE TABLE IF NOT EXISTS domain_term_tags (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              term_id INTEGER NOT NULL,
              tag TEXT NOT NULL,
              UNIQUE(term_id, tag),
              FOREIGN KEY(term_id) REFERENCES domain_terms(id)
            );

            CREATE INDEX IF NOT EXISTS idx_domain_term_pinyin
            ON domain_term_pinyin (pinyin, weight DESC);
            CREATE INDEX IF NOT EXISTS idx_domain_term_compact_pinyin
            ON domain_term_pinyin (compact_pinyin, weight DESC);
            CREATE INDEX IF NOT EXISTS idx_domain_term_short
            ON domain_term_pinyin (short, weight DESC);
            """
        )
        self.conn.commit()

    def import_dictionary(self, dictionary, file_path=None, file_hash=None, mode="merge"):
        if mode not in {"merge", "replace"}:
            raise ValueError("mode must be merge or replace")

        now = int(time.time())
        report = {
            "dictionary_name": dictionary["name"],
            "total_entries": dictionary.get("total_entries", len(dictionary["entries"])),
            "valid_entries": len(dictionary["entries"]),
            "skipped_entries": dictionary.get("skipped_entries", 0),
            "inserted_terms": 0,
            "updated_terms": 0,
            "inserted_pinyin": 0,
            "inserted_aliases": 0,
            "inserted_tags": 0,
        }

        cur = self.conn.execute(
            """
            INSERT INTO domain_dictionaries
            (name, version, description, source, locale, file_path, file_hash, imported_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                dictionary["name"],
                dictionary["version"],
                dictionary.get("description", ""),
                dictionary.get("source", ""),
                dictionary.get("locale", "zh-CN"),
                file_path,
                file_hash,
                now,
                now,
                now,
            ),
        )
        dictionary_id = cur.lastrowid

        for entry in dictionary["entries"]:
            term_id, inserted = self._upsert_term(dictionary_id, entry, now)
            if inserted:
                report["inserted_terms"] += 1
            else:
                report["updated_terms"] += 1
                if mode == "replace":
                    self._delete_term_children(term_id)
            report["inserted_pinyin"] += self._insert_pinyin(term_id, entry, now)
            report["inserted_aliases"] += self._insert_aliases(term_id, entry, now)
            report["inserted_tags"] += self._insert_tags(term_id, entry, now)

        self.conn.commit()
        return report

    def get_candidates(self, pinyin, limit=5):
        return [item["text"] for item in self.get_candidate_items(pinyin, limit=limit)]

    def get_candidate_items(self, pinyin, limit=5):
        normalized = normalize_pinyin(pinyin)
        compact = compact_pinyin(normalized)
        short = normalize_short(pinyin)
        if not normalized and not compact and not short:
            return []

        result = []
        seen = set()
        queries = [
            ("pinyin", "p.pinyin = ?", normalized, 600, "exact_pinyin"),
            ("compact_pinyin", "p.compact_pinyin = ?", compact, 500, "exact_compact"),
            ("short", "p.short = ?", short, 450, "exact_short"),
            ("pinyin", "p.pinyin LIKE ?", f"{normalized}%", 300, "prefix_pinyin"),
            ("compact_pinyin", "p.compact_pinyin LIKE ?", f"{compact}%", 250, "prefix_compact"),
            ("short", "p.short LIKE ?", f"{short}%", 200, "prefix_short"),
        ]
        for _name, where_sql, value, bonus, match_type in queries:
            if not value or value == "%":
                continue
            for row in self._query_terms(where_sql, value, limit):
                term = row["term"]
                if term in seen:
                    continue
                seen.add(term)
                result.append(
                    {
                        "text": term,
                        "score": bonus + row["term_weight"] + row["pinyin_weight"],
                        "source": "domain_dictionary",
                        "match_type": match_type,
                    }
                )
                if len(result) >= limit:
                    return result
        return result

    def get_context_items(self, pinyin, limit=8):
        compact_input = compact_pinyin(pinyin)
        if len(compact_input) < 4:
            return []

        cur = self.conn.execute(
            """
            SELECT t.term, t.type, t.weight AS term_weight, p.pinyin, p.compact_pinyin, p.short
            FROM domain_terms t
            JOIN domain_term_pinyin p ON p.term_id = t.id
            WHERE t.enabled = 1
              AND p.short IS NULL
              AND LENGTH(p.compact_pinyin) >= 4
              AND INSTR(?, p.compact_pinyin) > 0
            ORDER BY t.weight DESC, LENGTH(p.compact_pinyin) DESC, t.updated_at DESC
            LIMIT ?
            """
            ,
            (compact_input, max(limit * 4, limit)),
        )

        result = []
        seen = set()
        for row in cur.fetchall():
            compact_value = row["compact_pinyin"] or ""
            if row["term"] in seen:
                continue
            seen.add(row["term"])
            result.append(
                {
                    "text": row["term"],
                    "pinyin": row["pinyin"],
                    "type": row["type"],
                    "weight": row["term_weight"],
                    "source": "domain_dictionary",
                    "match_type": "contains_compact",
                }
            )
            if len(result) >= limit:
                break
        return result

    def _upsert_term(self, dictionary_id, entry, now):
        cur = self.conn.execute(
            "SELECT id, weight FROM domain_terms WHERE term = ? AND source = 'import'",
            (entry["term"],),
        )
        row = cur.fetchone()
        if row:
            self.conn.execute(
                """
                UPDATE domain_terms
                SET dictionary_id = ?,
                    normalized_term = ?,
                    type = ?,
                    weight = MAX(weight, ?),
                    enabled = ?,
                    comment = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    dictionary_id,
                    entry["normalized_term"],
                    entry["type"],
                    entry["weight"],
                    1 if entry["enabled"] else 0,
                    entry["comment"],
                    now,
                    row["id"],
                ),
            )
            return row["id"], False

        cur = self.conn.execute(
            """
            INSERT INTO domain_terms
            (dictionary_id, term, normalized_term, type, weight, enabled, source, comment, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'import', ?, ?, ?)
            """,
            (
                dictionary_id,
                entry["term"],
                entry["normalized_term"],
                entry["type"],
                entry["weight"],
                1 if entry["enabled"] else 0,
                entry["comment"],
                now,
                now,
            ),
        )
        return cur.lastrowid, True

    def _delete_term_children(self, term_id):
        self.conn.execute("DELETE FROM domain_term_pinyin WHERE term_id = ?", (term_id,))
        self.conn.execute("DELETE FROM domain_term_aliases WHERE term_id = ?", (term_id,))
        self.conn.execute("DELETE FROM domain_term_tags WHERE term_id = ?", (term_id,))

    def delete_term(self, term):
        rows = self.conn.execute(
            "SELECT id FROM domain_terms WHERE term = ?",
            (term,),
        ).fetchall()
        if not rows:
            return 0
        try:
            for row in rows:
                self._delete_term_children(row["id"])
            placeholders = ",".join("?" for _row in rows)
            cur = self.conn.execute(
                f"DELETE FROM domain_terms WHERE id IN ({placeholders})",
                tuple(row["id"] for row in rows),
            )
            self.conn.commit()
            return cur.rowcount
        except Exception:
            self.conn.rollback()
            raise

    def _insert_pinyin(self, term_id, entry, now):
        inserted = 0
        for value in entry["pinyin"]:
            cur = self.conn.execute(
                """
                INSERT OR IGNORE INTO domain_term_pinyin
                (term_id, pinyin, compact_pinyin, short, weight, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (term_id, value, compact_pinyin(value), None, entry["weight"], now, now),
            )
            inserted += cur.rowcount
        for value in entry["short"]:
            cur = self.conn.execute(
                """
                INSERT OR IGNORE INTO domain_term_pinyin
                (term_id, pinyin, compact_pinyin, short, weight, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (term_id, value, value, value, entry["weight"], now, now),
            )
            inserted += cur.rowcount
        return inserted

    def _insert_aliases(self, term_id, entry, now):
        inserted = 0
        for alias in entry["aliases"]:
            cur = self.conn.execute(
                """
                INSERT OR IGNORE INTO domain_term_aliases
                (term_id, alias, weight, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (term_id, alias, max(0, entry["weight"] - 10), now, now),
            )
            inserted += cur.rowcount
        return inserted

    def _insert_tags(self, term_id, entry, now):
        inserted = 0
        for tag in entry["tags"]:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO domain_term_tags (term_id, tag) VALUES (?, ?)",
                (term_id, tag),
            )
            inserted += cur.rowcount
        return inserted

    def _query_terms(self, where_sql, value, limit):
        cur = self.conn.execute(
            f"""
            SELECT t.term, t.weight AS term_weight, p.weight AS pinyin_weight, t.updated_at
            FROM domain_terms t
            JOIN domain_term_pinyin p ON p.term_id = t.id
            WHERE t.enabled = 1 AND {where_sql}
            ORDER BY t.weight DESC, p.weight DESC, LENGTH(p.pinyin) ASC, t.updated_at DESC
            LIMIT ?
            """,
            (value, limit),
        )
        return cur.fetchall()
