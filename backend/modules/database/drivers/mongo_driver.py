"""MongoDB driver — any Mongo deployment, and the app's own **Atlas cluster**.

The console's motivating case is administering the shared cluster (`backend/atlas.py`:
the social directory's `presence` records, and whatever else grows there), which is
why the built-in `atlas` connection exists — see `connections.py`. Nothing here is
Atlas-specific though: point it at a local `mongod` and it behaves the same.

**Dialect `mongo`, not `json`.** The vector dialect's body (`vector_base.py`) is a
collection plus a filter plus a query vector, because that is genuinely all a vector
store takes. MQL is a real query language, so it gets its own vocabulary — a `find`
with a filter, a projection and a sort, or an aggregation pipeline — rather than
being squeezed into `search`/`list`, which would be the silent reinterpretation the
vector drivers deliberately refuse::

    {"op": "collections"}
    {"op": "find",      "collection": "presence", "filter": {"person_id": "abc"}, "limit": 20}
    {"op": "find",      "collection": "presence", "projection": {"addresses": 1}, "sort": {"ts": -1}}
    {"op": "aggregate", "collection": "presence", "pipeline": [{"$group": {"_id": "$kind", "n": {"$sum": 1}}}]}
    {"op": "count",     "collection": "presence", "filter": {}}
    {"op": "distinct",  "collection": "presence", "field": "person_id"}
    {"op": "describe",  "collection": "presence"}
    {"op": "indexes",   "collection": "presence"}
    {"op": "databases"}
    {"op": "update",    "collection": "presence", "filter": {"_id": {"$oid": "…"}}, "update": {"$set": {"x": 1}}}
    {"op": "delete",    "collection": "presence", "filter": {…}, "many": true}

Three things worth knowing before editing this file:

* **Bodies are parsed as Extended JSON** (``bson.json_util``), so ``{"$oid": "…"}``,
  ``{"$date": "…"}`` and friends work. Guessing instead — coercing any 24-hex string
  to an ObjectId — would silently change the meaning of a filter on a string field.
  Results go the other way through ``jsonable``, which renders an ObjectId as bare
  hex because that is what is readable in a grid; to filter on one you type it back
  as ``{"$oid": "…"}``.
* **``aggregate`` is not inherently a read.** A pipeline ending in ``$out`` or
  ``$merge`` rewrites a collection, so the read-only gate inspects the pipeline
  (``base.mongo_query_is_read_only``), and this driver re-checks rather than trusting
  the caller.
* **Clients are cached per URI.** A ``MongoClient`` is a connection *pool* meant to
  outlive a single query; building one per query would pay SRV resolution plus a TLS
  handshake to Atlas every time. It is thread-safe, so the ``asyncio.to_thread``
  worker threads can share one.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from backend.modules.database.drivers.base import (
    MONGO_READ_OPS,
    ColumnInfo,
    ColumnSchema,
    DatabaseSchema,
    DriverError,
    QueryResult,
    TableSchema,
    jsonable,
    mongo_query_is_read_only,
)

provider = "mongodb"
dialect = "mongo"

# Ops this driver understands. Read ops come from base (shared with the read-only
# gate, so the two can't drift); the writes are listed here.
KNOWN_OPS = MONGO_READ_OPS | {
    "insert",
    "update",
    "delete",
    "create_collection",
    "drop_collection",
    "command",
}

# Documents sampled to infer a collection's fields for the schema sidebar.
_SAMPLE_SIZE = 25

# Cached clients, keyed by connection URI. See the module docstring.
_clients: dict[str, Any] = {}
_clients_lock = threading.Lock()


def _import_pymongo():  # type: ignore[no-untyped-def]
    try:
        from pymongo import MongoClient  # noqa: PLC0415
        from pymongo.errors import PyMongoError  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - pymongo is a core dependency
        raise DriverError("MongoDB support needs the 'pymongo' package.") from exc
    return MongoClient, PyMongoError


def _uri(config: dict[str, Any]) -> str:
    """Build the connection URI. An explicit `uri` wins; otherwise host/port/creds.

    Credentials are percent-encoded for the same reason `atlas.cluster_uri()` does it:
    Mongo passwords routinely contain `@`, `:`, `/` and `#`, every one of which is
    structural in a URI, and skipping this either errors or connects somewhere else.
    """
    from urllib.parse import quote_plus  # noqa: PLC0415

    explicit = str(config.get("uri") or "").strip()
    if explicit:
        return explicit
    host = str(config.get("host") or "").strip()
    if not host:
        raise DriverError(
            "mongodb: set 'uri' (mongodb:// or mongodb+srv://) or at least 'host'."
        )
    try:
        port = int(config.get("port") or 27017)
    except (TypeError, ValueError) as exc:
        # Config comes from a free-text form field, and `_uri` is called before
        # run_query's try block — an uncaught ValueError here would be a 500.
        raise DriverError(
            f"mongodb: 'port' must be a number, got {config['port']!r}."
        ) from exc
    user = str(config.get("user") or "").strip()
    password = str(config.get("password") or "").strip()
    auth = f"{quote_plus(user)}:{quote_plus(password)}@" if user else ""
    params = []
    if config.get("tls"):
        params.append("tls=true")
    if auth_source := str(config.get("auth_source") or "").strip():
        params.append(f"authSource={quote_plus(auth_source)}")
    query = f"?{'&'.join(params)}" if params else ""
    return f"mongodb://{auth}{host}:{port}/{query}"


def _client(config: dict[str, Any]) -> Any:
    MongoClient, _ = _import_pymongo()
    uri = _uri(config)
    with _clients_lock:
        cached = _clients.get(uri)
        if cached is not None:
            return cached
        try:
            client = MongoClient(
                uri,
                # Fail fast: a console query that can't reach the cluster should say
                # so in a couple of seconds, not hang the request for the default 30.
                serverSelectionTimeoutMS=5000,
                connectTimeoutMS=5000,
                appname="horrible-dashboard-console",
            )
        except Exception as exc:
            raise DriverError(f"mongodb: cannot connect: {exc}") from exc
        _clients[uri] = client
        return client


def _database(client: Any, config: dict[str, Any], override: str | None) -> Any:
    """The target database: the body's `db` (admins inspect several), else the
    connection's, else whatever the URI names."""
    name = (override or str(config.get("database") or "")).strip()
    if name:
        return client[name]
    default = client.get_default_database(default=None)
    if default is None:
        raise DriverError(
            "mongodb: no database selected — set 'database' on the connection, "
            'put one in the URI path, or pass "db" in the query body.'
        )
    return default


