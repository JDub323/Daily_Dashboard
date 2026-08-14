"""Windows Hello, as the lock on the portfolio panel.

WebAuthn is the only way a page can reach the laptop's fingerprint reader, and
it is worth being precise about what that buys, because the obvious reading is
wrong in two directions.

**It is not "fingerprint".** The page asks for *user verification*; Windows
decides how to satisfy it, and will happily accept the Windows PIN or a face
instead of a finger. There is no flag that says "finger only" -- the spec has
no vocabulary for it and Hello would not honour one. So this stops someone
walking past your open laptop. It does not stop someone who knows your PIN.

**It is much better than a password check.** No biometric ever reaches this
process. Registration stores a public key; unlocking verifies a signature made
by a private key that never leaves the TPM. There is no secret here to steal,
which is why the credential table below is unremarkable enough to sit in the
same SQLite file as everything else.

The gate is enforced *here*, on the server, rather than by hiding a rendered
panel client-side. A CSS gate leaves the numbers in the response body, in
devtools and in the back/forward cache; the panel would look locked while the
data had already been handed over. `/api/portfolio` returns 403 without a token
minted by a verified assertion, and the token expires whether or not any
browser is still counting.

Both stores are in memory, so a server restart re-locks. That is the intended
behaviour and not a limitation: nothing about a five-minute unlock should
survive the process that granted it.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any

# COSE algorithm identifiers. Windows Hello negotiates RS256 on most hardware
# and ES256 on some; both are offered at registration and both are verified
# here. Ed25519 is included because it costs three lines and a future
# authenticator may prefer it.
ES256 = -7
EDDSA = -8
RS256 = -257
SUPPORTED_ALGS = (ES256, EDDSA, RS256)

CHALLENGE_TTL_SECONDS = 120
"""How long a challenge stays usable. Long enough to find the fingerprint
reader, short enough that one cannot be captured and replayed later."""

# authenticatorData flag bits.
FLAG_USER_PRESENT = 0x01
FLAG_USER_VERIFIED = 0x04


class WebAuthnError(Exception):
    """A registration or assertion that did not verify.

    Deliberately carries a short, non-specific message: telling the caller
    exactly which check failed is a description of the lock's internals, and
    the honest answer for every case is the same one -- it did not verify.
    """


# --- base64url -------------------------------------------------------------


def b64url_decode(text: str) -> bytes:
    """Decode base64url, supplying the padding the browser omits."""
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (ValueError, TypeError):
        raise WebAuthnError("malformed request")


def b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


# --- the relying party -----------------------------------------------------


@dataclass(frozen=True)
class RelyingParty:
    """Who the credential is bound to.

    `rp_id` must be a *domain*, and an IP literal is not one -- so a dashboard
    reached at `http://127.0.0.1:8502` cannot use WebAuthn at all, and the
    browser rejects it before the server ever sees a request. This is the whole
    reason `server.main` opens `localhost`; see `for_settings` below.
    """

    rp_id: str
    origins: frozenset[str]
    name: str = "Daily Dashboard"

    def allows(self, origin: str) -> bool:
        return origin in self.origins


LOOPBACK = {"127.0.0.1", "::1", "0.0.0.0", "localhost"}


def for_settings(host: str, port: int, rp_id: str | None = None) -> RelyingParty:
    """The relying party this server presents.

    A loopback host becomes `localhost`, which is both a valid `rp_id` and a
    secure context -- the two things WebAuthn needs and plain HTTP on an IP
    cannot give. The `127.0.0.1` origin is deliberately *not* in the accepted
    set: the browser would refuse to produce an assertion for it anyway, and
    listing it would only mean failing later and less clearly.
    """
    domain = rp_id or ("localhost" if host in LOOPBACK else host)
    scheme = "http" if domain == "localhost" else "https"
    port_part = "" if port in (80, 443) else f":{port}"
    return RelyingParty(rp_id=domain, origins=frozenset({f"{scheme}://{domain}{port_part}"}))


# --- challenges ------------------------------------------------------------


class Challenges:
    """Single-use nonces, held in memory.

    Single use is the point: a challenge that can be presented twice is a
    signature that can be replayed. `take` removes as it reads, so a retried
    request fails rather than succeeding a second time.
    """

    def __init__(self, ttl: int = CHALLENGE_TTL_SECONDS) -> None:
        self._issued: dict[str, float] = {}
        self._ttl = ttl

    def issue(self) -> str:
        self._sweep()
        challenge = b64url_encode(secrets.token_bytes(32))
        self._issued[challenge] = time.monotonic() + self._ttl
        return challenge

    def take(self, challenge: str) -> None:
        """Consume a challenge, or raise if it was never issued or has expired."""
        self._sweep()
        if self._issued.pop(challenge, None) is None:
            raise WebAuthnError("this request has expired — try again")

    def _sweep(self) -> None:
        now = time.monotonic()
        for challenge in [c for c, expiry in self._issued.items() if expiry <= now]:
            del self._issued[challenge]


# --- unlock tokens ---------------------------------------------------------


class Unlocks:
    """Tokens minted by a verified assertion, expiring on their own.

    The five minutes are enforced here rather than by the browser's timer. A
    client-side countdown decides when the panel *looks* locked; this decides
    when the data stops being served, which is the one that matters if the tab
    is left open on a desk.
    """

    def __init__(self, ttl_seconds: int) -> None:
        self._tokens: dict[str, float] = {}
        self._ttl = ttl_seconds

    def mint(self) -> tuple[str, int]:
        """A fresh token and how many seconds it is good for."""
        self._sweep()
        token = secrets.token_urlsafe(32)
        self._tokens[token] = time.monotonic() + self._ttl
        return token, self._ttl

    def valid(self, token: str | None) -> bool:
        self._sweep()
        if not token:
            return False
        # compare_digest against every live token rather than a dict lookup:
        # the set is at most a handful of entries, and a timing-safe compare
        # costs nothing at that size.
        return any(secrets.compare_digest(token, live) for live in self._tokens)

    def remaining(self, token: str) -> int:
        """Seconds left on a token, for the panel's countdown. 0 if it is gone."""
        self._sweep()
        expiry = self._tokens.get(token)
        return max(0, int(expiry - time.monotonic())) if expiry else 0

    def revoke(self, token: str | None) -> None:
        if token:
            self._tokens.pop(token, None)

    def revoke_all(self) -> None:
        self._tokens.clear()

    def any_live(self) -> bool:
        self._sweep()
        return bool(self._tokens)

    def _sweep(self) -> None:
        now = time.monotonic()
        for token in [t for t, expiry in self._tokens.items() if expiry <= now]:
            del self._tokens[token]


