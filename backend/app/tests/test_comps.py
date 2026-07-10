"""Tests for the Comparable Sales (Comps) module.

Covers CRUD operations, max-5 enforcement, and email prompt injection.
"""

from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.models.models import DealComp
from app.schemas import DealCompCreate


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_db():
    """Create a mock async database session."""
    db = AsyncMock()
    db.execute = AsyncMock()
    db.get = AsyncMock()
    db.add = MagicMock()
    db.delete = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db


@pytest.fixture
def mock_deal():
    """Create a mock Deal instance."""
    deal = MagicMock()
    deal.id = "deal-1"
    deal.address = "123 Test St"
    return deal


@pytest.fixture
def sample_comp_data() -> dict:
    """Sample comp creation data."""
    return {
        "address": "456 Comp Ln",
        "sold_price": 250000.0,
        "sold_date": date(2024, 6, 15),
        "beds": 3,
        "baths": 2.0,
        "sqft": 1500,
        "distance_miles": 1.2,
        "notes": "Good comp for ARV validation",
    }


@pytest.fixture
def sample_comp() -> DealComp:
    """Create a sample DealComp instance."""
    comp = DealComp(
        id="comp-1",
        deal_id="deal-1",
        address="456 Comp Ln",
        sold_price=Decimal("250000"),
        sold_date=datetime(2024, 6, 15, tzinfo=timezone.utc),
        beds=3,
        baths=Decimal("2.0"),
        sqft=1500,
        distance_miles=Decimal("1.2"),
        notes="Good comp for ARV validation",
        created_at=datetime.now(timezone.utc),
    )
    return comp


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------


class TestDealCompModel:
    """Verify DealComp model has the right fields and relationships."""

    def test_dealcomp_has_all_fields(self, sample_comp):
        """All expected fields are present on a DealComp instance."""
        assert hasattr(sample_comp, "id")
        assert hasattr(sample_comp, "deal_id")
        assert hasattr(sample_comp, "address")
        assert hasattr(sample_comp, "sold_price")
        assert hasattr(sample_comp, "sold_date")
        assert hasattr(sample_comp, "beds")
        assert hasattr(sample_comp, "baths")
        assert hasattr(sample_comp, "sqft")
        assert hasattr(sample_comp, "distance_miles")
        assert hasattr(sample_comp, "notes")
        assert hasattr(sample_comp, "created_at")

    def test_dealcomp_repr(self, sample_comp):
        """__repr__ returns a meaningful string."""
        rep = repr(sample_comp)
        assert "DealComp" in rep
        assert "comp-1" in rep


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


class TestDealCompCreateSchema:
    """Verify DealCompCreate schema validation."""

    def test_valid_create(self, sample_comp_data):
        """Valid data passes schema validation."""
        schema = DealCompCreate(**sample_comp_data)
        assert schema.address == "456 Comp Ln"
        assert schema.sold_price == 250000.0

    def test_optional_fields(self):
        """Only required fields are address, sold_price, sold_date."""
        schema = DealCompCreate(
            address="789 Test Ave",
            sold_price=100000.0,
            sold_date=date(2024, 1, 1),
        )
        assert schema.beds is None
        assert schema.baths is None
        assert schema.notes is None


# ---------------------------------------------------------------------------
# API endpoint tests (mocked DB)
# ---------------------------------------------------------------------------


class TestAddComp:
    """Tests for POST /api/deals/{deal_id}/comps."""

    @pytest.mark.asyncio
    async def test_add_comp_success(self, mock_db, mock_deal, sample_comp_data):
        """Successfully add a comp."""
        from app.routers.deals import add_comp

        # First call: deal lookup → returns deal
        # Second call: count query → returns 0 existing comps
        deal_result = MagicMock(scalar_one_or_none=MagicMock(return_value=mock_deal))
        count_result_mock = MagicMock()
        count_result_mock.scalars.return_value.all.return_value = []
        mock_db.execute = AsyncMock(side_effect=[deal_result, count_result_mock])

        result = await add_comp(
            deal_id=mock_deal.id,
            comp_in=DealCompCreate(**sample_comp_data),
            db=mock_db,
        )

        assert result.address == sample_comp_data["address"]
        assert result.sold_price == sample_comp_data["sold_price"]
        assert mock_db.add.called
        assert mock_db.commit.called

    @pytest.mark.asyncio
    async def test_add_comp_deal_not_found(self, mock_db):
        """Adding comp to non-existent deal raises 404."""
        from app.routers.deals import add_comp

        mock_db.execute.return_value = MagicMock(
            scalar_one_or_none=MagicMock(return_value=None)
        )

        with pytest.raises(HTTPException) as exc:
            await add_comp(
                deal_id="nonexistent",
                comp_in=DealCompCreate(
                    address="Test",
                    sold_price=100000.0,
                    sold_date=date(2024, 1, 1),
                ),
                db=mock_db,
            )
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_add_comp_max_5_enforced(self, mock_db, mock_deal, sample_comp_data):
        """Adding a 6th comp raises 400."""
        from app.routers.deals import add_comp

        deal_result = MagicMock(scalar_one_or_none=MagicMock(return_value=mock_deal))
        count_result_mock = MagicMock()
        count_result_mock.scalars.return_value.all.return_value = [MagicMock() for _ in range(5)]
        mock_db.execute = AsyncMock(side_effect=[deal_result, count_result_mock])

        with pytest.raises(HTTPException) as exc:
            await add_comp(
                deal_id=mock_deal.id,
                comp_in=DealCompCreate(**sample_comp_data),
                db=mock_db,
            )
        assert exc.value.status_code == 400
        assert "Maximum 5 comps" in str(exc.value.detail)


