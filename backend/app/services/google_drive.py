"""Google Drive file upload and archive service.

Uploads deal photos and documents to a structured folder:
/DispoSwarm/Deals/{deal_id}/

Archives closed deal folders to:
/DispoSwarm/Closed Deals Archive/{deal_id}/

Uses OAuth 2.0 with a refresh token for authentication.
Requires: GOOGLE_DRIVE_CLIENT_ID, GOOGLE_DRIVE_CLIENT_SECRET,
          GOOGLE_DRIVE_REFRESH_TOKEN in .env
"""

import logging
from datetime import date
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

# ---------------------------------------------------------------------------
# Credentials & core helpers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


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
        folder_id = _ensure_deal_folder(service, deal_id)

        file_metadata = {
            "name": filename,
            "parents": [folder_id],
        }
        media = MediaIoBaseUpload(
            BytesIO(file_content),
            mimetype=mime_type or "application/octet-stream",
            resumable=True,
        )
        uploaded = (
            service.files()
            .create(body=file_metadata, media_body=media, fields="id, webViewLink")
            .execute()
        )
        file_id = uploaded["id"]
        logger.info("Uploaded %s to Drive (id=%s, folder=%s)", filename, file_id, folder_id)

        try:
            service.permissions().create(
                fileId=file_id,
                body={"type": "anyone", "role": "reader"},
                fields="id",
            ).execute()
        except Exception as e:
            logger.warning("Failed to set sharing permission for %s: %s", file_id, e, exc_info=True)

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
    return urls


# ---------------------------------------------------------------------------
# Archive & permission management
# ---------------------------------------------------------------------------

# AppState key for caching the archive folder ID
KEY_DRIVE_ARCHIVE_FOLDER = "drive_archive_folder_id"


async def _get_cached_archive_folder_id() -> Optional[str]:
    """Load the cached archive folder ID from app_state."""
    try:
        from app.database import async_session_factory
        from app.models.models import AppState
        from sqlalchemy import select
        async with async_session_factory() as db:
            row = await db.get(AppState, KEY_DRIVE_ARCHIVE_FOLDER)
            if row is not None:
                value = row.value
                if isinstance(value, dict):
                    return value.get("folder_id")
                return str(value) if value else None
            return None
    except Exception as e:
        logger.warning("Failed to load cached archive folder ID: %s", e, exc_info=True)
        return None


async def _cache_archive_folder_id(folder_id: str) -> None:
    """Persist the archive folder ID to app_state for reuse."""
    try:
        from app.database import async_session_factory
        from app.models.models import AppState
        from datetime import datetime, timezone
        async with async_session_factory() as db:
            existing = await db.get(AppState, KEY_DRIVE_ARCHIVE_FOLDER)
            if existing is not None:
                existing.value = {"folder_id": folder_id}
                existing.updated_at = datetime.now(timezone.utc)
            else:
                db.add(AppState(key=KEY_DRIVE_ARCHIVE_FOLDER, value={"folder_id": folder_id}))
            await db.commit()
            logger.debug("Cached archive folder ID: %s", folder_id)
    except Exception as e:
        logger.warning("Failed to cache archive folder ID: %s", e, exc_info=True)


async def get_or_create_archive_folder(drive_service) -> str:
    """Get or create the 'Closed Deals Archive' folder in Google Drive root.

    Checks app_state cache first to avoid redundant Drive API calls.
    If not cached, searches Drive for an existing 'Closed Deals Archive'
    folder at root level. If found, caches and returns its ID. If not found,
    creates the folder, caches, and returns the new ID.

    Returns:
        The folder ID of the archive folder.
    """
    # Check cache first
    cached = await _get_cached_archive_folder_id()
    if cached:
        return cached

    def _find_or_create() -> str:
        # Search for existing archive folder at root level
        query = (
            f"name = 'Closed Deals Archive'"
            f" and mimeType = '{DRIVE_FOLDER_MIME}'"
            f" and trashed = false"
            f" and 'root' in parents"
        )
        response = drive_service.files().list(
            q=query, spaces="drive", fields="files(id)"
        ).execute()
        folders = response.get("files", [])

        if folders:
            return folders[0]["id"]

        # Create the archive folder at root level
        metadata = {
            "name": "Closed Deals Archive",
            "mimeType": DRIVE_FOLDER_MIME,
            "parents": ["root"],
        }
        folder = drive_service.files().create(body=metadata, fields="id").execute()
        logger.info("Created 'Closed Deals Archive' folder (id=%s)", folder["id"])
        return folder["id"]

    import asyncio
    folder_id = await asyncio.to_thread(_find_or_create)

    # Cache for future use
    await _cache_archive_folder_id(folder_id)

    return folder_id


