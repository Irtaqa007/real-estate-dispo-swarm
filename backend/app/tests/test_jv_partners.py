"""Comprehensive tests for JV partner and deal JV partner requirement.

Covers:
- JVPartner model: source, phone fields
- JV partner creation with dedup (email, name)
- JV partner update with new fields
- JV partner listing and retrieval
- DealBase schema: jv_partner_id required
- DealCreate schema: validates jv_partner_id is present
"""

import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException
from pydantic import ValidationError

from app.routers import jv_partners as jv_router
from app.schemas import JVPartnerCreate, JVPartnerUpdate, JVPartnerResponse, DealCreate
from app.models.models import JVPartner


# ===========================================================================
# Schema validation tests
# ===========================================================================

class TestJVPartnerSchemas:

    def test_jv_partner_create_required_fields(self):
        """Name and email are required, phone/source are optional."""
        data = JVPartnerCreate(name="Test Partner", email="test@example.com")
        assert data.name == "Test Partner"
        assert data.email == "test@example.com"
        assert data.phone is None
        assert data.source is None

    def test_jv_partner_create_with_optional_fields(self):
        """Phone and source can be provided."""
        data = JVPartnerCreate(
            name="Test Partner",
            email="test@example.com",
            phone="555-123-4567",
            source="Referral",
        )
        assert data.phone == "555-123-4567"
        assert data.source == "Referral"

    def test_jv_partner_create_missing_name(self):
        """Name is required."""
        with pytest.raises(ValidationError):
            JVPartnerCreate(email="test@example.com")

    def test_jv_partner_create_missing_email(self):
        """Email is required."""
        with pytest.raises(ValidationError):
            JVPartnerCreate(name="Test Partner")

    def test_jv_partner_update_all_optional(self):
        """All update fields are optional."""
        data = JVPartnerUpdate()
        assert data.name is None
        assert data.email is None
        assert data.phone is None
        assert data.source is None

    def test_jv_partner_update_partial(self):
        """Partial update should work."""
        data = JVPartnerUpdate(phone="555-999-9999", source="Website")
        assert data.name is None
        assert data.email is None
        assert data.phone == "555-999-9999"
        assert data.source == "Website"

    def test_jv_partner_response_inherits_fields(self):
        """Response should include phone and source."""
        response = JVPartnerResponse(
            id=uuid.uuid4(),
            name="Test",
            email="test@example.com",
            phone="555-000-0000",
            source="Cold Call",
            created_at="2026-01-01T00:00:00Z",
        )
        assert response.phone == "555-000-0000"
        assert response.source == "Cold Call"


class TestDealSchemaJvRequired:

    def test_deal_create_without_jv_partner_fails(self):
        """jv_partner_id is required on DealCreate."""
        with pytest.raises(ValidationError, match="jv_partner_id"):
            DealCreate(
                address="123 Main St",
                property_type="House",
                condition_description="Good",
                arv=200000,
                asking_price=180000,
                floor_price=150000,
                contract_price=140000,
                title_status="Clear",
                beds=3,
                baths=2,
                sqft=1500,
                # jv_partner_id is missing
            )

    def test_deal_create_with_jv_partner_succeeds(self):
        """DealCreate should succeed with jv_partner_id."""
        jv_id = uuid.uuid4()
        deal = DealCreate(
            address="123 Main St",
            property_type="House",
            condition_description="Good",
            arv=200000,
            asking_price=180000,
            floor_price=150000,
            contract_price=140000,
            title_status="Clear",
            beds=3,
            baths=2,
            sqft=1500,
            jv_partner_id=jv_id,
        )
        assert deal.jv_partner_id == jv_id
        assert deal.jv_split_percentage == 50  # default

    def test_deal_create_land_requires_jv_partner(self):
        """Land deals also require jv_partner_id."""
        jv_id = uuid.uuid4()
        deal = DealCreate(
            address="1 Vacant Lot",
            property_type="Land",
            condition_description="Raw land",
            arv=50000,
            asking_price=45000,
            floor_price=35000,
            contract_price=30000,
            title_status="Clear",
            lot_size="1 acre",
            zoning="Residential",
            jv_partner_id=jv_id,
        )
        assert deal.jv_partner_id == jv_id

    def test_deal_response_allows_null_jv_partner(self):
        """DealResponse should allow jv_partner_id=None for backward compat."""
        from app.schemas import DealResponse
        response = DealResponse(
            id=uuid.uuid4(),
            address="123 Main St",
            property_type="House",
            condition_description="Old deal",
            arv=200000,
            asking_price=180000,
            floor_price=150000,
            contract_price=140000,
            title_status="Clear",
            beds=3,
            baths=2,
            sqft=1500,
            jv_partner_id=None,  # Existing deal without JV partner
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        )
        assert response.jv_partner_id is None

    def test_deal_update_allows_null_jv_partner(self):
        """DealUpdate should allow jv_partner_id=None."""
        from app.schemas import DealUpdate
        update = DealUpdate(jv_partner_id=None)
        assert update.jv_partner_id is None

    def test_deal_update_no_jv_partner_change(self):
        """DealUpdate should work without specifying jv_partner_id."""
        from app.schemas import DealUpdate
        update = DealUpdate(address="456 New St")
        assert "jv_partner_id" not in update.model_dump(exclude_unset=True)
        jv_id = uuid.uuid4()
        deal = DealCreate(
            address="1 Vacant Lot",
            property_type="Land",
            condition_description="Raw land",
            arv=50000,
            asking_price=45000,
            floor_price=35000,
            contract_price=30000,
            title_status="Clear",
            lot_size="1 acre",
            zoning="Residential",
            jv_partner_id=jv_id,
        )
        assert deal.jv_partner_id == jv_id


