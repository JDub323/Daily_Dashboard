"""The gate, over HTTP.

The claim these tests exist to check is narrow and worth stating: *no request
without a verified unlock can obtain a number from the positions file.* Every
other property of this feature is a convenience. This one is the feature.

So the tests below drive the real endpoints with a real software authenticator
and confirm both directions -- a genuine ceremony gets the data, and every way
of arriving without one does not.
"""

from __future__ import annotations

import base64

import pytest

from dashboard import webauthn as wa
from tests.test_portfolio import TRIMMED
from tests.test_webauthn import Authenticator, b64

DATE = "2026-08-14"


@pytest.fixture
def cfg(settings, tmp_path):
    """Settings with a fabricated positions file in place."""
    path = tmp_path / "positions.csv"
    path.write_text(TRIMMED, encoding="utf-8")
    return settings.model_copy(
        update={"paths": settings.paths.model_copy(update={"positions": str(path)})}
    )


@pytest.fixture
def client(cfg):
    from fastapi.testclient import TestClient

    from dashboard.server import create_app

    with TestClient(create_app(cfg), raise_server_exceptions=False) as c:
        # TestClient's default base_url is http://testserver, which is not the
        # origin the relying party accepts. Every request here therefore
        # presents the origin the real browser would.
        yield c


@pytest.fixture
def authenticator():
    return Authenticator(wa.ES256)


def enroll(client, auth, token=None):
    """Run the registration ceremony, as the panel does."""
    headers = {"authorization": f"Bearer {token}"} if token else {}
    options = client.post("/api/portfolio/enroll/begin", json={}, headers=headers).json()
    body = {
        "date": DATE,
        "credential_id": "test-credential",
        "client_data_json": auth.client_data(
            options["challenge"], type_="webauthn.create", origin="http://localhost:8502"
        ),
        "public_key": b64(auth.public_key),
        "alg": auth.alg,
    }
    return client.post("/api/portfolio/enroll/finish", json=body, headers=headers)


def unlock(client, auth):
    options = client.post("/api/portfolio/unlock/begin", json={}).json()
    assertion = auth.assertion(options["challenge"])
    return client.post(
        "/api/portfolio/unlock/finish",
        json={"date": DATE, "credential_id": "test-credential", **assertion},
    )


# --- the gate ---------------------------------------------------------------


def test_holdings_are_refused_without_a_token(client):
    assert client.get("/api/portfolio").status_code == 403


def test_holdings_are_refused_with_a_guessed_token(client):
    response = client.get("/api/portfolio", headers={"authorization": "Bearer hunter2"})
    assert response.status_code == 403


def test_status_leaks_nothing_while_locked(client, authenticator):
    """The one unauthenticated endpoint, and so the one that must stay empty.

    It says whether a credential exists and where to load the page. It must
    never grow a symbol, a total, or a count of positions -- if it does, the
    locked panel is drawing itself from data the lock was supposed to cover.
    """
    enroll(client, authenticator)
    body = client.get("/api/portfolio/status").json()
    assert body == {
        "enabled": True,
        "enrolled": True,
        "unlocked": False,
        "expires_in": 0,
        "present": True,
        "origin": "http://localhost:8502",
    }
    # Nothing from the file, by any name.
    assert "AAPL" not in str(body)


def test_the_full_ceremony_returns_the_holdings(client, authenticator):
    assert enroll(client, authenticator).status_code == 200
    granted = unlock(client, authenticator)
    assert granted.status_code == 200
    token = granted.json()["token"]

    held = client.get("/api/portfolio", headers={"authorization": f"Bearer {token}"})
    assert held.status_code == 200
    body = held.json()
    assert [p["symbol"] for p in body["positions"]] == ["AAPL", "NVDA", "BRK.B", "SPAXX**"]
    assert body["totals"]["value"] == 5000.0
    assert 0 < body["expires_in"] <= 300


def test_locking_revokes_the_token_immediately(client, authenticator):
    enroll(client, authenticator)
    token = unlock(client, authenticator).json()["token"]
    headers = {"authorization": f"Bearer {token}"}

    assert client.get("/api/portfolio", headers=headers).status_code == 200
    assert client.post("/api/portfolio/lock", headers=headers).status_code == 200
    assert client.get("/api/portfolio", headers=headers).status_code == 403


def test_an_expired_token_stops_serving_the_file(client, cfg, authenticator):
    """The countdown in the panel is a display. This is the enforcement.

    A tab left open on a desk keeps rendering its timer; what actually matters
    is that the endpoint stops answering, which it does from the server's own
    clock.
    """
    enroll(client, authenticator)
    token = unlock(client, authenticator).json()["token"]
    headers = {"authorization": f"Bearer {token}"}
    assert client.get("/api/portfolio", headers=headers).status_code == 200

    client.app.state.unlocks._tokens[token] = 0  # expire it where the server keeps it
    assert client.get("/api/portfolio", headers=headers).status_code == 403


def test_an_unlock_cannot_be_replayed(client, authenticator):
    """The captured request is a complete, correctly signed assertion.

    It fails only because its challenge has already been spent -- which is what
    single-use nonces are for, and the reason taking the challenge from the
    client's own data is safe.
    """
    enroll(client, authenticator)
    options = client.post("/api/portfolio/unlock/begin", json={}).json()
    body = {
        "date": DATE,
        "credential_id": "test-credential",
        **authenticator.assertion(options["challenge"]),
    }
    assert client.post("/api/portfolio/unlock/finish", json=body).status_code == 200
    replay = client.post("/api/portfolio/unlock/finish", json=body)
    assert replay.status_code == 400
    assert "expired" in replay.json()["error"]


