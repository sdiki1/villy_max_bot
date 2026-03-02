from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
engine: AsyncEngine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
)

SessionFactory = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


async def init_db() -> None:
    from app import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _ensure_single_support_session_per_user(conn)


async def _ensure_single_support_session_per_user(conn) -> None:
    # Bring old data to "1 chat (session) = 1 client" shape.
    await conn.execute(
        text(
            """
            WITH aggregates AS (
                SELECT
                    user_id,
                    MIN(id) AS keep_id,
                    BOOL_OR(is_open) AS any_open,
                    MAX(closed_at) AS last_closed_at
                FROM support_sessions
                GROUP BY user_id
            )
            UPDATE support_sessions AS s
            SET
                is_open = a.any_open,
                closed_at = CASE WHEN a.any_open THEN NULL ELSE a.last_closed_at END
            FROM aggregates AS a
            WHERE s.id = a.keep_id
            """
        )
    )

    await conn.execute(
        text(
            """
            WITH ranked AS (
                SELECT
                    id,
                    MIN(id) OVER (PARTITION BY user_id) AS keep_id
                FROM support_sessions
            )
            UPDATE support_messages AS m
            SET session_id = r.keep_id
            FROM ranked AS r
            WHERE m.session_id = r.id
              AND r.id <> r.keep_id
            """
        )
    )

    await conn.execute(
        text(
            """
            DELETE FROM support_sessions AS s
            USING (
                SELECT id
                FROM (
                    SELECT
                        id,
                        ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY id) AS rn
                    FROM support_sessions
                ) AS ranked
                WHERE ranked.rn > 1
            ) AS duplicates
            WHERE s.id = duplicates.id
            """
        )
    )

    await conn.execute(
        text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_support_sessions_user_id
            ON support_sessions (user_id)
            """
        )
    )


async def dispose_db() -> None:
    await engine.dispose()


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async with SessionFactory() as session:
        yield session
