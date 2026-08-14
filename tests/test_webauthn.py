"""The portfolio lock.

These tests sign real assertions with real keys rather than stubbing the
verifier, because a mocked signature check would pass against a verifier that
does not check signatures -- which is the only failure in this file that
matters. `_assertion` below is a software authenticator: it builds the exact
byte layout Windows Hello produces and signs it with a key generated in the
test, so `verify_assertion` is exercised end to end.

Both algorithms are covered. Hello negotiates RS256 on most hardware and ES256
on some, and which one a given laptop picks is not something this project gets
to choose.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa

from dashboard import webauthn as wa

RP = wa.RelyingParty(rp_id="localhost", origins=frozenset({"http://localhost:8502"}))
TODAY = dt.date(2026, 8, 14)


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


class Authenticator:
    """A software stand-in for the TPM, in whichever algorithm is asked for."""

    def __init__(self, alg: int = wa.ES256) -> None:
        self.alg = alg
        if alg == wa.ES256:
            self.key = ec.generate_private_key(ec.SECP256R1())
        elif alg == wa.RS256:
            self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        else:
            raise AssertionError(f"no test authenticator for alg {alg}")

    @property
    def public_key(self) -> bytes:
        """SPKI DER -- what `getPublicKey()` hands the server."""
        return self.key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )

    def _sign(self, message: bytes) -> bytes:
        if self.alg == wa.ES256:
            return self.key.sign(message, ec.ECDSA(hashes.SHA256()))
        return self.key.sign(message, padding.PKCS1v15(), hashes.SHA256())

    def client_data(self, challenge: str, *, type_: str, origin: str) -> str:
        return b64(
            json.dumps({"type": type_, "challenge": challenge, "origin": origin}).encode()
        )

    def auth_data(self, *, rp_id: str = "localhost", flags: int = 0x05, count: int = 0) -> bytes:
        # rpIdHash(32) || flags(1) || signCount(4). 0x05 is UP|UV, which is what
        # a Hello prompt that actually verified someone produces.
        return hashlib.sha256(rp_id.encode()).digest() + bytes([flags]) + count.to_bytes(4, "big")

    def assertion(
        self, challenge: str, *, origin: str = "http://localhost:8502",
        rp_id: str = "localhost", flags: int = 0x05, count: int = 0,
    ) -> dict:
        client_data = self.client_data(challenge, type_="webauthn.get", origin=origin)
        auth_data = self.auth_data(rp_id=rp_id, flags=flags, count=count)
        signed = auth_data + hashlib.sha256(wa.b64url_decode(client_data)).digest()
        return {
            "client_data_json": client_data,
            "authenticator_data": b64(auth_data),
            "signature": b64(self._sign(signed)),
        }


def verify(auth: Authenticator, challenge: str, *, stored: int = 0, **kwargs) -> int:
    body = auth.assertion(challenge, **kwargs)
    return wa.verify_assertion(
        **body, public_key=auth.public_key, alg=auth.alg, challenge=challenge, rp=RP,
        stored_sign_count=stored,
    )


# --- signature verification -------------------------------------------------


@pytest.mark.parametrize("alg", [wa.ES256, wa.RS256])
def test_a_genuine_assertion_verifies(alg):
    auth = Authenticator(alg)
    assert verify(auth, "challenge-one") == 0


@pytest.mark.parametrize("alg", [wa.ES256, wa.RS256])
def test_a_signature_from_another_key_does_not(alg):
    """The test that makes every other test in this file mean something."""
    auth, impostor = Authenticator(alg), Authenticator(alg)
    body = impostor.assertion("challenge-one")
    with pytest.raises(wa.WebAuthnError):
        wa.verify_assertion(
            **body, public_key=auth.public_key, alg=auth.alg, challenge="challenge-one", rp=RP
        )


def test_tampered_authenticator_data_does_not_verify():
    auth = Authenticator()
    body = auth.assertion("challenge-one")
    # Flip the sign counter after signing.
    raw = bytearray(wa.b64url_decode(body["authenticator_data"]))
    raw[36] = 9
    body["authenticator_data"] = b64(bytes(raw))
    with pytest.raises(wa.WebAuthnError):
        wa.verify_assertion(
            **body, public_key=auth.public_key, alg=auth.alg, challenge="challenge-one", rp=RP
        )


# --- the checks that are not the signature ----------------------------------


def test_an_assertion_for_another_origin_is_refused():
    """What stops another page on the machine driving this ceremony."""
    auth = Authenticator()
    with pytest.raises(wa.WebAuthnError, match="only works at"):
        verify(auth, "challenge-one", origin="https://evil.example")


def test_an_assertion_for_another_rp_id_is_refused():
    auth = Authenticator()
    with pytest.raises(wa.WebAuthnError, match="different site"):
        verify(auth, "challenge-one", rp_id="example.com")


def test_a_challenge_mismatch_is_refused():
    auth = Authenticator()
    body = auth.assertion("some-other-challenge")
    with pytest.raises(wa.WebAuthnError, match="expired"):
        wa.verify_assertion(
            **body, public_key=auth.public_key, alg=auth.alg, challenge="challenge-one", rp=RP
        )


def test_a_registration_assertion_cannot_be_spent_as_an_unlock():
    """`type` is what separates the two ceremonies.

    Without this check, the assertion collected while enrolling could be
    replayed at the unlock endpoint.
    """
    auth = Authenticator()
    client_data = auth.client_data(
        "challenge-one", type_="webauthn.create", origin="http://localhost:8502"
    )
    auth_data = auth.auth_data()
    signed = auth_data + hashlib.sha256(wa.b64url_decode(client_data)).digest()
    with pytest.raises(wa.WebAuthnError):
        wa.verify_assertion(
            client_data_json=client_data,
            authenticator_data=b64(auth_data),
            signature=b64(auth._sign(signed)),
            public_key=auth.public_key, alg=auth.alg, challenge="challenge-one", rp=RP,
        )


def test_user_verification_is_required():
    """UP without UV is "a key was present", not "Hello checked who you are".

    This is the bit that makes the panel's claim true, so it is checked rather
    than assumed -- an authenticator that skipped the prompt would otherwise
    unlock the portfolio.
    """
    auth = Authenticator()
    with pytest.raises(wa.WebAuthnError, match="did not verify you"):
        verify(auth, "challenge-one", flags=0x01)


def test_user_presence_is_required():
    auth = Authenticator()
    with pytest.raises(wa.WebAuthnError, match="no one confirmed"):
        verify(auth, "challenge-one", flags=0x04)


def test_a_regressing_sign_counter_is_a_clone():
    auth = Authenticator()
    with pytest.raises(wa.WebAuthnError, match="duplicated"):
        verify(auth, "challenge-one", count=3, stored=7)


def test_a_counter_stuck_at_zero_is_fine():
    """Windows Hello reports a constant 0 and the spec allows it.

    Only a counter that was moving and then went backwards is evidence of
    anything, so this must not be treated as a clone -- it is the ordinary case
    on the hardware this feature was written for.
    """
    auth = Authenticator()
    assert verify(auth, "challenge-one", count=0, stored=0) == 0


def test_a_short_authenticator_data_is_rejected_before_the_crypto():
    auth = Authenticator()
    with pytest.raises(wa.WebAuthnError, match="malformed"):
        wa.verify_assertion(
            client_data_json=auth.client_data(
                "c", type_="webauthn.get", origin="http://localhost:8502"
            ),
            authenticator_data=b64(b"tooshort"),
            signature=b64(b"x"),
            public_key=auth.public_key, alg=auth.alg, challenge="c", rp=RP,
        )


# --- challenges and tokens --------------------------------------------------


def test_a_challenge_is_single_use():
    """Replay protection is here, not in the signature check.

    The server takes the challenge named by the client's own data, which is
    only safe because this store accepts a nonce once.
    """
    challenges = wa.Challenges()
    challenge = challenges.issue()
    challenges.take(challenge)
    with pytest.raises(wa.WebAuthnError, match="expired"):
        challenges.take(challenge)


def test_an_unissued_challenge_is_refused():
    with pytest.raises(wa.WebAuthnError):
        wa.Challenges().take("not-one-of-ours")


def test_challenges_expire():
    challenges = wa.Challenges(ttl=-1)
    with pytest.raises(wa.WebAuthnError):
        challenges.take(challenges.issue())


def test_a_token_is_valid_until_it_expires():
    unlocks = wa.Unlocks(300)
    token, ttl = unlocks.mint()
    assert ttl == 300
    assert unlocks.valid(token)
    assert 0 < unlocks.remaining(token) <= 300


def test_an_expired_token_stops_being_accepted():
    """The five minutes are enforced here, not by the browser's countdown."""
    unlocks = wa.Unlocks(-1)
    token, _ = unlocks.mint()
    assert not unlocks.valid(token)
    assert unlocks.remaining(token) == 0