# ---------------------------------------------------------------------------
# Query body
# ---------------------------------------------------------------------------


class MongoQuery:
    """One parsed query body. Plain attributes; ops read only what they need."""

    def __init__(self, body: dict[str, Any]) -> None:
        self.body = body
        self.op = str(body.get("op") or "").strip().lower()
        self.collection = str(body["collection"]) if body.get("collection") else None
        self.db = str(body["db"]) if body.get("db") else None
        self.filter: dict[str, Any] = body.get("filter") or {}
        self.projection = body.get("projection")
        self.sort = body.get("sort")
        self.pipeline: list[Any] = body.get("pipeline") or []
        self.documents: list[dict[str, Any]] = body.get("documents") or []
        self.update = body.get("update")
        self.field = body.get("field")
        self.command = body.get("command")
        self.many = bool(body.get("many", False))
        self.limit = body.get("limit", 50)
        self.skip = body.get("skip", 0)

    def require_collection(self) -> str:
        if not self.collection:
            raise DriverError(
                f"mongodb: op '{self.op}' needs a \"collection\". "
                f'Example: {{"op": "{self.op}", "collection": "my_collection"}}'
            )
        return self.collection


def parse_query(raw: str) -> MongoQuery:
    """Parse an Extended-JSON query body, or raise DriverError with a usable example.

    Error text matters: this is a surface a human types into, and bson's own
    "Expecting value: line 1 column 1" teaches nobody anything.
    """
    text = (raw or "").strip()
    if not text:
        raise DriverError(
            'mongodb: empty query. Try {"op": "collections"} to list collections.'
        )
    try:
        from bson import json_util  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - ships with pymongo
        raise DriverError("MongoDB support needs the 'pymongo' package.") from exc
    try:
        body = json_util.loads(text)
    except Exception as exc:
        raise DriverError(
            f"mongodb: query must be a JSON object ({exc}). "
            'Example: {"op": "find", "collection": "my_collection", '
            '"filter": {}, "limit": 20}'
        ) from exc
    if not isinstance(body, dict):
        raise DriverError(
            f"mongodb: query must be a JSON object, got {type(body).__name__}."
        )

    op = str(body.get("op") or "").strip().lower()
    if not op:
        raise DriverError(
            f'mongodb: query needs an "op". Known ops: {", ".join(sorted(KNOWN_OPS))}.'
        )
    if op not in KNOWN_OPS:
        raise DriverError(
            f"mongodb: unknown op {op!r}. Known ops: {', '.join(sorted(KNOWN_OPS))}."
        )

    for key in ("filter", "projection", "update", "command"):
        value = body.get(key)
        if value is not None and not isinstance(value, dict):
            raise DriverError(f'mongodb: "{key}" must be an object.')
    if body.get("pipeline") is not None and not isinstance(body["pipeline"], list):
        raise DriverError('mongodb: "pipeline" must be an array of aggregation stages.')
    if body.get("documents") is not None:
        docs = body["documents"]
        if not isinstance(docs, list) or not all(isinstance(d, dict) for d in docs):
            raise DriverError('mongodb: "documents" must be an array of objects.')
    for key in ("limit", "skip"):
        value = body.get(key)
        if value is not None and (
            not isinstance(value, int) or isinstance(value, bool)
        ):
            raise DriverError(f'mongodb: "{key}" must be an integer.')
    if body.get("limit") is not None and body["limit"] < 1:
        raise DriverError('mongodb: "limit" must be a positive integer.')
    if body.get("skip") is not None and body["skip"] < 0:
        raise DriverError('mongodb: "skip" must be a non-negative integer.')

    return MongoQuery(body)


