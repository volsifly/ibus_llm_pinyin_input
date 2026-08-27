import os
import sqlite3
import time


class LLMStatsStore:
    def __init__(self, path):
        self.path = os.path.expanduser(path)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.init_db()

    def connect(self):
        conn = sqlite3.connect(self.path, timeout=3)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self):
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS llm_request_stats (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  created_at INTEGER NOT NULL,
                  profile_id TEXT NOT NULL,
                  model TEXT NOT NULL,
                  operation TEXT NOT NULL,
                  success INTEGER NOT NULL,
                  latency_ms INTEGER NOT NULL,
                  prompt_tokens INTEGER NOT NULL DEFAULT 0,
                  completion_tokens INTEGER NOT NULL DEFAULT 0,
                  total_tokens INTEGER NOT NULL DEFAULT 0,
                  candidate_count INTEGER NOT NULL DEFAULT 0,
                  error TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_llm_stats_created_at ON llm_request_stats(created_at DESC)"
            )

    def record(self, profile_id, model, operation, success, latency_ms, usage=None, candidate_count=0, error=""):
        usage = usage if isinstance(usage, dict) else {}
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO llm_request_stats
                (created_at, profile_id, model, operation, success, latency_ms,
                 prompt_tokens, completion_tokens, total_tokens, candidate_count, error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(time.time()), profile_id or "default", model or "", operation,
                    1 if success else 0, max(0, int(latency_ms or 0)),
                    int(usage.get("prompt_tokens") or 0),
                    int(usage.get("completion_tokens") or 0),
                    int(usage.get("total_tokens") or 0),
                    max(0, int(candidate_count or 0)), str(error or "")[:500],
                ),
            )

    def summary(self, days=30):
        since = int(time.time()) - max(1, int(days)) * 86400
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) calls,
                       SUM(success) successes,
                       COALESCE(SUM(prompt_tokens), 0) prompt_tokens,
                       COALESCE(SUM(completion_tokens), 0) completion_tokens,
                       COALESCE(SUM(total_tokens), 0) total_tokens,
                       COALESCE(AVG(latency_ms), 0) avg_latency_ms,
                       COALESCE(MIN(latency_ms), 0) min_latency_ms,
                       COALESCE(MAX(latency_ms), 0) max_latency_ms
                FROM llm_request_stats WHERE created_at >= ?
                """,
                (since,),
            ).fetchone()
            return dict(row)

    def recent(self, limit=100):
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT created_at, profile_id, model, operation, success, latency_ms,
                       prompt_tokens, completion_tokens, total_tokens, candidate_count, error
                FROM llm_request_stats ORDER BY id DESC LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
            return [dict(row) for row in rows]

    def grouped_by_model(self, days=30):
        since = int(time.time()) - max(1, int(days)) * 86400
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT model,
                       COUNT(*) calls,
                       SUM(success) successes,
                       COALESCE(SUM(prompt_tokens), 0) prompt_tokens,
                       COALESCE(SUM(completion_tokens), 0) completion_tokens,
                       COALESCE(SUM(total_tokens), 0) total_tokens,
                       COALESCE(AVG(latency_ms), 0) avg_latency_ms,
                       COALESCE(MIN(latency_ms), 0) min_latency_ms,
                       COALESCE(MAX(latency_ms), 0) max_latency_ms,
                       COALESCE(AVG(candidate_count), 0) avg_candidate_count
                FROM llm_request_stats
                WHERE created_at >= ?
                GROUP BY model
                ORDER BY calls DESC, total_tokens DESC, model ASC
                """,
                (since,),
            ).fetchall()
            return [dict(row) for row in rows]
