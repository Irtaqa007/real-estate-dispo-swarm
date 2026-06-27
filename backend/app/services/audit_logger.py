"""Structured audit trail logger.

Provides a consistent interface for logging all significant actions to the
activity_log table. Every action is recorded with:
- who/what performed the action (entity type + ID)
- what action was taken
- structured metadata (JSONB)
- when it happened (server-generated timestamp)

Usage:
    from app.services.audit_logger import audit

    await audit.log_campaign_sent(db, campaign, buyer, send_result)
    await audit.log_reply_received(db, campaign, classification, from_email)
    await audit.log_deal_closed(db, deal, close_result)
    await audit.log_buyer_updated(db, buyer, changes)
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import ActivityLog

logger = logging.getLogger(__name__)


class AuditLogger:
    """Structured audit trail for the application."""

    # ------------------------------------------------------------------
    # Campaign actions
    # ------------------------------------------------------------------

    @staticmethod
    async def log_campaign_sent(
        db: AsyncSession,
        campaign_id: uuid.UUID,
        deal_id: uuid.UUID,
        buyer_id: uuid.UUID,
        touch_number: int,
        to_email: str,
        subject: str,
        message_id: str,
    ) -> None:
        """Log that a campaign email was sent.

        Args:
            db: Database session.
            campaign_id: The campaign that was sent.
            deal_id: The deal this campaign belongs to.
            buyer_id: The buyer who received the email.
            touch_number: Which touch in the sequence (1-6).
            to_email: Recipient email address.
            subject: Email subject line.
            message_id: Gmail SMTP Message-ID.
        """
        entry = ActivityLog(
            id=uuid.uuid4(),
            entity_type="campaign",
            entity_id=campaign_id,
            action="email_sent",
            metadata_json={
                "deal_id": str(deal_id),
                "buyer_id": str(buyer_id),
                "touch_number": touch_number,
                "to_email": to_email,
                "subject": subject[:200],
                "message_id": message_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )
        db.add(entry)

    @staticmethod
    async def log_campaign_failed(
        db: AsyncSession,
        campaign_id: uuid.UUID,
        deal_id: uuid.UUID,
        buyer_id: uuid.UUID,
        touch_number: int,
        to_email: str,
        error: str,
    ) -> None:
        """Log that a campaign email failed to send.

        Args:
            db: Database session.
            campaign_id: The campaign that failed.
            deal_id: The deal this campaign belongs to.
            buyer_id: The buyer who was to receive it.
            touch_number: Which touch in the sequence (1-6).
            to_email: Recipient email address.
            error: Error message from the send attempt.
        """
        entry = ActivityLog(
            id=uuid.uuid4(),
            entity_type="campaign",
            entity_id=campaign_id,
            action="email_failed",
            metadata_json={
                "deal_id": str(deal_id),
                "buyer_id": str(buyer_id),
                "touch_number": touch_number,
                "to_email": to_email,
                "error": error[:500],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )
        db.add(entry)

    # ------------------------------------------------------------------
    # Reply actions
    # ------------------------------------------------------------------

    @staticmethod
    async def log_reply_received(
        db: AsyncSession,
        campaign_id: uuid.UUID,
        from_email: str,
        subject: str,
        reply_intent: str,
        ai_insights: str,
        sentiment: int,
        deal_id: Optional[uuid.UUID] = None,
        buyer_id: Optional[uuid.UUID] = None,
        campaigns_paused: int = 0,
        buybox_updated: bool = False,
    ) -> None:
        """Log that a buyer reply was received and classified.

        Args:
            db: Database session.
            campaign_id: The campaign the reply was matched to.
            from_email: Sender email address.
            subject: Reply subject line.
            reply_intent: Classified intent (Interested, Pass, etc.).
            ai_insights: AI-generated insight summary.
            sentiment: Sentiment score (1-5).
            deal_id: Optional deal UUID.
            buyer_id: Optional buyer UUID.
            campaigns_paused: Number of queued campaigns paused.
            buybox_updated: Whether the buyer's buy box was updated.
        """
        entry = ActivityLog(
            id=uuid.uuid4(),
            entity_type="campaign",
            entity_id=campaign_id,
            action="reply_received",
            metadata_json={
                "from_email": from_email,
                "subject": subject[:200],
                "reply_intent": reply_intent,
                "ai_extracted_insights": ai_insights[:500] if ai_insights else None,
                "sentiment": sentiment,
                "deal_id": str(deal_id) if deal_id else None,
                "buyer_id": str(buyer_id) if buyer_id else None,
                "campaigns_paused": campaigns_paused,
                "buybox_updated": buybox_updated,
            },
        )
        db.add(entry)

    # ------------------------------------------------------------------
    # Deal actions
    # ------------------------------------------------------------------

    @staticmethod
    async def log_deal_closed(
        db: AsyncSession,
        deal_id: uuid.UUID,
        closed_price: float,
        net_spread: float,
        jv_payout: float,
        my_payout: float,
        jv_split_pct: float,
        buyer_id: Optional[uuid.UUID] = None,
        jv_partner_id: Optional[uuid.UUID] = None,
    ) -> None:
        """Log that a deal was closed with payout details.

        Args:
            db: Database session.
            deal_id: The deal that was closed.
            closed_price: Final sale price.
            net_spread: Net spread = closed_price - contract_price.
            jv_payout: Payout to JV partner.
            my_payout: Payout to you.
            jv_split_pct: JV split percentage.
            buyer_id: Optional assigned buyer UUID.
            jv_partner_id: Optional JV partner UUID.
        """
        entry = ActivityLog(
            id=uuid.uuid4(),
            entity_type="deal",
            entity_id=deal_id,
            action="closed",
            metadata_json={
                "closed_price": closed_price,
                "net_spread": net_spread,
                "jv_payout": jv_payout,
                "my_payout": my_payout,
                "jv_split_pct": jv_split_pct,
                "buyer_id": str(buyer_id) if buyer_id else None,
                "jv_partner_id": str(jv_partner_id) if jv_partner_id else None,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )
        db.add(entry)

    @staticmethod
    async def log_deal_status_change(
        db: AsyncSession,
        deal_id: uuid.UUID,
        from_status: str,
        to_status: str,
        assigned_buyer_id: Optional[uuid.UUID] = None,
    ) -> None:
        """Log a deal status transition.

        Args:
            db: Database session.
            deal_id: The deal that changed status.
            from_status: Previous status.
            to_status: New status.
            assigned_buyer_id: Optional buyer UUID assigned.
        """
        entry = ActivityLog(
            id=uuid.uuid4(),
            entity_type="deal",
            entity_id=deal_id,
            action="status_change",
            metadata_json={
                "from_status": from_status,
                "to_status": to_status,
                "assigned_buyer_id": str(assigned_buyer_id) if assigned_buyer_id else None,
            },
        )
        db.add(entry)

    # ------------------------------------------------------------------
    # Buyer actions
    # ------------------------------------------------------------------

    @staticmethod
    async def log_buyer_updated(
        db: AsyncSession,
        buyer_id: uuid.UUID,
        changes: Dict[str, Any],
        updated_by: str = "system",
    ) -> None:
        """Log that a buyer profile was updated with what changed.

        Args:
            db: Database session.
            buyer_id: The buyer that was updated.
            changes: Dict of field names to (old_value, new_value) tuples.
            updated_by: What triggered the update (e.g., 'system', 'ai_classification').
        """
        entry = ActivityLog(
            id=uuid.uuid4(),
            entity_type="buyer",
            entity_id=buyer_id,
            action="profile_updated",
            metadata_json={
                "changes": changes,
                "updated_by": updated_by,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )
        db.add(entry)

    # ------------------------------------------------------------------
    # Generic action
    # ------------------------------------------------------------------

    @staticmethod
    async def log(
        db: AsyncSession,
        entity_type: str,
        entity_id: uuid.UUID,
        action: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Log an arbitrary action to the activity log.

        Args:
            db: Database session.
            entity_type: Type of entity (e.g., 'deal', 'campaign', 'buyer', 'jv').
            entity_id: UUID of the entity.
            action: Action name (e.g., 'created', 'updated', 'deleted').
            metadata: Optional dict of additional metadata.
        """
        entry = ActivityLog(
            id=uuid.uuid4(),
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            metadata_json=metadata or {},
        )
        db.add(entry)


# Singleton instance for convenient import
audit = AuditLogger()
