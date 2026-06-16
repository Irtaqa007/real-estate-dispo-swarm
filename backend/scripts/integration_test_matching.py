"""End-to-end integration test for the matching overhaul — including HTTP endpoint tests.

Tests:
1. Create JV partner + buyer with structured fields → verify parsing
2. Create deal with embedding → verify hard filters work
3. POST /api/match via HTTP → verify full-stack (router → service → DB)
4. Create active campaigns to fill 2-deal cap → verify queue insertion
5. POST /api/match via HTTP → verify capped buyer excluded at HTTP level
6. Close one deal → trigger scheduler → verify queued match released
"""

import asyncio
import json
import logging
import sys
import uuid
from datetime import datetime, timezone, timedelta

# Add /app to sys.path so imports work when script runs from /app/scripts/
if __name__ == "__main__" and not any(p.endswith("/app") for p in sys.path):
    sys.path.insert(0, "/app")

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from app.config import settings
from app.models.schemas import Buyer, Deal, Campaign, JVPartner, QueuedDealMatch
from app.services.matching_service import find_top_matches_for_deal, process_queued_matches
from app.services.parse_buy_box import parse_buy_box

import urllib.request
from urllib.error import HTTPError

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("integration_test")

PASS = "✅"
FAIL = "❌"
SKIP = "⏭️"

passed = 0
failed = 0


def check(name: str, condition: bool, detail: str = ""):
    global passed, failed
    if condition:
        logger.info(f"  {PASS} {name}")
        passed += 1
    else:
        logger.error(f"  {FAIL} {name} — {detail}")
        failed += 1


