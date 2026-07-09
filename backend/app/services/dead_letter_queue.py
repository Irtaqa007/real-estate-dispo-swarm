"""Dead Letter Queue service for failed campaign emails.

When send_email fails after all retry attempts, the campaign is moved to
the failed_campaigns table with the full error details. A retry endpoint
allows re-attempting delivery of failed campaigns.
"""

import logging
import smtplib
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Campaign, FailedCampaign
from app.services.gmail_service import send_email as send_email_resilient

logger = logging.getLogger(__name__)

# Minimum cooldown between retry attempts (in seconds).
# Prevents rapid, repeated retries that waste API calls.
RETRY_COOLDOWN_SECONDS = 3600  # 1 hour


async def move_to_dlq(
    db: AsyncSession,
    campaign: Campaign,
    error_message: str,
) -> FailedCampaign:
    """Move a failed campaign to the dead letter queue.

    Sets the campaign status to 'Failed' and creates a FailedCampaign record
    with the error details for later retry.

    Args:
        db: Database session.
        campaign: The campaign that failed to send.
        error_message: Description of the error.

    Returns:
        The created FailedCampaign record.
    """
    # Create DLQ record
    dlq_entry = FailedCampaign(
        id=uuid.uuid4(),
        campaign_id=campaign.id,
        error_message=error_message[:2000],  # Truncate to fit column
        retry_count=0,
        resolved=False,
    )
    db.add(dlq_entry)

    # Update campaign status
    campaign.status = "Failed"
    db.add(campaign)

    logger.error(
        "Campaign %s (touch %d) moved to DLQ — error: %.200s",
        campaign.id, campaign.touch_number, error_message,
    )

    return dlq_entry


async def retry_failed_campaign(
    db: AsyncSession,
    dlq_entry: FailedCampaign,
) -> dict:
    """Retry sending a failed campaign.

    Increments retry_count, attempts to send the email via Gmail SMTP,
    and marks the DLQ entry as resolved if successful.

    Args:
        db: Database session.
        dlq_entry: The FailedCampaign record to retry.

    Returns:
        dict with keys: success (bool), error (str or None).
    """
    # Fetch the campaign
    campaign = await db.get(Campaign, dlq_entry.campaign_id)
    if not campaign:
        return {
            "success": False,
            "error": f"Campaign {dlq_entry.campaign_id} not found",
        }

    # Fetch the buyer for their email
    from app.models.models import Buyer
    buyer = await db.get(Buyer, campaign.buyer_id)
    if not buyer or not buyer.email:
        return {
            "success": False,
            "error": f"Buyer {campaign.buyer_id} not found or no email",
        }

    if not campaign.subject or not campaign.body:
        return {
            "success": False,
            "error": "Campaign has no subject or body content",
        }

    # Enforce cooldown: reject retry if last attempt was within the cooldown window
    if dlq_entry.last_retry_at is not None:
        seconds_since_last = (datetime.now(timezone.utc) - dlq_entry.last_retry_at).total_seconds()
        if seconds_since_last < RETRY_COOLDOWN_SECONDS:
            remaining = int(RETRY_COOLDOWN_SECONDS - seconds_since_last)
            minutes_remaining = remaining // 60
            logger.warning(
                "DLQ retry blocked by cooldown for campaign %s — last retry %ds ago, "
                "need %ds cooldown (%dmin remaining)",
                dlq_entry.campaign_id, int(seconds_since_last),
                RETRY_COOLDOWN_SECONDS, minutes_remaining,
            )
            return {
                "success": False,
                "error": f"Cooldown active. Wait {minutes_remaining} minute(s) before retrying.",
            }

    # Update retry metadata
    dlq_entry.retry_count += 1
    dlq_entry.last_retry_at = datetime.now(timezone.utc)
    db.add(dlq_entry)

    try:
        # Attempt to send the email
        send_result = await send_email_resilient(
            to=buyer.email if buyer else None,  # type: ignore
            subject=campaign.subject,
            body=campaign.body,
            campaign_id=campaign.id.hex,
            send_type="campaign",
        )

        # Mark as resolved
        dlq_entry.resolved = True
        campaign.status = "Sent"
        campaign.sent_at = datetime.now(timezone.utc)
        db.add(dlq_entry)
        db.add(campaign)

        logger.info(
            "DLQ retry successful: campaign %s (touch %d) sent to %s — message_id: %s",
            campaign.id, campaign.touch_number, buyer.email,
            send_result.get("message_id", "unknown"),
        )

        return {"success": True, "error": None}

    except Exception as e:
        error_str = str(e)[:2000]
        logger.warning(
            "DLQ retry failed for campaign %s (attempt %d): %s",
            campaign.id, dlq_entry.retry_count, error_str,
            exc_info=True,
        )

        # Update error message with latest failure
        dlq_entry.error_message = error_str
        db.add(dlq_entry)

        return {"success": False, "error": error_str}
