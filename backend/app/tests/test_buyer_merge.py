"""Comprehensive tests for the buyer_merge service.

Covers:
- find_duplicate_buyer: name+company match, email match, partial match, no match
- merge_buy_boxes: AI merge success, AI failure fallback, empty inputs
- _append_fallback: all combinations
- add_email_to_buyer: new email, existing email, duplicate prevention
- get_all_buyer_emails: primary + additional, no buyer found
- find_buyer_by_any_email: primary match, additional match, no match
- merge_new_into_existing_buyer: full merge workflow
"""

import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from typing import Optional
from uuid import UUID

from app.models.models import Buyer, BuyerEmail
from app.services import buyer_merge as bm


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_buyer():
    buyer = MagicMock(spec=Buyer)
    buyer.id = uuid.uuid4()
    buyer.full_name = "John Smith"
    buyer.email = "john@example.com"
    buyer.affiliation = "ABC Realty"
    buyer.buy_box = "3-4 bed houses in Dallas under $250k"
    buyer.buy_box_embedding = None
    buyer.status = "Active"
    return buyer


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.execute = AsyncMock()
    db.get = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    return db


@pytest.fixture
def mock_buyer_email():
    be = MagicMock(spec=BuyerEmail)
    be.id = uuid.uuid4()
    be.buyer_id = uuid.uuid4()
    be.email = "additional@example.com"
    be.email_verified = False
    return be


# ===========================================================================
# find_duplicate_buyer tests
# ===========================================================================

