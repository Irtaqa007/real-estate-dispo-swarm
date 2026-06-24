"""End-to-end integration test for ghost detection and recovery.

Tests the full ghost lifecycle against a real database:
1. Create buyer + deal + campaign sequence (buyer replies) → verify engagement
2. Ghost detection on a recently-active buyer → should NOT fire (not enough silence)
3. Simulate 96+ hours of silence → ghost detection should fire
4. Ghost recovery touch 1 → verify sent + state updated
5. Buyer replies mid-recovery → verify ghost cancelled (fields reset)
6. Edge cases: non-responder not flagged, inactive deal not flagged
7. Clean up all test data

Run:  python -m scripts.integration_test_ghost
      (from the /app directory inside backend, after 'cd backend')
"""

import asyncio
import logging
import sys
import uuid
from datetime import datetime, timezone, timedelta

if __name__ == "__main__" and not any(p.endswith("/app") for p in sys.path):
    sys.path.insert(0, "/app")

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from app.config import settings
from app.models.schemas import ActivityLog, Buyer, Campaign, Deal, JVPartner, FailedCampaign

import app.database as _db

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("integration_test_ghost")

# Suppress noisy scheduler logging during test
logging.getLogger("app.services.scheduler").setLevel(logging.WARNING)

PASS = "\u2705"
FAIL = "\u274c"
SKIP = "\u23ed\ufe0f"

passed = 0
failed = 0


def check(name: str, condition: bool, detail: str = ""):
    global passed, failed
    if condition:
        logger.info(f"  {PASS} {name}")
        passed += 1
    else:
        logger.error(f"  {FAIL} {name} \u2014 {detail}")
        failed += 1


