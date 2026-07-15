import json
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from backend.modules.database.secrets_store import get_secret
from backend.modules.tasks import queue
from backend.modules.library.store import create_source
from backend.modules.library.models import IngestRequest
from backend.modules.library.queue_handlers import ingest_source
import logging

logger = logging.getLogger(__name__)

def get_credentials():
    creds_json = get_secret("google_oauth_credentials")
    if not creds_json:
        return None
    try:
        creds_data = json.loads(creds_json)
        return Credentials.from_authorized_user_info(creds_data)
    except Exception as e:
        logger.error(f"Failed to load google credentials: {e}")
        return None

async def sync_google_drive(payload: dict) -> None:
    """Syncs recent files from Google Drive and pushes them to the library."""
    creds = get_credentials()
    if not creds:
        logger.warning("Skipping Google Drive sync: no credentials found.")
        return

    try:
        service = build("drive", "v3", credentials=creds)
        
        # We query for documents and PDFs modified recently, or just the top 20 recent docs
        # In a real sync we'd use pageTokens and sync state, but for MVP we fetch top 20 text/pdf files.
        query = "(mimeType='application/pdf' or mimeType='application/vnd.google-apps.document' or mimeType='text/plain') and trashed = false"
        
        results = service.files().list(
            q=query,
            pageSize=20, 
            fields="nextPageToken, files(id, name, mimeType, modifiedTime, webViewLink)",
            orderBy="modifiedTime desc"
        ).execute()
        
        items = results.get("files", [])
        if not items:
            logger.info("No files found in Google Drive.")
            return

        for item in items:
            file_id = item["id"]
            name = item["name"]
            mime = item["mimeType"]
            link = item.get("webViewLink")
            
            logger.info(f"Syncing Google Drive file: {name} ({file_id})")
            
            text_content = ""
            
            # Export Google Docs to plain text
            if mime == "application/vnd.google-apps.document":
                try:
                    request = service.files().export_media(fileId=file_id, mimeType="text/plain")
                    text_content = request.execute().decode("utf-8")
                except HttpError as e:
                    logger.error(f"Failed to export google doc {name}: {e}")
                    continue
            
            # Download plain text files
            elif mime == "text/plain":
                try:
                    request = service.files().get_media(fileId=file_id)
                    text_content = request.execute().decode("utf-8", errors="ignore")
                except HttpError as e:
                    logger.error(f"Failed to download text file {name}: {e}")
                    continue
            
            # For PDFs, we'd need to download and parse. We can save to a temp file and use PyMuPDF.
            elif mime == "application/pdf":
                # To keep it simple for now, we'll skip PDFs or just note them. 
                # (PyMuPDF integration would go here).
                logger.info(f"Skipping PDF {name} for now (requires PyMuPDF byte parsing)")
                continue

            if text_content:
                # Add to library
                source = create_source(
                    library="google_drive",
                    type="note",
                    title=name,
                    url=link,
                    author="Google Drive",
                    tags=["google-drive"]
                )
                
                # We can reuse ingest_source since we already extracted the text
                await ingest_source(source["id"], IngestRequest(
                    type="note",
                    library="google_drive",
                    text=text_content,
                    title=name,
                    url=link,
                ))

    except HttpError as error:
        logger.error(f"An error occurred during Google Drive sync: {error}")

queue.register_handler("sync_google_drive", sync_google_drive)

