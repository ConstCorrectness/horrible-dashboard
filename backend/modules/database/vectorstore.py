import os
import json
import struct
import math
import sqlite3
from pathlib import Path
from contextlib import contextmanager
from typing import Any, Generator


# Path to the database file
def get_db_path() -> Path:
    data_dir = Path(os.environ.get("HORRIBLE_DATA_DIR", ".data"))
    return data_dir / "vector_store.db"


def float_list_to_bytes(vec: list[float]) -> bytes:
    """Pack a list of floats into a binary BLOB."""
    return struct.pack(f"{len(vec)}f", *vec)


def bytes_to_float_list(b: bytes) -> list[float]:
    """Unpack a binary BLOB into a list of floats."""
    if not b:
        return []
    n = len(b) // 4
    return list(struct.unpack(f"{n}f", b))


def cosine_similarity(v1_bytes: bytes, v2_bytes: bytes) -> float:
    """
    Calculate the cosine similarity between two float vectors represented as bytes.
    If the vector sizes differ (e.g. fallback vs remote model), it truncates/pads gracefully.
    """
    if not v1_bytes or not v2_bytes:
        return 0.0

    n1 = len(v1_bytes) // 4
    n2 = len(v2_bytes) // 4
    if n1 == 0 or n2 == 0:
        return 0.0

    v1 = struct.unpack(f"{n1}f", v1_bytes)
    v2 = struct.unpack(f"{n2}f", v2_bytes)

    # Use the minimum overlapping dimension
    dim = min(n1, n2)

    dot_product = 0.0
    sum_sq1 = 0.0
    sum_sq2 = 0.0

    for i in range(dim):
        dot_product += v1[i] * v2[i]
        sum_sq1 += v1[i] * v1[i]
        sum_sq2 += v2[i] * v2[i]

    # For safety with zero vectors
    if sum_sq1 <= 0.0 or sum_sq2 <= 0.0:
        return 0.0

    return dot_product / (math.sqrt(sum_sq1) * math.sqrt(sum_sq2))


@contextmanager
def get_db_conn() -> Generator[sqlite3.Connection, None, None]:
    """Context manager to yield a SQLite database connection with custom functions registered."""
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    # Register our custom cosine similarity function
    conn.create_function("cosine_similarity", 2, cosine_similarity)

    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Initialize the database tables and indexes."""
    with get_db_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY,
                collection TEXT NOT NULL,
                text TEXT NOT NULL,
                metadata TEXT NOT NULL,
                embedding BLOB NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_collection ON documents(collection)"
        )


def upsert_document(
    doc_id: str,
    collection: str,
    text: str,
    metadata: dict[str, Any],
    embedding: list[float],
) -> None:
    """Upsert a document into the database."""
    metadata_json = json.dumps(metadata)
    embedding_blob = float_list_to_bytes(embedding)

    with get_db_conn() as conn:
        conn.execute(
            """
            INSERT INTO documents (id, collection, text, metadata, embedding)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                collection = excluded.collection,
                text = excluded.text,
                metadata = excluded.metadata,
                embedding = excluded.embedding
            """,
            (doc_id, collection, text, metadata_json, embedding_blob),
        )


def delete_document(doc_id: str) -> bool:
    """Delete a document by ID. Returns True if a document was deleted, False otherwise."""
    with get_db_conn() as conn:
        cursor = conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
        return cursor.rowcount > 0


def search_documents(
    collection: str, query_embedding: list[float], limit: int
) -> list[dict[str, Any]]:
    """Perform a semantic search in a given collection using cosine similarity."""
    query_blob = float_list_to_bytes(query_embedding)

    with get_db_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, collection, text, metadata,
                   cosine_similarity(embedding, ?) AS score
            FROM documents
            WHERE collection = ?
            ORDER BY score DESC
            LIMIT ?
            """,
            (query_blob, collection, limit),
        ).fetchall()

        results = []
        for r in rows:
            results.append(
                {
                    "id": r["id"],
                    "collection": r["collection"],
                    "text": r["text"],
                    "metadata": json.loads(r["metadata"]),
                    "score": float(r["score"]),
                }
            )
        return results


def list_documents(
    collection: str | None, limit: int, offset: int
) -> tuple[list[dict[str, Any]], int]:
    """Retrieve documents optionally filtered by collection with total count."""
    with get_db_conn() as conn:
        if collection:
            total = conn.execute(
                "SELECT COUNT(*) FROM documents WHERE collection = ?", (collection,)
            ).fetchone()[0]
            rows = conn.execute(
                """
                SELECT id, collection, text, metadata, created_at
                FROM documents
                WHERE collection = ?
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
                """,
                (collection, limit, offset),
            ).fetchall()
        else:
            total = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            rows = conn.execute(
                """
                SELECT id, collection, text, metadata, created_at
                FROM documents
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()

        docs = []
        for r in rows:
            docs.append(
                {
                    "id": r["id"],
                    "collection": r["collection"],
                    "text": r["text"],
                    "metadata": json.loads(r["metadata"]),
                    "created_at": r["created_at"],
                }
            )
        return docs, total


def get_db_stats() -> dict[str, Any]:
    """Get database statistics (path, disk size, record counts, active collections)."""
    db_path = get_db_path()
    size_bytes = db_path.stat().st_size if db_path.exists() else 0

    with get_db_conn() as conn:
        total_docs = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]

        collection_rows = conn.execute(
            "SELECT collection, COUNT(*) as count FROM documents GROUP BY collection"
        ).fetchall()

        collections = [
            {"name": r["collection"], "count": r["count"]} for r in collection_rows
        ]

    return {
        "db_path": str(db_path),
        "size_bytes": size_bytes,
        "num_documents": total_docs,
        "collections": collections,
    }
