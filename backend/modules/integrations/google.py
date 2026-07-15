import os
import json
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials

from backend.modules.database.secrets_store import upsert_secret, get_secret

router = APIRouter(prefix="/integrations/google", tags=["integrations"])

# Scopes we need for Google Drive (read-only for syncing)
SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
]

def get_client_config():
    """Retrieve the OAuth client config from secrets or environment."""
    client_id = get_secret("google_client_id") or os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = get_secret("google_client_secret") or os.environ.get("GOOGLE_CLIENT_SECRET")
    if not client_id or not client_secret:
        return None
    
    return {
        "web": {
            "client_id": client_id,
            "project_id": "horrible-dashboard",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "client_secret": client_secret,
        }
    }


@router.get("/auth")
def auth(request: Request):
    """Initiates the OAuth flow and redirects the user to Google."""
    config = get_client_config()
    if not config:
        raise HTTPException(
            status_code=400, 
            detail="Google Client ID and Secret are not configured in the secrets store."
        )

    # Use the request URL to dynamically construct the redirect URI
    redirect_uri = str(request.url_for("auth_callback"))
    
    flow = Flow.from_client_config(
        config,
        scopes=SCOPES,
        redirect_uri=redirect_uri
    )

    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent"  # Ensure we get a refresh token
    )

    return RedirectResponse(auth_url)


@router.get("/callback")
def auth_callback(request: Request, code: str):
    """Callback from Google after successful authentication."""
    config = get_client_config()
    if not config:
        raise HTTPException(status_code=400, detail="Missing Google configuration.")

    redirect_uri = str(request.url_for("auth_callback"))
    flow = Flow.from_client_config(
        config,
        scopes=SCOPES,
        redirect_uri=redirect_uri
    )
    
    try:
        flow.fetch_token(code=code)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to fetch token: {e}")

    credentials = flow.credentials
    # Save the full credentials object as JSON so we can restore it easily.
    creds_json = credentials.to_json()
    upsert_secret("google_oauth_credentials", creds_json)

    # Redirect back to the frontend integration settings
    return RedirectResponse("/settings")

@router.get("/status")
def status():
    """Returns whether Google integration is configured and authenticated."""
    config = get_client_config()
    creds = get_secret("google_oauth_credentials")
    
    is_configured = config is not None
    is_authenticated = creds is not None
    
    return {
        "configured": is_configured,
        "authenticated": is_authenticated
    }

@router.post("/sync")
def trigger_sync():
    """Enqueue a Google Drive sync task."""
    from backend.modules.tasks.queue import enqueue_task
    
    creds = get_secret("google_oauth_credentials")
    if not creds:
        raise HTTPException(status_code=400, detail="Google integration is not authenticated.")
    
    task_id = enqueue_task("sync_google_drive", {})
    return {"status": "queued", "task_id": task_id}