async def archive_deal_folder(
    drive_service,
    deal_folder_id: str,
    deal_address: str,
) -> dict:
    """Move a deal's Drive folder to the archive folder.

    Steps:
    1. Get or create the 'Closed Deals Archive' folder
    2. Move the deal folder into the archive by changing its parent
    3. Rename the archived folder to include the close date

    Args:
        drive_service: Authenticated Google Drive service instance.
        deal_folder_id: The Drive folder ID of the deal to archive.
        deal_address: The deal's address (used in the renamed folder).

    Returns:
        dict with keys:
            success (bool): Whether the archive operation succeeded.
            archive_folder_id (str): ID of the 'Closed Deals Archive' folder.
            archived_folder_id (str): The deal folder's ID (same, just moved).
            error (Optional[str]): Error message if failed.
    """
    import asyncio

    try:
        archive_folder_id = await get_or_create_archive_folder(drive_service)

        def _archive() -> None:
            # Move folder into archive parent
            drive_service.files().update(
                fileId=deal_folder_id,
                addParents=archive_folder_id,
                removeParents="root",
                fields="id, parents",
            ).execute()

            # Rename to include close date
            today_str = date.today().isoformat()
            new_name = f"[CLOSED {today_str}] {deal_address}"
            drive_service.files().update(
                fileId=deal_folder_id,
                body={"name": new_name},
                fields="id",
            ).execute()

            logger.info(
                "Archived deal folder %s → '%s' (archive=%s)",
                deal_folder_id, new_name, archive_folder_id,
            )

        await asyncio.to_thread(_archive)

        return {
            "success": True,
            "archive_folder_id": archive_folder_id,
            "archived_folder_id": deal_folder_id,
            "error": None,
        }

    except Exception as e:
        logger.error(
            "Failed to archive deal folder %s: %s",
            deal_folder_id, e, exc_info=True,
        )
        return {
            "success": False,
            "archive_folder_id": "",
            "archived_folder_id": deal_folder_id,
            "error": str(e),
        }


async def revoke_shared_links(
    drive_service,
    folder_id: str,
) -> int:
    """Remove 'anyone with link' sharing permissions from all files in a folder.

    Lists all files in the folder recursively, finds permissions where
    type="anyone", and deletes them.

    Args:
        drive_service: Authenticated Google Drive service instance.
        folder_id: The folder ID whose files' permissions should be revoked.

    Returns:
        int: Number of permissions revoked.
    """
    import asyncio

    def _revoke() -> int:
        revoked_count = 0
        page_token = None

        while True:
            # List files in the folder (non-recursive — just direct children)
            response = drive_service.files().list(
                q=f"'{folder_id}' in parents and trashed = false",
                spaces="drive",
                fields="nextPageToken, files(id, name)",
                pageToken=page_token,
            ).execute()

            files = response.get("files", [])

            for file_entry in files:
                file_id = file_entry["id"]
                try:
                    # List permissions on this file
                    perms = drive_service.permissions().list(
                        fileId=file_id,
                        fields="permissions(id, type)",
                    ).execute()

                    for perm in perms.get("permissions", []):
                        if perm.get("type") == "anyone":
                            drive_service.permissions().delete(
                                fileId=file_id,
                                permissionId=perm["id"],
                            ).execute()
                            revoked_count += 1
                except Exception as e:
                    logger.warning(
                        "Failed to revoke permissions on file %s: %s",
                        file_id, e, exc_info=True,
                    )

            page_token = response.get("nextPageToken")
            if not page_token:
                break

        if revoked_count > 0:
            logger.info(
                "Revoked %d shared link permission(s) in folder %s",
                revoked_count, folder_id,
            )
        return revoked_count

    return await asyncio.to_thread(_revoke)


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