def _sort_spec(sort: Any) -> list[tuple[str, int]] | None:
    """`{"ts": -1, "name": 1}` → pymongo's list of pairs.

    A dict is used rather than a list of pairs because that is what a human types, and
    it is safe here: JSON objects keep their order through `json_util.loads`, so a
    two-key sort stays a two-key sort in the order written.
    """
    if sort is None:
        return None
    if isinstance(sort, list):
        return [(str(k), int(v)) for k, v in sort]
    if not isinstance(sort, dict):
        raise DriverError('mongodb: "sort" must be an object like {"field": -1}.')
    spec = []
    for key, value in sort.items():
        if value not in (1, -1, "asc", "desc"):
            raise DriverError(
                f"mongodb: sort direction for {key!r} must be 1 or -1, got {value!r}."
            )
        spec.append((str(key), 1 if value in (1, "asc") else -1))
    return spec


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


# Kept separate from `vector_base.records_to_result` on purpose: the ordering rules
# differ. A vector hit leads with id/score/text and trails with the raw vector; a
# Mongo document leads with `_id` and otherwise wants its **own field order**
# preserved, since that order is meaningful and stable in a document store.
def _documents_to_result(
    documents: list[dict[str, Any]],
    *,
    started: float,
    row_limit: int,
    message: str | None = None,
) -> QueryResult:
    elapsed = (time.perf_counter() - started) * 1000
    if not documents:
        return QueryResult(
            columns=[],
            rows=[],
            rowcount=0,
            elapsed_ms=elapsed,
            message=message or "OK",
        )
    truncated = len(documents) > row_limit
    kept = documents[:row_limit]

    order: list[str] = []
    for doc in kept:
        for key in doc:
            if key not in order:
                order.append(key)
    if "_id" in order:
        order = ["_id", *[k for k in order if k != "_id"]]

    rows = [[jsonable(doc.get(col)) for col in order] for doc in kept]
    return QueryResult(
        columns=[ColumnInfo(name=c) for c in order],
        rows=rows,
        rowcount=len(rows),
        elapsed_ms=elapsed,
        truncated=truncated,
        message=message,
    )


def _affected(count: int, *, started: float, verb: str) -> QueryResult:
    return QueryResult(
        columns=[],
        rows=[],
        rowcount=0,
        elapsed_ms=(time.perf_counter() - started) * 1000,
        affected=count,
        message=f"{verb} OK",
    )


def _bson_type(value: Any) -> str:
    """A BSON-ish type name for the schema sidebar."""
    name = type(value).__name__
    return {
        "ObjectId": "objectId",
        "dict": "object",
        "list": "array",
        "str": "string",
        "bool": "bool",
        "int": "int",
        "float": "double",
        "NoneType": "null",
        "datetime": "date",
        "Decimal128": "decimal",
        "Binary": "binData",
        "bytes": "binData",
    }.get(name, name)


# ---------------------------------------------------------------------------
# Driver contract
# ---------------------------------------------------------------------------


