"""Comprehensive tests for the email verification → embedding chain.

Covers:
- _verify_email_background: triggers embedding on valid, skips on invalid/catch_all
- _generate_buyer_embedding_background: generates and saves embedding
- Both handle exceptions gracefully
- Edge cases: buyer deleted before verification, no buy_box
"""

import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

from app.models.schemas import Buyer
from app.routers import buyers as buyers_router


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def buyer_id():
    return uuid.uuid4()


@pytest.fixture
def mock_buyer(buyer_id):
    buyer = MagicMock(spec=Buyer)
    buyer.id = buyer_id
    buyer.full_name = "John Smith"
    buyer.email = "john@example.com"
    buyer.buy_box = "3-4 bed houses in Dallas under $250k"
    buyer.buy_box_embedding = None
    buyer.email_verified = False
    buyer.email_verification_status = None
    return buyer


# ===========================================================================
# _verify_email_background tests
# ===========================================================================

class TestVerifyEmailBackground:
    """Test the verification → embedding chain."""

    @pytest.mark.asyncio
    async def test_valid_triggers_embedding(self, buyer_id, mock_buyer):
        """When email is valid, embedding should be triggered."""
        with patch.object(buyers_router, "verify_email", AsyncMock(return_value={
            "email": "test@example.com", "result": "valid", "score": 85, "details": {}
        })):
            with patch("app.database.async_session_factory") as mock_factory:
                mock_session = AsyncMock()
                mock_session.get = AsyncMock(return_value=mock_buyer)
                mock_factory.return_value.__aenter__.return_value = mock_session

                with patch.object(buyers_router, "_generate_buyer_embedding_background", AsyncMock()) as mock_embed:
                    await buyers_router._verify_email_background(buyer_id, "test@example.com")

        assert mock_buyer.email_verified is True
        assert mock_buyer.email_verification_status == "valid"
        mock_embed.assert_awaited_once_with(buyer_id, "3-4 bed houses in Dallas under $250k")

    @pytest.mark.asyncio
    async def test_invalid_skips_embedding(self, buyer_id, mock_buyer):
        """When email is invalid, embedding should NOT be triggered."""
        with patch.object(buyers_router, "verify_email", AsyncMock(return_value={
            "email": "bad@example.com", "result": "invalid", "score": 0, "details": {}
        })):
            with patch("app.database.async_session_factory") as mock_factory:
                mock_session = AsyncMock()
                mock_session.get = AsyncMock(return_value=mock_buyer)
                mock_factory.return_value.__aenter__.return_value = mock_session

                with patch.object(buyers_router, "_generate_buyer_embedding_background", AsyncMock()) as mock_embed:
                    await buyers_router._verify_email_background(buyer_id, "bad@example.com")

        assert mock_buyer.email_verified is False
        assert mock_buyer.email_verification_status == "invalid"
        mock_embed.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_catch_all_skips_embedding(self, buyer_id, mock_buyer):
        """Catch-all verification should NOT trigger embedding."""
        with patch.object(buyers_router, "verify_email", AsyncMock(return_value={
            "email": "catch@example.com", "result": "catch_all", "score": 55, "details": {}
        })):
            with patch("app.database.async_session_factory") as mock_factory:
                mock_session = AsyncMock()
                mock_session.get = AsyncMock(return_value=mock_buyer)
                mock_factory.return_value.__aenter__.return_value = mock_session

                with patch.object(buyers_router, "_generate_buyer_embedding_background", AsyncMock()) as mock_embed:
                    await buyers_router._verify_email_background(buyer_id, "catch@example.com")

        assert mock_buyer.email_verified is False
        mock_embed.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_buy_box_skips_embedding(self, buyer_id):
        """Verified email but no buy box should NOT trigger embedding."""
        no_box_buyer = MagicMock(spec=Buyer)
        no_box_buyer.id = buyer_id
        no_box_buyer.buy_box = None
        no_box_buyer.email = "test@example.com"

        with patch.object(buyers_router, "verify_email", AsyncMock(return_value={
            "email": "test@example.com", "result": "valid", "score": 85, "details": {}
        })):
            with patch("app.database.async_session_factory") as mock_factory:
                mock_session = AsyncMock()
                mock_session.get = AsyncMock(return_value=no_box_buyer)
                mock_factory.return_value.__aenter__.return_value = mock_session

                with patch.object(buyers_router, "_generate_buyer_embedding_background", AsyncMock()) as mock_embed:
                    await buyers_router._verify_email_background(buyer_id, "test@example.com")

        mock_embed.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_buyer_not_found_skips(self, buyer_id):
        with patch.object(buyers_router, "verify_email", AsyncMock(return_value={
            "email": "test@example.com", "result": "valid", "score": 85, "details": {}
        })):
            with patch("app.database.async_session_factory") as mock_factory:
                mock_session = AsyncMock()
                mock_session.get = AsyncMock(return_value=None)
                mock_factory.return_value.__aenter__.return_value = mock_session

                with patch.object(buyers_router, "_generate_buyer_embedding_background", AsyncMock()) as mock_embed:
                    await buyers_router._verify_email_background(buyer_id, "test@example.com")

        mock_embed.assert_not_awaited()
        mock_session.add.assert_not_called()
        mock_session.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_verification_exception_handled(self, buyer_id):
        with patch.object(buyers_router, "verify_email", AsyncMock(side_effect=Exception("Network error"))):
            await buyers_router._verify_email_background(buyer_id, "test@example.com")
            # Should not raise

    @pytest.mark.asyncio
    async def test_embedding_exception_handled(self, buyer_id, mock_buyer):
        with patch.object(buyers_router, "verify_email", AsyncMock(return_value={
            "email": "test@example.com", "result": "valid", "score": 85, "details": {}
        })):
            with patch("app.database.async_session_factory") as mock_factory:
                mock_session = AsyncMock()
                mock_session.get = AsyncMock(return_value=mock_buyer)
                mock_factory.return_value.__aenter__.return_value = mock_session

                with patch.object(buyers_router, "_generate_buyer_embedding_background", AsyncMock(side_effect=Exception("Embed error"))):
                    await buyers_router._verify_email_background(buyer_id, "test@example.com")
                    # Should not raise

        assert mock_buyer.email_verified is True


