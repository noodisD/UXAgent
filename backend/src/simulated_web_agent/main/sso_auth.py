# sso_auth.py — verification of the short-lived tokens csaapps.pl issues at
# /auth/token.php?aud=<audience> (administrator's SSO guide, "Backend: weryfikacja
# tokenu").
#
# Token: base64url(JSON {sub, email, name, exp}) + "." + base64url(HMAC-SHA256(part1, AUTH_SECRET)).
# Nothing here raises: a token is untrusted input and every failure is "not a user".
import base64
import hashlib
import hmac
import json
import time

# No leeway on exp: the guide rejects as soon as exp < now. Both ends are on
# NTP-synced clocks and tokens live hours, so skew buys nothing but a longer
# window for a leaked token.
CLOCK_SKEW_SECONDS = 0


def parse_domains(raw: str) -> frozenset[str]:
    """ALLOWED_DOMAINS: comma list -> lowercase, stripped, empties dropped."""
    return frozenset(d.strip().lower() for d in (raw or "").split(",") if d.strip())


def _b64u_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def bearer_token(header: str) -> str:
    """The token of an 'Authorization: Bearer <token>' header, '' otherwise.
    The scheme is case-insensitive and extra spaces around the token are fine."""
    scheme, _, token = (header or "").partition(" ")
    return token.strip() if scheme.lower() == "bearer" else ""


def verify_token(token, secret, allowed_domains, now: float | None = None) -> dict | None:
    """The payload of a valid token, else None.

    Valid = signature matches (constant-time compare), exp is not in the past,
    and the e-mail's domain (the part after the last '@') is in allowed_domains.
    An empty allowed_domains rejects everything — default deny.
    """
    try:
        if not isinstance(token, str) or not token or not secret or not allowed_domains:
            return None
        key = secret.encode() if isinstance(secret, str) else bytes(secret)
        parts = token.split(".")
        if len(parts) != 2:
            return None
        body, sig = parts
        expected = base64.urlsafe_b64encode(
            hmac.new(key, body.encode(), hashlib.sha256).digest()
        ).rstrip(b"=").decode()
        if not hmac.compare_digest(expected, sig):
            return None
        payload = json.loads(_b64u_decode(body))
        if not isinstance(payload, dict):
            return None
        exp = payload.get("exp")
        email = payload.get("email")
        if isinstance(exp, bool) or not isinstance(exp, (int, float)) or not isinstance(email, str):
            return None
        if exp < (time.time() if now is None else now) - CLOCK_SKEW_SECONDS:
            return None
        # No '@' would make rpartition return the whole string as the "domain".
        _, at, domain = email.lower().rpartition("@")
        if not at or domain not in {d.lower() for d in allowed_domains}:
            return None
        return payload
    except Exception:
        return None
