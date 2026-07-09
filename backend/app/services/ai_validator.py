"""Pre-send AI output validation guard.

Runs on every AI-generated output before it leaves the system.
Catches hallucinations, factual errors, and identity violations
before they reach a real buyer or title company.

ValidationResult fields:
    valid (bool): Whether the content passed all checks.
    confidence (float): Worst-case confidence across all checks (0.0 to 1.0).
    violations (list[str]): Human-readable descriptions of all violations.
    severity (str): "block" | "warn" | "pass"
    corrected_content (str | None): Auto-fixed version if applicable.
    checks_run (list[str]): Which checks were executed.
"""

import json
import logging
import re
import uuid as uuid_mod
from dataclasses import dataclass, field
from typing import Any, Optional

from app.config import settings
from app.models.models import Buyer, Deal
from app.services.audit_logger import audit
from app.services.groq_client import groq_chat_completion, extract_json_block

logger = logging.getLogger(__name__)

# ── Hallucination guard model (fast, not 70b) ──
_HALLUCINATION_MODEL = "llama-3.1-8b-instant"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class ValidationResult:
    """Result of validating a single AI-generated output.

    Attributes:
        valid: Whether content is safe to send.
        confidence: Worst-case confidence across all checks (0.0 to 1.0).
        violations: Human-readable violation descriptions.
        severity: "block" (do not send), "warn" (send but log), "pass" (send).
        corrected_content: Auto-fixed content (only for severity="warn").
        checks_run: List of check names that were executed.
    """
    valid: bool = True
    confidence: float = 1.0
    violations: list[str] = field(default_factory=list)
    severity: str = "pass"
    corrected_content: Optional[str] = None
    checks_run: list[str] = field(default_factory=list)


# ===========================================================================
# Individual checks
# ===========================================================================


# ---------------------------------------------------------------------------
# CHECK 1 — Placeholder Detection
# ---------------------------------------------------------------------------

_PLACEHOLDER_PATTERNS = [
    r"\[Name\]",
    r"\[Address\]",
    r"\[Price\]",
    r"\[Date\]",
    r"\[City\]",
    r"\[ARV\]",
    r"\{\{.*?\}\}",       # Jinja-style
    r"\[.*?\]",           # any remaining bracket placeholder
]


def _check_placeholders(content: str, checks_run: list[str]) -> tuple[Optional[str], Optional[str]]:
    """Check for unfilled template placeholders.

    Returns:
        Tuple of (violation_text, severity) or (None, None) if clean.
    """
    checks_run.append("placeholder_detection")
    for pattern in _PLACEHOLDER_PATTERNS:
        matches = re.findall(pattern, content)
        if matches:
            found = ", ".join(set(matches))
            return (
                f"Unfilled template placeholder(s) detected: {found}",
                "block",
            )
    return None, None


# ---------------------------------------------------------------------------
# CHECK 2 — Operator Sign-Off Present
# ---------------------------------------------------------------------------


