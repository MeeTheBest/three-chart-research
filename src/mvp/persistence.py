"""Durable, bounded snapshots for the one-process research service.

SQLite is used on a developer's computer; PostgreSQL is used on Render.
Snapshots contain JSON, never executable pickle data. Compression keeps full
model replies affordable on the free database without trimming their content.
"""

from __future__ import annotations

import json
import zlib
from datetime import datetime
from typing import Any

from sqlalchemy import Column, Float, LargeBinary, MetaData, String, Table, create_engine, delete, select


class SnapshotRepository:
    def __init__(self, url: str, table_name: str = "research_sessions") -> None:
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+psycopg://", 1)
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+psycopg://", 1)
        self.engine = create_engine(url, pool_pre_ping=True)
        metadata = MetaData()
        self.table = Table(
            table_name, metadata,
            Column("id", String(64), primary_key=True),
            Column("expires_at", Float, nullable=False, index=True),
            Column("snapshot", LargeBinary, nullable=False),
        )
        metadata.create_all(self.engine)

    def save(self, session_id: str, snapshot: dict[str, Any], expires_at: datetime) -> None:
        encoded = zlib.compress(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")).encode(), 3)
        with self.engine.begin() as connection:
            # A single application process serializes writes under its store
            # lock. Delete+insert is atomic and works on SQLite and PostgreSQL.
            connection.execute(delete(self.table).where(self.table.c.id == session_id))
            connection.execute(self.table.insert().values(id=session_id, expires_at=expires_at.timestamp(), snapshot=encoded))

    def load(self, session_id: str, now: datetime) -> dict[str, Any] | None:
        with self.engine.begin() as connection:
            row = connection.execute(select(self.table.c.snapshot).where(
                self.table.c.id == session_id, self.table.c.expires_at > now.timestamp(),
            )).first()
        return json.loads(zlib.decompress(row[0])) if row else None

    def delete(self, session_id: str) -> None:
        with self.engine.begin() as connection:
            connection.execute(delete(self.table).where(self.table.c.id == session_id))

    def purge_expired(self, now: datetime) -> None:
        with self.engine.begin() as connection:
            connection.execute(delete(self.table).where(self.table.c.expires_at <= now.timestamp()))

    def active_ids(self, now: datetime) -> list[str]:
        with self.engine.connect() as connection:
            return list(connection.execute(select(self.table.c.id).where(self.table.c.expires_at > now.timestamp())).scalars())

    def delete_prefix(self, prefix: str) -> None:
        with self.engine.begin() as connection:
            connection.execute(delete(self.table).where(self.table.c.id.startswith(prefix, autoescape=True)))
