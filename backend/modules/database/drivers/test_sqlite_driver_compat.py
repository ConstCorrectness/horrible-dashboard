from typing import Any
import sqlite3
import os
import tempfile
from backend.modules.database.drivers.sqlite_driver import introspect, _connect

def test_introspect():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    config = {"path": db_path}

    # Setup test DB
    conn = _connect(config)
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT NOT NULL, age INTEGER)")
    conn.execute("CREATE TABLE posts (id INTEGER PRIMARY KEY, user_id INTEGER, title TEXT NOT NULL)")
    conn.execute("CREATE VIEW user_emails AS SELECT email FROM users")
    conn.commit()
    conn.close()

    try:
        schema = introspect(config)

        tables = {t.name: t for t in schema.tables}

        assert "users" in tables
        assert "posts" in tables
        assert "user_emails" in tables
        assert "sqlite_master" not in tables

        users_table = tables["users"]
        assert len(users_table.columns) == 3

        cols = {c.name: c for c in users_table.columns}
        assert cols["id"].primary_key is True
        assert cols["email"].nullable is False
        assert cols["age"].nullable is True
        assert cols["age"].type == "INTEGER"

        print("Compat tests passed!")

    finally:
        os.unlink(db_path)

if __name__ == "__main__":
    test_introspect()