def _check_sign_off(content: str, checks_run: list[str]) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Check that the operator's email signature appears in the content.

    Returns:
        Tuple of (violation_text, severity, corrected_content).
        corrected_content is the content with sign-off appended if missing.
    """
    checks_run.append("operator_sign_off")
    sign_off = settings.operator_signature.strip()
    if not sign_off:
        return None, None, None

    # Primary check: look for the full signature string
    if sign_off.lower() in content.lower():
        return None, None, None

    # Secondary check: if sign-off is multi-line, check that the
    # last line (typically the name) appears in the last 20% of
    # the content (near the end, not in the greeting)
    sign_off_last_line = sign_off.strip().split("\n")[-1].strip()
    if sign_off_last_line:
        content_tail = content[int(len(content) * 0.8):]
        if sign_off_last_line.lower() in content_tail.lower():
            return None, None, None

    # Sign-off genuinely missing — auto-correct
    corrected = content.rstrip() + "\n\n" + sign_off
    return (
        f"Missing operator sign-off — auto-appended",
        "warn",
        corrected,
    )


# ---------------------------------------------------------------------------
# CHECK 3 — Floor Price Protection
# ---------------------------------------------------------------------------


_DOLLAR_AMOUNT_RE = re.compile(r"\$[\d,]+(?:\.\d{1,2})?")


def _check_floor_price(
    content: str,
    content_type: str,
    deal: Optional[Deal],
    checks_run: list[str],
) -> tuple[Optional[str], Optional[str]]:
    """Check that no dollar amount in content is below deal floor price.

    Only runs for negotiation_email when deal and floor_price are available.

    Returns:
        Tuple of (violation_text, severity) or (None, None) if clean.
    """
    if content_type != "negotiation_email" or deal is None:
        return None, None

    floor_price = deal.floor_price
    if floor_price is None:
        return None, None

    checks_run.append("floor_price_protection")

    floor_val = float(floor_price)
    amounts = _DOLLAR_AMOUNT_RE.findall(content)

    for amount_str in amounts:
        # Strip $ and commas, parse float
        amount_val = float(amount_str.replace("$", "").replace(",", ""))
        # Skip amounts too small to be a deal price
        # (below 10% of floor = clearly not a property price)
        if amount_val < floor_val * 0.10:
            continue
        if amount_val < floor_val:
            return (
                f"Price ${amount_val:,.2f} quoted below floor price "
                f"${floor_val:,.2f} — BLOCKED",
                "block",
            )

    return None, None


# ---------------------------------------------------------------------------
# CHECK 4 — Deal Financial Accuracy
# ---------------------------------------------------------------------------


def _check_financial_accuracy(
    content: str,
    deal: Optional[Deal],
    checks_run: list[str],
) -> tuple[Optional[str], Optional[str]]:
    """Check that key financial figures in content are within tolerance of actual values.

    Only runs when deal is provided. Warn-only (does not block).

    Returns:
        Tuple of (violation_text, severity) or (None, None) if clean/unclear.
    """
    if deal is None:
        return None, None

    checks_run.append("financial_accuracy")

    figures_to_check: dict[str, Optional[float]] = {
        "arv": float(deal.arv) if deal.arv is not None else None,
        "asking": float(deal.asking_price) if deal.asking_price is not None else None,
        "repairs": float(deal.repair_estimate) if deal.repair_estimate is not None else None,
    }

    violations: list[str] = []
    amounts_in_content = _DOLLAR_AMOUNT_RE.findall(content)
    parsed_amounts = []
    for amt in amounts_in_content:
        try:
            parsed_amounts.append(float(amt.replace("$", "").replace(",", "")))
        except ValueError:
            continue

    if not parsed_amounts:
        return None, None  # No numbers to check

    for label, actual_val in figures_to_check.items():
        if actual_val is None or actual_val <= 0:
            continue

        # Look for any amount within ±5% of the actual value
        close_match = None
        for amt in parsed_amounts:
            if abs(amt - actual_val) / actual_val <= 0.05:
                close_match = amt
                break

        if close_match is not None:
            # The figure appears to be mentioned accurately — fine
            continue

        # Check if any number is within a wider 15% band (meaning the deal
        # financials ARE referenced), but not close enough
        referenced = False
        for amt in parsed_amounts:
            if abs(amt - actual_val) / actual_val <= 0.15:
                referenced = True
                break

        if referenced:
            violations.append(
                f"Deal financial figure '{label}' appears mentioned in content "
                f"(closest: ${min(abs(amt - actual_val) for amt in parsed_amounts):,.0f} "
                f"off from actual ${actual_val:,.0f}) — verify accuracy"
            )

    if violations:
        return ("; ".join(violations), "warn")

    return None, None


# ---------------------------------------------------------------------------
# CHECK 5 — Identity Consistency (banned phrases)
# ---------------------------------------------------------------------------


def _check_banned_phrases(
    content: str,
    checks_run: list[str],
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Check that no banned phrases appear in content.

    Returns:
        Tuple of (violation_text, severity, corrected_content).
        corrected_content has banned phrases removed.
    """
    checks_run.append("identity_consistency")
    never_say = settings.operator_never_say
    if not never_say or not never_say.strip():
        return None, None, None

    banned = [p.strip() for p in never_say.split(",") if p.strip()]
    if not banned:
        return None, None, None

    violations: list[str] = []
    corrected = content

    for phrase in banned:
        if phrase.lower() in corrected.lower():
            violations.append(f"Banned phrase detected: '{phrase}'")
            corrected = corrected.replace(phrase, "")

    if violations:
        # Clean up extra whitespace from removals
        corrected = re.sub(r"  +", " ", corrected)
        corrected = re.sub(r"\n{3,}", "\n\n", corrected)
        corrected = corrected.strip()
        return (
            "; ".join(violations),
            "warn",
            corrected,
        )

    return None, None, None


