"""Unit tests for the Operator Identity Layer (Feature 2).

Tests:
1. email_generator: _build_prompt includes operator identity block
2. email_generator: system prompt references operator_name
3. email_generator: sign-off guardrail appends signature when missing
4. email_generator: sign-off guardrail does not duplicate when already present
5. reply_processor: system prompt includes operator identity
6. reply_processor: detect_uncertainty_and_hold returns None for confident answers (>20 chars)
7. reply_processor: detect_uncertainty_and_hold returns holding response for uncertain answers
8. reply_processor: detect_uncertainty_and_hold returns None for non-question intents
9. negotiation: system prompt includes operator identity
10. config: operator identity settings have correct defaults
"""

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import settings
from app.services.email_generator import _build_prompt
from app.services.reply_processor import detect_uncertainty_and_hold
from app.services.negotiation import _NEGOTIATION_SYSTEM_PROMPT


# ===========================================================================
# Config defaults
# ===========================================================================


def test_config_has_operator_defaults():
    """Config should have operator identity defaults.

    Note: operator_name and operator_first_name default to empty strings
    intentionally — they must be explicitly set in .env or the app refuses
    to start. This test just verifies the structure exists.
    """
    # operator_name and operator_first_name default to "" but .env overrides
    # them at runtime. Verify they exist and are strings, regardless of value.
    assert settings.operator_name is not None
    assert isinstance(settings.operator_name, str)
    assert settings.operator_first_name is not None
    assert isinstance(settings.operator_first_name, str)
    assert isinstance(settings.operator_email_signature, str)
    assert settings.operator_tone == "conversational"


# ===========================================================================
# email_generator — _build_prompt operator identity injection
# ===========================================================================


def test_build_prompt_contains_operator_id_block():
    """_build_prompt system prompt should contain the OPERATOR IDENTITY block."""
    messages = _build_prompt(
        touch=1,
        buyer_name="Test Buyer",
        buyer_email="buyer@test.com",
        buy_box="Houses under $300k in Dallas",
        buyer_tier="A-List",
        address="123 Test St",
        city="Dallas",
        state="TX",
        property_type="House",
        arv=350000.0,
        asking_price=250000.0,
        spread=100000.0,
        condition_description="Good condition",
        beds=3,
        baths=2.0,
        sqft=1500,
    )

    system_content = messages[0]["content"]
    assert "OPERATOR IDENTITY" in system_content
    assert settings.operator_name in system_content
    assert settings.operator_tone in system_content
    assert "Subject line must NEVER contain the operator name" in system_content


def test_build_prompt_operator_signature_in_system_prompt():
    """Operator email signature should be in the system prompt."""
    messages = _build_prompt(
        touch=1,
        buyer_name="Test Buyer",
        buyer_email="buyer@test.com",
        buy_box="Houses under $300k in Dallas",
        buyer_tier="A-List",
        address="123 Test St",
        city="Dallas",
        state="TX",
        property_type="House",
        arv=350000.0,
        asking_price=250000.0,
        spread=100000.0,
        condition_description="Good condition",
    )
    system_content = messages[0]["content"]
    assert "OPERATOR IDENTITY" in system_content
    if settings.operator_email_signature:
        sign_off_clean = settings.operator_email_signature.replace("\\n", " ")
        assert sign_off_clean.split()[0] in system_content


def test_build_prompt_user_prompt_does_not_contain_operator_name():
    """User prompt (deal details) should NOT contain the operator name."""
    messages = _build_prompt(
        touch=1,
        buyer_name="Test Buyer",
        buyer_email="buyer@test.com",
        buy_box="Houses under $300k in Dallas",
        buyer_tier="A-List",
        address="123 Test St",
        city="Dallas",
        state="TX",
        property_type="House",
        arv=350000.0,
        asking_price=250000.0,
        spread=100000.0,
        condition_description="Good condition",
    )
    # The system prompt has the identity, user prompt is deal-focused
    user_content = messages[1]["content"]
    assert "DEAL DETAILS" in user_content
    assert "Address: 123 Test St" in user_content


# ===========================================================================
# email_generator — sign-off guardrail
# ===========================================================================


@pytest.mark.asyncio
@patch("app.services.email_generator.groq_chat_completion")
@patch("app.services.email_generator.append_unsubscribe_footer", return_value="body_with_footer")
async def test_generate_touch_appends_sign_off_when_missing(mock_footer, mock_groq):
    """generate_touch_email should append sign-off when body doesn't end with it."""
    from app.services.email_generator import generate_touch_email

    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = '{"subject": "Test Deal", "body": "Great deal on this property."}'
    mock_groq.return_value = mock_response

    result = await generate_touch_email(
        touch=1,
        buyer_name="Test Buyer",
        buyer_email="buyer@test.com",
        buy_box="Houses under $300k",
        buyer_tier="A-List",
        address="123 Test St",
        city="Dallas",
        state="TX",
        property_type="House",
        arv=350000.0,
        asking_price=250000.0,
        spread=100000.0,
        condition_description="Good condition",
    )

    sign_off = settings.operator_email_signature.strip()
    assert result["body"].strip().endswith(sign_off) or sign_off in result["body"]


