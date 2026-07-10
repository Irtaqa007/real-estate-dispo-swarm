"""Native email verification service (no third-party APIs).

Performs a 3-step verification process:
1. Regex format validation (using email-validator library)
2. MX record lookup (using dnspython)
3. SMTP handshake (connect to MX server, send HELO/MAIL FROM/RCPT TO)

Returns a dict with result, score, and details.
"""

import asyncio
import logging
import re
import smtplib
import socket
from typing import Optional

import dns.resolver
from email_validator import EmailNotValidError, validate_email

logger = logging.getLogger(__name__)

# Common disposable email domains (abbreviated list)
DISPOSABLE_DOMAINS = {
    "mailinator.com", "guerrillamail.com", "temp-mail.org", "fakeinbox.com",
    "throwaway.email", "yopmail.com", "trashmail.com", "sharklasers.com",
    "grr.la", "mailcatch.com", "spambox.us", "tempemail.net",
}

# Timeout for SMTP operations (seconds)
SMTP_TIMEOUT = 10


def _regex_validate(email: str) -> dict:
    """Step 1: Validate email format using the email-validator library.

    Returns dict with: is_valid (bool), normalized_email (str|None), error (str|None).
    """
    try:
        validation = validate_email(email, check_deliverability=False)
        return {
            "is_valid": True,
            "normalized_email": validation.normalized,
            "error": None,
        }
    except EmailNotValidError as e:
        return {
            "is_valid": False,
            "normalized_email": None,
            "error": str(e),
        }


def _check_mx_records(domain: str) -> dict:
    """Step 2: Look up MX records for the given domain.

    Returns dict with: mx_found (bool), mx_servers (list), error (str|None).
    """
    try:
        answers = dns.resolver.resolve(domain, "MX", lifetime=5)
        mx_records = sorted(
            [(int(r.preference), str(r.exchange).rstrip(".")) for r in answers],
            key=lambda x: x[0],
        )
        if mx_records:
            return {
                "mx_found": True,
                "mx_servers": [server for _, server in mx_records],
                "error": None,
            }
        return {
            "mx_found": False,
            "mx_servers": [],
            "error": "No MX records found",
        }
    except dns.resolver.NoAnswer:
        return {
            "mx_found": False,
            "mx_servers": [],
            "error": "No MX records found (NoAnswer)",
        }
    except dns.resolver.NXDOMAIN:
        return {
            "mx_found": False,
            "mx_servers": [],
            "error": "Domain does not exist (NXDOMAIN)",
        }
    except dns.resolver.Timeout:
        return {
            "mx_found": False,
            "mx_servers": [],
            "error": "DNS lookup timed out",
        }
    except Exception as e:
        logger.warning("MX lookup failed: %s", e, exc_info=True)
        return {
            "mx_found": False,
            "mx_servers": [],
            "error": f"DNS lookup error: {e}",
        }


def _smtp_check(mx_server: str, email: str, from_email: str = "verify@realestate-dispo.com") -> dict:
    """Step 3: Perform SMTP handshake to verify the mailbox.

    Connects to the MX server and sends HELO, MAIL FROM, RCPT TO.
    Returns dict with: success (bool|None), code (int|None), message (str).

    Note: Many servers block or throttle this, so treat as a secondary signal.
    """
    try:
        with smtplib.SMTP(mx_server, 25, timeout=SMTP_TIMEOUT) as smtp:
            smtp.ehlo_or_helo_if_needed()
            smtp.mail(from_email)
            code, message = smtp.rcpt(email)

            if code == 250:
                return {"success": True, "code": code, "message": message.decode()}
            elif code in (550, 551, 552, 553, 450, 451, 452):
                return {"success": False, "code": code, "message": message.decode()}
            else:
                # Unknown response — inconclusive
                return {"success": None, "code": code, "message": message.decode()}

    except smtplib.SMTPConnectError:
        return {"success": None, "code": None, "message": "SMTP connection refused"}
    except smtplib.SMTPServerDisconnected:
        return {"success": None, "code": None, "message": "SMTP server disconnected"}
    except socket.timeout:
        return {"success": None, "code": None, "message": "SMTP connection timed out"}
    except ConnectionRefusedError as e:
        return {"success": None, "code": None, "message": "Connection refused"}
    except Exception as e:
        logger.warning("SMTP catch-all check failed: %s", e, exc_info=True)
        return {"success": None, "code": None, "message": f"SMTP error: {e}"}