# ---------------------------------------------------------------------------
# CHECK 6 — AI Hallucination Guard
# ---------------------------------------------------------------------------


async def _check_hallucination(
    content: str,
    deal: Deal,
    checks_run: list[str],
) -> tuple[Optional[str], Optional[str], float]:
    """Use a fast LLM to check for hallucinations, invented facts, false guarantees.

    Returns:
        Tuple of (violation_text, severity, confidence).
        Returns (None, None, 1.0) on parse failure or if deal is not provided.
    """
    if deal is None:
        return None, None, 1.0

    checks_run.append("hallucination_guard")

    system_prompt = (
        "You are a factual accuracy checker for real estate emails. "
        "You verify that email content does not contain invented facts, "
        "false guarantees, or claims not supported by the deal data "
        "provided. Respond ONLY in JSON."
    )

    arv_val = float(deal.arv) if deal.arv else 0
    asking_val = float(deal.asking_price) if deal.asking_price else 0
    repair_val = float(deal.repair_estimate) if deal.repair_estimate else 0

    user_prompt = (
        f"Deal facts:\n"
        f"Address: {deal.address}\n"
        f"City: {deal.city}, {deal.state}\n"
        f"Property type: {deal.property_type}\n"
        f"ARV: ${arv_val:,.0f}\n"
        f"Asking price: ${asking_val:,.0f}\n"
        f"Repair estimate: ${repair_val:,.0f}\n"
        f"\n"
        f"Email content to check:\n"
        f"{content}\n"
        f"\n"
        f"Check for:\n"
        f"1. Any claims about the property not supported by the deal facts "
        f"(invented features, amenities, history)\n"
        f"2. Any guarantees (guaranteed returns, guaranteed close, "
        f"guaranteed title)\n"
        f"3. Any false urgency claims ('only 1 left', 'expires today' "
        f"when no deadline is set)\n"
        f"\n"
        f"Respond in JSON only:\n"
        f"{{\n"
        f"  'hallucination_detected': true/false,\n"
        f"  'confidence': 0.0-1.0,\n"
        f"  'violations': ['description of each violation'],\n"
        f"  'severity': 'block' or 'warn'\n"
        f"}}"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    try:
        response = await groq_chat_completion(
            messages=messages,
            model=_HALLUCINATION_MODEL,
            temperature=0.1,
            max_tokens=300,
        )
        raw = response.choices[0].message.content.strip()

        # Strip markdown fences if present
        if raw.startswith("```"):
            lines = raw.split("\n")
            raw = "\n".join(line for line in lines if not line.strip().startswith("```"))

        parsed = json.loads(raw)
        hallucination_detected = parsed.get("hallucination_detected", False)
        confidence = float(parsed.get("confidence", 0.5))
        violations = parsed.get("violations", [])
        severity = parsed.get("severity", "warn")

        if not hallucination_detected or not violations:
            return None, None, 1.0

        return (
            "; ".join(violations),
            severity if severity in ("block", "warn") else "warn",
            confidence,
        )

    except (json.JSONDecodeError, Exception) as e:
        logger.warning(
            "Hallucination guard: failed to parse LLM response, skipping check: %s",
            e,
            exc_info=True,
        )
        return None, None, 1.0


# ===========================================================================
# Main validation function
# ===========================================================================


async def validate_ai_output(
    content: str,
    content_type: str,
    deal: Optional[Deal] = None,
    buyer: Optional[Buyer] = None,
    context: Optional[dict] = None,
) -> ValidationResult:
    """Validate an AI-generated output before sending.

    Runs all applicable checks and returns a ValidationResult with the
    aggregate verdict.

    Args:
        content: The AI-generated text to validate.
        content_type: One of "campaign_email", "reply_email",
                      "ghost_recovery_email", "negotiation_email",
                      "contract_field".
        deal: Optional Deal model for financial checks.
        buyer: Optional Buyer model for context.
        context: Optional dict with additional context.

    Returns:
        ValidationResult with verdict and auto-corrections.
    """
    if not content or not content.strip():
        return ValidationResult(
            valid=False,
            confidence=0.0,
            violations=["Empty content"],
            severity="block",
            checks_run=["empty_content_check"],
        )

    result = ValidationResult()

    # Track the running corrected content across auto-correctable checks
    corrected_content = content

    # ── CHECK 1: Placeholder Detection ──
    violation, sev = _check_placeholders(content, result.checks_run)
    if violation:
        result.violations.append(violation)
        _update_severity(result, sev)

    # ── CHECK 2: Operator Sign-Off ──
    violation, sev, corrected = _check_sign_off(corrected_content, result.checks_run)
    if violation:
        result.violations.append(violation)
        _update_severity(result, sev)
        if corrected is not None:
            corrected_content = corrected
            result.corrected_content = corrected_content

    # ── CHECK 3: Floor Price Protection ──
    violation, sev = _check_floor_price(content, content_type, deal, result.checks_run)
    if violation:
        result.violations.append(violation)
        _update_severity(result, sev)

    # ── CHECK 4: Deal Financial Accuracy ──
    violation, sev = _check_financial_accuracy(content, deal, result.checks_run)
    if violation:
        result.violations.append(violation)
        _update_severity(result, sev)

    # ── CHECK 5: Identity Consistency (banned phrases) ──
    violation, sev, corrected = _check_banned_phrases(corrected_content, result.checks_run)
    if violation:
        result.violations.append(violation)
        _update_severity(result, sev)
        if corrected is not None:
            corrected_content = corrected
            result.corrected_content = corrected_content

    # ── CHECK 6: AI Hallucination Guard (AI-assisted) ──
    violation, sev, confidence = await _check_hallucination(
        content, deal, result.checks_run,
    )
    if violation:
        result.violations.append(violation)
        _update_severity(result, sev or "warn")
        result.confidence = min(result.confidence, confidence)
    else:
        result.confidence = min(result.confidence, confidence)

    # ── Finalize result ──
    result.valid = (result.severity == "pass")
    result.confidence = max(0.0, min(1.0, result.confidence))

    # If severity is block, do NOT send corrected content
    if result.severity == "block":
        result.corrected_content = None

    # Logging
    if result.severity == "block":
        logger.error(
            "AI output BLOCKED by validator [%s]: %s",
            content_type, "; ".join(result.violations),
        )
        # Dashboard alert via audit log
        await _log_validation_block(result, content_type, buyer, deal)
    elif result.severity == "warn":
        logger.warning(
            "AI output WARN by validator [%s]: %s",
            content_type, "; ".join(result.violations),
        )

    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _update_severity(result: ValidationResult, severity: str) -> None:
    """Update aggregate severity. Block wins over warn, warn wins over pass."""
    if severity == "block":
        result.severity = "block"
    elif severity == "warn" and result.severity != "block":
        result.severity = "warn"


async def _log_validation_block(
    result: ValidationResult,
    content_type: str,
    buyer: Optional[Buyer],
    deal: Optional[Deal],
) -> None:
    """Create an activity log entry when an AI output is blocked."""
    # Use lazy import to avoid circular dependency at module level
    import app.database as _db
    try:
        async with _db.async_session_factory() as db:
            entity_type_map = {
                "negotiation_email": "negotiation",
                "campaign_email": "campaign",
                "reply_email": "campaign",
                "ghost_recovery_email": "campaign",
                "contract_field": "deal",
            }
            entity_type = entity_type_map.get(content_type, "campaign")

            await audit.log(
                db,
                entity_type=entity_type,
                entity_id=deal.id if deal else uuid_mod.uuid4(),
                action="ai_output_blocked",
                metadata={
                    "content_type": content_type,
                    "buyer_id": str(buyer.id) if buyer else None,
                    "deal_id": str(deal.id) if deal else None,
                    "violations": result.violations,
                    "checks_run": result.checks_run,
                    "alert_user": True,
                    "action_required": (
                        "AI-generated email blocked before send. "
                        "Review violations and resend manually if appropriate."
                    ),
                },
            )
            await db.commit()
    except Exception as e:
        logger.error("Failed to log validation block to activity log: %s", e, exc_info=True)