def test_an_assertion_from_another_key_does_not_unlock(client, authenticator):
    enroll(client, authenticator)
    options = client.post("/api/portfolio/unlock/begin", json={}).json()
    impostor = Authenticator(wa.ES256)
    response = client.post(
        "/api/portfolio/unlock/finish",
        json={
            "date": DATE,
            "credential_id": "test-credential",
            **impostor.assertion(options["challenge"]),
        },
    )
    assert response.status_code == 400
    assert client.get("/api/portfolio").status_code == 403


def test_an_unenrolled_credential_id_is_refused(client, authenticator):
    enroll(client, authenticator)
    options = client.post("/api/portfolio/unlock/begin", json={}).json()
    response = client.post(
        "/api/portfolio/unlock/finish",
        json={
            "date": DATE,
            "credential_id": "some-other-credential",
            **authenticator.assertion(options["challenge"]),
        },
    )
    assert response.status_code == 400


# --- enrolment --------------------------------------------------------------


def test_the_first_enrolment_needs_no_token(client, authenticator):
    """Trust on first use, and the reason it is acceptable.

    Any secret the server could demand here would have to live on the same disk
    as the CSV, and anyone who can read that disk can read the CSV directly
    without going near this panel. So the first enrolment is open, and the
    panel says to do it immediately.
    """
    assert enroll(client, authenticator).status_code == 200


def test_a_second_enrolment_requires_an_unlock(client, authenticator):
    """Once one credential exists, adding another is a privileged act.

    Without this, the lock would be decorative: anyone at the machine could
    enrol their own Hello credential and open the panel with it.
    """
    enroll(client, authenticator)
    # Refused at `begin`, so a second authenticator never even gets a challenge
    # to sign -- and `finish` refuses independently, so a challenge captured
    # some other way would not help either.
    assert client.post("/api/portfolio/enroll/begin", json={}).status_code == 403
    second = Authenticator(wa.ES256)
    response = client.post(
        "/api/portfolio/enroll/finish",
        json={
            "date": DATE,
            "credential_id": "second-credential",
            "client_data_json": second.client_data(
                "borrowed-challenge", type_="webauthn.create", origin="http://localhost:8502"
            ),
            "public_key": b64(second.public_key),
            "alg": second.alg,
        },
    )
    assert response.status_code == 403


def test_a_second_enrolment_succeeds_with_an_unlock(client, authenticator):
    enroll(client, authenticator)
    token = unlock(client, authenticator).json()["token"]
    assert client.post(
        "/api/portfolio/enroll/begin", json={}, headers={"authorization": f"Bearer {token}"}
    ).status_code == 200


def test_unlocking_before_enrolling_is_a_404(client):
    assert client.post("/api/portfolio/unlock/begin", json={}).status_code == 404


# --- switched off -----------------------------------------------------------


def test_a_disabled_panel_serves_nothing(settings, tmp_path):
    from fastapi.testclient import TestClient

    from dashboard.server import create_app

    path = tmp_path / "positions.csv"
    path.write_text(TRIMMED, encoding="utf-8")
    cfg = settings.model_copy(
        update={
            "paths": settings.paths.model_copy(update={"positions": str(path)}),
            "portfolio": settings.portfolio.model_copy(update={"enabled": False}),
        }
    )
    with TestClient(create_app(cfg), raise_server_exceptions=False) as client:
        assert client.get("/api/portfolio/status").json()["enabled"] is False
        # 404 rather than 403: the switch is checked before the token, because
        # a feature that is off has no lock to be on the wrong side of.
        assert client.get("/api/portfolio").status_code == 404
        assert client.post("/api/portfolio/enroll/begin", json={}).status_code == 404
        assert client.post("/api/portfolio/unlock/begin", json={}).status_code == 404


# --- the file is not reachable any other way --------------------------------


def test_the_positions_file_is_not_served_as_a_static_asset(client):
    """The static mount is a catch-all at `/`.

    `config.Settings` refuses at startup to let the positions file live under
    it; this is the same guarantee checked from the outside, against the paths
    someone would actually try.
    """
    for path in ("/positions.csv", "/../positions.csv", "/web/positions.csv"):
        assert client.get(path).status_code in (403, 404)


def test_a_bearer_token_is_not_a_cookie(client, authenticator):
    """Nothing about the unlock is stored where the browser would resend it.

    A cookie would ride along on every request to this origin -- including the
    static mount, which puts the token in the access log and makes it reachable
    from any page that can provoke a request here.
    """
    enroll(client, authenticator)
    unlock(client, authenticator)
    assert not client.cookies
    # And the fresh session, holding no header, is still locked out.
    assert client.get("/api/portfolio").status_code == 403


def test_warnings_never_carry_a_cell_value(client, authenticator, cfg):
    """An error string is a place numbers escape to. Checked at the boundary."""
    cfg.path("positions").write_text(
        TRIMMED.replace("$2200.00", "twenty-two hundred"), encoding="utf-8"
    )
    enroll(client, authenticator)
    token = unlock(client, authenticator).json()["token"]
    body = client.get("/api/portfolio", headers={"authorization": f"Bearer {token}"}).json()
    assert body["warnings"] == ["line 2 (AAPL): could not read value"]
    assert "twenty-two hundred" not in str(body["warnings"])


def test_base64url_round_trips_without_padding():
    raw = bytes(range(64))
    assert wa.b64url_decode(wa.b64url_encode(raw)) == raw
    assert "=" not in wa.b64url_encode(raw)
    # And the padded form a stricter client might send is still accepted.
    assert wa.b64url_decode(base64.urlsafe_b64encode(raw).decode()) == raw