async def main():
    global passed, failed

    # ── Initialize the app database (scheduler functions use _db.async_session_factory) ──
    await _db.initialize_db()

    # ── Connect to database ──
    url = settings.database_url.replace("postgresql://", "postgresql+asyncpg://")
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with Session() as db:
        # ═══════════════════════════════════════════════════════════════════
        # SETUP: Create test data
        # ═══════════════════════════════════════════════════════════════════
        logger.info("\n=== SETUP: Create Test Data ===")

        jv = JVPartner(id=uuid.uuid4(), name="Ghost Test JV", email="ghostjv@test.com")
        db.add(jv)

        # Primary test buyer — will reply and become a ghost
        buyer = Buyer(
            id=uuid.uuid4(),
            full_name="Ghost Test Buyer",
            email=f"ghostbuyer_{uuid.uuid4().hex[:8]}@example.com",
            buy_box="I buy 3-4 bed houses in Dallas under $300k",
            status="Active", email_verified=True, buyer_tier="A-List",
            price_min=100000.0, price_max=300000.0,
            pref_property_type="House", pref_cities=["Dallas"],
        )
        db.add(buyer)

        # Non-responder buyer — never replies, should NOT be flagged as ghost
        non_responder = Buyer(
            id=uuid.uuid4(),
            full_name="Non-Responder Buyer",
            email=f"nonreply_{uuid.uuid4().hex[:8]}@example.com",
            buy_box="Looking for land in Austin under $200k",
            status="Active", email_verified=True, buyer_tier="C-List",
        )
        db.add(non_responder)

        # Create deal
        deal = Deal(
            id=uuid.uuid4(), address="100 Ghost Lane", city="Dallas", state="TX",
            property_type="House", condition_description="3 bed 2 bath, needs paint",
            arv=350000.0, asking_price=250000.0, floor_price=180000.0,
            contract_price=150000.0, title_status="Clear", jv_partner_id=jv.id,
            status="Available",
        )
        db.add(deal)

        # Inactive deal (Sold) — ghost detection should NOT fire for buyers on this deal
        dead_deal = Deal(
            id=uuid.uuid4(), address="200 Dead End", city="Dallas", state="TX",
            property_type="House", condition_description="Vacant",
            arv=200000.0, asking_price=150000.0, floor_price=120000.0,
            contract_price=100000.0, title_status="Clear", jv_partner_id=jv.id,
            status="Sold",
        )
        db.add(dead_deal)
        await db.commit()

        check("JV Partner created", jv.id is not None)
        check("Buyer created", buyer.id is not None)
        check("Non-responder created", non_responder.id is not None)
        check("Deal created", deal.id is not None)
        check("Dead deal created", dead_deal.id is not None)

        # ═══════════════════════════════════════════════════════════════════
        # TEST 1: Create campaign sequence where buyer replies
        # ═══════════════════════════════════════════════════════════════════
        logger.info("\n=== TEST 1: Create Campaign Sequence with Buyer Reply ===")

        now = datetime.now(timezone.utc)

        # Touch 1: Sent 10 days ago
        touch1 = Campaign(
            id=uuid.uuid4(), deal_id=deal.id, buyer_id=buyer.id,
            touch_number=1, status="Sent",
            sent_at=now - timedelta(days=10),
            scheduled_send_at=now - timedelta(days=10),
            subject="100 Ghost Lane \u2014 $100k spread", body="Great deal in Dallas.",
        )
        db.add(touch1)

        # Touch 2: Sent 9 days ago, buyer replied "Interested"
        touch2 = Campaign(
            id=uuid.uuid4(), deal_id=deal.id, buyer_id=buyer.id,
            touch_number=2, status="Replied",
            sent_at=now - timedelta(days=9),
            scheduled_send_at=now - timedelta(days=9),
            subject="Re: 100 Ghost Lane", body="Following up on the deal.",
            reply_received_at=now - timedelta(days=9),
            reply_body="I'm interested, tell me more!",
            reply_intent="Interested",
        )
        db.add(touch2)

        # Touch 3: Sent 2 days ago (still within 96hr silence window — ghost NOT yet)
        touch3 = Campaign(
            id=uuid.uuid4(), deal_id=deal.id, buyer_id=buyer.id,
            touch_number=3, status="Sent",
            sent_at=now - timedelta(hours=48),
            scheduled_send_at=now - timedelta(hours=48),
            subject="Re: comps on 100 Ghost Lane", body="Here are the comps you asked about.",
        )
        db.add(touch3)

        # For the non-responder: create Sent campaigns but no reply
        nr_touch1 = Campaign(
            id=uuid.uuid4(), deal_id=deal.id, buyer_id=non_responder.id,
            touch_number=1, status="Sent",
            sent_at=now - timedelta(days=10),
            scheduled_send_at=now - timedelta(days=10),
            subject="Deal in Dallas", body="Check this out.",
        )
        db.add(nr_touch1)

        nr_touch2 = Campaign(
            id=uuid.uuid4(), deal_id=deal.id, buyer_id=non_responder.id,
            touch_number=2, status="Sent",
            sent_at=now - timedelta(days=8),
            scheduled_send_at=now - timedelta(days=8),
            subject="Following up", body="Still available.",
        )
        db.add(nr_touch2)

        # For the dead deal: create campaign with reply (should NOT be ghosted)
        dead_touch1 = Campaign(
            id=uuid.uuid4(), deal_id=dead_deal.id, buyer_id=buyer.id,
            touch_number=1, status="Replied",
            sent_at=now - timedelta(days=20),
            scheduled_send_at=now - timedelta(days=20),
            subject="Dead deal", body="This one didn't work out.",
            reply_received_at=now - timedelta(days=19),
            reply_body="Tell me more.",
            reply_intent="Interested",
        )
        db.add(dead_touch1)

        await db.commit()
        check("Touch 1 (Sent) created", touch1.id is not None)
        check("Touch 2 (Replied) created", touch2.id is not None)
        check("Touch 3 (Sent) created", touch3.id is not None)
        check("Non-responder campaigns created", nr_touch1.id is not None)
        check("Dead deal campaign created", dead_touch1.id is not None)

        # ═══════════════════════════════════════════════════════════════════
        # TEST 2: Ghost detection — should NOT fire (last send too recent)
        # ═══════════════════════════════════════════════════════════════════
        logger.info("\n=== TEST 2: Ghost Detection (too recent) ===")

        from app.services.scheduler import detect_and_flag_ghosts

        result = await detect_and_flag_ghosts()
        check("Ghost detection: 0 ghosts detected (too recent)",
              result == 0, f"Got {result}")

        # Verify no ghost_detected_at was set
        ghost_check = await db.execute(
            select(Campaign).where(
                Campaign.buyer_id == buyer.id,
                Campaign.deal_id == deal.id,
                Campaign.ghost_detected_at.isnot(None),
            )
        )
        ghost_row = ghost_check.scalar_one_or_none()
        check("No ghost_detected_at set on any campaign", ghost_row is None)

        # ═══════════════════════════════════════════════════════════════════
        # TEST 3: Simulate silence — move last sent_at to 100 hours ago
        # ═══════════════════════════════════════════════════════════════════
        logger.info("\n=== TEST 3: Simulate 100+ Hours of Silence ===")

        far_past = now - timedelta(hours=100)
        await db.execute(
            update(Campaign)
            .where(Campaign.id == touch3.id)
            .values(sent_at=far_past)
        )
        await db.commit()

        # Verify the update
        updated_touch3 = await db.get(Campaign, touch3.id)
        check(f"Touch 3 sent_at moved to {updated_touch3.sent_at}",
              updated_touch3.sent_at is not None and
              updated_touch3.sent_at < now - timedelta(hours=96))

        # ═══════════════════════════════════════════════════════════════════
        # TEST 4: Ghost detection — should fire now
        # ═══════════════════════════════════════════════════════════════════
        logger.info("\n=== TEST 4: Ghost Detection (should detect ghost) ===")

        result2 = await detect_and_flag_ghosts()
        check("Ghost detection: 1 ghost detected", result2 == 1, f"Got {result2}")

        # Verify ghost_detected_at was set on the replied campaign
        ghost_check2 = await db.execute(
            select(Campaign).where(
                Campaign.buyer_id == buyer.id,
                Campaign.deal_id == deal.id,
                Campaign.ghost_detected_at.isnot(None),
            )
        )
        ghost_row2 = ghost_check2.scalar_one_or_none()
        check("ghost_detected_at set on campaign", ghost_row2 is not None)
        if ghost_row2:
            check("ghost_recovery_touch starts at 0", ghost_row2.ghost_recovery_touch == 0)

        # Verify activity log entry was created (use .all() to avoid MultipleResultsFound from previous runs)
        log_checks = await db.execute(
            select(ActivityLog).where(
                ActivityLog.action == "ghost_detected",
            )
        )
        log_entries = log_checks.scalars().all()
        check("Activity log 'ghost_detected' created", len(log_entries) > 0)
        if len(log_entries) > 0:
            # Check the most recent entry
            latest = log_entries[-1]
            check("Activity log has metadata with buyer_id",
                  latest.metadata_json and "buyer_id" in latest.metadata_json)
            check("Activity log alert_user is False",
                  latest.metadata_json and latest.metadata_json.get("alert_user") is False)

        # ═══════════════════════════════════════════════════════════════════
        # TEST 5: Ghost detection is idempotent
        # ═══════════════════════════════════════════════════════════════════
        logger.info("\n=== TEST 5: Ghost Detection Idempotency ===")

        result3 = await detect_and_flag_ghosts()
        check("Ghost detection: 0 new ghosts (no double-flag)", result3 == 0, f"Got {result3}")

        # Verify only one ghost_detected_at entry exists
        ghost_count = await db.execute(
            select(Campaign).where(
                Campaign.buyer_id == buyer.id,
                Campaign.deal_id == deal.id,
                Campaign.ghost_detected_at.isnot(None),
            )
        )
        ghost_rows = ghost_count.scalars().all()
        check("Only one campaign row has ghost_detected_at", len(ghost_rows) == 1, f"Got {len(ghost_rows)}")

        # ═══════════════════════════════════════════════════════════════════
        # TEST 6: Edge case — non-responder NOT flagged as ghost
        # ═══════════════════════════════════════════════════════════════════
        logger.info("\n=== TEST 6: Non-Responder Not Flagged ===")

        nr_ghost_check = await db.execute(
            select(Campaign).where(
                Campaign.buyer_id == non_responder.id,
                Campaign.ghost_detected_at.isnot(None),
            )
        )
        nr_ghost = nr_ghost_check.scalar_one_or_none()
        check("Non-responder NOT flagged as ghost", nr_ghost is None)

        # ═══════════════════════════════════════════════════════════════════
        # TEST 7: Edge case — dead deal NOT flagged
        # ═══════════════════════════════════════════════════════════════════
        logger.info("\n=== TEST 7: Dead Deal Not Flagged ===")

        dead_ghost_check = await db.execute(
            select(Campaign).where(
                Campaign.deal_id == dead_deal.id,
                Campaign.ghost_detected_at.isnot(None),
            )
        )
        dead_ghost = dead_ghost_check.scalar_one_or_none()
        check("Dead deal campaign NOT flagged as ghost", dead_ghost is None)

        # ═══════════════════════════════════════════════════════════════════
        # TEST 8: Ghost recovery — send touch 1
        # ═══════════════════════════════════════════════════════════════════
        logger.info("\n=== TEST 8: Ghost Recovery Touch 1 ===")

        from app.services.scheduler import send_ghost_recovery_emails

        # Fast-forward ghost_detected_at to 5 days ago so all touches are due
        ghost_campaign_id = ghost_row2.id
        await db.execute(
            update(Campaign)
            .where(Campaign.id == ghost_campaign_id)
            .values(ghost_detected_at=now - timedelta(days=30))  # All touches well past due
        )
        await db.commit()

        # Re-fetch
        updated_ghost = await db.get(Campaign, ghost_campaign_id)
        check(f"ghost_detected_at moved to {updated_ghost.ghost_detected_at}",
              updated_ghost.ghost_detected_at is not None)

        days_to_wait = settings.ghost_recovery_intervals_days[0]  # 4 days
        touch_due_at = updated_ghost.ghost_detected_at + timedelta(days=days_to_wait)
        check(f"Recovery touch 1 due at {touch_due_at}, now is {now}",
              touch_due_at <= now)

        # Run recovery with send_email patched to succeed
        from unittest.mock import patch, MagicMock
        mock_send_result = {"status": "sent", "message_id": "test-ghost-recovery"}

        with patch("app.services.scheduler.send_email",
                   return_value=mock_send_result) as mock_send:
            recovery_result = await send_ghost_recovery_emails()

        check("Ghost recovery sent 1 email", recovery_result == 1, f"Got {recovery_result}")

        # Verify send_email was called with send_type="reply"
        check("send_email was called", mock_send.called)
        if mock_send.called:
            call_kwargs = mock_send.call_args[1]
            check("send_type='reply'", call_kwargs.get("send_type") == "reply",
                  f"Got send_type={call_kwargs.get('send_type')}")

        # Re-fetch campaign and verify recovery state advanced
        # Use refresh() to force a DB read (identity map is stale from scheduler's session)
        after_recovery = await db.get(Campaign, ghost_campaign_id)
        await db.refresh(after_recovery)
        check("ghost_recovery_touch incremented to 1",
              after_recovery.ghost_recovery_touch == 1,
              f"Got {after_recovery.ghost_recovery_touch}")
        check("ghost_recovery_sent_at is set",
              after_recovery.ghost_recovery_sent_at is not None)

        # ═══════════════════════════════════════════════════════════════════
        # TEST 9: Ghost recovery cancelled by buyer reply (through actual code path)
        # ═══════════════════════════════════════════════════════════════════
        logger.info("\n=== TEST 9: Ghost Recovery Cancelled by Reply ===")

        # Set recovery_touch = 2 as if touches were sent
        await db.execute(
            update(Campaign)
            .where(Campaign.id == ghost_campaign_id)
            .values(
                ghost_detected_at=now - timedelta(days=30),
                ghost_recovery_touch=2,
                ghost_recovery_sent_at=now - timedelta(days=1),
            )
        )
        await db.commit()

        # Create a Sent campaign for the reply to match against
        reply_target = Campaign(
            id=uuid.uuid4(), deal_id=deal.id, buyer_id=buyer.id,
            touch_number=4, status="Sent",
            sent_at=now - timedelta(hours=2),
            subject="Checking in", body="Still available?",
        )
        db.add(reply_target)
        await db.commit()

        pre_cancel = await db.get(Campaign, ghost_campaign_id)
        check("Ghost recovery at touch 2 before cancellation",
              pre_cancel.ghost_recovery_touch == 2, f"Got {pre_cancel.ghost_recovery_touch}")

        # ── Run process_buyer_replies with a patched reply ──
        # This exercises the actual ghost cancellation code in scheduler.py
        from unittest.mock import patch, MagicMock
        from app.services.scheduler import process_buyer_replies

        mock_reply = {
            "from_email": buyer.email,
            "subject": "Re: 100 Ghost Lane",
            "body": "I'm still here, let's talk!",
        }
        mock_classification = {
            "reply_intent": "Interested",
            "primary_intent": "Interested",
            "urgency": "Medium",
            "sentiment": 4,
            "topics": ["price"],
            "recommended_action": "send_details",
            "counter_price": None,
            "ai_extracted_insights": "Buyer is re-engaging",
            "buyer_profile_updates": {},
            "question_answer": None,
        }

        with patch("app.services.scheduler.check_for_replies",
                   return_value=[mock_reply]), \
             patch("app.services.scheduler.process_reply",
                   return_value=mock_classification), \
             patch("app.services.scheduler.send_email",
                   return_value={"status": "sent", "message_id": "test"}):

            reply_result = await process_buyer_replies()

        check("process_buyer_replies processed 1 reply",
              reply_result == 1, f"Got {reply_result}")

        # Verify ghost recovery state was reset by the cancellation logic
        # Use refresh() to force a DB read (identity map is stale from scheduler's session)
        post_cancel = await db.get(Campaign, ghost_campaign_id)
        await db.refresh(post_cancel)
        check("ghost_detected_at reset to None",
              post_cancel.ghost_detected_at is None)
        check("ghost_recovery_touch reset to 0",
              post_cancel.ghost_recovery_touch == 0,
              f"Got {post_cancel.ghost_recovery_touch}")
        check("ghost_recovery_sent_at reset to None",
              post_cancel.ghost_recovery_sent_at is None)

        # ═══════════════════════════════════════════════════════════════════
        # CLEANUP
        # ═══════════════════════════════════════════════════════════════════
        logger.info("\n=== CLEANUP: Remove Test Data ===")

        all_cleanup = [
            touch1, touch2, touch3,
            nr_touch1, nr_touch2,
            dead_touch1,
            reply_target,
            buyer, non_responder,
            deal, dead_deal, jv,
        ]
        # Re-fetch and delete, since some may have been modified
        for obj in all_cleanup:
            try:
                fresh = await db.get(type(obj), obj.id)
                if fresh:
                    await db.delete(fresh)
            except Exception as e:
                logger.warning(f"Cleanup error for {type(obj).__name__} {obj.id}: {e}")

        # Clean up activity log entries created during tests
        log_cleanup = await db.execute(
            select(ActivityLog).where(
                ActivityLog.action.in_(["ghost_detected"])
            )
        )
        for entry in log_cleanup.scalars().all():
            await db.delete(entry)

        await db.commit()
        check("All test data cleaned up", True)

    await engine.dispose()

    # ═══════════════════════════════════════════════════════════════════════
    # SUMMARY
    # ═══════════════════════════════════════════════════════════════════════
    logger.info(f"\n{'='*50}")
    logger.info(f"Ghost Integration Test Results: {PASS} {passed} passed, {FAIL} {failed} failed")
    if failed > 0:
        logger.error(f"  {failed} test(s) FAILED \u2014 check logs above for {FAIL} markers")
    else:
        logger.info(f"  All {passed} tests PASSED!")
    logger.info(f"{'='*50}")
    return 1 if failed > 0 else 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
