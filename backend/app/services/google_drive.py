"""Google Drive file upload service.

Uploads deal photos and documents to a structured folder:
/DispoSwarm/Deals/{deal_id}/

Uses OAuth 2.0 with a refresh token for authentication.
Requires: GOOGLE_DRIVE_CLIENT_ID, GOOGLE_DRIVE_CLIENT_SECRET,
          GOOGLE_DRIVE_REFRESH_TOKEN in .env
"""

import logging
import mimetypes
from io import BytesIO
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

from app.config import settings

logger = logging.getLogger(__name__)

DRIVE_FOLDER_MIME = "application/vnd.google-apps.folder"

# Scopes needed for full Drive file access
SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def _get_credentials() -> Credentials:
    """Build OAuth2 credentials from the configured refresh token."""
    if not settings.google_drive_client_id:
        raise ValueError("GOOGLE_DRIVE_CLIENT_ID is not configured")
    if not settings.google_drive_client_secret:
        raise ValueError("GOOGLE_DRIVE_CLIENT_SECRET is not configured")
    if not settings.google_drive_refresh_token:
        raise ValueError("GOOGLE_DRIVE_REFRESH_TOKEN is not configured")

    creds = Credentials(
        token=None,
        refresh_token=settings.google_drive_refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.google_drive_client_id,
        client_secret=settings.google_drive_client_secret,
        scopes=SCOPES,
    )

    # Refresh the token to get a valid access token
    creds.refresh(Request())
    return creds


def _get_or_create_folder(service, name: str, parent_id: Optional[str] = None) -> str:
    """Get an existing folder by name (and optional parent), or create it.

    Returns the folder ID.
    """
    query = (
        f"name = '{name}' and mimeType = '{DRIVE_FOLDER_MIME}' and trashed = false"
    )
    if parent_id:
        query += f" and '{parent_id}' in parents"

    response = service.files().list(q=query, spaces="drive", fields="files(id)").execute()
    folders = response.get("files", [])

    if folders:
        return folders[0]["id"]

    # Folder doesn't exist — create it
    metadata = {
        "name": name,
        "mimeType": DRIVE_FOLDER_MIME,
    }
    if parent_id:
        metadata["parents"] = [parent_id]

    folder = service.files().create(body=metadata, fields="id").execute()
    logger.info("Created Drive folder: %s (id=%s)", name, folder["id"])
    return folder["id"]


def _ensure_deal_folder(service, deal_id: str) -> str:
    """Ensure the full folder path exists and return the deal folder ID.

    Creates: DispoSwarm/ → Deals/ → {deal_id}/
    """
    root_id = _get_or_create_folder(service, "DispoSwarm")
    deals_id = _get_or_create_folder(service, "Deals", parent_id=root_id)
    deal_folder_id = _get_or_create_folder(service, deal_id, parent_id=deals_id)
    return deal_folder_id


async def upload_file(file_content: bytes, filename: str, mime_type: str, deal_id: str) -> str:
    """Upload a file to Google Drive under the deal's folder.

    Args:
        file_content: Raw bytes of the file.
        filename: Original filename (e.g. "photo.jpg", "contract.pdf").
        mime_type: MIME type of the file (e.g. "image/jpeg", "application/pdf").
        deal_id: UUID string of the deal to associate the file with.

    Returns:
        A shareable Google Drive URL (anyone with the link can view).

    Raises:
        ValueError: If Drive credentials are not configured.
        googleapiclient.errors.HttpError: If the Drive API call fails.
    """
    import asyncio

    def _upload() -> str:
        creds = _get_credentials()
        service = build("drive", "v3", credentials=creds)

        # Ensure the deal folder exists
        folder_id = _ensure_deal_folder(service, deal_id)

        # Prepare the file metadata
        file_metadata = {
            "name": filename,
            "parents": [folder_id],
        }

        # Prepare the media body
        media = MediaIoBaseUpload(
            BytesIO(file_content),
            mimetype=mime_type or "application/octet-stream",
            resumable=True,
        )

        # Upload the file
        uploaded = (
            service.files()
            .create(body=file_metadata, media_body=media, fields="id, webViewLink")
            .execute()
        )

        file_id = uploaded["id"]
        logger.info(
            "Uploaded %s to Drive (id=%s, folder=%s)", filename, file_id, folder_id
        )

        # Set sharing permission — anyone with the link can view
        try:
            service.permissions().create(
                fileId=file_id,
                body={"type": "anyone", "role": "reader"},
                fields="id",
            ).execute()
        except Exception as e:
            logger.warning("Failed to set sharing permission for %s: %s", file_id, e, exc_info=True)

        # Return the web view link (fall back to built link if not returned)
        web_view_link = uploaded.get("webViewLink")
        if not web_view_link:
            web_view_link = f"https://drive.google.com/file/d/{file_id}/view"
        return web_view_link

    return await asyncio.to_thread(_upload)


async def upload_multiple(
    files: list[tuple[bytes, str, str]], deal_id: str
) -> list[str]:
    """Upload multiple files to Google Drive for a single deal.

    Args:
        files: List of (file_content, filename, mime_type) tuples.
        deal_id: UUID string of the deal.

    Returns:
        List of shareable Google Drive URLs, one per file.
    """
    urls = []
    for content, filename, mime_type in files:
        try:
            url = await upload_file(content, filename, mime_type, deal_id)
            urls.append(url)
        except Exception as e:
            logger.error("Failed to upload %s for deal %s: %s", filename, deal_id, e, exc_info=True)
            # Continue with remaining files
    return urls