# ===========================================================================
# _generate_buyer_embedding_background tests
# ===========================================================================

class TestGenerateBuyerEmbeddingBackground:

    @pytest.mark.asyncio
    async def test_generates_and_saves(self, buyer_id):
        mock_buyer = MagicMock(spec=Buyer)
        mock_buyer.id = buyer_id
        mock_buyer.buy_box_embedding = None
        expected_embedding = [0.1, 0.2, 0.3, 0.4]

        with patch("app.database.async_session_factory") as mock_factory:
            mock_session = AsyncMock()
            mock_session.get = AsyncMock(return_value=mock_buyer)
            mock_factory.return_value.__aenter__.return_value = mock_session

            with patch.object(buyers_router, "generate_embedding", AsyncMock(return_value=expected_embedding)):
                await buyers_router._generate_buyer_embedding_background(buyer_id, "Buy box text")

        assert mock_buyer.buy_box_embedding == expected_embedding
        mock_session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_buyer_not_found_skips_save(self, buyer_id):
        """When buyer not found, embedding is generated but not saved to DB."""
        mock_embedding = [0.1, 0.2, 0.3]

        with patch.object(buyers_router, "generate_embedding", AsyncMock(return_value=mock_embedding)) as mock_embed:
            with patch("app.database.async_session_factory") as mock_factory:
                mock_session = AsyncMock()
                mock_session.get = AsyncMock(return_value=None)
                mock_factory.return_value.__aenter__.return_value = mock_session

                await buyers_router._generate_buyer_embedding_background(buyer_id, "Buy box text")

        # Embedding IS generated (it runs before DB lookup), but NOT saved to DB
        mock_embed.assert_awaited_once_with("Buy box text", input_type="search_query")
        mock_session.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_api_exception_handled(self, buyer_id):
        mock_buyer = MagicMock(spec=Buyer)
        mock_buyer.id = buyer_id

        with patch("app.database.async_session_factory") as mock_factory:
            mock_session = AsyncMock()
            mock_session.get = AsyncMock(return_value=mock_buyer)
            mock_factory.return_value.__aenter__.return_value = mock_session

            with patch.object(buyers_router, "generate_embedding", AsyncMock(side_effect=Exception("Cohere down"))):
                await buyers_router._generate_buyer_embedding_background(buyer_id, "Buy box text")
                # Should not raise


# ===========================================================================
# create_buyer — verification queuing tests
# ===========================================================================

class TestCreateBuyerVerificationQueue:

    @pytest.mark.asyncio
    async def test_queues_verification_not_embedding_directly(self):
        """create_buyer should queue verification but NOT embedding directly."""
        mock_request_data = MagicMock()
        mock_request_data.full_name = "Test User"
        mock_request_data.email = "test@example.com"
        mock_request_data.affiliation = "Test Co"
        mock_request_data.buy_box = "Houses under $300k"
        mock_request_data.buyer_tier = "C-List"
        mock_request_data.status = "Active"
        mock_request_data.notes = None

        mock_background_tasks = MagicMock()
        mock_db = AsyncMock()

        with patch.object(buyers_router, "find_duplicate_buyer", AsyncMock(return_value=(None, ""))):
            with patch.object(buyers_router, "Buyer") as MockBuyer:
                MockBuyer.return_value = MagicMock()
                mock_db.commit = AsyncMock()
                mock_db.refresh = AsyncMock()

                await buyers_router.create_buyer(
                    mock_request_data, mock_background_tasks, mock_db
                )

        assert mock_background_tasks.add_task.call_count == 1
        task_fn = mock_background_tasks.add_task.call_args[0][0]
        assert task_fn == buyers_router._verify_email_background
