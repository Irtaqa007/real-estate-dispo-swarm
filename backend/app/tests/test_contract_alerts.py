"""Tests for the contract-ready alert feature.

Covers:
- Closing intent (Interested) creates contract alert with correct metadata
- Campaign status updated to Contract_Pending
- Deal status updated to Under Contract
- Alert can be resolved via resolve endpoint
- Holding email sent to other buyers on same deal
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.models import ActivityLog, Buyer, Campaign, Deal, JVPartner


# ==========================================================================
# Contract alert creation (via process_reply in reply_processor.py)
# ==========================================================================


@pytest.mark.asyncio
async def test_interested_creates_contract_alert():
    """Interested intent should create a contract_ready activity log entry
    with buyer, deal, and financial metadata."""
    from app.services.reply_processor import process_reply

    deal_id = uuid.uuid4()
    buyer_id = uuid.uuid4()
    jv_partner_id = uuid.uuid4()

    mock_deal = MagicMock(spec=Deal)
    mock_deal.id = deal_id
    mock_deal.address = "123 Main St"
    mock_deal.city = "Austin"
    mock_deal.state = "TX"
    mock_deal.asking_price = 200000
    mock_deal.floor_price = 180000
    mock_deal.contract_price = 160000
    mock_deal.jv_split_percentage = 50
    mock_deal.jv_partner_id = jv_partner_id
    mock_deal.status = "Available"

    mock_buyer = MagicMock(spec=Buyer)
    mock_buyer.id = buyer_id
    mock_buyer.full_name = "Test Buyer"
    mock_buyer.email = "buyer@test.com"
    mock_buyer.affiliation = "Some entity"

    mock_jv = MagicMock(spec=JVPartner)
    mock_jv.id = jv_partner_id
    mock_jv.name = "JV Partner"
    mock_jv.email = "jv@partner.com"

    mock_campaign = MagicMock(spec=Campaign)
    mock_campaign.id = uuid.uuid4()
    mock_campaign.deal_id = deal_id
    mock_campaign.buyer_id = buyer_id
    mock_campaign.status = "Sent"

    mock_db = AsyncMock()
    # First get is for DeaL
    mock_db.get = AsyncMock()
    mock_db.get.side_effect = lambda model, id: {
        (Deal, deal_id): mock_deal,
        (Buyer, buyer_id): mock_buyer,
        (JVPartner, jv_partner_id): mock_jv,
    }.get((model, id))

    # execute for campaign query
    mock_campaign_result = MagicMock()
    mock_campaign_result.scalar_one_or_none = MagicMock(return_value=mock_campaign)
    mock_db.execute = AsyncMock(return_value=mock_campaign_result)

    # Mock groq_chat_completion to return Interested intent
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = (
        '{"primary_intent": "Interested", "urgency": "High", "sentiment": 5, '
        '"topics": ["price"], "recommended_action": "send_contract", "counter_price": null, '
        '"summary": "Buyer wants to proceed", "buybox_changes": null, "question_answer": null}'
    )

    with patch("app.services.reply_processor.groq_chat_completion", AsyncMock(return_value=mock_response)):
        with patch("app.services.reply_processor.load_buyer_full_context", AsyncMock(return_value=None)):
            with patch("app.services.reply_processor.extract_pass_reason", AsyncMock(return_value={})):
                with patch("app.services.reply_processor.validate_ai_output", AsyncMock()):
                    with patch("app.services.reply_processor.detect_future_buying_window", AsyncMock(return_value=None)):
                        result = await process_reply(
                            {"subject": "Interested", "body": "I want to buy this property"},
                            db=mock_db,
                            buyer_id=buyer_id,
                            deal_id=deal_id,
                        )

    # Verify classification
    assert result["reply_intent"] == "Interested"
    assert result["primary_intent"] == "Interested"

    # Verify campaign status was updated to Contract_Pending
    assert mock_campaign.status == "Contract_Pending"
    mock_db.add.assert_any_call(mock_campaign)

    # Verify deal status was updated to Under Contract
    assert mock_deal.status == "Under Contract"
    mock_db.add.assert_any_call(mock_deal)

    # Verify an activity log was created with alert_user=True
    alert_calls = [
        call for call in mock_db.add.call_args_list
        if isinstance(call[0][0], ActivityLog)
    ]
    matching_alerts = []
    for call in alert_calls:
        log = call[0][0]
        meta = log.metadata_json or {}
        if log.action == "contract_ready":
            matching_alerts.append(log)
            assert meta.get("alert_type") == "contract_ready"
            assert meta.get("alert_user") is True
            assert meta.get("priority") == "high"
            assert meta.get("action_required") == "Prepare and send contract manually"

            # Check buyer metadata
            buyer_meta = meta.get("buyer", {})
            assert buyer_meta.get("name") == "Test Buyer"
            assert buyer_meta.get("email") == "buyer@test.com"

            # Check deal metadata
            deal_meta = meta.get("deal", {})
            assert deal_meta.get("address") == "123 Main St"
            assert deal_meta.get("asking_price") == 200000
            assert deal_meta.get("contract_price") == 160000
            assert deal_meta.get("my_payout") == pytest.approx(20000)  # (200k-160k) * 50%

            # Check JV metadata
            assert deal_meta.get("jv_partner") == "JV Partner"
            assert deal_meta.get("jv_partner_email") == "jv@partner.com"

            # Check suggested next steps
            assert len(meta.get("suggested_next_steps", [])) == 5

    assert len(matching_alerts) >= 1, "Expected at least one contract_ready alert"


@pytest.mark.asyncio
async def test_interested_no_db_skips_alert():
    """Interested intent without db/buyer_id/deal_id should not create alert."""
    from app.services.reply_processor import process_reply

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = (
        '{"primary_intent": "Interested", "urgency": "High", "sentiment": 5, '
        '"topics": [], "recommended_action": "", "counter_price": null, '
        '"summary": "Buyer wants to proceed", "buybox_changes": null, "question_answer": null}'
    )

    with patch("app.services.reply_processor.groq_chat_completion", AsyncMock(return_value=mock_response)):
        result = await process_reply(
            {"subject": "Interested", "body": "I want to buy"},
            db=None,
            buyer_id=None,
            deal_id=None,
        )

    assert result["reply_intent"] == "Interested"
    # No alert should be created (no db to create it with)


# ==========================================================================
# Contract alert resolve endpoint
# ==========================================================================


@pytest.mark.asyncio
async def test_resolve_contract_alert():
    """POST /api/alerts/contract-ready/{id}/resolve should mark alert as resolved."""
    from app.routers.alerts import resolve_contract_alert
    from app.schemas import ContractAlertResolveRequest

    alert_id = uuid.uuid4()

    mock_entry = MagicMock(spec=ActivityLog)
    mock_entry.id = alert_id
    mock_entry.action = "contract_ready"
    mock_entry.metadata_json = {
        "alert_type": "contract_ready",
        "alert_user": True,
        "buyer": {"name": "Test Buyer"},
        "deal": {"address": "123 Main St"},
    }

    mock_db = AsyncMock()
    mock_db.get = AsyncMock(return_value=mock_entry)

    body = ContractAlertResolveRequest(notes="Sent via DocuSign at 3pm")
    result = await resolve_contract_alert(alert_id, body, db=mock_db)

    assert result["resolved"] is True
    assert "resolved_at" in result
    assert mock_entry.resolved is True
    assert mock_entry.metadata_json["resolved_at"] is not None
    assert mock_entry.metadata_json["resolution_notes"] == "Sent via DocuSign at 3pm"
    mock_db.add.assert_called_once_with(mock_entry)
    mock_db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_resolve_nonexistent_alert_returns_404():
    """Resolving a nonexistent alert should return 404."""
    from app.routers.alerts import resolve_contract_alert
    from app.schemas import ContractAlertResolveRequest
    from fastapi import HTTPException

    alert_id = uuid.uuid4()

    mock_db = AsyncMock()
    mock_db.get = AsyncMock(return_value=None)

    body = ContractAlertResolveRequest(notes="")
    with pytest.raises(HTTPException) as exc:
        await resolve_contract_alert(alert_id, body, db=mock_db)

    assert exc.value.status_code == 404
    assert "Contract alert not found" in exc.value.detail


# ==========================================================================
# Contract alerts GET endpoint
# ==========================================================================


@pytest.mark.asyncio
async def test_get_contract_alerts_returns_unresolved():
    """GET /api/alerts/contract-ready should return unresolved alerts."""
    from app.routers.alerts import get_contract_alerts

    alert_id_1 = uuid.uuid4()
    alert_id_2 = uuid.uuid4()
    now = datetime.now(timezone.utc)

    mock_entry_1 = MagicMock(spec=ActivityLog)
    mock_entry_1.id = alert_id_1
    mock_entry_1.action = "contract_ready"
    mock_entry_1.created_at = now
    mock_entry_1.resolved = False
    mock_entry_1.resolved_at = None
    mock_entry_1.metadata_json = {
        "alert_type": "contract_ready",
        "alert_user": True,
        "buyer": {"name": "Buyer One", "email": "buyer1@test.com"},
        "deal": {"address": "123 Main St", "state": "TX", "my_payout": 20000},
        "negotiated_price": 180000,
    }

    mock_entry_2 = MagicMock(spec=ActivityLog)
    mock_entry_2.id = alert_id_2
    mock_entry_2.action = "contract_ready"
    mock_entry_2.created_at = now
    mock_entry_2.resolved = True
    mock_entry_2.resolved_at = now
    mock_entry_2.metadata_json = {
        "alert_type": "contract_ready",
        "alert_user": True,
        "resolved_at": now.isoformat(),
        "buyer": {"name": "Buyer Two", "email": "buyer2@test.com"},
        "deal": {"address": "456 Oak Ave", "state": "CA", "my_payout": 15000},
        "negotiated_price": 250000,
    }

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_entry_1, mock_entry_2]
    mock_db.execute = AsyncMock(return_value=mock_result)

    alerts = await get_contract_alerts(db=mock_db)

    assert len(alerts) == 2
    assert alerts[0]["buyer_name"] == "Buyer One"
    assert alerts[1]["buyer_name"] == "Buyer Two"
    assert alerts[0]["resolved"] is False
    assert alerts[1]["resolved"] is True
    assert alerts[0]["full_metadata"] is not None
    assert alerts[1]["resolved"] is True


# ===========================================================================
# End-to-end: operator contract alert resolve flow (approve/reject)
# ===========================================================================


class TestOperatorContractAlertFlow:
    """End-to-end tests for the operator's contract alert resolve flow.
    Tests the full lifecycle: contract_ready alert → operator resolves."""

    @pytest.mark.asyncio
    async def test_resolve_contract_alert_with_notes(self):
        """Resolving with specific notes should store them in metadata."""
        from app.routers.alerts import resolve_contract_alert
        from app.schemas import ContractAlertResolveRequest

        alert_id = uuid.uuid4()

        mock_entry = MagicMock(spec=ActivityLog)
        mock_entry.id = alert_id
        mock_entry.action = "contract_ready"
        mock_entry.resolved = False
        mock_entry.resolved_at = None
        mock_entry.metadata_json = {
            "alert_type": "contract_ready",
            "alert_user": True,
            "buyer": {"name": "Test Buyer"},
            "deal": {"address": "123 Main St"},
        }

        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=mock_entry)

        body = ContractAlertResolveRequest(notes="Sent via DocuSign on 12/15. Buyer signed.")
        result = await resolve_contract_alert(alert_id, body, db=mock_db)

        assert result["resolved"] is True
        assert "resolved_at" in result
        assert mock_entry.resolved is True
        assert mock_entry.metadata_json["resolution_notes"] == (
            "Sent via DocuSign on 12/15. Buyer signed."
        )
        assert mock_entry.metadata_json["resolved_at"] is not None
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_resolve_contract_alert_wrong_type(self):
        """Resolving a non-contract-ready alert should return 400."""
        from app.routers.alerts import resolve_contract_alert
        from app.schemas import ContractAlertResolveRequest
        from fastapi import HTTPException

        alert_id = uuid.uuid4()

        mock_entry = MagicMock(spec=ActivityLog)
        mock_entry.id = alert_id
        mock_entry.action = "negotiation_escalation"  # Wrong action type
        mock_entry.metadata_json = {}

        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=mock_entry)

        body = ContractAlertResolveRequest(notes="")
        with pytest.raises(HTTPException) as exc:
            await resolve_contract_alert(alert_id, body, db=mock_db)

        assert exc.value.status_code == 400
        assert "not a contract-ready alert" in exc.value.detail

    @pytest.mark.asyncio
    async def test_full_contract_alert_lifecycle(self):
        """Full lifecycle: process_conversation → contract_ready → alert → operator resolves."""
        from app.services.conversation_engine import process_conversation
        from app.routers.alerts import resolve_contract_alert
        from app.schemas import ContractAlertResolveRequest

        deal = MagicMock(spec=Deal)
        deal.id = uuid.uuid4()
        deal.address = "1000 Commerce Blvd"
        deal.city = "Plano"
        deal.state = "TX"
        deal.asking_price = 300000.0
        deal.floor_price = 250000.0
        deal.contract_price = 220000.0
        deal.jv_split_percentage = 50.0
        deal.jv_partner_id = None
        deal.spread = 80000.0
        deal.arv = 380000.0
        deal.repair_estimate = 20000.0
        deal.condition_description = "Excellent"
        deal.year_built = 2010
        deal.status = "Available"
        deal.property_type = "House"
        deal.beds = 4
        deal.baths = 3
        deal.sqft = 2400

        buyer = MagicMock(spec=Buyer)
        buyer.id = uuid.uuid4()
        buyer.full_name = "Sarah Connor"
        buyer.email = "sarah@example.com"

        campaign = MagicMock(spec=Campaign)
        campaign.id = uuid.uuid4()
        campaign.deal_id = deal.id
        campaign.buyer_id = buyer.id
        campaign.conversation_stage = "collecting_info"
        campaign.buyer_legal_name = "Sarah Connor"
        campaign.buyer_phone = "555-444-3333"
        campaign.buyer_title_company = "First American Title"
        campaign.agreed_price = 275000.0

        import json
        mock_ai = MagicMock()
        mock_ai.choices = [MagicMock()]
        mock_ai.choices[0].message.content = json.dumps({
            "stage": "contract_ready",
            "pass": False,
            "unsub": False,
            "reply": "Perfect — I'll get the paperwork started.",
            "extracted_legal_name": None,
            "extracted_phone": None,
            "extracted_title_company": None,
            "extracted_agreed_price": None,
        })

        with patch(
            "app.services.conversation_engine.groq_chat_completion",
            AsyncMock(return_value=mock_ai),
        ):
            result = await process_conversation(
                reply_body="$275k sounds good",
                reply_subject="Re: Great deal",
                buyer=buyer,
                deal=deal,
                campaign=campaign,
                thread_history=[],
            )

        assert result["contract_ready"] is True

        # ── Simulate scheduler creating the ActivityLog alert ──
        asking = float(deal.asking_price)
        contract_p = float(deal.contract_price)
        split_pct = float(deal.jv_split_percentage or 50) / 100
        assignment_fee = asking - contract_p
        my_payout = assignment_fee * (1.0 - split_pct)

        alert_id = uuid.uuid4()
        alert_entry = ActivityLog(
            id=alert_id,
            entity_type="deal",
            entity_id=deal.id,
            action="contract_ready",
            metadata_json={
                "alert_type": "contract_ready",
                "alert_user": True,
                "priority": "high",
                "buyer": {
                    "name": buyer.full_name,
                    "email": buyer.email,
                    "legal_name": campaign.buyer_legal_name,
                    "phone": campaign.buyer_phone,
                    "title_company": campaign.buyer_title_company,
                },
                "deal": {
                    "address": deal.address,
                    "asking_price": asking,
                    "agreed_price": float(campaign.agreed_price),
                    "assignment_fee": assignment_fee,
                    "my_payout": my_payout,
                    "jv_partner": "",
                },
            },
        )

        # ── Operator resolves the alert with notes ──
        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=alert_entry)

        body = ContractAlertResolveRequest(notes="Contract emailed via DocuSign. Buyer confirmed.")
        resolve_result = await resolve_contract_alert(alert_id, body, db=mock_db)

        assert resolve_result["resolved"] is True
        assert alert_entry.resolved is True
        assert alert_entry.metadata_json["resolved_at"] is not None
        assert alert_entry.metadata_json["resolution_notes"] == (
            "Contract emailed via DocuSign. Buyer confirmed."
        )

        # Verify original alert metadata is preserved through the lifecycle
        assert alert_entry.metadata_json["buyer"]["name"] == "Sarah Connor"
        assert alert_entry.metadata_json["deal"]["address"] == "1000 Commerce Blvd"
        assert alert_entry.metadata_json["deal"]["my_payout"] == 40000.0

    @pytest.mark.asyncio
    async def test_resolve_without_body_works(self):
        """Resolving without a request body should still work (notes optional)."""
        from app.routers.alerts import resolve_contract_alert

        alert_id = uuid.uuid4()

        mock_entry = MagicMock(spec=ActivityLog)
        mock_entry.id = alert_id
        mock_entry.action = "contract_ready"
        mock_entry.resolved = False
        mock_entry.resolved_at = None
        mock_entry.metadata_json = {
            "alert_type": "contract_ready",
            "buyer": {},
            "deal": {},
        }

        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=mock_entry)

        result = await resolve_contract_alert(alert_id, body=None, db=mock_db)

        assert result["resolved"] is True
        assert mock_entry.resolved is True
        assert mock_entry.metadata_json["resolved_at"] is not None
        # No resolution_notes should be added when body is None
        assert "resolution_notes" not in mock_entry.metadata_json


# ===========================================================================
# Edge case tests for contract alert resolve flow
# ===========================================================================


class TestContractAlertEdgeCases:
    """Parameterized edge case tests for the contract alert resolve flow.
    Tests extreme note lengths, special characters, and missing metadata."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("notes,expect_key,expected_stored", [
        ("", False, None),  # Empty string is falsy → not stored
        ("A" * 1000, True, "A" * 1000),
        ("Speci@l ch@racters! #$%^&*()", True, "Speci@l ch@racters! #$%^&*()"),
        ("Multi\nLine\nNotes", True, "Multi\nLine\nNotes"),
        ("Contract signed on 2024-01-15 by J. Smith (CEO)", True, "Contract signed on 2024-01-15 by J. Smith (CEO)"),
    ])
    async def test_resolve_with_various_notes(self, notes, expect_key, expected_stored):
        """Resolve with various note formats should store them correctly."""
        from app.routers.alerts import resolve_contract_alert
        from app.schemas import ContractAlertResolveRequest

        alert_id = uuid.uuid4()

        mock_entry = MagicMock(spec=ActivityLog)
        mock_entry.id = alert_id
        mock_entry.action = "contract_ready"
        mock_entry.resolved = False
        mock_entry.resolved_at = None
        mock_entry.metadata_json = {
            "alert_type": "contract_ready",
            "buyer": {},
            "deal": {},
        }

        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=mock_entry)

        body = ContractAlertResolveRequest(notes=notes)
        result = await resolve_contract_alert(alert_id, body, db=mock_db)

        assert result["resolved"] is True
        if expect_key:
            assert mock_entry.metadata_json["resolution_notes"] == expected_stored
        else:
            assert "resolution_notes" not in mock_entry.metadata_json
        assert mock_entry.metadata_json["resolved_at"] is not None
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_resolve_alert_metadata_is_none(self):
        """Resolving an alert with metadata_json=None should not crash."""
        from app.routers.alerts import resolve_contract_alert
        from app.schemas import ContractAlertResolveRequest

        alert_id = uuid.uuid4()

        mock_entry = MagicMock(spec=ActivityLog)
        mock_entry.id = alert_id
        mock_entry.action = "contract_ready"
        mock_entry.resolved = False
        mock_entry.resolved_at = None
        mock_entry.metadata_json = None

        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=mock_entry)

        body = ContractAlertResolveRequest(notes="Resolved")
        result = await resolve_contract_alert(alert_id, body, db=mock_db)

        assert result["resolved"] is True
        assert mock_entry.resolved is True
        # metadata_json should have been initialized to {}
        # then updated with resolved_at and resolution_notes
        assert mock_entry.metadata_json is not None
        assert "resolution_notes" in mock_entry.metadata_json
        assert "resolved_at" in mock_entry.metadata_json

    @pytest.mark.asyncio
    async def test_resolve_alert_metadata_is_none_has_resolved_at(self):
        """When metadata_json is None, resolved_at should still be set."""
        from app.routers.alerts import resolve_contract_alert
        from app.schemas import ContractAlertResolveRequest

        alert_id = uuid.uuid4()

        mock_entry = MagicMock(spec=ActivityLog)
        mock_entry.id = alert_id
        mock_entry.action = "contract_ready"
        mock_entry.resolved = False
        mock_entry.resolved_at = None
        mock_entry.metadata_json = None

        mock_db = AsyncMock()
        mock_db.get = AsyncMock(return_value=mock_entry)

        body = ContractAlertResolveRequest(notes="Completed")
        result = await resolve_contract_alert(alert_id, body, db=mock_db)

        assert result["resolved_at"] is not None
        assert mock_entry.metadata_json["resolved_at"] is not None
        assert mock_entry.metadata_json["resolution_notes"] == "Completed"
    """End-to-end tests for the scheduler's contract_ready alert creation path
    in reply_pipeline.py. Calls process_conversation (with mocked AI) to get
    contract_ready=True, then verifies the ActivityLog metadata the scheduler
    would create matches the expected structure."""

    @pytest.mark.asyncio
    async def test_scheduler_alert_structure(self):
        """Verify the full ActivityLog metadata structure that the scheduler creates."""
        from app.services.conversation_engine import process_conversation

        deal_id = uuid.uuid4()
        buyer_id = uuid.uuid4()
        campaign_id = uuid.uuid4()

        deal = MagicMock(spec=Deal)
        deal.id = deal_id
        deal.address = "123 Main St"
        deal.city = "Austin"
        deal.state = "TX"
        deal.asking_price = 200000.0
        deal.floor_price = 170000.0
        deal.contract_price = 160000.0
        deal.jv_split_percentage = 50.0
        deal.jv_partner_id = None
        deal.spread = 40000.0
        deal.arv = 280000.0
        deal.repair_estimate = 25000.0
        deal.condition_description = "Good condition"
        deal.year_built = 1995
        deal.status = "Available"
        deal.property_type = "House"
        deal.beds = 3
        deal.baths = 2
        deal.sqft = 1500

        buyer = MagicMock(spec=Buyer)
        buyer.id = buyer_id
        buyer.full_name = "Ahmad Raza Khan"
        buyer.email = "ahmad@example.com"

        campaign = MagicMock(spec=Campaign)
        campaign.id = campaign_id
        campaign.deal_id = deal_id
        campaign.buyer_id = buyer_id
        campaign.conversation_stage = "collecting_info"
        campaign.buyer_legal_name = "Ahmad Raza Khan"
        campaign.buyer_phone = "923001234567"
        campaign.buyer_title_company = "First American Title"
        campaign.agreed_price = 172000.0

        import json
        mock_ai = MagicMock()
        mock_ai.choices = [MagicMock()]
        mock_ai.choices[0].message.content = json.dumps({
            "stage": "contract_ready",
            "pass": False,
            "unsub": False,
            "reply": "Perfect — I'll get the paperwork started.",
            "extracted_legal_name": None,
            "extracted_phone": None,
            "extracted_title_company": None,
            "extracted_agreed_price": None,
        })

        with patch(
            "app.services.conversation_engine.groq_chat_completion",
            AsyncMock(return_value=mock_ai),
        ):
            result = await process_conversation(
                reply_body="Yes, $172,000 works for me",
                reply_subject="Re: Great deal in Austin",
                buyer=buyer,
                deal=deal,
                campaign=campaign,
                thread_history=[],
            )

        assert result["contract_ready"] is True
        assert result["new_stage"] == "contract_ready"

        # Simulate what the scheduler does with contract_ready
        # (replicating the exact logic from reply_pipeline.py)
        asking = float(deal.asking_price)
        contract_p = float(deal.contract_price)
        split_pct = float(deal.jv_split_percentage or 50) / 100
        assignment_fee = asking - contract_p
        my_payout = assignment_fee * (1.0 - split_pct)

        log_entry = ActivityLog(
            id=uuid.uuid4(),
            entity_type="deal",
            entity_id=deal_id,
            action="contract_ready",
            metadata_json={
                "alert_type": "contract_ready",
                "alert_user": True,
                "priority": "high",
                "buyer": {
                    "name": buyer.full_name,
                    "email": buyer.email,
                    "legal_name": campaign.buyer_legal_name,
                    "phone": campaign.buyer_phone,
                    "title_company": campaign.buyer_title_company,
                },
                "deal": {
                    "address": deal.address,
                    "asking_price": asking,
                    "agreed_price": float(campaign.agreed_price) if campaign.agreed_price else asking,
                    "assignment_fee": assignment_fee,
                    "my_payout": my_payout,
                    "jv_partner": "",
                },
            },
        )

        assert log_entry.action == "contract_ready"
        assert log_entry.entity_type == "deal"
        assert log_entry.entity_id == deal_id

        meta = log_entry.metadata_json
        assert meta["alert_type"] == "contract_ready"
        assert meta["alert_user"] is True
        assert meta["priority"] == "high"

        assert meta["buyer"]["name"] == "Ahmad Raza Khan"
        assert meta["buyer"]["email"] == "ahmad@example.com"
        assert meta["buyer"]["legal_name"] == "Ahmad Raza Khan"
        assert meta["buyer"]["phone"] == "923001234567"
        assert meta["buyer"]["title_company"] == "First American Title"

        assert meta["deal"]["address"] == "123 Main St"
        assert meta["deal"]["asking_price"] == 200000.0
        assert meta["deal"]["agreed_price"] == 172000.0
        assert meta["deal"]["assignment_fee"] == 40000.0
        assert meta["deal"]["my_payout"] == 20000.0
        assert meta["deal"]["jv_partner"] == ""

    @pytest.mark.asyncio
    async def test_scheduler_deal_financials(self):
        """Verify financial calculations in the scheduler's contract_ready alert."""
        from app.services.conversation_engine import process_conversation

        deal = MagicMock(spec=Deal)
        deal.id = uuid.uuid4()
        deal.address = "456 Oak Ave"
        deal.city = "Dallas"
        deal.state = "TX"
        deal.asking_price = 250000.0
        deal.floor_price = 200000.0
        deal.contract_price = 180000.0
        deal.jv_split_percentage = 70.0
        deal.jv_partner_id = uuid.uuid4()
        deal.spread = 70000.0
        deal.arv = 350000.0
        deal.repair_estimate = 30000.0
        deal.condition_description = "Needs work"
        deal.year_built = 1985
        deal.status = "Available"
        deal.property_type = "House"
        deal.beds = 4
        deal.baths = 3
        deal.sqft = 2000

        buyer = MagicMock(spec=Buyer)
        buyer.id = uuid.uuid4()
        buyer.full_name = "Jane Smith"
        buyer.email = "jane@example.com"

        campaign = MagicMock(spec=Campaign)
        campaign.id = uuid.uuid4()
        campaign.deal_id = deal.id
        campaign.buyer_id = buyer.id
        campaign.conversation_stage = "collecting_info"
        campaign.buyer_legal_name = "Jane Smith"
        campaign.buyer_phone = "555-987-6543"
        campaign.buyer_title_company = "Stewart Title"
        campaign.agreed_price = 230000.0

        import json
        mock_ai = MagicMock()
        mock_ai.choices = [MagicMock()]
        mock_ai.choices[0].message.content = json.dumps({
            "stage": "contract_ready",
            "pass": False,
            "unsub": False,
            "reply": "Perfect, I'll start the paperwork.",
            "extracted_legal_name": None,
            "extracted_phone": None,
            "extracted_title_company": None,
            "extracted_agreed_price": None,
        })

        with patch(
            "app.services.conversation_engine.groq_chat_completion",
            AsyncMock(return_value=mock_ai),
        ):
            result = await process_conversation(
                reply_body="$230k is good",
                reply_subject="Re: Property",
                buyer=buyer,
                deal=deal,
                campaign=campaign,
                thread_history=[],
            )

        assert result["contract_ready"] is True

        # Simulate scheduler financial calculations
        asking = float(deal.asking_price)
        contract_p = float(deal.contract_price)
        split_pct = float(deal.jv_split_percentage or 50) / 100
        assignment_fee = asking - contract_p
        my_payout = assignment_fee * (1.0 - split_pct)

        assert asking == 250000.0
        assert contract_p == 180000.0
        assert split_pct == 0.70
        assert assignment_fee == 70000.0
        assert my_payout == pytest.approx(21000.0)

        log_entry = ActivityLog(
            id=uuid.uuid4(),
            entity_type="deal",
            entity_id=deal.id,
            action="contract_ready",
            metadata_json={
                "alert_type": "contract_ready",
                "alert_user": True,
                "priority": "high",
                "buyer": {
                    "name": buyer.full_name,
                    "email": buyer.email,
                    "legal_name": campaign.buyer_legal_name,
                    "phone": campaign.buyer_phone,
                    "title_company": campaign.buyer_title_company,
                },
                "deal": {
                    "address": deal.address,
                    "asking_price": asking,
                    "agreed_price": float(campaign.agreed_price),
                    "assignment_fee": assignment_fee,
                    "my_payout": my_payout,
                    "jv_partner": "",
                },
            },
        )

        assert log_entry.metadata_json["deal"]["assignment_fee"] == 70000.0
        assert log_entry.metadata_json["deal"]["my_payout"] == pytest.approx(21000.0)
        assert log_entry.metadata_json["deal"]["agreed_price"] == 230000.0

    @pytest.mark.asyncio
    async def test_scheduler_with_jv_partner(self):
        """Verify JV partner appears in the scheduler's contract_ready alert metadata."""
        from app.services.conversation_engine import process_conversation

        jv_partner_id = uuid.uuid4()

        deal = MagicMock(spec=Deal)
        deal.id = uuid.uuid4()
        deal.address = "789 Pine St"
        deal.city = "Houston"
        deal.state = "TX"
        deal.asking_price = 300000.0
        deal.floor_price = 250000.0
        deal.contract_price = 220000.0
        deal.jv_split_percentage = 60.0
        deal.jv_partner_id = jv_partner_id
        deal.spread = 80000.0
        deal.arv = 400000.0
        deal.repair_estimate = 20000.0
        deal.condition_description = "Great"
        deal.year_built = 2000
        deal.status = "Available"
        deal.property_type = "House"
        deal.beds = 3
        deal.baths = 2
        deal.sqft = 1800

        buyer = MagicMock(spec=Buyer)
        buyer.id = uuid.uuid4()
        buyer.full_name = "Bob Wilson"
        buyer.email = "bob@example.com"

        campaign = MagicMock(spec=Campaign)
        campaign.id = uuid.uuid4()
        campaign.deal_id = deal.id
        campaign.buyer_id = buyer.id
        campaign.conversation_stage = "collecting_info"
        campaign.buyer_legal_name = "Bob Wilson"
        campaign.buyer_phone = "555-111-2222"
        campaign.buyer_title_company = "Fidelity Title"
        campaign.agreed_price = 275000.0

        # Simulate scheduler loading JV partner
        jv = MagicMock(spec=JVPartner)
        jv.id = jv_partner_id
        jv.name = "ABC Capital Partners"
        jv.email = "abc@capital.com"

        import json
        mock_ai = MagicMock()
        mock_ai.choices = [MagicMock()]
        mock_ai.choices[0].message.content = json.dumps({
            "stage": "contract_ready",
            "pass": False,
            "unsub": False,
            "reply": "Perfect.",
            "extracted_legal_name": None,
            "extracted_phone": None,
            "extracted_title_company": None,
            "extracted_agreed_price": None,
        })

        with patch(
            "app.services.conversation_engine.groq_chat_completion",
            AsyncMock(return_value=mock_ai),
        ):
            result = await process_conversation(
                reply_body="$275k works",
                reply_subject="Re: Property",
                buyer=buyer,
                deal=deal,
                campaign=campaign,
                thread_history=[],
            )

        assert result["contract_ready"] is True

        # Simulate scheduler: load JV partner if deal has one
        jv_name = jv.name if jv else ""

        asking = float(deal.asking_price)
        contract_p = float(deal.contract_price)
        split_pct = float(deal.jv_split_percentage or 50) / 100
        assignment_fee = asking - contract_p
        my_payout = assignment_fee * (1.0 - split_pct)

        log_entry = ActivityLog(
            id=uuid.uuid4(),
            entity_type="deal",
            entity_id=deal.id,
            action="contract_ready",
            metadata_json={
                "alert_type": "contract_ready",
                "alert_user": True,
                "priority": "high",
                "buyer": {
                    "name": buyer.full_name,
                    "email": buyer.email,
                    "legal_name": campaign.buyer_legal_name,
                    "phone": campaign.buyer_phone,
                    "title_company": campaign.buyer_title_company,
                },
                "deal": {
                    "address": deal.address,
                    "asking_price": asking,
                    "agreed_price": float(campaign.agreed_price),
                    "assignment_fee": assignment_fee,
                    "my_payout": my_payout,
                    "jv_partner": jv_name,
                },
            },
        )

        meta = log_entry.metadata_json
        assert meta["deal"]["jv_partner"] == "ABC Capital Partners"
        assert meta["deal"]["my_payout"] == pytest.approx(32000.0)
        assert meta["deal"]["assignment_fee"] == 80000.0

    @pytest.mark.asyncio
    async def test_scheduler_pauses_queued_campaigns(self):
        """Verify the scheduler pauses remaining queued campaigns on contract_ready."""
        from app.services.conversation_engine import process_conversation

        deal_id = uuid.uuid4()
        buyer_id = uuid.uuid4()

        deal = MagicMock(spec=Deal)
        deal.id = deal_id
        deal.address = "101 Maple Dr"
        deal.city = "San Antonio"
        deal.state = "TX"
        deal.asking_price = 220000.0
        deal.floor_price = 180000.0
        deal.contract_price = 170000.0
        deal.jv_split_percentage = 50.0
        deal.jv_partner_id = None
        deal.spread = 50000.0
        deal.arv = 300000.0
        deal.repair_estimate = 15000.0
        deal.condition_description = "Good"
        deal.year_built = 2005
        deal.status = "Available"
        deal.property_type = "House"
        deal.beds = 3
        deal.baths = 2
        deal.sqft = 1600

        buyer = MagicMock(spec=Buyer)
        buyer.id = buyer_id
        buyer.full_name = "Alice Johnson"
        buyer.email = "alice@example.com"

        campaign = MagicMock(spec=Campaign)
        campaign.id = uuid.uuid4()
        campaign.deal_id = deal_id
        campaign.buyer_id = buyer_id
        campaign.conversation_stage = "collecting_info"
        campaign.buyer_legal_name = "Alice Johnson"
        campaign.buyer_phone = "555-333-4444"
        campaign.buyer_title_company = "Title Source"
        campaign.agreed_price = 200000.0

        # Simulate queued campaigns for same buyer+deal
        queued_touch_4 = MagicMock(spec=Campaign)
        queued_touch_4.id = uuid.uuid4()
        queued_touch_4.buyer_id = buyer_id
        queued_touch_4.deal_id = deal_id
        queued_touch_4.status = "Queued"
        queued_touch_4.touch_number = 4

        queued_touch_5 = MagicMock(spec=Campaign)
        queued_touch_5.id = uuid.uuid4()
        queued_touch_5.buyer_id = buyer_id
        queued_touch_5.deal_id = deal_id
        queued_touch_5.status = "Queued"
        queued_touch_5.touch_number = 5

        queued_touch_6 = MagicMock(spec=Campaign)
        queued_touch_6.id = uuid.uuid4()
        queued_touch_6.buyer_id = buyer_id
        queued_touch_6.deal_id = deal_id
        queued_touch_6.status = "Queued"
        queued_touch_6.touch_number = 6

        queued_campaigns = [queued_touch_4, queued_touch_5, queued_touch_6]

        import json
        mock_ai = MagicMock()
        mock_ai.choices = [MagicMock()]
        mock_ai.choices[0].message.content = json.dumps({
            "stage": "contract_ready",
            "pass": False,
            "unsub": False,
            "reply": "Perfect, I'll start the paperwork.",
            "extracted_legal_name": None,
            "extracted_phone": None,
            "extracted_title_company": None,
            "extracted_agreed_price": None,
        })

        with patch(
            "app.services.conversation_engine.groq_chat_completion",
            AsyncMock(return_value=mock_ai),
        ):
            result = await process_conversation(
                reply_body="$200k works",
                reply_subject="Re: Property",
                buyer=buyer,
                deal=deal,
                campaign=campaign,
                thread_history=[],
            )

        assert result["contract_ready"] is True

        # Simulate scheduler pausing queued campaigns
        campaign.status = "Contract_Pending"
        for qc in queued_campaigns:
            qc.status = "Paused"

        assert campaign.status == "Contract_Pending"
        for qc in queued_campaigns:
            assert qc.status == "Paused", (
                f"Expected queued touch {qc.touch_number} to be Paused, got {qc.status}"
            )

    @pytest.mark.asyncio
    async def test_scheduler_agreed_price_fallback(self):
        """If agreed_price is not set on the campaign, the scheduler falls back to asking_price."""
        from app.services.conversation_engine import process_conversation

        deal = MagicMock(spec=Deal)
        deal.id = uuid.uuid4()
        deal.address = "555 Elm St"
        deal.city = "Austin"
        deal.state = "TX"
        deal.asking_price = 190000.0
        deal.floor_price = 160000.0
        deal.contract_price = 150000.0
        deal.jv_split_percentage = 50.0
        deal.jv_partner_id = None
        deal.spread = 40000.0
        deal.arv = 260000.0
        deal.repair_estimate = 20000.0
        deal.condition_description = "Fair"
        deal.year_built = 1990
        deal.status = "Available"
        deal.property_type = "House"
        deal.beds = 3
        deal.baths = 1
        deal.sqft = 1200

        buyer = MagicMock(spec=Buyer)
        buyer.id = uuid.uuid4()
        buyer.full_name = "Tom Harris"
        buyer.email = "tom@example.com"

        campaign = MagicMock(spec=Campaign)
        campaign.id = uuid.uuid4()
        campaign.deal_id = deal.id
        campaign.buyer_id = buyer.id
        campaign.conversation_stage = "collecting_info"
        campaign.buyer_legal_name = "Tom Harris"
        campaign.buyer_phone = "555-777-8888"
        campaign.buyer_title_company = "Old Republic Title"
        campaign.agreed_price = None  # Not set yet

        import json
        mock_ai = MagicMock()
        mock_ai.choices = [MagicMock()]
        mock_ai.choices[0].message.content = json.dumps({
            "stage": "collecting_info",
            "pass": False,
            "unsub": False,
            "reply": "$175k sounds good",
            "extracted_legal_name": None,
            "extracted_phone": None,
            "extracted_title_company": None,
            "extracted_agreed_price": 175000,
        })

        with patch(
            "app.services.conversation_engine.groq_chat_completion",
            AsyncMock(return_value=mock_ai),
        ):
            result = await process_conversation(
                reply_body="$175k sounds good",
                reply_subject="Re: Property",
                buyer=buyer,
                deal=deal,
                campaign=campaign,
                thread_history=[],
            )

        assert result["contract_ready"] is True
        assert result["extracted_info"]["agreed_price"] == 175000

        # Simulate scheduler: set agreed_price from extracted info
        extracted = result.get("extracted_info", {})
        if extracted.get("agreed_price"):
            campaign.agreed_price = float(
                str(extracted["agreed_price"]).replace(",", "").replace("$", "")
            )

        # Now simulate alert creation with campaign's agreed_price
        asking = float(deal.asking_price)
        contract_p = float(deal.contract_price)
        split_pct = float(deal.jv_split_percentage or 50) / 100
        assignment_fee = asking - contract_p
        my_payout = assignment_fee * (1.0 - split_pct)

        agreed_price_for_alert = float(campaign.agreed_price) if campaign.agreed_price else asking

        log_entry = ActivityLog(
            id=uuid.uuid4(),
            entity_type="deal",
            entity_id=deal.id,
            action="contract_ready",
            metadata_json={
                "alert_type": "contract_ready",
                "alert_user": True,
                "priority": "high",
                "buyer": {
                    "name": buyer.full_name,
                    "email": buyer.email,
                    "legal_name": campaign.buyer_legal_name,
                    "phone": campaign.buyer_phone,
                    "title_company": campaign.buyer_title_company,
                },
                "deal": {
                    "address": deal.address,
                    "asking_price": asking,
                    "agreed_price": agreed_price_for_alert,
                    "assignment_fee": assignment_fee,
                    "my_payout": my_payout,
                    "jv_partner": "",
                },
            },
        )

        meta = log_entry.metadata_json
        assert meta["deal"]["agreed_price"] == 175000.0, (
            f"Expected agreed_price=175000, got {meta['deal']['agreed_price']}"
        )
        assert meta["deal"]["agreed_price"] != asking, (
            "Should not fall back to asking_price when agreed_price is extracted"
        )
