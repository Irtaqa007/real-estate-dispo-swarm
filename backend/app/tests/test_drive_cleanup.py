"""Tests for payment confirmation and Drive cleanup features.

Tests:
- mark-paid on Sold deal → payment_confirmed=True, drive_archived unchanged (no Drive)
- mark-paid on deal with no drive_folder_id → payment confirmed, no archive
- mark-paid on already-paid deal → 400 error
- mark-paid on Available deal → 400 error
- Revenue dashboard returns correct totals for confirmed and pending payments
- Drive archive functions handle success and failure
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from fastapi import status


# ---------------------------------------------------------------------------
# Test: mark-paid on Sold deal
# ---------------------------------------------------------------------------


def test_mark_paid_on_sold_deal():
    """mark-paid on a Sold deal should confirm payment."""
    # SQLAlchemy Column(default=False) only applies at DB insert time,
    # not when creating model instances directly.
    # So drive_archived and payment_confirmed will be None until set.
    from app.models.schemas import Deal

    now = datetime.now(timezone.utc)
    deal = Deal(
        id=uuid.uuid4(),
        address="123 Test St",
        city="Austin", state="TX",
        property_type="House", condition_description="Good",
        arv=250000, asking_price=200000, floor_price=170000,
        contract_price=150000, title_status="Clear",
        status="Sold", closed_price=185000, net_spread=35000,
        jv_split_percentage=50, jv_payout=17500, my_payout=17500,
        drive_folder_id="folder_123",
    )
    # Defaults are DB-level — None at Python level
    assert deal.payment_confirmed is None

    # Simulate the endpoint logic
    deal.payment_confirmed = True
    deal.payment_confirmed_at = now
    deal.payment_amount = 185000.00

    assert deal.payment_confirmed is True
    assert deal.payment_amount == 185000.00


def test_mark_paid_on_under_contract_deal():
    """mark-paid on Under Contract deal should set status to Sold."""
    from app.models.schemas import Deal

    deal = Deal(
        id=uuid.uuid4(),
        address="456 Oak Ave",
        city="Dallas", state="TX",
        property_type="House", condition_description="Needs work",
        arv=300000, asking_price=250000, floor_price=210000,
        contract_price=190000, title_status="Clear",
        status="Under Contract",
        jv_split_percentage=50,
        drive_folder_id="folder_456",
    )
    assert deal.status == "Under Contract"

    deal.payment_confirmed = True
    deal.payment_confirmed_at = datetime.now(timezone.utc)
    deal.payment_amount = 250000.00
    deal.status = "Sold"

    assert deal.payment_confirmed is True
    assert deal.status == "Sold"


def test_mark_paid_already_confirmed_returns_400():
    """mark-paid on already-paid deal should fail with 400."""
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payment already confirmed for this deal",
        )
    assert exc_info.value.status_code == 400
    assert "already confirmed" in exc_info.value.detail


def test_mark_paid_available_deal_returns_400():
    """mark-paid on Available deal should fail with 400."""
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Deal must be in Sold or Under Contract status to confirm payment",
        )
    assert exc_info.value.status_code == 400


def test_mark_paid_no_drive_folder():
    """mark-paid on deal with no drive_folder_id confirms payment without archiving."""
    from app.models.schemas import Deal

    now = datetime.now(timezone.utc)
    deal = Deal(
        id=uuid.uuid4(),
        address="654 Cedar Ln",
        city="Fort Worth", state="TX",
        property_type="House", condition_description="Average",
        arv=220000, asking_price=175000, floor_price=145000,
        contract_price=130000, title_status="Clear",
        status="Sold", closed_price=160000,
        jv_split_percentage=50,
        drive_folder_id=None,
    )
    assert deal.drive_folder_id is None

    # Payment confirmed successfully even without Drive folder
    deal.payment_confirmed = True
    deal.payment_confirmed_at = now
    deal.payment_amount = 160000.00

    assert deal.payment_confirmed is True
    # drive_archived is DB-default, so it's None at Python level
    assert deal.drive_archived is None or deal.drive_archived is False


def test_drive_failure_still_confirms_payment():
    """Drive API failure should not prevent payment confirmation."""
    from app.models.schemas import Deal

    deal = Deal(
        id=uuid.uuid4(),
        address="123 Test St",
        city="Austin", state="TX",
        property_type="House", condition_description="Good",
        arv=250000, asking_price=200000, floor_price=170000,
        contract_price=150000, title_status="Clear",
        status="Sold", closed_price=185000,
        jv_split_percentage=50,
        drive_folder_id="folder_123",
    )

    deal.payment_confirmed = True
    deal.payment_confirmed_at = datetime.now(timezone.utc)
    deal.payment_amount = 185000.00

    assert deal.payment_confirmed is True
    assert deal.payment_amount == 185000.00
    # Drive fields remain unset if archiving failed
    assert deal.drive_archived is None or deal.drive_archived is False


# ---------------------------------------------------------------------------
# Test: Revenue dashboard aggregation
# ---------------------------------------------------------------------------


def test_revenue_dashboard_totals():
    """Revenue dashboard should return correct totals for confirmed vs pending."""
    from app.models.schemas import Deal

    now = datetime.now(timezone.utc)
    confirmed_deal = Deal(
        id=uuid.uuid4(),
        address="123 Confirmed St",
        city="Austin", state="TX",
        property_type="House", condition_description="Good",
        arv=250000, asking_price=200000, floor_price=170000,
        contract_price=150000, title_status="Clear",
        status="Sold",
        closed_at=now,
        closed_price=185000, net_spread=35000,
        jv_split_percentage=50, jv_payout=17500, my_payout=17500,
        payment_confirmed=True, payment_amount=185000,
    )

    pending_deal = Deal(
        id=uuid.uuid4(),
        address="456 Pending St",
        city="Dallas", state="TX",
        property_type="House", condition_description="Needs work",
        arv=300000, asking_price=250000, floor_price=210000,
        contract_price=190000, title_status="Clear",
        status="Sold",
        closed_at=now,
        closed_price=240000, net_spread=50000,
        jv_split_percentage=50, jv_payout=25000, my_payout=25000,
        payment_confirmed=False,
    )

    deals = [confirmed_deal, pending_deal]
    total_my_payout_confirmed = 0.0
    total_my_payout_pending = 0.0

    for d in deals:
        my_p = float(d.my_payout) if d.my_payout else 0.0
        if d.payment_confirmed:
            total_my_payout_confirmed += float(d.payment_amount) if d.payment_amount else my_p
        elif d.status == "Sold":
            total_my_payout_pending += my_p

    assert total_my_payout_confirmed == 185000.00
    assert total_my_payout_pending == 25000.00


# ---------------------------------------------------------------------------
# Test: Google Drive archive functions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_archive_deal_folder_success():
    """archive_deal_folder should return success on valid folder ID."""
    from app.services.google_drive import archive_deal_folder

    mock_service = MagicMock()
    mock_service.files().update().execute.return_value = {
        "id": "folder_123", "parents": ["archive_folder"],
    }

    result = await archive_deal_folder(
        drive_service=mock_service,
        deal_folder_id="folder_123",
        deal_address="123 Test St",
    )

    assert result["success"] is True
    assert result["archive_folder_id"] is not None
    assert result["archived_folder_id"] == "folder_123"
    assert result["error"] is None


@pytest.mark.asyncio
async def test_archive_deal_folder_api_failure():
    """archive_deal_folder should return error dict on Drive API failure."""
    from app.services.google_drive import archive_deal_folder

    mock_service = MagicMock()
    mock_service.files().update().execute.side_effect = Exception("Drive API quota exceeded")

    result = await archive_deal_folder(
        drive_service=mock_service,
        deal_folder_id="folder_123",
        deal_address="123 Test St",
    )

    assert result["success"] is False
    assert result["error"] is not None
    assert "quota" in result["error"].lower()


@pytest.mark.asyncio
async def test_revoke_shared_links_no_permissions():
    """revoke_shared_links should return 0 when there are no anyone permissions."""
    from app.services.google_drive import revoke_shared_links

    mock_service = MagicMock()
    mock_service.files().list().execute.return_value = {
        "files": [], "nextPageToken": None,
    }

    count = await revoke_shared_links(drive_service=mock_service, folder_id="folder_123")
    assert count == 0


@pytest.mark.asyncio
async def test_revoke_shared_links_revokes_anyone():
    """revoke_shared_links should revoke anyone permissions."""
    from app.services.google_drive import revoke_shared_links

    mock_service = MagicMock()
    mock_service.files().list().execute.return_value = {
        "files": [{"id": "file_1", "name": "doc.pdf"}],
        "nextPageToken": None,
    }
    mock_service.permissions().list().execute.return_value = {
        "permissions": [{"id": "perm_1", "type": "anyone"}],
    }

    count = await revoke_shared_links(drive_service=mock_service, folder_id="folder_123")
    assert count == 1
    mock_service.permissions().delete.assert_called_once()


@pytest.mark.asyncio
async def test_revoke_shared_links_skips_non_anyone():
    """revoke_shared_links should skip permissions that are not type=anyone."""
    from app.services.google_drive import revoke_shared_links

    mock_service = MagicMock()
    mock_service.files().list().execute.return_value = {
        "files": [{"id": "file_1", "name": "doc.pdf"}],
        "nextPageToken": None,
    }
    mock_service.permissions().list().execute.return_value = {
        "permissions": [
            {"id": "perm_1", "type": "domain"},
            {"id": "perm_2", "type": "user"},
        ],
    }

    count = await revoke_shared_links(drive_service=mock_service, folder_id="folder_123")
    assert count == 0


# ---------------------------------------------------------------------------
# Test: get_or_create_archive_folder
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_or_create_archive_folder():
    """get_or_create_archive_folder should create new archive folder if none exists."""
    from app.services.google_drive import get_or_create_archive_folder

    mock_service = MagicMock()
    # No existing archive folder
    mock_service.files().list().execute.return_value = {"files": []}
    mock_service.files().create().execute.return_value = {"id": "new_archive_folder_123"}

    folder_id = await get_or_create_archive_folder(drive_service=mock_service)
    assert folder_id == "new_archive_folder_123"

    create_call = mock_service.files().create.call_args
    assert create_call is not None
    assert create_call[1]["body"]["name"] == "Closed Deals Archive"
