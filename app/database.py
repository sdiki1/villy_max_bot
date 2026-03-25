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
IS_POSTGRES = engine.url.get_backend_name().startswith("postgresql")
INIT_DB_LOCK_KEY_1 = 24031991
INIT_DB_LOCK_KEY_2 = 17042024

SessionFactory = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


async def init_db() -> None:
    from app import models  # noqa: F401

    async with engine.begin() as conn:
        if IS_POSTGRES:
            await _acquire_init_db_lock(conn)
        await conn.run_sync(Base.metadata.create_all)
        await _ensure_single_support_session_per_user(conn)
        await _ensure_user_schema(conn)
        await _ensure_support_message_schema(conn)
        await _ensure_order_schema(conn)
        await _ensure_wb_auto_reply_schema(conn)


async def _acquire_init_db_lock(conn) -> None:
    await conn.execute(
        text(
            """
            SELECT pg_advisory_xact_lock(:lock_key_1, :lock_key_2)
            """
        ),
        {
            "lock_key_1": INIT_DB_LOCK_KEY_1,
            "lock_key_2": INIT_DB_LOCK_KEY_2,
        },
    )


async def _ensure_single_support_session_per_user(conn) -> None:
    if await _support_session_unique_index_exists(conn):
        return

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


async def _support_session_unique_index_exists(conn) -> bool:
    result = await conn.scalar(
        text(
            """
            SELECT to_regclass('public.uq_support_sessions_user_id') IS NOT NULL
            """
        )
    )
    return bool(result)


async def _ensure_wb_auto_reply_schema(conn) -> None:
    await conn.execute(
        text(
            """
            ALTER TABLE wb_auto_reply_settings
            ADD COLUMN IF NOT EXISTS is_enabled BOOLEAN NOT NULL DEFAULT FALSE
            """
        )
    )
    await conn.execute(
        text(
            """
            ALTER TABLE wb_auto_reply_settings
            ADD COLUMN IF NOT EXISTS answer_template TEXT NOT NULL DEFAULT ''
            """
        )
    )
    await conn.execute(
        text(
            """
            ALTER TABLE wb_auto_reply_settings
            ADD COLUMN IF NOT EXISTS feedback_ai_enabled BOOLEAN NOT NULL DEFAULT FALSE
            """
        )
    )
    await conn.execute(
        text(
            """
            ALTER TABLE wb_auto_reply_settings
            ADD COLUMN IF NOT EXISTS feedback_ai_prompt TEXT NOT NULL DEFAULT ''
            """
        )
    )


async def _ensure_order_schema(conn) -> None:
    await conn.execute(
        text(
            """
            ALTER TABLE orders
            ADD COLUMN IF NOT EXISTS product_size VARCHAR(100)
            """
        )
    )


async def _ensure_user_schema(conn) -> None:
    await conn.execute(
        text(
            """
            ALTER TABLE users
            ADD COLUMN IF NOT EXISTS admin_display_name VARCHAR(255)
            """
        )
    )
    await conn.execute(
        text(
            """
            ALTER TABLE users
            ADD COLUMN IF NOT EXISTS is_archived BOOLEAN NOT NULL DEFAULT FALSE
            """
        )
    )
    await conn.execute(
        text(
            """
            ALTER TABLE users
            ADD COLUMN IF NOT EXISTS is_chat_mode BOOLEAN NOT NULL DEFAULT FALSE
            """
        )
    )


async def _ensure_support_message_schema(conn) -> None:
    await conn.execute(
        text(
            """
            ALTER TABLE support_messages
            ADD COLUMN IF NOT EXISTS max_message_id VARCHAR(255)
            """
        )
    )
    await conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_support_messages_max_message_id
            ON support_messages (max_message_id)
            """
        )
    )


async def dispose_db() -> None:
    await engine.dispose()


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async with SessionFactory() as session:
        yield session
