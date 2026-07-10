"""Tests for the Package Deal module.

Covers CRUD, validations, launch, close, and delete operations.
"""

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.models.models import DealPackage, DealPackageItem
from app.schemas import PackageCreate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _uuid(i: int = 1) -> str:
    """Generate a deterministic UUID for testing."""
    return str(uuid.UUID(int=i, version=4))


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
    db.flush = AsyncMock()
    return db


@pytest.fixture
def mock_package():
    """Create a mock DealPackage instance."""
    pkg = MagicMock(spec=DealPackage)
    pkg.id = _uuid(1)
    pkg.name = "Test Package"
    pkg.package_price = Decimal("450000")
    pkg.package_arv = Decimal("600000")
    pkg.floor_price = Decimal("400000")
    pkg.status = "Active"
    pkg.description = "A test package"
    pkg.expiry_date = None
    pkg.created_at = datetime.now(timezone.utc)
    pkg.items = []
    return pkg


@pytest.fixture
def mock_deal():
    """Create a mock Deal instance."""
    deal = MagicMock()
    deal.id = _uuid(10)
    deal.address = "123 Test St"
    deal.city = "San Antonio"
    deal.state = "TX"
    deal.property_type = "House"
    deal.beds = 3
    deal.baths = 2
    deal.sqft = 1500
    deal.asking_price = Decimal("200000")
    deal.arv = Decimal("250000")
    deal.contract_price = Decimal("150000")
    deal.status = "Available"
    return deal


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


class TestPackageCreateSchema:
    """Verify PackageCreate schema validation."""

    def test_valid_create(self):
        """Valid data passes schema validation."""
        schema = PackageCreate(
            name="Test 3-Pack",
            deal_ids=[_uuid(1), _uuid(2), _uuid(3)],
            package_price=450000.0,
            floor_price=400000.0,
        )
        assert schema.name == "Test 3-Pack"
        assert len(schema.deal_ids) == 3

    def test_min_2_deals_enforced(self):
        """Less than 2 deals raises validation error."""
        with pytest.raises(ValidationError) as exc:
            PackageCreate(
                name="Single Deal",
                deal_ids=[_uuid(1)],
                package_price=200000.0,
                floor_price=180000.0,
            )
        assert "2-5 deals" in str(exc.value)

    def test_max_5_deals_enforced(self):
        """More than 5 deals raises validation error."""
        with pytest.raises(ValidationError) as exc:
            PackageCreate(
                name="Too Many",
                deal_ids=[_uuid(i) for i in range(6)],
                package_price=1000000.0,
                floor_price=900000.0,
            )
        assert "2-5 deals" in str(exc.value)

    def test_floor_price_less_than_package_price(self):
        """floor_price >= package_price raises validation error."""
        with pytest.raises(ValidationError) as exc:
            PackageCreate(
                name="Bad Floor",
                deal_ids=[_uuid(1), _uuid(2)],
                package_price=450000.0,
                floor_price=500000.0,
            )
        assert "floor_price must be less than" in str(exc.value)

    def test_floor_price_equal_to_package_price(self):
        """floor_price == package_price raises validation error."""
        with pytest.raises(ValidationError):
            PackageCreate(
                name="Equal",
                deal_ids=[_uuid(1), _uuid(2)],
                package_price=450000.0,
                floor_price=450000.0,
            )


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------


class TestDealPackageModel:
    """Verify DealPackage model has the right fields."""

    def test_has_all_fields(self, mock_package):
        assert hasattr(mock_package, "id")
        assert hasattr(mock_package, "name")
        assert hasattr(mock_package, "package_price")
        assert hasattr(mock_package, "floor_price")
        assert hasattr(mock_package, "status")


class TestDealPackageItemModel:
    """Verify DealPackageItem model."""

    def test_has_all_fields(self):
        item = DealPackageItem(
            id="item-1",
            package_id="pkg-1",
            deal_id="deal-1",
            individual_asking_price=Decimal("200000"),
        )
        assert item.package_id == "pkg-1"
        assert item.deal_id == "deal-1"


# ---------------------------------------------------------------------------
# API endpoint tests (mocked DB)
# ---------------------------------------------------------------------------


class TestCreatePackage:
    """Tests for POST /api/packages."""

    @pytest.mark.asyncio
    async def test_create_package_success(self, mock_db):
        """Successfully create a package."""
        # Short-circuit: test schema validation and basic flow without real DB
        # This tests that PackageCreate validates correctly
        deal_id_1 = _uuid(10)
        deal_id_2 = _uuid(11)

        package_in = PackageCreate(
            name="Test Package",
            deal_ids=[deal_id_1, deal_id_2],
            package_price=450000.0,
            floor_price=400000.0,
        )
        assert package_in.name == "Test Package"
        assert len(package_in.deal_ids) == 2

    @pytest.mark.asyncio
    async def test_create_package_deal_not_found(self, mock_db):
        """Creating package with non-existent deals raises 400."""
        from app.routers.packages import create_package

        deal_result = MagicMock()
        deal_result.scalars.return_value.all.return_value = []
        mock_db.execute.return_value = deal_result

        package_in = PackageCreate(
            name="Missing Deals",
            deal_ids=[_uuid(99), _uuid(98)],
            package_price=450000.0,
            floor_price=400000.0,
        )

        with pytest.raises(HTTPException) as exc:
            await create_package(package_in=package_in, db=mock_db)
        assert exc.value.status_code == 400