# --- verification ----------------------------------------------------------


def _load_verifier():
    """`cryptography`, or a message saying how to get it.

    Imported here rather than at module scope so that a dashboard whose venv
    predates this feature still *boots* -- every other panel works, and this
    one says what to run. An ImportError at the top of the module would take
    the whole server down over a locked panel nobody had opened yet.
    """
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec, padding
    except ImportError:
        raise WebAuthnError(
            "the portfolio lock needs the `cryptography` package: "
            "re-run  pip install --editable '.[dev]'  in the project venv"
        )
    return hashes, serialization, ec, padding


def _client_data(raw: bytes, expect_type: str, challenge: str, rp: RelyingParty) -> None:
    """Check the browser's account of what it was asked to sign.

    Three things, and all three matter: the *type* stops an assertion collected
    during registration being replayed as a login, the *challenge* stops any
    replay at all, and the *origin* is what stops a page on another site from
    driving this ceremony.
    """
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise WebAuthnError("malformed request")
    if data.get("type") != expect_type:
        raise WebAuthnError("malformed request")
    if not secrets.compare_digest(str(data.get("challenge", "")), challenge):
        raise WebAuthnError("this request has expired — try again")
    if not rp.allows(str(data.get("origin", ""))):
        raise WebAuthnError(
            f"the lock only works at {next(iter(rp.origins))} — "
            "open the dashboard there rather than at its IP address"
        )


@dataclass(frozen=True)
class Registration:
    credential_id: str
    public_key: bytes
    """SPKI DER, as `AuthenticatorAttestationResponse.getPublicKey()` returns it."""

    alg: int


def verify_registration(
    *, credential_id: str, client_data_json: str, public_key: str, alg: int,
    challenge: str, rp: RelyingParty,
) -> Registration:
    """Accept a newly created credential.

    Takes the browser's `getPublicKey()` output rather than the attestation
    object, which means this module needs no CBOR decoder and no COSE key
    parser -- two formats whose only job here would be to yield a public key
    the browser has already extracted. There is no attestation check because
    there is nothing to check it against: this is a personal dashboard
    enrolling the laptop it runs on, not an enterprise deciding which
    authenticators it trusts.

    The security of the whole arrangement rests on `verify_assertion` below,
    where a signature is checked against this key. Registration only has to
    record the right key for the right origin.
    """
    if alg not in SUPPORTED_ALGS:
        raise WebAuthnError("this authenticator uses a signature type the dashboard cannot verify")
    _client_data(b64url_decode(client_data_json), "webauthn.create", challenge, rp)
    key = b64url_decode(public_key)
    if not key:
        # Safari and older Chrome return null from getPublicKey() for some
        # authenticators. Better to say so than to store an empty key and fail
        # every future unlock with "did not verify".
        raise WebAuthnError("the browser did not return a public key for this authenticator")
    return Registration(credential_id=credential_id, public_key=key, alg=alg)