def test(config: dict[str, Any]) -> None:
    client = _client(config)
    try:
        client.admin.command("ping")
    except Exception as exc:
        raise DriverError(f"mongodb: {exc}") from exc


def _guard_writes(
    config: dict[str, Any], q: MongoQuery, raw: str, read_only: bool
) -> None:
    """Refuse a write when the request asked for read-only — or when the *connection*
    is read-only.

    The connection-level flag is the load-bearing one: the built-in `atlas` connection
    carries `read_only` unless `ATLAS_ADMIN=rw`, and enforcing it here means no caller
    can opt out of it, agent tools included. Checking it in the route only would leave
    `driver.run_query` as an unguarded back door.
    """
    is_read = mongo_query_is_read_only(raw)
    if config.get("read_only") and not is_read:
        hint = (
            " Set ATLAS_ADMIN=rw to allow writes to the shared cluster."
            if config.get("builtin")
            else ""
        )
        raise DriverError(
            f"mongodb: this connection is read-only — op '{q.op}' writes.{hint}"
        )
    if read_only and not is_read:
        detail = (
            " A pipeline with $out or $merge rewrites a collection."
            if q.op == "aggregate"
            else ""
        )
        raise DriverError(
            f"mongodb: op '{q.op}' writes; uncheck read-only to run it.{detail}"
        )


def run_query(
    config: dict[str, Any],
    sql: str,
    params: list[Any] | None = None,
    *,
    read_only: bool = False,
    row_limit: int = 1000,
) -> QueryResult:
    _, PyMongoError = _import_pymongo()
    q = parse_query(sql)
    _guard_writes(config, q, sql, read_only)

    started = time.perf_counter()
    client = _client(config)
    limit = min(int(q.limit), row_limit)

    try:
        if q.op == "databases":
            records = [
                {
                    "database": d.get("name"),
                    "size_on_disk": d.get("sizeOnDisk"),
                    "empty": d.get("empty"),
                }
                for d in client.list_databases()
            ]
            return _documents_to_result(records, started=started, row_limit=row_limit)

        db = _database(client, config, q.db)

        if q.op == "collections":
            records = []
            for name in sorted(db.list_collection_names()):
                try:
                    count = db[name].estimated_document_count()
                except PyMongoError:  # a broken collection should still be listed
                    count = None
                records.append({"collection": name, "documents": count})
            return _documents_to_result(records, started=started, row_limit=row_limit)

        if q.op == "stats":
            stats = db.command("dbStats")
            return _documents_to_result(
                [{k: v for k, v in stats.items() if k != "raw"}],
                started=started,
                row_limit=row_limit,
            )

        if q.op == "command":
            if not isinstance(q.command, dict) or not q.command:
                raise DriverError('mongodb: op "command" needs a "command" object.')
            return _documents_to_result(
                [dict(db.command(q.command))], started=started, row_limit=row_limit
            )

        name = q.require_collection()
        coll = db[name]

        if q.op in {"find", "find_one"}:
            cursor = coll.find(q.filter, q.projection)
            if spec := _sort_spec(q.sort):
                cursor = cursor.sort(spec)
            if q.skip:
                cursor = cursor.skip(int(q.skip))
            # The body's `limit` is this dialect's LIMIT clause, already clamped to the
            # console's row limit, so asking for 20 and getting 20 is not truncation —
            # same as a `SELECT … LIMIT 20`. `truncated` is reserved for the console
            # cutting a result off, which for Mongo only happens in `aggregate`.
            docs = list(cursor.limit(1 if q.op == "find_one" else limit))
            return _documents_to_result(docs, started=started, row_limit=row_limit)

        if q.op == "aggregate":
            if not q.pipeline:
                raise DriverError('mongodb: op "aggregate" needs a "pipeline" array.')
            docs = []
            for doc in coll.aggregate(q.pipeline):
                docs.append(doc)
                if len(docs) > row_limit:
                    break
            return _documents_to_result(docs, started=started, row_limit=row_limit)

        if q.op == "count":
            return _documents_to_result(
                [{"collection": name, "count": coll.count_documents(q.filter)}],
                started=started,
                row_limit=row_limit,
            )

        if q.op == "distinct":
            if not q.field:
                raise DriverError(
                    'mongodb: op "distinct" needs a "field" (the key to collect).'
                )
            values = coll.distinct(str(q.field), q.filter)
            return _documents_to_result(
                [{str(q.field): v} for v in values],
                started=started,
                row_limit=row_limit,
            )

        if q.op == "indexes":
            records = [
                {
                    "name": ix.get("name"),
                    "keys": dict(ix.get("key") or {}),
                    "unique": ix.get("unique", False),
                    "sparse": ix.get("sparse", False),
                    "ttl_seconds": ix.get("expireAfterSeconds"),
                }
                for ix in coll.list_indexes()
            ]
            return _documents_to_result(records, started=started, row_limit=row_limit)

        if q.op == "describe":
            return _documents_to_result(
                _describe_collection(coll), started=started, row_limit=row_limit
            )

        if q.op == "insert":
            if not q.documents:
                raise DriverError('mongodb: op "insert" needs "documents".')
            result = coll.insert_many(q.documents)
            return _affected(len(result.inserted_ids), started=started, verb="INSERT")

        if q.op == "update":
            if not isinstance(q.update, dict) or not q.update:
                raise DriverError(
                    'mongodb: op "update" needs an "update" object, '
                    'e.g. {"$set": {"field": 1}}.'
                )
            if not q.filter:
                raise DriverError(
                    'mongodb: op "update" needs a "filter" — pass {"filter": {}} '
                    "explicitly if you really mean every document."
                )
            result = (
                coll.update_many(q.filter, q.update)
                if q.many
                else coll.update_one(q.filter, q.update)
            )
            return _affected(result.modified_count, started=started, verb="UPDATE")

        if q.op == "delete":
            if not q.filter:
                raise DriverError(
                    'mongodb: op "delete" needs a "filter" — pass {"filter": {}} '
                    "explicitly if you really mean every document."
                )
            result = coll.delete_many(q.filter) if q.many else coll.delete_one(q.filter)
            return _affected(result.deleted_count, started=started, verb="DELETE")

        if q.op == "create_collection":
            db.create_collection(name)
            return _affected(1, started=started, verb="CREATE")

        if q.op == "drop_collection":
            coll.drop()
            return _affected(1, started=started, verb="DROP")

        raise DriverError(f"mongodb: op '{q.op}' is not implemented.")
    except DriverError:
        raise
    except Exception as exc:
        raise DriverError(f"mongodb: {q.op} failed: {exc}") from exc


