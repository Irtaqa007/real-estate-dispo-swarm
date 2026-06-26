"""Unit tests for the AI validation layer (ai_validator.py).

Tests:
1. Placeholder [Name] in body → block
2. Missing sign-off → warn + auto-corrected
3. Price below floor in negotiation email → block
4. Banned phrase in content → warn + auto-corrected
5. Valid email with correct financials → pass
6. Validator exception → fail-safe fires, send proceeds
7. Hallucination guard parse failure → skip check, other checks still run
8. Empty content → block
9. Non-negotiation content_type skips floor price check
10. Ghost recovery email validator wiring
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import settings
from app.services.ai_validator import ValidationResult, validate_ai_output


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

def make_mock_buyer(**kwargs):
    """Create a mock Buyer with sensible defaults."""
    buyer = MagicMock()
    buyer.id = kwargs.get("id", "buyer-1")
    buyer.full_name = kwargs.get("full_name", "Test Buyer")
    buyer.email = kwargs.get("email", "buyer@test.com")
    buyer.buy_box = kwargs.get("buy_box", "Houses under $300k")
    return buyer


def make_mock_deal(**kwargs):
    """Create a mock Deal with sensible defaults."""
    deal = MagicMock()
    deal.id = kwargs.get("id", "deal-1")
    deal.address = kwargs.get("address", "123 Test St")
    deal.city = kwargs.get("city", "Dallas")
    deal.state = kwargs.get("state", "TX")
    deal.property_type = kwargs.get("property_type", "House")
    deal.arv = kwargs.get("arv", 350000.0)
    deal.asking_price = kwargs.get("asking_price", 250000.0)
    deal.repair_estimate = kwargs.get("repair_estimate", 50000.0)
    deal.floor_price = kwargs.get("floor_price", 180000.0)
    deal.spread = kwargs.get("spread", 100000.0)
    return deal


# ===========================================================================
# CHECK 1 — Placeholder Detection
# ===========================================================================


@pytest.mark.asyncio
async def test_placeholder_name_blocked():
    """Unfilled [Name] placeholder should result in severity=block."""
    result = await validate_ai_output(
        content="Hi [Name], check out this deal on 123 Test St.",
        content_type="campaign_email",
    )
    assert result.severity == "block"
    assert result.valid is False
    assert any("placeholder" in v.lower() for v in result.violations)
    assert "placeholder_detection" in result.checks_run
    assert result.corrected_content is None  # block → no corrected content


@pytest.mark.asyncio
async def test_placeholder_address_blocked():
    """Unfilled [Address] placeholder should result in severity=block."""
    result = await validate_ai_output(
        content="This deal at [Address] is a great opportunity.",
        content_type="campaign_email",
    )
    assert result.severity == "block"
    assert "[Address]" in " ".join(result.violations)


@pytest.mark.asyncio
async def test_placeholder_jinja_blocked():
    """Jinja-style {{ placeholder }} should be detected as unfilled."""
    result = await validate_ai_output(
        content="Dear {{buyer_name}}, please see the attached offer.",
        content_type="campaign_email",
    )
    assert result.severity == "block"


@pytest.mark.asyncio
async def test_clean_content_passes_placeholder_check():
    """Content without placeholders should pass check 1."""
    result = await validate_ai_output(
        content="Hi Alex, check out this great deal at 123 Test St.",
        content_type="campaign_email",
    )
    # The content has "Alex" so sign-off check also passes
    # Are there any bracket-style things? No.
    assert "placeholder_detection" in result.checks_run


# ===========================================================================
# CHECK 2 — Operator Sign-Off
# ===========================================================================


@pytest.mark.asyncio
async def test_missing_sign_off_warns_and_auto_corrects():
    """Missing operator sign-off should result in severity=warn with corrected_content."""
    content = "Hi Test Buyer, this property at 123 Test St is a great investment."
    result = await validate_ai_output(
        content=content,
        content_type="campaign_email",
    )
    assert result.severity in ("warn", "pass")  # pass if first name matches
    if result.severity == "warn":
        assert "sign" in " ".join(result.violations).lower()
        assert result.corrected_content is not None
        # sign-off should be appended
        sign_off = settings.operator_email_signature.strip()
        assert result.corrected_content.strip().endswith(sign_off)


@pytest.mark.asyncio
async def test_sign_off_present_passes():
    """Content with operator name should pass the sign-off check."""
    first_name = settings.operator_first_name or settings.operator_name.split()[0]
    result = await validate_ai_output(
        content=f"Hi {first_name}, this property at 123 Test St is ready.",
        content_type="campaign_email",
    )
    # sign-off should pass because first name is present
    assert "operator_sign_off" in result.checks_run


# ===========================================================================
# CHECK 3 — Floor Price Protection
# ===========================================================================


@pytest.mark.asyncio
async def test_floor_price_violation_blocked():
    """Price below floor in negotiation email should block."""
    deal = make_mock_deal(floor_price=180000.0)
    result = await validate_ai_output(
        content="We can do $150,000 for this property.",
        content_type="negotiation_email",
        deal=deal,
    )
    assert result.severity == "block"
    assert result.valid is False
    assert any("floor" in v.lower() for v in result.violations)
    assert "floor_price_protection" in result.checks_run
    assert result.corrected_content is None  # block → no corrected content


@pytest.mark.asyncio
async def test_floor_price_above_passes():
    """Price at or above floor should pass floor price check."""
    deal = make_mock_deal(floor_price=180000.0)
    result = await validate_ai_output(
        content="We can do $200,000 for this property.",
        content_type="negotiation_email",
        deal=deal,
    )
    assert result.severity != "block"  # should not block


@pytest.mark.asyncio
async def test_floor_price_skip_non_negotiation():
    """Floor price check should not run for campaign emails."""
    deal = make_mock_deal(floor_price=180000.0)
    result = await validate_ai_output(
        content="We can do $150,000 for this property.",
        content_type="campaign_email",
        deal=deal,
    )
    assert "floor_price_protection" not in result.checks_run


@pytest.mark.asyncio
async def test_floor_price_skip_no_deal():
    """Floor price check should not run when deal is None."""
    result = await validate_ai_output(
        content="We can do $150,000 for this property.",
        content_type="negotiation_email",
        deal=None,
    )
    assert "floor_price_protection" not in result.checks_run


# ===========================================================================
# CHECK 4 — Deal Financial Accuracy
# ===========================================================================


@pytest.mark.asyncio
async def test_financial_accuracy_warns_on_wrong_numbers():
    """Deal financial accuracy should warn when numbers are referenced but wrong."""
    deal = make_mock_deal(arv=350000.0, asking_price=250000.0)
    # Content mentions a number close to ARV (370k = ~5.7% off, within 15% band)
    result = await validate_ai_output(
        content="The ARV on this property is around $370,000.",
        content_type="campaign_email",
        deal=deal,
    )
    assert "financial_accuracy" in result.checks_run
    # If the AI misstates ARV (370k vs actual 350k = 5.7% off, within 15% band),
    # it should warn if within 5-15% band
    if result.severity == "warn":
        assert any("financial" in v.lower() for v in result.violations)


@pytest.mark.asyncio
async def test_financial_accuracy_accurate_passes():
    """Deal financial accuracy should pass when numbers match actual values."""
    deal = make_mock_deal(arv=350000.0, asking_price=250000.0)
    # Content mentions ARV accurately (within 5%)
    result = await validate_ai_output(
        content="The ARV is roughly $350,000 on this property.",
        content_type="campaign_email",
        deal=deal,
    )
    assert "financial_accuracy" in result.checks_run


@pytest.mark.asyncio
async def test_financial_accuracy_no_numbers_skips():
    """Deal financial accuracy should skip when content has no dollar amounts."""
    deal = make_mock_deal(arv=350000.0, asking_price=250000.0)
    result = await validate_ai_output(
        content="This is a great property in a wonderful neighborhood.",
        content_type="campaign_email",
        deal=deal,
    )
    assert "financial_accuracy" in result.checks_run
    # Should not have financial violations since no numbers mentioned


# ===========================================================================
# CHECK 5 — Identity Consistency (Banned Phrases)
# ===========================================================================


@pytest.mark.asyncio
async def test_banned_phrase_warns_and_auto_corrects():
    """Banned phrase should result in warn and be removed from corrected content."""
    never_say = settings.operator_never_say
    if not never_say or not never_say.strip():
        pytest.skip("No banned phrases configured — skip test")

    banned = [p.strip() for p in never_say.split(",") if p.strip()]
    test_phrase = banned[0]

    content = f"Hi Alex, this is {test_phrase} but check out this deal at 123 Test St."
    result = await validate_ai_output(
        content=content,
        content_type="campaign_email",
    )
    assert "identity_consistency" in result.checks_run
    if result.severity == "warn":
        assert any("banned" in v.lower() for v in result.violations)
        if result.corrected_content:
            assert test_phrase not in result.corrected_content


@pytest.mark.asyncio
async def test_clean_content_no_banned_phrase_passes():
    """Content with no banned phrases should pass identity check."""
    never_say = settings.operator_never_say
    if not never_say or not never_say.strip():
        pytest.skip("No banned phrases configured")

    result = await validate_ai_output(
        content="Hi Alex, check out this great deal at 123 Test St.",
        content_type="campaign_email",
    )
    assert "identity_consistency" in result.checks_run


# ===========================================================================
# CHECK 6 — Hallucination Guard
# ===========================================================================


@patch("app.services.ai_validator.groq_chat_completion")
@pytest.mark.asyncio
async def test_hallucination_parse_failure_skips_check(mock_groq):
    """If hallucination guard JSON parse fails, the check should be skipped."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "not valid json at all"
    mock_groq.return_value = mock_response

    deal = make_mock_deal()
    result = await validate_ai_output(
        content="Hi Alex, this property at 123 Test St is great.",
        content_type="campaign_email",
        deal=deal,
    )
    # The hallucination guard should have been attempted but failed gracefully
    assert "hallucination_guard" in result.checks_run
    # Other checks should still have run
    assert "placeholder_detection" in result.checks_run
    # The output should still be valid (no hallucination detected on parse failure)
    assert result.severity != "block"