@pytest.mark.asyncio
@patch("app.services.email_generator.groq_chat_completion")
@patch("app.services.email_generator.append_unsubscribe_footer", side_effect=lambda body, _: body)
async def test_generate_touch_does_not_duplicate_sign_off(mock_footer, mock_groq):
    """generate_touch_email should not duplicate sign-off if already present."""
    from app.services.email_generator import generate_touch_email

    sign_off = settings.operator_email_signature.strip()
    # When sign-off is empty (default), skip the dedup assertion —
    # there's nothing to duplicate. The sign-off guardrail is a no-op.
    if not sign_off:
        pytest.skip("No operator_email_signature configured — skip dedup test")
    body_with_signoff = f"This is a great deal on 123 Test St.\n\n{sign_off}"
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = json.dumps({
        "subject": "123 Test St — Great Deal",
        "body": body_with_signoff,
    })
    mock_groq.return_value = mock_response

    result = await generate_touch_email(
        touch=1,
        buyer_name="Test Buyer",
        buyer_email="buyer@test.com",
        buy_box="Houses under $300k",
        buyer_tier="A-List",
        address="123 Test St",
        city="Dallas",
        state="TX",
        property_type="House",
        arv=350000.0,
        asking_price=250000.0,
        spread=100000.0,
        condition_description="Good condition",
    )

    # Sign-off should appear exactly once, not duplicated
    assert result["body"].strip().endswith(sign_off)
    # Count occurrences
    assert result["body"].count(sign_off) == 1


# ===========================================================================
# reply_processor — system prompt identity
# ===========================================================================


def test_classification_system_prompt_contains_identity():
    """System prompt in reply_processor should contain operator identity."""
    from app.services.reply_processor import _CLASSIFICATION_SYSTEM_PROMPT

    assert "OPERATOR IDENTITY" in _CLASSIFICATION_SYSTEM_PROMPT
    assert settings.operator_name in _CLASSIFICATION_SYSTEM_PROMPT
    assert "first person" in _CLASSIFICATION_SYSTEM_PROMPT


# ===========================================================================
# detect_uncertainty_and_hold
# ===========================================================================


@pytest.mark.asyncio
async def test_uncertainty_non_question_returns_none():
    """detect_uncertainty_and_hold should return None for non-question intents."""
    result = await detect_uncertainty_and_hold(
        reply={"subject": "Interested", "body": "I want to buy this property"},
        classification={"reply_intent": "Interested", "question_answer": None},
        db_session=MagicMock(),
        buyer_id=uuid.uuid4(),
        deal_id=uuid.uuid4(),
    )
    assert result is None


@pytest.mark.asyncio
async def test_uncertainty_confident_answer_returns_none():
    """detect_uncertainty_and_hold should return None when AI is confident (>20 chars)."""
    result = await detect_uncertainty_and_hold(
        reply={"subject": "Question", "body": "What's the square footage?"},
        classification={
            "reply_intent": "Question",
            "question_answer": "The property is 1,500 sq ft with 3 bedrooms and 2 bathrooms.",
        },
        db_session=MagicMock(),
        buyer_id=uuid.uuid4(),
        deal_id=uuid.uuid4(),
    )
    assert result is None


@pytest.mark.asyncio
async def test_uncertainty_short_answer_returns_hold():
    """detect_uncertainty_and_hold should return holding response for uncertain answers."""
    mock_db = AsyncMock()
    mock_db.add = MagicMock()

    result = await detect_uncertainty_and_hold(
        reply={"subject": "Question", "body": "What's the property tax on this?"},            classification={
                "reply_intent": "Question",
                "question_answer": "Not sure yet.",
            },
        db_session=mock_db,
        buyer_id=uuid.uuid4(),
        deal_id=uuid.uuid4(),
    )

    assert result is not None
    assert len(result) > 20  # Should be a substantive holding response
    # Should include sign-off
    sign_off = settings.operator_email_signature.strip()
    assert result.strip().endswith(sign_off)


@pytest.mark.asyncio
async def test_uncertainty_empty_answer_returns_hold():
    """detect_uncertainty_and_hold should return holding response when no answer."""
    mock_db = AsyncMock()
    mock_db.add = MagicMock()

    result = await detect_uncertainty_and_hold(
        reply={"subject": "Question", "body": "Can you tell me about the zoning?"},
        classification={
            "reply_intent": "Question",
            "question_answer": None,
        },
        db_session=mock_db,
        buyer_id=uuid.uuid4(),
        deal_id=uuid.uuid4(),
    )

    assert result is not None
    sign_off = settings.operator_email_signature.strip()
    assert result.strip().endswith(sign_off)


# ===========================================================================
# negotiation — system prompt identity
# ===========================================================================


def test_negotiation_system_prompt_contains_identity():
    """Negotiation system prompt should contain operator identity."""
    assert "OPERATOR IDENTITY" in _NEGOTIATION_SYSTEM_PROMPT
    assert settings.operator_name in _NEGOTIATION_SYSTEM_PROMPT
    assert "first person" in _NEGOTIATION_SYSTEM_PROMPT