def _describe_collection(coll: Any) -> list[dict[str, Any]]:
    """Field → types/presence, inferred from a sample.

    A document store has no declared schema, so this is honest sampling, not
    introspection: `in_sample` says how many of the sampled documents carried the
    field, which is the closest thing to "nullable" that exists here.
    """
    docs = list(coll.find({}, limit=_SAMPLE_SIZE))
    seen: dict[str, set[str]] = {}
    counts: dict[str, int] = {}
    for doc in docs:
        for key, value in doc.items():
            seen.setdefault(key, set()).add(_bson_type(value))
            counts[key] = counts.get(key, 0) + 1
    ordered = ["_id", *sorted(k for k in seen if k != "_id")]
    return [
        {
            "field": key,
            "types": ", ".join(sorted(seen[key])),
            "in_sample": f"{counts[key]}/{len(docs)}",
        }
        for key in ordered
        if key in seen
    ]


def introspect(config: dict[str, Any]) -> DatabaseSchema:
    """Collections as tables, with fields sampled from the first few documents.

    Sampling is the only option — Mongo collections have no declared schema — so the
    sidebar shows what is actually in there, and a field missing from every sampled
    document is a field the sidebar can't know about. `$jsonSchema` validators are not
    consulted: most collections have none, and a validator describes what is *allowed*
    rather than what is stored.
    """
    _, PyMongoError = _import_pymongo()
    client = _client(config)
    tables: list[TableSchema] = []
    try:
        db = _database(client, config, None)
        for name in sorted(db.list_collection_names()):
            columns: list[ColumnSchema] = []
            try:
                for row in _describe_collection(db[name]):
                    field = str(row["field"])
                    columns.append(
                        ColumnSchema(
                            name=field,
                            type=str(row["types"]),
                            nullable=field != "_id",
                            primary_key=field == "_id",
                        )
                    )
            except PyMongoError:  # an unreadable collection still belongs in the tree
                pass
            tables.append(TableSchema(name=name, columns=columns))
    except DriverError:
        raise
    except Exception as exc:
        raise DriverError(f"mongodb: introspect failed: {exc}") from exc
    return DatabaseSchema(tables=tables)