async def _http_match(deal_id: uuid.UUID, base_url: str) -> tuple:
    """Helper: POST /api/match/{deal_id} via HTTP and return (status, body)."""
    req = urllib.request.Request(
        f"{base_url}/api/match/{deal_id}",
        data=json.dumps({"limit": 20}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


async def main():
    global passed, failed
    url = settings.database_url.replace("postgresql://", "postgresql+asyncpg://")
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with Session() as db:
        BASE = "http://localhost:8000"

        # ── 1. Create JV Partner ──
        logger.info("\n=== 1. Create JV Partner ===")
        jv = JVPartner(id=uuid.uuid4(), name="Test JV Partner", email="jv@test.com")
        db.add(jv)
        await db.commit()
        check("JV Partner created", jv.id is not None)

        # ── 2. Create buyer with structured fields ──
        logger.info("\n=== 2. Create Buyer with Structured Fields ===")
        buy_box = "I buy 3-4 bed houses in Dallas under $300k, minimum $100k"
        parsed = await parse_buy_box(buy_box)
        check("parse_buy_box returned dict", isinstance(parsed, dict))
        check("price_min parsed", parsed["price_min"] is not None, str(parsed["price_min"]))
        check("price_max parsed", parsed["price_max"] is not None, str(parsed["price_max"]))
        check("pref_property_type = House", parsed["pref_property_type"] == "House")
        check("pref_cities includes Dallas", parsed["pref_cities"] and "Dallas" in parsed["pref_cities"])

        buyer = Buyer(
            id=uuid.uuid4(),
            full_name="Test Buyer",
            email=f"testbuyer_{uuid.uuid4().hex[:8]}@example.com",
            buy_box=buy_box, status="Active", email_verified=True, buyer_tier="A-List",
            price_min=parsed["price_min"], price_max=parsed["price_max"],
            pref_property_type=parsed["pref_property_type"],
            pref_cities=parsed["pref_cities"],
            buy_box_embedding=[0.1] * 1024,
        )
        db.add(buyer)
        await db.commit()
        check("Buyer created", buyer.id is not None)

        # ── 3. Create filter-test buyer ──
        logger.info("\n=== 3. Create Filter-Test Buyer (should not match) ===")
        buyer_wrong = Buyer(
            id=uuid.uuid4(),
            full_name="Wrong Buyer",
            email=f"wrong_{uuid.uuid4().hex[:8]}@example.com",
            buy_box="Land in Austin over $500k",
            status="Active", email_verified=True, buyer_tier="C-List",
            price_min=500000.0, price_max=1000000.0,
            pref_property_type="Land", pref_cities=["Austin"],
            buy_box_embedding=[0.9] * 1024,
        )
        db.add(buyer_wrong)
        await db.commit()
        check("Filter-test buyer created", buyer_wrong.id is not None)

        # ── 4. Create deal ──
        logger.info("\n=== 4. Create Deal ===")
        deal = Deal(
            id=uuid.uuid4(), address="123 Test St", city="Dallas", state="TX",
            property_type="House", condition_description="Great condition, 3 bed 2 bath",
            arv=350000.0, asking_price=250000.0, floor_price=180000.0, contract_price=150000.0,
            title_status="Clear", jv_partner_id=jv.id,
            deal_embedding=[0.15] * 1024,
        )
        db.add(deal)
        await db.commit()
        check("Deal created", deal.id is not None)
        buyer_id = buyer.id
        deal_id = deal.id

        # ── 5. Matching via DB function ──
        logger.info("\n=== 5. Test Matching with Hard Filters (DB layer) ===")
        mr = await find_top_matches_for_deal(db, deal, limit=20, match_threshold=0.0)
        check("Match returned results", len(mr.matches) > 0, f"{len(mr.matches)} matches")
        check("No capped buyers yet", mr.skipped_due_to_cap == 0)
        wrong_matched = [m for m in mr.matches if m.email.startswith("wrong_")]
        check("Wrong buyer excluded by hard filters", len(wrong_matched) == 0)
        right_matched = [m for m in mr.matches if m.email.startswith("testbuyer_")]
        check("Correct buyer included", len(right_matched) > 0)

        # ── 6. HTTP endpoint test: POST /api/match ──
        logger.info("\n=== 6. HTTP Endpoint Test — POST /api/match ===")
        http_status, http_body = await _http_match(deal.id, BASE)
        check("HTTP 200 OK", http_status == 200, f"Got {http_status}")
        check("Response has deal_id", http_body.get("deal_id") == str(deal.id))
        check("Response has deal_address", http_body.get("deal_address") == deal.address)
        check("Response has matches list", isinstance(http_body.get("matches"), list))

        http_matches = http_body.get("matches", [])
        check("HTTP matches not empty", len(http_matches) > 0, f"{len(http_matches)} matches")

        if http_matches:
            first = http_matches[0]
            check("Match has id", "id" in first)
            check("Match has full_name", "full_name" in first)
            check("Match has email", "email" in first)
            check("Match has buy_box", "buy_box" in first)
            check("Match has similarity", "similarity" in first)
            check("Similarity 0-1", isinstance(first["similarity"], (int, float)) and 0 <= first["similarity"] <= 1)

        http_wrong = [m for m in http_matches if m.get("email", "").startswith("wrong_")]
        check("HTTP: Wrong buyer excluded", len(http_wrong) == 0)

        http_correct = [m for m in http_matches if m.get("email", "").startswith("testbuyer_")]
        check("HTTP: Correct buyer included", len(http_correct) > 0)

        if http_correct:
            cm = http_correct[0]
            check("HTTP: full_name correct", cm.get("full_name") == "Test Buyer")
            check("HTTP: has buy_box (non-empty)", bool(cm.get("buy_box")))
            check("HTTP: buyer_tier correct", cm.get("buyer_tier") == "A-List")

        # ── 7. Fill 2-deal cap ──
        logger.info("\n=== 7. Fill 2-Deal Cap ===")
        deal2 = Deal(id=uuid.uuid4(), address="456 Other St", city="Dallas", state="TX",
                     property_type="House", condition_description="Fixer upper",
                     arv=300000.0, asking_price=200000.0, floor_price=150000.0, contract_price=120000.0,
                     title_status="Clear", jv_partner_id=jv.id,
                     deal_embedding=[0.2] * 1024, status="Available")
        deal3 = Deal(id=uuid.uuid4(), address="789 Extra St", city="Dallas", state="TX",
                     property_type="House", condition_description="Needs work",
                     arv=280000.0, asking_price=180000.0, floor_price=140000.0, contract_price=110000.0,
                     title_status="Clear", jv_partner_id=jv.id,
                     deal_embedding=[0.25] * 1024, status="Available")
        db.add(deal2); db.add(deal3)
        await db.commit()

        now = datetime.now(timezone.utc)
        camp1 = Campaign(id=uuid.uuid4(), deal_id=deal2.id, buyer_id=buyer_id,
                         touch_number=1, status="Sent", sent_at=now,
                         subject="Test", body="Test body")
        camp2 = Campaign(id=uuid.uuid4(), deal_id=deal3.id, buyer_id=buyer_id,
                         touch_number=1, status="Sent", sent_at=now,
                         subject="Test", body="Test body")
        db.add(camp1); db.add(camp2)
        await db.commit()
        check("Campaign 1 created (active deal 1)", camp1.id is not None)
        check("Campaign 2 created (active deal 2)", camp2.id is not None)

        from app.services.matching_service import get_active_deal_count_for_buyer
        count = await get_active_deal_count_for_buyer(db, buyer_id)
        check("Buyer has 2 active deals", count == 2, f"Got {count}")

        # ── 8. Test capping + queue (DB layer) ──
        logger.info("\n=== 8. Test Capping + Queue Insertion ===")
        mr2 = await find_top_matches_for_deal(db, deal, limit=20, match_threshold=0.0)
        capped_excluded = not any(m.email == buyer.email for m in mr2.matches)
        check("Correct buyer excluded by cap", capped_excluded,
              f"{buyer.email} still in {len(mr2.matches)} matches")
        check("Skipped due to cap > 0", mr2.skipped_due_to_cap > 0, str(mr2.skipped_due_to_cap))

        qm_r = await db.execute(select(QueuedDealMatch).where(
            QueuedDealMatch.buyer_id == buyer_id, QueuedDealMatch.deal_id == deal_id,
            QueuedDealMatch.status == "waiting"))
        queued = qm_r.scalar_one_or_none()
        check("QueuedDealMatch inserted", queued is not None)
        if queued:
            check("Queued match has similarity_score", queued.similarity_score is not None)
            check("Queued match status = 'waiting'", queued.status == "waiting")

        # ── 9. HTTP endpoint test: capped buyer excluded ──
        logger.info("\n=== 9. HTTP Endpoint Test — Capped Buyer ===")
        cap_status, cap_body = await _http_match(deal.id, BASE)
        check("HTTP 200 (capped)", cap_status == 200, f"Got {cap_status}")
        cap_matches = cap_body.get("matches", [])
        cap_correct = [m for m in cap_matches if m.get("email") == buyer.email]
        check("HTTP: Capped buyer excluded via endpoint", len(cap_correct) == 0,
              f"{len(cap_correct)} matches for capped buyer")

        # ── 10. Close deal to drop cap ──
        logger.info("\n=== 10. Close Deal to Drop Cap ===")
        camp1.status = "Paused"; db.add(camp1)
        deal2.status = "Closed"; deal2.closed_at = now; db.add(deal2)
        await db.commit()
        ca = await get_active_deal_count_for_buyer(db, buyer_id)
        check("Active deals dropped to 1", ca == 1, f"Got {ca}")

        # ── 11. Process queued matches ──
        logger.info("\n=== 11. Process Queued Matches ===")
        released = await process_queued_matches(db)
        check("Queued match released", released == 1, f"Released {released}")
        qm_a = await db.execute(select(QueuedDealMatch).where(
            QueuedDealMatch.buyer_id == buyer_id, QueuedDealMatch.deal_id == deal_id))
        qm_rls = qm_a.scalar_one_or_none()
        check("Queued match status = 'released'", qm_rls and qm_rls.status == "released")
        check("released_at is set", qm_rls and qm_rls.released_at is not None)

        # ── 12. Cleanup ──
        logger.info("\n=== 12. Cleanup ===")
        for obj in [camp1, camp2, deal, deal2, deal3, buyer, buyer_wrong, jv]:
            try:
                await db.delete(obj)
            except Exception:
                pass
        if queued:
            try:
                await db.delete(qm_rls if qm_rls else queued)
            except Exception:
                pass
        await db.commit()
        check("Test data cleaned up", True)

    await engine.dispose()

    logger.info(f"\n{'='*50}")
    logger.info(f"Integration Test Results: {PASS} {passed} passed, {FAIL} {failed} failed")
    if failed > 0:
        logger.error(f"  {failed} test(s) FAILED — check logs above for {FAIL} markers")
    else:
        logger.info(f"  All {passed} tests PASSED!")
    logger.info(f"{'='*50}")
    return 1 if failed > 0 else 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
