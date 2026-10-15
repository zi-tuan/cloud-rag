"""认证和切换状态使用的 SQLite 连接；与信封计数器持久化分开。"""
from contextlib import contextmanager
from pathlib import Path
import sqlite3


class SQLiteDatabase:
    def __init__(self, path: str | Path):
        self.path = str(path)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            db.execute("PRAGMA synchronous=FULL")
            with db:
                yield db
        finally:
            db.close()