# ===========================================================================
# JV Partner Router Tests
# ===========================================================================

class TestCreateJVPartner:

    @pytest.mark.asyncio
    async def test_create_with_all_fields(self):
        """Should create JV partner with name, email, phone, source."""
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        jv_in = JVPartnerCreate(
            name="John Doe",
            email="john@example.com",
            phone="555-123-4567",
            source="Referral",
        )

        result = await jv_router.create_jv_partner(jv_in, mock_db)

        assert result.name == "John Doe"
        assert result.email == "john@example.com"
        assert result.phone == "555-123-4567"
        assert result.source == "Referral"

        # Verify the added JV partner has all fields
        added = mock_db.add.call_args[0][0]
        assert added.name == "John Doe"
        assert added.email == "john@example.com"
        assert added.phone == "555-123-4567"
        assert added.source == "Referral"

    @pytest.mark.asyncio
    async def test_create_without_optional_fields(self):
        """Should create JV partner without phone and source."""
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        jv_in = JVPartnerCreate(name="Jane Doe", email="jane@example.com")

        result = await jv_router.create_jv_partner(jv_in, mock_db)

        added = mock_db.add.call_args[0][0]
        assert added.phone is None
        assert added.source is None

    @pytest.mark.asyncio
    async def test_duplicate_email_returns_409(self):
        """Same email should raise 409."""
        existing = MagicMock(spec=JVPartner)
        existing.email = "dup@example.com"

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=existing)))

        jv_in = JVPartnerCreate(name="Dup Person", email="dup@example.com")

        with pytest.raises(HTTPException) as exc:
            await jv_router.create_jv_partner(jv_in, mock_db)
        assert exc.value.status_code == 409
        assert "already exists" in str(exc.value.detail).lower()

    @pytest.mark.asyncio
    async def test_duplicate_name_returns_409(self):
        """Same name (case-insensitive) should raise 409."""
        mock_result_email = MagicMock()
        mock_result_email.scalar_one_or_none.return_value = None

        existing = MagicMock(spec=JVPartner)
        existing.name = "Test Partner"

        mock_result_name = MagicMock()
        mock_result_name.scalar_one_or_none.return_value = existing

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(side_effect=[mock_result_email, mock_result_name])

        jv_in = JVPartnerCreate(name="test partner", email="different@example.com")

        with pytest.raises(HTTPException) as exc:
            await jv_router.create_jv_partner(jv_in, mock_db)
        assert exc.value.status_code == 409
        assert "test partner" in str(exc.value.detail).lower()

    @pytest.mark.asyncio
    async def test_unique_name_with_different_email_succeeds(self):
        """Different name and email should succeed."""
        mock_execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
        mock_db = AsyncMock()
        mock_db.execute = mock_execute
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        jv_in = JVPartnerCreate(name="Unique Name", email="unique@example.com")
        result = await jv_router.create_jv_partner(jv_in, mock_db)
        assert result.name == "Unique Name"


class TestUpdateJVPartner:

    @pytest.mark.asyncio
    async def test_update_phone_and_source(self):
        """Should update phone and source fields."""
        mock_jv = MagicMock(spec=JVPartner)
        mock_jv.id = uuid.uuid4()
        mock_jv.phone = None
        mock_jv.source = None

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=mock_jv)))
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        jv_in = JVPartnerUpdate(phone="555-999-9999", source="Website")

        result = await jv_router.update_jv_partner(mock_jv.id, jv_in, mock_db)

        assert mock_jv.phone == "555-999-9999"
        assert mock_jv.source == "Website"
        mock_db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_update_not_found(self):
        """Non-existent JV partner should return 404."""
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))

        with pytest.raises(HTTPException) as exc:
            await jv_router.update_jv_partner(uuid.uuid4(), JVPartnerUpdate(name="New"), mock_db)
        assert exc.value.status_code == 404


class TestListJVPartners:

    @pytest.mark.asyncio
    async def test_list_includes_new_fields(self):
        """List should return partners with phone and source."""
        mock_partner = MagicMock(spec=JVPartner)
        mock_partner.id = uuid.uuid4()
        mock_partner.name = "Test"
        mock_partner.email = "test@example.com"
        mock_partner.phone = "555-000-0000"
        mock_partner.source = "Referral"
        mock_partner.deals_linked = []
        mock_partner.total_deals_submitted = 0
        mock_partner.total_deals_closed = 0
        mock_partner.total_revenue_generated = 0
        mock_partner.avg_buyer_feedback_score = 0
        mock_partner.title_issue_rate = 0
        mock_partner.overprice_flag_count = 0
        mock_partner.total_split_revenue = 0
        mock_partner.created_at = None

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_partner]

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        results = await jv_router.list_jv_partners(db=mock_db)
        assert len(results) == 1
        assert results[0].phone == "555-000-0000"
        assert results[0].source == "Referral"