class TestGetComps:
    """Tests for GET /api/deals/{deal_id}/comps."""

    @pytest.mark.asyncio
    async def test_get_comps_for_deal(self, mock_db, sample_comp):
        """Get comps returns list ordered by sold_date DESC."""
        from app.routers.deals import list_comps

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [sample_comp]
        mock_db.execute.return_value = mock_result

        result = await list_comps(deal_id="deal-1", db=mock_db)
        assert len(result) == 1
        assert result[0].address == "456 Comp Ln"

    @pytest.mark.asyncio
    async def test_get_comps_empty(self, mock_db):
        """Get comps returns empty list when none exist."""
        from app.routers.deals import list_comps

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db.execute.return_value = mock_result

        result = await list_comps(deal_id="deal-1", db=mock_db)
        assert result == []


class TestDeleteComp:
    """Tests for DELETE /api/deals/{deal_id}/comps/{comp_id}."""

    @pytest.mark.asyncio
    async def test_delete_comp_success(self, mock_db, sample_comp):
        """Successfully delete a comp."""
        from app.routers.deals import delete_comp

        mock_db.execute.return_value = MagicMock(
            scalar_one_or_none=MagicMock(return_value=sample_comp)
        )

        result = await delete_comp(deal_id="deal-1", comp_id="comp-1", db=mock_db)
        assert result is None
        assert mock_db.delete.called
        assert mock_db.commit.called

    @pytest.mark.asyncio
    async def test_delete_comp_not_found(self, mock_db):
        """Delete non-existent comp raises 404."""
        from app.routers.deals import delete_comp

        mock_db.execute.return_value = MagicMock(
            scalar_one_or_none=MagicMock(return_value=None)
        )

        with pytest.raises(HTTPException) as exc:
            await delete_comp(deal_id="deal-1", comp_id="nonexistent", db=mock_db)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_comp_wrong_deal(self, mock_db):
        """Delete comp from wrong deal raises 404."""
        from app.routers.deals import delete_comp

        mock_db.execute.return_value = MagicMock(
            scalar_one_or_none=MagicMock(return_value=None)
        )

        with pytest.raises(HTTPException) as exc:
            await delete_comp(deal_id="wrong-deal", comp_id="comp-1", db=mock_db)
        assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# Email prompt injection tests
# ---------------------------------------------------------------------------


class TestCompsInEmailPrompt:
    """Verify comps are injected into email prompts at the right touch.

    Tests _build_prompt directly (private function) because it's the function
    that contains the comps injection logic. This avoids the complexity of
    mocking Groq API calls and frozen Pydantic settings.
    """

    def _build_comp_list(self):
        return [{
            "address": "456 Comp Ln",
            "sold_price": 250000,
            "sold_date": "June 2024",
            "beds": 3,
            "baths": 2.0,
            "sqft": 1500,
        }]

    def test_comp_appears_in_touch_3_prompt(self):
        """Comps data appears in the prompt for touch >= 3."""
        from app.services.email_generator import _build_prompt

        messages = _build_prompt(
            touch=3,
            buyer_name="Test Buyer",
            buyer_email="test@test.com",
            buy_box="SFR in San Antonio",
            buyer_tier="A-List",
            address="123 Test St",
            city="San Antonio",
            state="TX",
            property_type="House",
            arv=300000.0,
            asking_price=200000.0,
            spread=50000.0,
            condition_description="Good condition",
            beds=3,
            baths=2,
            sqft=1500,
            comps=self._build_comp_list(),
        )

        user_msg = next((m["content"] for m in messages if m["role"] == "user"), "")
        assert "COMP DATA" in user_msg
        assert "456 Comp Ln" in user_msg

    def test_comp_NOT_in_touch_1_prompt(self):
        """Comps data is NOT injected for touch < 3."""
        from app.services.email_generator import _build_prompt

        messages = _build_prompt(
            touch=1,
            buyer_name="Test Buyer",
            buyer_email="test@test.com",
            buy_box="SFR in San Antonio",
            buyer_tier="A-List",
            address="123 Test St",
            city="San Antonio",
            state="TX",
            property_type="House",
            arv=300000.0,
            asking_price=200000.0,
            spread=50000.0,
            condition_description="Good condition",
            beds=3,
            baths=2,
            sqft=1500,
            comps=self._build_comp_list(),
        )

        user_msg = next((m["content"] for m in messages if m["role"] == "user"), "")
        assert "COMP DATA" not in user_msg

    def test_no_comp_no_hallucination_instruction(self):
        """When no comps exist and touch >= 3, prompt says 'do not invent'."""
        from app.services.email_generator import _build_prompt

        messages = _build_prompt(
            touch=3,
            buyer_name="Test Buyer",
            buyer_email="test@test.com",
            buy_box="SFR in San Antonio",
            buyer_tier="A-List",
            address="123 Test St",
            city="San Antonio",
            state="TX",
            property_type="House",
            arv=300000.0,
            asking_price=200000.0,
            spread=50000.0,
            condition_description="Good condition",
            beds=3,
            baths=2,
            sqft=1500,
            comps=None,
        )

        user_msg = next((m["content"] for m in messages if m["role"] == "user"), "")
        assert "do not invent" in user_msg.lower()
        assert "no comp data available" in user_msg.lower()