class TestFindDuplicateBuyer:

    @pytest.mark.asyncio
    async def test_exact_name_company_match(self, mock_db, mock_buyer):
        """Same name + same company should return a match."""
        buyer_result = MagicMock()
        buyer_result.scalars.return_value.all.return_value = [mock_buyer]
        email_result = MagicMock()
        email_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(side_effect=[buyer_result, email_result])

        result, reason = await bm.find_duplicate_buyer(
            mock_db, "John Smith", "ABC Realty", "new@example.com"
        )
        assert result is mock_buyer
        assert reason == "name_company_match"

    @pytest.mark.asyncio
    async def test_exact_email_match(self, mock_db, mock_buyer):
        """Same email should return a match regardless of name (single DB call)."""
        email_result = MagicMock()
        email_result.scalar_one_or_none.return_value = mock_buyer
        mock_db.execute.return_value = email_result

        result, reason = await bm.find_duplicate_buyer(
            mock_db, "John Smith", "", "john@example.com"
        )
        assert result is mock_buyer
        assert reason == "exact_duplicate_email"

    @pytest.mark.asyncio
    async def test_case_insensitive_name_match(self, mock_db, mock_buyer):
        """Name matching should be case-insensitive."""
        mock_buyer.full_name = "john smith"
        buyer_result = MagicMock()
        buyer_result.scalars.return_value.all.return_value = [mock_buyer]
        email_result = MagicMock()
        email_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(side_effect=[buyer_result, email_result])

        result, reason = await bm.find_duplicate_buyer(
            mock_db, "John Smith", "ABC Realty", "new@example.com"
        )
        assert result is mock_buyer
        assert reason == "name_company_match"

    @pytest.mark.asyncio
    async def test_partial_name_match(self, mock_db, mock_buyer):
        """Name where one contains the other should match."""
        mock_buyer.full_name = "John Smith Jr."
        buyer_result = MagicMock()
        buyer_result.scalars.return_value.all.return_value = [mock_buyer]
        email_result = MagicMock()
        email_result.scalar_one_or_none.return_value = None
        # Third call when partial match not found: falls back to email lookup
        not_found_result = MagicMock()
        not_found_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(side_effect=[buyer_result, email_result, not_found_result])

        result, reason = await bm.find_duplicate_buyer(
            mock_db, "John Smith", "ABC Realty", "new@example.com"
        )
        assert result is mock_buyer
        assert reason == "name_company_match"

    @pytest.mark.asyncio
    async def test_no_affiliation_match_via_email(self, mock_db, mock_buyer):
        """Without affiliation, should fall back to email match (single DB call)."""
        email_result = MagicMock()
        email_result.scalar_one_or_none.return_value = mock_buyer
        mock_db.execute.return_value = email_result

        result, reason = await bm.find_duplicate_buyer(
            mock_db, "John Smith", "", "john@example.com"
        )
        assert result is mock_buyer
        assert reason == "exact_duplicate_email"

    @pytest.mark.asyncio
    async def test_no_match_found(self, mock_db):
        buyer_result = MagicMock()
        buyer_result.scalars.return_value.all.return_value = []
        email_result = MagicMock()
        email_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(side_effect=[buyer_result, email_result])

        result, reason = await bm.find_duplicate_buyer(
            mock_db, "Jane Doe", "XYZ Corp", "jane@example.com"
        )
        assert result is None
        assert reason == ""

    @pytest.mark.asyncio
    async def test_empty_name_returns_none(self, mock_db):
        result, reason = await bm.find_duplicate_buyer(
            mock_db, "", None, "test@example.com"
        )
        assert result is None
        assert reason == ""

    @pytest.mark.asyncio
    async def test_do_not_contact_skipped(self, mock_db, mock_buyer):
        mock_buyer.status = "Do Not Contact"
        buyer_result = MagicMock()
        buyer_result.scalars.return_value.all.return_value = []
        email_result = MagicMock()
        email_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(side_effect=[buyer_result, email_result])

        result, reason = await bm.find_duplicate_buyer(
            mock_db, "John Smith", "ABC Realty", "new@example.com"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_affiliation_partial_match(self, mock_db, mock_buyer):
        mock_buyer.affiliation = "ABC Realty Group LLC"
        buyer_result = MagicMock()
        buyer_result.scalars.return_value.all.return_value = [mock_buyer]
        email_result = MagicMock()
        email_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(side_effect=[buyer_result, email_result])

        result, reason = await bm.find_duplicate_buyer(
            mock_db, "John Smith", "ABC Realty", "new@example.com"
        )
        assert result is mock_buyer
        assert reason == "name_company_match"

    @pytest.mark.asyncio
    async def test_same_email_as_additional(self, mock_db, mock_buyer, mock_buyer_email):
        mock_buyer_email.buyer_id = mock_buyer.id
        mock_buyer_email.email = "new@example.com"
        buyer_result = MagicMock()
        buyer_result.scalars.return_value.all.return_value = [mock_buyer]
        email_result = MagicMock()
        email_result.scalar_one_or_none.return_value = mock_buyer_email
        mock_db.execute = AsyncMock(side_effect=[buyer_result, email_result])

        result, reason = await bm.find_duplicate_buyer(
            mock_db, "John Smith", "ABC Realty", "new@example.com"
        )
        assert result is mock_buyer
        assert reason == "exact_duplicate_email"


# ===========================================================================
# merge_buy_boxes tests
# ===========================================================================

class TestMergeBuyBoxes:

    @pytest.mark.asyncio
    async def test_empty_existing_returns_new(self):
        assert await bm.merge_buy_boxes("", "new criteria") == "new criteria"

    @pytest.mark.asyncio
    async def test_empty_new_returns_existing(self):
        assert await bm.merge_buy_boxes("existing criteria", "") == "existing criteria"

    @pytest.mark.asyncio
    async def test_both_empty_returns_empty(self):
        assert await bm.merge_buy_boxes("", "") == ""

    @pytest.mark.asyncio
    async def test_ai_successful_merge(self):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="Merged: houses in Dallas OR Arlington fixer-uppers"))]
        with patch.object(bm, "groq_chat_completion", AsyncMock(return_value=mock_response)):
            result = await bm.merge_buy_boxes("houses in Dallas", "fixer-uppers in Arlington")
        assert "Dallas" in result
        assert "Arlington" in result

    @pytest.mark.asyncio
    async def test_ai_failure_falls_back_to_append(self):
        with patch.object(bm, "groq_chat_completion", AsyncMock(side_effect=Exception("API error"))):
            result = await bm.merge_buy_boxes("Houses in Dallas", "Fixer-uppers in Arlington")
        assert "Dallas" in result
        assert "Arlington" in result
        assert "Additionally" in result

    @pytest.mark.asyncio
    async def test_ai_short_result_falls_back(self):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="Short"))]
        with patch.object(bm, "groq_chat_completion", AsyncMock(return_value=mock_response)):
            result = await bm.merge_buy_boxes("Very long existing criteria text that is very descriptive", "More new criteria to add")
        assert "Additionally" in result
        assert len(result) > 50

    @pytest.mark.asyncio
    async def test_json_code_fence_stripped(self):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="```\nMerged: Dallas and Arlington\n```"))]
        with patch.object(bm, "groq_chat_completion", AsyncMock(return_value=mock_response)):
            result = await bm.merge_buy_boxes("Dallas", "Arlington")
        assert "```" not in result
        assert "Dallas" in result and "Arlington" in result


# ===========================================================================
# _append_fallback tests (sync)
# ===========================================================================

