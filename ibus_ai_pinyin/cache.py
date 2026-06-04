import os
import sqlite3
import time


class CandidateCache:
    def __init__(self, path):
        self.path = os.path.expanduser(path)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.init_db()

    def init_db(self):
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS candidates (
              pinyin TEXT NOT NULL,
              candidate TEXT NOT NULL,
              score INTEGER NOT NULL DEFAULT 0,
              source TEXT NOT NULL DEFAULT 'llm',
              created_at INTEGER NOT NULL,
              updated_at INTEGER NOT NULL,
              PRIMARY KEY (pinyin, candidate)
            )
            """
        )
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_candidates_pinyin_score
            ON candidates (pinyin, score DESC, updated_at DESC)
            """
        )
        self.conn.commit()

    def get(self, pinyin, limit=5):
        cur = self.conn.execute(
            """
            SELECT candidate
            FROM candidates
            WHERE pinyin = ?
            ORDER BY score DESC, updated_at DESC
            LIMIT ?
            """,
            (pinyin, limit),
        )
        return [row[0] for row in cur.fetchall()]

    def has(self, pinyin, candidate):
        cur = self.conn.execute(
            """
            SELECT 1
            FROM candidates
            WHERE pinyin = ? AND candidate = ?
            LIMIT 1
            """,
            (pinyin, candidate),
        )
        return cur.fetchone() is not None

    def put_many(self, pinyin, candidates, source="llm"):
        now = int(time.time())
        for candidate in candidates:
            self.conn.execute(
                """
                INSERT INTO candidates (pinyin, candidate, score, source, created_at, updated_at)
                VALUES (?, ?, 0, ?, ?, ?)
                ON CONFLICT(pinyin, candidate)
                DO UPDATE SET updated_at = excluded.updated_at
                """,
                (pinyin, candidate, source, now, now),
            )
        self.conn.commit()

    def promote(self, pinyin, candidate):
        now = int(time.time())
        self.conn.execute(
            """
            UPDATE candidates
            SET score = score + 10,
                updated_at = ?
            WHERE pinyin = ? AND candidate = ?
            """,
            (now, pinyin, candidate),
        )
        self.conn.commit()

    def delete(self, pinyin, candidate):
        cur = self.conn.execute(
            """
            DELETE FROM candidates
            WHERE pinyin = ? AND candidate = ?
            """,
            (pinyin, candidate),
        )
        self.conn.commit()
        return cur.rowcount
