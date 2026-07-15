from typing import Any
import logging
from backend.modules.tasks import queue
from backend.modules.library.ingest import ingest_source
from backend.modules.library.models import IngestRequest

logger = logging.getLogger(__name__)

async def handle_ingest_source(payload: dict[str, Any]) -> None:
    source_id = payload.get("source_id")
    req_dict = payload.get("req")
    if not source_id or not req_dict:
        logger.error(f"Invalid payload for ingest_source: {payload}")
        return
    
    req = IngestRequest(**req_dict)
    await ingest_source(source_id, req)

queue.register_handler("ingest_source", handle_ingest_source)
