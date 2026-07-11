"""SQLite 断点续跑状态存储。"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .batch_models import BatchStatus


REUSABLE_STATUSES = {"success", "conflict", "needs_review", "skipped_no_text", "input_too_long"}


@dataclass(frozen=True)
class StateRecord:
    status: BatchStatus
    structured_result_path: Path | None
    raw_response_path: Path | None
    attempts: int


class BatchStateStore:
    """每条记录完成后立即提交，支持进程中断后恢复。"""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.db_path)
        self._connection.row_factory = sqlite3.Row
        self._init_schema()

    def close(self) -> None:
        self._connection.close()

    def reusable_record(
        self,
        *,
        source_file_fingerprint: str,
        worksheet: str,
        original_row_number: int,
        input_hash: str,
        prompt_hash: str,
        schema_version: str,
        model_name: str,
    ) -> StateRecord | None:
        row = self._connection.execute(
            """
            SELECT * FROM batch_records
            WHERE worksheet = ?
              AND original_row_number = ?
              AND input_hash = ?
              AND prompt_hash = ?
              AND schema_version = ?
              AND model_name = ?
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (
                worksheet,
                original_row_number,
                input_hash,
                prompt_hash,
                schema_version,
                model_name,
            ),
        ).fetchone()
        if row is None or row["status"] not in REUSABLE_STATUSES:
            return None
        return StateRecord(
            status=row["status"],
            structured_result_path=Path(row["structured_result_path"]) if row["structured_result_path"] else None,
            raw_response_path=Path(row["raw_response_path"]) if row["raw_response_path"] else None,
            attempts=int(row["attempts"]),
        )

    def mark_processing(
        self,
        *,
        source_file_fingerprint: str,
        worksheet: str,
        original_row_number: int,
        input_hash: str,
        prompt_hash: str,
        schema_version: str,
        model_name: str,
    ) -> int:
        attempts = self._next_attempts(
            source_file_fingerprint=source_file_fingerprint,
            worksheet=worksheet,
            original_row_number=original_row_number,
            input_hash=input_hash,
            prompt_hash=prompt_hash,
            schema_version=schema_version,
            model_name=model_name,
        )
        self.upsert(
            source_file_fingerprint=source_file_fingerprint,
            worksheet=worksheet,
            original_row_number=original_row_number,
            input_hash=input_hash,
            prompt_hash=prompt_hash,
            schema_version=schema_version,
            model_name=model_name,
            status="processing",
            attempts=attempts,
            structured_result_path=None,
            raw_response_path=None,
            error_type=None,
            error_message_sanitized=None,
        )
        return attempts

    def upsert(
        self,
        *,
        source_file_fingerprint: str,
        worksheet: str,
        original_row_number: int,
        input_hash: str,
        prompt_hash: str,
        schema_version: str,
        model_name: str,
        status: BatchStatus,
        attempts: int,
        structured_result_path: Path | None,
        raw_response_path: Path | None,
        error_type: str | None,
        error_message_sanitized: str | None,
    ) -> None:
        now = _utc_now()
        self._connection.execute(
            """
            INSERT INTO batch_records (
                source_file_fingerprint, worksheet, original_row_number, input_hash,
                prompt_hash, schema_version, model_name, status, attempts,
                structured_result_path, raw_response_path, error_type,
                error_message_sanitized, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (
                source_file_fingerprint, worksheet, original_row_number,
                input_hash, prompt_hash, schema_version, model_name
            )
            DO UPDATE SET
                status = excluded.status,
                attempts = excluded.attempts,
                structured_result_path = excluded.structured_result_path,
                raw_response_path = excluded.raw_response_path,
                error_type = excluded.error_type,
                error_message_sanitized = excluded.error_message_sanitized,
                updated_at = excluded.updated_at
            """,
            (
                source_file_fingerprint,
                worksheet,
                original_row_number,
                input_hash,
                prompt_hash,
                schema_version,
                model_name,
                status,
                attempts,
                str(structured_result_path) if structured_result_path else None,
                str(raw_response_path) if raw_response_path else None,
                error_type,
                error_message_sanitized,
                now,
                now,
            ),
        )
        self._connection.commit()

    def _next_attempts(
        self,
        *,
        source_file_fingerprint: str,
        worksheet: str,
        original_row_number: int,
        input_hash: str,
        prompt_hash: str,
        schema_version: str,
        model_name: str,
    ) -> int:
        row = self._connection.execute(
            """
            SELECT attempts FROM batch_records
            WHERE source_file_fingerprint = ?
              AND worksheet = ?
              AND original_row_number = ?
              AND input_hash = ?
              AND prompt_hash = ?
              AND schema_version = ?
              AND model_name = ?
            """,
            (
                source_file_fingerprint,
                worksheet,
                original_row_number,
                input_hash,
                prompt_hash,
                schema_version,
                model_name,
            ),
        ).fetchone()
        return 1 if row is None else int(row["attempts"]) + 1

    def _init_schema(self) -> None:
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS batch_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_file_fingerprint TEXT NOT NULL,
                worksheet TEXT NOT NULL,
                original_row_number INTEGER NOT NULL,
                input_hash TEXT NOT NULL,
                prompt_hash TEXT NOT NULL,
                schema_version TEXT NOT NULL,
                model_name TEXT NOT NULL,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL,
                structured_result_path TEXT,
                raw_response_path TEXT,
                error_type TEXT,
                error_message_sanitized TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (
                    source_file_fingerprint, worksheet, original_row_number,
                    input_hash, prompt_hash, schema_version, model_name
                )
            )
            """
        )
        self._connection.commit()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