class TestAppendFallback:

    def test_both_existing(self):
        result = bm._append_fallback("Houses in Dallas", "Fixer-uppers in Arlington")
        assert result == "Houses in Dallas. Additionally, fixer-uppers in Arlington"

    def test_empty_existing(self):
        assert bm._append_fallback("", "New text") == "New text"

    def test_empty_new(self):
        assert bm._append_fallback("Existing", "") == "Existing"

    def test_both_empty(self):
        assert bm._append_fallback("", "") == ""

    def test_existing_trail_stripped(self):
        result = bm._append_fallback("Houses in Dallas.", "Fixer-uppers")
        assert result == "Houses in Dallas. Additionally, fixer-uppers"


# ===========================================================================
# add_email_to_buyer tests
# ===========================================================================

class TestAddEmailToBuyer:

    @pytest.mark.asyncio
    async def test_add_new_email(self, mock_db, mock_buyer):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        result = await bm.add_email_to_buyer(mock_db, mock_buyer, "new@example.com")

        assert mock_db.add.called
        added = mock_db.add.call_args[0][0]
        assert isinstance(added, BuyerEmail)
        assert added.email == "new@example.com"
        assert added.buyer_id == mock_buyer.id

    @pytest.mark.asyncio
    async def test_existing_email_returns_existing(self, mock_db, mock_buyer, mock_buyer_email):
        mock_buyer_email.buyer_id = mock_buyer.id
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_buyer_email
        mock_db.execute.return_value = mock_result

        result = await bm.add_email_to_buyer(mock_db, mock_buyer, "additional@example.com")
        assert result is mock_buyer_email
        mock_db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_case_insensitive_dedup(self, mock_db, mock_buyer, mock_buyer_email):
        mock_buyer_email.buyer_id = mock_buyer.id
        mock_buyer_email.email = "ADDITIONAL@EXAMPLE.COM"
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_buyer_email
        mock_db.execute.return_value = mock_result

        result = await bm.add_email_to_buyer(mock_db, mock_buyer, "additional@example.com")
        assert result is mock_buyer_email
        mock_db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_email_stripped_and_lowered(self, mock_db, mock_buyer):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        await bm.add_email_to_buyer(mock_db, mock_buyer, "  New@Example.COM  ")
        added = mock_db.add.call_args[0][0]
        assert added.email == "new@example.com"


# ===========================================================================
# get_all_buyer_emails tests
# ===========================================================================

class TestGetAllBuyerEmails:

    @pytest.mark.asyncio
    async def test_primary_only(self, mock_db, mock_buyer):
        mock_db.get.return_value = mock_buyer
        exec_result = MagicMock()
        exec_result.scalars.return_value.all.return_value = []
        mock_db.execute.return_value = exec_result

        emails = await bm.get_all_buyer_emails(mock_db, mock_buyer.id)
        assert emails == ["john@example.com"]

    @pytest.mark.asyncio
    async def test_with_additional(self, mock_db, mock_buyer):
        mock_db.get.return_value = mock_buyer
        exec_result = MagicMock()
        exec_result.scalars.return_value.all.return_value = ["alt1@test.com", "alt2@test.com"]
        mock_db.execute.return_value = exec_result

        emails = await bm.get_all_buyer_emails(mock_db, mock_buyer.id)
        assert emails == ["john@example.com", "alt1@test.com", "alt2@test.com"]

    @pytest.mark.asyncio
    async def test_duplicate_additional_skipped(self, mock_db, mock_buyer):
        mock_db.get.return_value = mock_buyer
        exec_result = MagicMock()
        exec_result.scalars.return_value.all.return_value = ["john@example.com"]
        mock_db.execute.return_value = exec_result

        emails = await bm.get_all_buyer_emails(mock_db, mock_buyer.id)
        assert emails == ["john@example.com"]

    @pytest.mark.asyncio
    async def test_buyer_not_found(self, mock_db):
        mock_db.get.return_value = None
        emails = await bm.get_all_buyer_emails(mock_db, uuid.uuid4())
        assert emails == []


# ===========================================================================
# find_buyer_by_any_email tests
# ===========================================================================

