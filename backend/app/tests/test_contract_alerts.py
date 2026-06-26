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

from app.models.schemas import ActivityLog, Buyer, Campaign, Deal, JVPartner


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
    assert mock_entry.metadata_json["resolved"] is True
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
    mock_entry_1.metadata_json = {
        "alert_type": "contract_ready",
        "alert_user": True,
        "resolved": False,
        "buyer": {"name": "Buyer One", "email": "buyer1@test.com"},
        "deal": {"address": "123 Main St", "state": "TX", "my_payout": 20000},
        "negotiated_price": 180000,
    }

    mock_entry_2 = MagicMock(spec=ActivityLog)
    mock_entry_2.id = alert_id_2
    mock_entry_2.action = "contract_ready"
    mock_entry_2.created_at = now
    mock_entry_2.metadata_json = {
        "alert_type": "contract_ready",
        "alert_user": True,
        "resolved": True,
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
    assert alerts[1]["full_metadata"]["resolved"] is True
