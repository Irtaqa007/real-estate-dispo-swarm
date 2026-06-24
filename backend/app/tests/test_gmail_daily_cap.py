"""Unit tests for the Gmail daily send cap feature.

Tests:
1. _check_daily_cap allows replies regardless of cap
2. _check_daily_cap blocks campaigns when cap is hit
3. send_email returns deferred_cap for campaigns at cap
4. get_gmail_send_status calculates remaining/percent correctly
5. increment_gmail_send_count increments and resets on date change
6. load_gmail_daily_sends returns correct data
7. save_gmail_daily_sends persists correctly
"""

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import state_persistence as sp
from app.services.gmail_service import _check_daily_cap, send_email


# ===========================================================================
# _check_daily_cap tests
# ===========================================================================


@pytest.mark.asyncio
@patch("app.services.gmail_service.get_gmail_send_status")
async def test_check_daily_cap_reply_always_allowed(mock_get_status):
    """send_type='reply' should bypass cap check entirely."""
    result = await _check_daily_cap("reply")
    assert result is True
    mock_get_status.assert_not_called()


@pytest.mark.asyncio
@patch("app.services.gmail_service.get_gmail_send_status")
async def test_check_daily_cap_campaign_below_cap_allowed(mock_get_status):
    """send_type='campaign' below cap should be allowed."""
    mock_get_status.return_value = {
        "sends_today": 100,
        "daily_cap": 400,
        "remaining": 300,
        "percent_used": 25.0,
        "cap_hit": False,
        "warning_threshold_hit": False,
        "resets_at": "2026-06-22T00:00:00+05:00",
    }
    result = await _check_daily_cap("campaign")
    assert result is True


@pytest.mark.asyncio
@patch("app.services.gmail_service.get_gmail_send_status")
async def test_check_daily_cap_campaign_at_cap_blocked(mock_get_status):
    """send_type='campaign' at cap should be blocked."""
    mock_get_status.return_value = {
        "sends_today": 400,
        "daily_cap": 400,
        "remaining": 0,
        "percent_used": 100.0,
        "cap_hit": True,
        "warning_threshold_hit": True,
        "resets_at": "2026-06-22T00:00:00+05:00",
    }
    result = await _check_daily_cap("campaign")
    assert result is False


@pytest.mark.asyncio
@patch("app.services.gmail_service.get_gmail_send_status")
async def test_check_daily_cap_above_cap_blocked(mock_get_status):
    """send_type='campaign' above cap should be blocked."""
    mock_get_status.return_value = {
        "sends_today": 405,
        "daily_cap": 400,
        "remaining": 0,
        "percent_used": 101.25,
        "cap_hit": True,
        "warning_threshold_hit": True,
        "resets_at": "2026-06-22T00:00:00+05:00",
    }
    result = await _check_daily_cap("campaign")
    assert result is False


# ===========================================================================
# send_email — deferred_cap response tests
# ===========================================================================


@pytest.mark.asyncio
@patch("app.services.gmail_service.get_gmail_send_status")
@patch("app.services.gmail_service._wait_for_rate_limit", return_value=None)
@patch("app.services.gmail_service._send_email_inner", return_value={"status": "sent", "message_id": "test", "sent_at": "now"})
@patch("app.services.gmail_service.increment_gmail_send_count", return_value=101)
async def test_send_email_reply_not_deferred(
    mock_inc, mock_send_inner, mock_wait, mock_get_status, monkeypatch
):
    """send_type='reply' should never be deferred, even at cap."""
    mock_get_status.return_value = {
        "sends_today": 400, "daily_cap": 400, "remaining": 0,
        "percent_used": 100.0, "cap_hit": True,
        "warning_threshold_hit": True, "resets_at": "",
    }
    monkeypatch.setattr("app.services.gmail_service.gmail_circuit_breaker", MagicMock())
    monkeypatch.setattr("app.services.gmail_service.record_metric", lambda _: None)

    result = await send_email(
        to="buyer@test.com", subject="Test", body="Hello", send_type="reply"
    )
    assert result["status"] == "sent"  # reply bypasses cap


@pytest.mark.asyncio
@patch("app.services.gmail_service.get_gmail_send_status")
async def test_send_email_campaign_deferred_at_cap(mock_get_status, monkeypatch):
    """send_type='campaign' at cap should return deferred_cap."""
    mock_get_status.return_value = {
        "sends_today": 400, "daily_cap": 400, "remaining": 0,
        "percent_used": 100.0, "cap_hit": True,
        "warning_threshold_hit": True, "resets_at": "",
    }
    monkeypatch.setattr("app.services.gmail_service.record_metric", lambda _: None)

    result = await send_email(
        to="buyer@test.com", subject="Test", body="Hello", send_type="campaign"
    )
    assert result["status"] == "deferred_cap"
    assert "Daily send cap reached" in result.get("reason", "")


# ===========================================================================
# get_gmail_send_status tests
# ===========================================================================


