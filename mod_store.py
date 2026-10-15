"""SQLite separado por servidor. Sem credenciais e sem serviços adicionais."""
import asyncio
import os
import sqlite3
from contextlib import closing
from pathlib import Path


class Store:
    def __init__(self, path=None):
        cloud = bool(os.getenv("PORT") or os.getenv("RAILWAY_ENVIRONMENT_ID"))
        mount = os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
        self.persistent = not cloud or bool(mount)
        self.path = Path(path) if path else Path(mount or Path(__file__).parent / "data") / "moderation.sqlite3"

    def _run(self, operation, args):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=10)) as connection, connection:
            connection.row_factory = sqlite3.Row
            connection.execute("CREATE TABLE IF NOT EXISTS config (guild INTEGER PRIMARY KEY, channel INTEGER NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS warnings (id INTEGER PRIMARY KEY AUTOINCREMENT, guild INTEGER NOT NULL, target INTEGER NOT NULL, actor INTEGER NOT NULL, reason TEXT NOT NULL, created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, removed_by INTEGER, removed_reason TEXT)")
            connection.execute("CREATE INDEX IF NOT EXISTS warnings_guild_target ON warnings(guild,target)")
            if operation == "configure":
                connection.execute("INSERT INTO config VALUES (?,?) ON CONFLICT(guild) DO UPDATE SET channel=excluded.channel",args)
            elif operation == "channel":
                row=connection.execute("SELECT channel FROM config WHERE guild=?",args).fetchone()
                return row[0] if row else None
            elif operation == "warn":
                return connection.execute("INSERT INTO warnings(guild,target,actor,reason) VALUES (?,?,?,?)",args).lastrowid
            elif operation == "list":
                rows=connection.execute("SELECT * FROM warnings WHERE guild=? AND target=? AND removed_by IS NULL ORDER BY id DESC LIMIT 10 OFFSET ?",args).fetchall()
                return [dict(row) for row in rows]
            elif operation == "get":
                row=connection.execute("SELECT * FROM warnings WHERE guild=? AND id=? AND removed_by IS NULL",args).fetchone()
                return dict(row) if row else None
            elif operation == "remove":
                return connection.execute("UPDATE warnings SET removed_by=?,removed_reason=? WHERE guild=? AND id=? AND removed_by IS NULL",args).rowcount
            connection.commit()

    async def call(self, operation, *args):
        return await asyncio.to_thread(self._run, operation, args)
