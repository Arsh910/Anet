"""store.py — Lightweight SQLite conversation store (replaces LangGraph AsyncSqliteSaver)."""

import aiosqlite


class ConversationStore:
    """Per-session message store backed by SQLite. Only persists message history —
    plan/step state lives as local variables inside Engine.run_turn()."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def __aenter__(self) -> "ConversationStore":
        self._db = await aiosqlite.connect(self._db_path)
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                thread  TEXT    NOT NULL,
                role    TEXT    NOT NULL,
                content TEXT    NOT NULL
            )
        """)
        await self._db.commit()
        return self

    async def __aexit__(self, *_exc) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    # ── Read ──────────────────────────────────────────────────────────────────

    async def load(self, thread_id: str) -> list[dict]:
        """Return all messages for a thread as [{"role": ..., "content": ...}]."""
        async with self._db.execute(
            "SELECT role, content FROM messages WHERE thread = ? ORDER BY id",
            (thread_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [{"role": r[0], "content": r[1]} for r in rows]

    async def message_count(self, thread_id: str) -> int:
        async with self._db.execute(
            "SELECT COUNT(*) FROM messages WHERE thread = ?", (thread_id,)
        ) as cur:
            row = await cur.fetchone()
        return row[0] if row else 0

    # ── Write ─────────────────────────────────────────────────────────────────

    async def append(self, thread_id: str, role: str, content: str) -> None:
        await self._db.execute(
            "INSERT INTO messages (thread, role, content) VALUES (?, ?, ?)",
            (thread_id, role, content),
        )
        await self._db.commit()

    async def replace_all(self, thread_id: str, messages: list[dict]) -> None:
        """Replace entire thread history — used by /forget and /compress."""
        await self._db.execute("DELETE FROM messages WHERE thread = ?", (thread_id,))
        for m in messages:
            await self._db.execute(
                "INSERT INTO messages (thread, role, content) VALUES (?, ?, ?)",
                (thread_id, m["role"], m["content"]),
            )
        await self._db.commit()