def test_revoking_a_token_locks_immediately():
    unlocks = wa.Unlocks(300)
    token, _ = unlocks.mint()
    unlocks.revoke(token)
    assert not unlocks.valid(token)


def test_an_absent_or_empty_token_is_never_valid():
    unlocks = wa.Unlocks(300)
    unlocks.mint()
    assert not unlocks.valid(None)
    assert not unlocks.valid("")
    assert not unlocks.valid("guessed")


# --- the relying party ------------------------------------------------------


def test_a_loopback_host_becomes_localhost():
    """An IP literal cannot be an rp_id, so 127.0.0.1 has to map to localhost.

    This is the same fact that makes `server.main` open localhost rather than
    the bound address.
    """
    rp = wa.for_settings("127.0.0.1", 8502)
    assert rp.rp_id == "localhost"
    assert rp.origins == frozenset({"http://localhost:8502"})


def test_the_ip_origin_is_not_accepted():
    """Deliberately absent rather than an oversight.

    The browser refuses to produce an assertion for it anyway; accepting it
    server-side would only move the failure later and make it less clear.
    """
    assert not wa.for_settings("127.0.0.1", 8502).allows("http://127.0.0.1:8502")


def test_a_real_hostname_is_https():
    rp = wa.for_settings("dash.example", 443)
    assert rp.origins == frozenset({"https://dash.example"})


