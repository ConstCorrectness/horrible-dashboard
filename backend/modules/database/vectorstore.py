import os
import json
import time
from pathlib import Path
from typing import Any

import lancedb

def get_db_path() -> Path:
    data_dir = Path(os.environ.get("HORRIBLE_DATA_DIR", ".data"))
    return data_dir / "lancedb"

def _get_db():
    db_path = get_db_path()
    db_path.mkdir(parents=True, exist_ok=True)
    return lancedb.connect(str(db_path))

def init_db() -> None:
    """Initialize the database. LanceDB handles this automatically on connect/create_table."""
    _get_db()

def upsert_document(
    doc_id: str,
    collection: str,
    text: str,
    metadata: dict[str, Any],
    embedding: list[float],
) -> None:
    """Upsert a document into the database."""
    db = _get_db()
    data = [{
        "id": doc_id,
        "text": text,
        "metadata": json.dumps(metadata),
        "vector": embedding,
        "created_at": time.time(),
    }]
    
    if collection in db.table_names():
        tbl = db.open_table(collection)
        # LanceDB merge insert
        tbl.merge_insert("id") \
            .when_matched_update_all() \
            .when_not_matched_insert_all() \
            .execute(data)
    else:
        db.create_table(collection, data=data)

def delete_document(doc_id: str) -> bool:
    """Delete a document by ID across all collections."""
    db = _get_db()
    deleted = False
    for tbl_name in db.table_names():
        tbl = db.open_table(tbl_name)
        # Lancedb delete uses a sql-like where clause
        try:
            # check if it exists first to return correct boolean
            res = tbl.search().where(f"id = '{doc_id}'").limit(1).to_list()
            if res:
                tbl.delete(f"id = '{doc_id}'")
                deleted = True
        except Exception:
            pass
    return deleted

def delete_collection(collection: str) -> int:
    """Delete every document in a collection (used for a full reindex)."""
    db = _get_db()
    if collection in db.table_names():
        tbl = db.open_table(collection)
        count = len(tbl)
        db.drop_table(collection)
        return count
    return 0

def search_documents(
    collection: str, query_embedding: list[float], limit: int
) -> list[dict[str, Any]]:
    """Perform a semantic search in a given collection using cosine similarity."""
    db = _get_db()
    if collection not in db.table_names():
        return []
        
    tbl = db.open_table(collection)
    try:
        # cosine distance is default in LanceDB if we specify it or we can just use search()
        results = tbl.search(query_embedding).metric("cosine").limit(limit).to_list()
    except Exception:
        return []
        
    out = []
    for r in results:
        out.append({
            "id": r["id"],
            "collection": collection,
            "text": r["text"],
            "metadata": json.loads(r["metadata"]),
            "score": 1.0 - float(r.get("_distance", 0.0))  # LanceDB returns distance, convert to similarity
        })
    return out

def list_documents(
    collection: str | None, limit: int, offset: int
) -> tuple[list[dict[str, Any]], int]:
    """Retrieve documents optionally filtered by collection with total count."""
    db = _get_db()
    docs = []
    total = 0
    
    if collection:
        if collection in db.table_names():
            tbl = db.open_table(collection)
            total = len(tbl)
            # Fetch all and sort in python (fine for moderate sizes, or use lancedb query)
            results = tbl.search().limit(total).to_list()
            results.sort(key=lambda x: x.get("created_at", 0), reverse=True)
            page = results[offset:offset+limit]
            for r in page:
                docs.append({
                    "id": r["id"],
                    "collection": collection,
                    "text": r["text"],
                    "metadata": json.loads(r["metadata"]),
                    "created_at": r.get("created_at", 0),
                })
    else:
        # Collect from all tables
        all_results = []
        for tbl_name in db.table_names():
            tbl = db.open_table(tbl_name)
            t_len = len(tbl)
            total += t_len
            res = tbl.search().limit(t_len).to_list()
            for r in res:
                r["_collection"] = tbl_name
            all_results.extend(res)
            
        all_results.sort(key=lambda x: x.get("created_at", 0), reverse=True)
        page = all_results[offset:offset+limit]
        for r in page:
            docs.append({
                "id": r["id"],
                "collection": r["_collection"],
                "text": r["text"],
                "metadata": json.loads(r["metadata"]),
                "created_at": r.get("created_at", 0),
            })
                
    return docs, total

def get_db_stats() -> dict[str, Any]:
    """Get database statistics (path, disk size, record counts, active collections)."""
    db_path = get_db_path()
    size_bytes = 0
    if db_path.exists():
        for f in db_path.glob("**/*"):
            if f.is_file():
                size_bytes += f.stat().st_size
                
    db = _get_db()
    total_docs = 0
    collections = []
    
    for tbl_name in db.table_names():
        tbl = db.open_table(tbl_name)
        count = len(tbl)
        total_docs += count
        collections.append({"name": tbl_name, "count": count})
        
    return {
        "db_path": str(db_path),
        "size_bytes": size_bytes,
        "num_documents": total_docs,
        "collections": collections,
    }