def verify_assertion(
    *, client_data_json: str, authenticator_data: str, signature: str,
    public_key: bytes, alg: int, challenge: str, rp: RelyingParty,
    stored_sign_count: int = 0,
) -> int:
    """Verify an unlock, returning the authenticator's new signature counter.

    The order is deliberate -- everything cheap and structural first, the
    signature last, so a malformed request never reaches the crypto.
    """
    hashes, serialization, ec, padding = _load_verifier()

    client_data = b64url_decode(client_data_json)
    _client_data(client_data, "webauthn.get", challenge, rp)

    auth_data = b64url_decode(authenticator_data)
    if len(auth_data) < 37:
        raise WebAuthnError("malformed request")

    # The credential is bound to the rp_id it was created under; a signature
    # made for a different site cannot be spent here.
    if not secrets.compare_digest(auth_data[:32], hashlib.sha256(rp.rp_id.encode()).digest()):
        raise WebAuthnError("this credential belongs to a different site")

    flags = auth_data[32]
    if not flags & FLAG_USER_PRESENT:
        raise WebAuthnError("no one confirmed that unlock")
    # The bit that makes this Hello rather than "a key was plugged in". Without
    # it the ceremony proves possession of the laptop and nothing else, which
    # is exactly what the panel is trying not to rely on.
    if not flags & FLAG_USER_VERIFIED:
        raise WebAuthnError("Windows Hello did not verify you — try the fingerprint again")

    sign_count = int.from_bytes(auth_data[33:37], "big")
    # A counter that goes backwards means two authenticators are answering for
    # one credential, i.e. a clone. Hello reports a constant 0, which the spec
    # allows and which this has to tolerate: only a counter that was moving and
    # then regressed is evidence of anything.
    if stored_sign_count and sign_count and sign_count <= stored_sign_count:
        raise WebAuthnError("this credential looks duplicated; re-enrol it")

    signed = auth_data + hashlib.sha256(client_data).digest()
    key = serialization.load_der_public_key(public_key)
    try:
        if alg == ES256:
            key.verify(b64url_decode(signature), signed, ec.ECDSA(hashes.SHA256()))
        elif alg == RS256:
            key.verify(b64url_decode(signature), signed, padding.PKCS1v15(), hashes.SHA256())
        elif alg == EDDSA:
            key.verify(b64url_decode(signature), signed)
        else:
            raise WebAuthnError("unsupported signature type")
    except WebAuthnError:
        raise
    except Exception:
        # Every failure mode of `verify` -- bad signature, wrong key type,
        # truncated DER -- means the same thing to the caller.
        raise WebAuthnError("that did not verify")
    return sign_count


def creation_options(rp: RelyingParty, challenge: str, user_id: bytes, exclude: list[str]) -> dict:
    """`navigator.credentials.create()` options, as JSON-safe values.

    Built here rather than in JavaScript so that the challenge, the rp_id and
    the verification requirement are decided by the side that will check them.

    `platform` + `required` is the pair that means Windows Hello specifically:
    the built-in authenticator, and it must verify a human rather than merely
    be present. `residentKey: discouraged` keeps this off the limited on-TPM
    slots -- the credential is looked up by id from the database, so there is
    nothing to gain by storing it on the authenticator.
    """
    return {
        "rp": {"id": rp.rp_id, "name": rp.name},
        "user": {"id": b64url_encode(user_id), "name": "portfolio", "displayName": "Portfolio"},
        "challenge": challenge,
        "pubKeyCredParams": [{"type": "public-key", "alg": alg} for alg in SUPPORTED_ALGS],
        "timeout": CHALLENGE_TTL_SECONDS * 1000,
        "attestation": "none",
        "excludeCredentials": [{"type": "public-key", "id": cid} for cid in exclude],
        "authenticatorSelection": {
            "authenticatorAttachment": "platform",
            "userVerification": "required",
            "residentKey": "discouraged",
        },
    }


def request_options(rp: RelyingParty, challenge: str, allow: list[str]) -> dict[str, Any]:
    """`navigator.credentials.get()` options."""
    return {
        "rpId": rp.rp_id,
        "challenge": challenge,
        "timeout": CHALLENGE_TTL_SECONDS * 1000,
        "userVerification": "required",
        "allowCredentials": [{"type": "public-key", "id": cid} for cid in allow],
    }