def _compute_score(
    regex_valid: bool,
    mx_found: bool,
    smtp_result: Optional[bool],
    domain: str,
) -> tuple[str, int]:
    """Compute the overall verification result and confidence score.

    Score 0-100 where:
    - 0-20: Invalid
    - 21-40: Likely invalid
    - 41-60: Unknown / Catch-all domain
    - 61-80: Likely valid
    - 81-100: Valid
    """
    score = 0

    # Regex validation (base score)
    if regex_valid:
        score += 30
    else:
        return "invalid", 0

    # Check disposable domain
    domain_lower = domain.lower()
    is_disposable = any(
        domain_lower == d or domain_lower.endswith(f".{d}")
        for d in DISPOSABLE_DOMAINS
    )

    # MX records (medium weight)
    if mx_found:
        score += 30
    else:
        return "invalid", score

    # SMTP check (high weight, but may be inconclusive)
    if smtp_result == True:
        score += 40
    elif smtp_result == False:
        score -= 20  # SMTP explicitly rejected
    # smtp_result is None — inconclusive, no change

    # Determine result label
    if score >= 80:
        result = "valid"
    elif score >= 50:
        # Catch-all domain detection: if MX exists but SMTP is inconclusive,
        # the domain may have a catch-all configured
        if is_disposable:
            result = "catch_all"
        elif smtp_result is None and score < 70:
            result = "catch_all"
        else:
            result = "valid"
    elif score >= 20:
        result = "unknown"
    else:
        result = "invalid"

    # Clamp score to 0-100
    score = max(0, min(100, score))

    return result, score


async def verify_email(email: str) -> dict:
    """Run the full 3-step email verification pipeline.

    Args:
        email: The email address to verify.

    Returns:
        dict with keys: email, result, score, details
    """
    logger.info("Starting verification for: %s", email)

    # Step 1: Regex validation
    regex_result = _regex_validate(email)
    regex_valid = regex_result["is_valid"]
    normalized_email = regex_result.get("normalized_email", email)

    if not regex_valid:
        logger.info("Regex validation failed for: %s — %s", email, regex_result["error"])
        return {
            "email": email,
            "result": "invalid",
            "score": 0,
            "details": {
                "regex_valid": False,
                "mx_found": False,
                "smtp_check": None,
            },
        }

    # Extract domain
    domain = normalized_email.split("@")[1].lower()

    # Step 2: MX record lookup (run in thread pool to avoid blocking)
    mx_result = await asyncio.to_thread(_check_mx_records, domain)
    mx_found = mx_result["mx_found"]
    mx_servers = mx_result.get("mx_servers", [])

    # Step 3: SMTP handshake (run in thread pool)
    smtp_result: Optional[bool] = None
    smtp_performed = False

    if mx_found and mx_servers:
        # Try the highest-priority MX server first
        primary_mx = mx_servers[0]
        try:
            smtp_response = await asyncio.to_thread(_smtp_check, primary_mx, normalized_email)
            smtp_performed = True
            if smtp_response["success"] == True:
                smtp_result = True
            elif smtp_response["success"] == False:
                smtp_result = False
            else:
                smtp_result = None
            logger.debug("SMTP check for %s via %s: %s", email, primary_mx, smtp_response["message"])
        except Exception as e:
            logger.warning("SMTP check failed for %s: %s", email, e, exc_info=True)
            smtp_result = None

    # Compute final result and score
    result, score = _compute_score(regex_valid, mx_found, smtp_result, domain)

    logger.info("Verification complete for %s: %s (score=%d)", email, result, score)

    return {
        "email": email,
        "result": result,
        "score": score,
        "details": {
            "regex_valid": regex_valid,
            "mx_found": mx_found,
            "smtp_check": smtp_result,
        },
    }