class TestFindBuyerByAnyEmail:

    @pytest.mark.asyncio
    async def test_find_by_primary_email(self, mock_db, mock_buyer):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_buyer
        mock_db.execute.return_value = mock_result

        result = await bm.find_buyer_by_any_email(mock_db, "john@example.com")
        assert result is mock_buyer

    @pytest.mark.asyncio
    async def test_find_by_additional_email(self, mock_db, mock_buyer, mock_buyer_email):
        primary_result = MagicMock()
        primary_result.scalar_one_or_none.return_value = None
        be_result = MagicMock()
        be_result.scalar_one_or_none.return_value = mock_buyer_email
        mock_buyer_email.buyer_id = mock_buyer.id
        mock_db.get.return_value = mock_buyer
        mock_db.execute = AsyncMock(side_effect=[primary_result, be_result])

        result = await bm.find_buyer_by_any_email(mock_db, "additional@example.com")
        assert result is mock_buyer

    @pytest.mark.asyncio
    async def test_no_match_returns_none(self, mock_db):
        primary_result = MagicMock()
        primary_result.scalar_one_or_none.return_value = None
        be_result = MagicMock()
        be_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(side_effect=[primary_result, be_result])

        result = await bm.find_buyer_by_any_email(mock_db, "unknown@example.com")
        assert result is None

    @pytest.mark.asyncio
    async def test_case_insensitive_search(self, mock_db, mock_buyer):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_buyer
        mock_db.execute.return_value = mock_result

        result = await bm.find_buyer_by_any_email(mock_db, "JOHN@EXAMPLE.COM")
        assert result is mock_buyer


# ===========================================================================
# merge_new_into_existing_buyer tests
# ===========================================================================

class TestMergeNewIntoExistingBuyer:

    @pytest.mark.asyncio
    async def test_merge_buy_box_only(self, mock_db, mock_buyer):
        with patch.object(bm, "merge_buy_boxes", AsyncMock(return_value="Merged buy box")):
            with patch.object(bm, "generate_embedding", AsyncMock(return_value=[0.1, 0.2])):
                with patch.object(bm.audit, "log_buyer_updated", AsyncMock()):
                    changes = await bm.merge_new_into_existing_buyer(
                        mock_db, mock_buyer, "New criteria", new_email=None
                    )
        assert "buy_box" in changes
        assert mock_buyer.buy_box == "Merged buy box"
        assert "additional_email_added" not in changes

    @pytest.mark.asyncio
    async def test_merge_with_new_email(self, mock_db, mock_buyer):
        with patch.object(bm, "merge_buy_boxes", AsyncMock(return_value="Merged")):
            with patch.object(bm, "generate_embedding", AsyncMock(return_value=[0.1, 0.2])):
                with patch.object(bm, "add_email_to_buyer", AsyncMock(return_value=MagicMock())):
                    with patch.object(bm.audit, "log_buyer_updated", AsyncMock()):
                        changes = await bm.merge_new_into_existing_buyer(
                            mock_db, mock_buyer, "New criteria", new_email="alt@example.com"
                        )
        assert "additional_email_added" in changes
        assert "buy_box" in changes
        assert "embedding_regenerated" in changes

    @pytest.mark.asyncio
    async def test_skip_email_if_same_as_primary(self, mock_db, mock_buyer):
        with patch.object(bm, "merge_buy_boxes", AsyncMock(return_value=mock_buyer.buy_box)):
            with patch.object(bm.audit, "log_buyer_updated", AsyncMock()):
                changes = await bm.merge_new_into_existing_buyer(
                    mock_db, mock_buyer, mock_buyer.buy_box, new_email="john@example.com"
                )
        assert "additional_email_added" not in changes

    @pytest.mark.asyncio
    async def test_skip_merge_if_same_buy_box(self, mock_db, mock_buyer):
        with patch.object(bm.audit, "log_buyer_updated", AsyncMock()):
            changes = await bm.merge_new_into_existing_buyer(
                mock_db, mock_buyer, mock_buyer.buy_box, new_email=None
            )
        assert "buy_box" not in changes

    @pytest.mark.asyncio
    async def test_embedding_failure_non_fatal(self, mock_db, mock_buyer):
        with patch.object(bm, "merge_buy_boxes", AsyncMock(return_value="New merged")):
            with patch.object(bm, "generate_embedding", AsyncMock(side_effect=Exception("API down"))):
                with patch.object(bm.audit, "log_buyer_updated", AsyncMock()):
                    changes = await bm.merge_new_into_existing_buyer(
                        mock_db, mock_buyer, "New criteria"
                    )
        assert "buy_box" in changes
        assert "embedding_regenerated" not in changes

    @pytest.mark.asyncio
    async def test_audit_failure_non_fatal(self, mock_db, mock_buyer):
        with patch.object(bm, "merge_buy_boxes", AsyncMock(return_value="Merged")):
            with patch.object(bm, "generate_embedding", AsyncMock(return_value=[0.1])):
                with patch.object(bm.audit, "log_buyer_updated", AsyncMock(side_effect=Exception("Audit error"))):
                    changes = await bm.merge_new_into_existing_buyer(
                        mock_db, mock_buyer, "New criteria", new_email="alt@test.com"
                    )
        assert "buy_box" in changes
        assert "additional_email_added" in changes