def test_an_explicit_rp_id_wins():
    assert wa.for_settings("127.0.0.1", 8502, "dash.example").rp_id == "dash.example"


# --- registration -----------------------------------------------------------


def test_registration_records_the_key():
    auth = Authenticator()
    registered = wa.verify_registration(
        credential_id="cred-1",
        client_data_json=auth.client_data(
            "challenge-one", type_="webauthn.create", origin="http://localhost:8502"
        ),
        public_key=b64(auth.public_key),
        alg=wa.ES256,
        challenge="challenge-one",
        rp=RP,
    )
    assert registered.public_key == auth.public_key
    assert registered.alg == wa.ES256


def test_registration_refuses_an_unverifiable_algorithm():
    auth = Authenticator()
    with pytest.raises(wa.WebAuthnError, match="signature type"):
        wa.verify_registration(
            credential_id="cred-1",
            client_data_json=auth.client_data(
                "challenge-one", type_="webauthn.create", origin="http://localhost:8502"
            ),
            public_key=b64(auth.public_key),
            alg=-65535,  # RS1, which nothing here will verify
            challenge="challenge-one",
            rp=RP,
        )


def test_registration_refuses_a_foreign_origin():
    auth = Authenticator()
    with pytest.raises(wa.WebAuthnError, match="only works at"):
        wa.verify_registration(
            credential_id="cred-1",
            client_data_json=auth.client_data(
                "challenge-one", type_="webauthn.create", origin="https://evil.example"
            ),
            public_key=b64(auth.public_key),
            alg=wa.ES256,
            challenge="challenge-one",
            rp=RP,
        )
