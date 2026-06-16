"""Title company email coordination router.

Provides endpoints to manually trigger title email checks and view results.
"""

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas import TitleCheckEmailsResponse, TitleEmailCheckItem
from app.services.title_coordinator import process_title_emails

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/title", tags=["title"])


@router.post("/check-emails", response_model=TitleCheckEmailsResponse)
async def check_title_emails(db: AsyncSession = Depends(get_db)):
    """Manually trigger a Gmail inbox check for title company emails.

    Fetches unread emails from the Gmail inbox, filters by known title
    company domains or title-related subject keywords, classifies each
    via Groq AI, updates deal records based on the classified intent
    (Title_Clear, Docs_Needed, Scheduled, Funded, Closed), and logs
    everything to the activity log.

    Returns:
        TitleCheckEmailsResponse with per-email results.
    """
    result = await process_title_emails(db)

    items = [
        TitleEmailCheckItem(**r) for r in result["results"]
    ]

    return TitleCheckEmailsResponse(
        total_found=result["total_found"],
        processed=result["processed"],
        results=items,
    )
