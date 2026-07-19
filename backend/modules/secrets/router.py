from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.modules.database import secrets_store

router = APIRouter(prefix="/secrets", tags=["secrets"])


class SecretInput(BaseModel):
    provider_name: str
    secret_value: str


class SecretResponse(BaseModel):
    provider_name: str


@router.on_event("startup")
async def startup_event():
    # Ensure the secrets table exists
    secrets_store.init_db()


@router.get("/")
async def list_secrets():
    """List all stored secret providers (without returning the keys)."""
    providers = secrets_store.list_providers()
    return {"providers": providers}


@router.put("/")
async def upsert_secret(secret_in: SecretInput):
    """Store or update a secret for a provider."""
    try:
        secrets_store.upsert_secret(secret_in.provider_name, secret_in.secret_value)
        return SecretResponse(provider_name=secret_in.provider_name)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{provider_name}")
async def delete_secret(provider_name: str):
    """Delete a secret by provider name."""
    success = secrets_store.delete_secret(provider_name)
    if not success:
        raise HTTPException(status_code=404, detail="Provider not found")
    return {"status": "success"}