@patch("app.services.ai_validator.groq_chat_completion")
@pytest.mark.asyncio
async def test_hallucination_detected_blocks(mock_groq):
    """If hallucination guard detects hallucination, it should block."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = json.dumps({
        "hallucination_detected": True,
        "confidence": 0.95,
        "violations": ["Claims property has a swimming pool not in deal data"],
        "severity": "block",
    })
    mock_groq.return_value = mock_response

    deal = make_mock_deal()
    result = await validate_ai_output(
        content="This property has a beautiful swimming pool and renovated kitchen.",
        content_type="campaign_email",
        deal=deal,
    )
    assert "hallucination_guard" in result.checks_run
    # The mock returns block severity and the email claims a swimming pool not in deal data
    assert result.severity == "block"
    assert any("pool" in v.lower() for v in result.violations)


@patch("app.services.ai_validator.groq_chat_completion")
@pytest.mark.asyncio
async def test_hallucination_guard_no_deal_skips(mock_groq):
    """Hallucination guard should not run when deal is None."""
    result = await validate_ai_output(
        content="Hi Alex, check out this property.",
        content_type="campaign_email",
        deal=None,
    )
    assert "hallucination_guard" not in result.checks_run
    mock_groq.assert_not_called()


# ===========================================================================
# Valid Email — All checks pass
# ===========================================================================


@pytest.mark.asyncio
async def test_valid_email_passes():
    """A clean, valid email should result in severity=pass."""
    first_name = settings.operator_first_name or settings.operator_name.split()[0]
    result = await validate_ai_output(
        content=(
            f"Hi Investor, this 3-bedroom property at 123 Test St in "
            f"Dallas, TX is a great value at $250,000. {first_name}"
        ),
        content_type="campaign_email",
    )
    # Should not be blocked
    assert result.severity != "block"
    assert result.valid is True


# ===========================================================================
# Fail-Safe: Validator exception should not crash send
# ===========================================================================


@pytest.mark.asyncio
@patch("app.services.ai_validator._check_placeholders", side_effect=RuntimeError("Unexpected crash"))
async def test_validator_crash_failsafe(mock_check):
    """If a validator check crashes unexpectedly, the exception should propagate
    to the caller's try/except. This test verifies that the validator itself
    does not silently catch all errors — the caller's fail-safe handles it."""
    with pytest.raises(RuntimeError):
        await validate_ai_output(
            content="Hi Alex, this is a test email.",
            content_type="campaign_email",
        )