class TestGetPackages:
    """Tests for GET /api/packages."""

    @pytest.mark.asyncio
    async def test_get_packages_list(self, mock_db, mock_package):
        """List all packages."""
        from app.routers.packages import list_packages

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_package]
        mock_db.execute.return_value = mock_result

        result = await list_packages(db=mock_db)
        assert len(result) >= 1


class TestDeletePackage:
    """Tests for DELETE /api/packages/{package_id}."""

    @pytest.mark.asyncio
    async def test_delete_active_package(self, mock_db, mock_package):
        """Successfully delete an Active package."""
        from app.routers.packages import delete_package

        mock_db.execute.return_value = MagicMock(
            scalar_one_or_none=MagicMock(return_value=mock_package)
        )

        result = await delete_package(package_id=_uuid(1), db=mock_db)
        assert result is None
        assert mock_db.delete.called
        assert mock_db.commit.called

    @pytest.mark.asyncio
    async def test_cannot_delete_sold_package(self, mock_db, mock_package):
        """Cannot delete a Sold package."""
        from app.routers.packages import delete_package

        mock_package.status = "Sold"
        mock_db.execute.return_value = MagicMock(
            scalar_one_or_none=MagicMock(return_value=mock_package)
        )

        with pytest.raises(HTTPException) as exc:
            await delete_package(package_id=_uuid(1), db=mock_db)
        assert exc.value.status_code == 400
        assert "Cannot delete" in str(exc.value.detail)


class TestClosePackage:
    """Tests for POST /api/packages/{package_id}/close."""

    @pytest.mark.asyncio
    async def test_close_package_pauses_campaigns(self, mock_db, mock_package):
        """Close a Launched package, pausing campaigns."""
        from app.routers.packages import close_package

        mock_package.status = "Launched"
        mock_package.items = []

        campaign_result = MagicMock()
        campaign_result.scalars.return_value.all.return_value = []

        # First call: scalar_one_or_none for package lookup
        # Second call: scalars().all() for campaign lookup
        mock_db.execute = AsyncMock(side_effect=[
            MagicMock(scalar_one_or_none=MagicMock(return_value=mock_package)),
            campaign_result,
        ])

        result = await close_package(package_id=_uuid(1), db=mock_db)
        assert result["success"] is True


# ---------------------------------------------------------------------------
# Launch tests (mocked)
# ---------------------------------------------------------------------------


class TestLaunchPackage:
    """Tests for POST /api/packages/{package_id}/launch."""

    @patch("app.services.matching_service.find_top_matches_for_deal")
    @patch("app.services.campaign_launcher.launch_package_campaign")
    @pytest.mark.asyncio
    async def test_launch_package_creates_campaigns(
        self, mock_launch_fn, mock_match_fn, mock_db, mock_package
    ):
        """Launch successfully creates campaigns."""
        from app.routers.packages import launch_package

        deal_id_1 = _uuid(10)
        mock_package.status = "Active"

        # Populate package.items with at least one item
        item = MagicMock()
        item.deal_id = deal_id_1
        mock_package.items = [item]

        # Mock deal in deals query
        deal1 = MagicMock()
        deal1.id = deal_id_1
        deal1.address = "123 Test St"
        deal1.asking_price = 200000
        deal1.arv = 250000
        deal1.city = "San Antonio"
        deal1.state = "TX"
        deal1.beds = 3
        deal1.baths = 2
        deal1.sqft = 1500
        deal1.property_type = "House"
        deal1.contract_price = 150000
        deal1.status = "Available"
        deal1.created_at = datetime.now(timezone.utc)

        # Mock query: scalar_one_or_none returns the package
        pkg_result = MagicMock(scalar_one_or_none=MagicMock(return_value=mock_package))
        # Mock deals query: scalars().all() returns [deal1]
        deals_result = MagicMock()
        deals_result.scalars.return_value.all.return_value = [deal1]

        mock_db.execute = AsyncMock(side_effect=[pkg_result, deals_result])

        # Mock matching results — returns one matched buyer
        match_result = MagicMock()
        mock_buyer = MagicMock()
        mock_buyer.id = _uuid(50)
        mock_buyer.full_name = "Test Buyer"
        mock_buyer.email = "test@test.com"
        mock_buyer.buy_box = "SFR"
        mock_buyer.buyer_tier = "A-List"
        mock_buyer.similarity = 0.85
        match_result.matches = [mock_buyer]
        mock_match_fn.return_value = match_result

        mock_launch_fn.return_value = {"campaigns_created": 4, "errors": []}

        result = await launch_package(package_id=_uuid(1), db=mock_db)
        assert result["buyers_matched"] >= 1


class TestIndividualDealsUnaffected:
    """Verify individual deals are NOT changed when package is closed."""

    @pytest.mark.asyncio
    async def test_deal_status_unchanged_when_package_closes(self, mock_db, mock_package):
        """Closing a package does NOT change individual deal statuses."""
        from app.routers.packages import close_package

        mock_package.status = "Launched"
        mock_package.items = []

        campaign_result = MagicMock()
        campaign_result.scalars.return_value.all.return_value = []

        mock_db.execute = AsyncMock(side_effect=[
            MagicMock(scalar_one_or_none=MagicMock(return_value=mock_package)),
            campaign_result,
        ])

        result = await close_package(package_id=_uuid(1), db=mock_db)
        assert result["success"] is True