@pytest.mark.asyncio
@patch("app.services.state_persistence.load_gmail_daily_sends")
@patch("app.services.state_persistence.settings")
async def test_get_status_zero_sends(mock_settings, mock_load):
    """Status with zero sends should show correct values."""
    mock_load.return_value = {"count": 0, "date": "2026-06-21", "reset_at": "2026-06-22T00:00:00+05:00"}
    mock_settings.gmail_daily_cap = 400
    mock_settings.gmail_timezone = "Asia/Karachi"

    status = await sp.get_gmail_send_status()
    assert status["sends_today"] == 0
    assert status["daily_cap"] == 400
    assert status["remaining"] == 400
    assert status["percent_used"] == 0.0
    assert status["cap_hit"] is False
    assert status["warning_threshold_hit"] is False


@pytest.mark.asyncio
@patch("app.services.state_persistence.load_gmail_daily_sends")
@patch("app.services.state_persistence.settings")
async def test_get_status_partial_sends(mock_settings, mock_load):
    """Status with partial sends should calculate remaining correctly."""
    mock_load.return_value = {"count": 247, "date": "2026-06-21", "reset_at": "2026-06-22T00:00:00+05:00"}
    mock_settings.gmail_daily_cap = 400

    status = await sp.get_gmail_send_status()
    assert status["sends_today"] == 247
    assert status["remaining"] == 153
    assert status["percent_used"] == 61.75


@pytest.mark.asyncio
@patch("app.services.state_persistence.load_gmail_daily_sends")
@patch("app.services.state_persistence.settings")
async def test_get_status_near_cap_warning(mock_settings, mock_load):
    """Status at 90%+ should trigger warning_threshold_hit."""
    mock_load.return_value = {"count": 360, "date": "2026-06-21", "reset_at": "2026-06-22T00:00:00+05:00"}
    mock_settings.gmail_daily_cap = 400

    status = await sp.get_gmail_send_status()
    assert status["warning_threshold_hit"] is True
    assert status["cap_hit"] is False


@pytest.mark.asyncio
@patch("app.services.state_persistence.load_gmail_daily_sends")
@patch("app.services.state_persistence.settings")
async def test_get_status_cap_hit(mock_settings, mock_load):
    """Status at exactly cap should show cap_hit=True."""
    mock_load.return_value = {"count": 400, "date": "2026-06-21", "reset_at": "2026-06-22T00:00:00+05:00"}
    mock_settings.gmail_daily_cap = 400

    status = await sp.get_gmail_send_status()
    assert status["cap_hit"] is True
    assert status["remaining"] == 0


# ===========================================================================
# increment_gmail_send_count tests
# ===========================================================================


@pytest.mark.asyncio
@patch("app.services.state_persistence.load_gmail_daily_sends")
@patch("app.services.state_persistence.save_gmail_daily_sends")
@patch("app.services.state_persistence.settings")
@patch("zoneinfo.ZoneInfo")
async def test_increment_new_day_resets(mock_zoneinfo, mock_settings, mock_save, mock_load):
    """increment_gmail_send_count on a new day should reset to 1."""
    from datetime import timezone
    mock_zoneinfo.return_value = timezone.utc
    mock_load.return_value = {"count": 250, "date": "2026-06-20", "reset_at": ""}
    mock_settings.gmail_timezone = "Asia/Karachi"

    new_count = await sp.increment_gmail_send_count()
    assert new_count == 1


@pytest.mark.asyncio
@patch("app.services.state_persistence.load_gmail_daily_sends")
@patch("app.services.state_persistence.save_gmail_daily_sends")
@patch("app.services.state_persistence.settings")
@patch("zoneinfo.ZoneInfo")
async def test_increment_same_day_increments(mock_zoneinfo, mock_settings, mock_save, mock_load):
    """increment_gmail_send_count on the same day should increase by 1."""
    from datetime import datetime, timezone
    mock_zoneinfo.return_value = timezone.utc
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    mock_load.return_value = {"count": 100, "date": today_str, "reset_at": ""}
    mock_settings.gmail_timezone = "Asia/Karachi"

    new_count = await sp.increment_gmail_send_count()
    assert new_count == 101


# ===========================================================================
# load_gmail_daily_sends / save_gmail_daily_sends tests
# ===========================================================================


@pytest.mark.asyncio
@patch("app.services.state_persistence._get_state")
async def test_load_empty_returns_defaults(mock_get):
    """load_gmail_daily_sends with no data should return empty dict."""
    mock_get.return_value = None
    result = await sp.load_gmail_daily_sends()
    assert result == {}


@pytest.mark.asyncio
@patch("app.services.state_persistence._get_state")
async def test_load_returns_stored_data(mock_get):
    """load_gmail_daily_sends should return stored data correctly."""
    mock_get.return_value = {"date": "2026-06-21", "count": 42, "reset_at": "2026-06-22T00:00:00+05:00"}
    result = await sp.load_gmail_daily_sends()
    assert result["date"] == "2026-06-21"
    assert result["count"] == 42
    assert "reset_at" in result


@pytest.mark.asyncio
@patch("app.services.state_persistence._set_state")
async def test_save_persists_data(mock_set):
    """save_gmail_daily_sends should call _set_state with correct data."""
    await sp.save_gmail_daily_sends(55, "2026-06-21", "2026-06-22T00:00:00+05:00")
    mock_set.assert_awaited_once_with(
        sp.KEY_GMAIL_DAILY_SENDS,
        {"date": "2026-06-21", "count": 55, "reset_at": "2026-06-22T00:00:00+05:00"},
    )