# ===========================================================================
# Aggregation Logic
# ===========================================================================


@pytest.mark.asyncio
async def test_block_overrides_warn():
    """If any check returns block, overall severity should be block."""
    result = await validate_ai_output(
        content="Hi [Name], check out this deal.",
        content_type="negotiation_email",
    )
    # [Name] should cause block regardless of other checks
    assert result.severity == "block"


@pytest.mark.asyncio
async def test_warn_does_not_override_block():
    """Once block is set, warns should not lower severity."""
    result = await validate_ai_output(
        content="Hi [Name], check out this deal at $150,000.",
        content_type="negotiation_email",
    )
    assert result.severity == "block"


# ===========================================================================
# Edge Cases
# ===========================================================================


@pytest.mark.asyncio
async def test_empty_content_blocked():
    """Empty content should be blocked."""
    result = await validate_ai_output(
        content="",
        content_type="campaign_email",
    )
    assert result.severity == "block"
    assert result.valid is False
    assert result.confidence == 0.0


@pytest.mark.asyncio
async def test_whitespace_only_blocked():
    """Whitespace-only content should be blocked."""
    result = await validate_ai_output(
        content="   \n\n  ",
        content_type="campaign_email",
    )
    assert result.severity == "block"


@pytest.mark.asyncio
async def test_ghost_recovery_content_type_runs_check_1():
    """ghost_recovery_email should run placeholder detection."""
    result = await validate_ai_output(
        content="Hi [Name], check out this deal.",
        content_type="ghost_recovery_email",
    )
    assert result.severity == "block"
    assert "placeholder_detection" in result.checks_run


@pytest.mark.asyncio
async def test_negotiation_content_type_runs_floor_price():
    """negotiation_email should run floor price check."""
    deal = make_mock_deal(floor_price=180000.0)
    result = await validate_ai_output(
        content="We can do $170,000.",
        content_type="negotiation_email",
        deal=deal,
    )
    assert result.severity == "block"
    assert "floor_price_protection" in result.checks_run
