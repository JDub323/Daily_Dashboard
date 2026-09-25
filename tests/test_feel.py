"""The feel log: the row, the pings, and what came before a feeling.

Everything here is in-memory or on the temp database. Nothing pushes to a real
phone: the one path that would (`feel.send_all`) is driven with `webpush`
stubbed, and the scheduler is driven by calling `feel.tick` with a clock.
"""

from __future__ import annotations

import base64
import datetime as dt
import random
import sqlite3

import pytest
from pydantic import ValidationError

from dashboard import feel, health
from dashboard.config import FeelSettings, FeelWindow
from dashboard.models import api
from dashboard.models import domain as d
from dashboard.models.common import InputState, SessionKind, Symptom, Tired

from .conftest import TODAY

DATE = TODAY.isoformat()


def row(hour: int = 17, energy: int = 3, mood: int = 3, *, minute: int | None = None,
        days_ago: int = 0, **words) -> d.FeelLog:
    return d.FeelLog(
        id=hour * 100 + days_ago, date=TODAY - dt.timedelta(days=days_ago), hour=hour,
        minute=minute, energy=energy, mood=mood, **words,
    )


def request(**fields) -> api.FeelRequest:
    return api.FeelRequest(**{"date": TODAY, "hour": 17, "energy": 3, "mood": 3, **fields})


# --- the request ------------------------------------------------------------


def test_a_kind_of_tired_needs_a_tired_answer():
    """The phone prunes the kinds as the scale moves, so a body that disagrees
    is a client bug -- refused, not repaired, so somebody finds it."""
    with pytest.raises(ValidationError, match="energy at 2 or under"):
        request(energy=3, tired=["malaise"])
    assert request(energy=2, tired=["malaise"]).tired == [Tired.malaise]


def test_a_kind_of_low_needs_a_low_answer():
    with pytest.raises(ValidationError, match="mood at 2 or under"):
        request(mood=4, low=["down"])


def test_words_come_back_in_one_order_whoever_sent_them():
    body = request(energy=1, tired=["flat", "sleepy", "flat"],
                   symptoms={"headache": 1, "sore_throat": 2})
    assert body.tired == [Tired.sleepy, Tired.flat]
    assert list(body.symptoms) == [Symptom.sore_throat, Symptom.headache]


def test_a_grade_is_one_to_three():
    with pytest.raises(ValidationError):
        request(symptoms={"cough": 4})
    with pytest.raises(ValidationError):
        request(symptoms={"cough": 0})


def test_a_blank_other_box_is_no_note():
    assert request(note="   ").note is None


# --- the table ------------------------------------------------------------------


def test_a_feel_round_trips_with_its_words(repo):
    written = repo.add_feel(
        TODAY, 17, 2, 2, tired=[Tired.malaise, Tired.drained], low=["down"],
        symptoms={Symptom.headache: 1, Symptom.thirsty: 2}, note="weird taste", prompted=True,
    )
    [read] = repo.feel_on(TODAY)
    assert read == written
    assert read.tired == [Tired.drained, Tired.malaise]
    assert read.symptoms == {Symptom.headache: 1, Symptom.thirsty: 2}
    assert read.words == ["drained", "malaise", "down", "headache", "thirsty", "other"]
    assert read.prompted is True


def test_deleting_a_feel_takes_its_words_with_it(repo):
    entry = repo.add_feel(TODAY, 9, 1, 3, tired=[Tired.sleepy], symptoms={Symptom.cough: 1})
    assert repo.delete_feel(entry.id)
    assert repo.conn.execute("SELECT count(*) FROM feel_tag").fetchone()[0] == 0


def test_the_table_keeps_severity_to_the_symptoms(repo):
    """A kind carries no grade and a symptom always does -- the CHECK, for
    anything that writes to the file without coming through a request."""
    entry = repo.add_feel(TODAY, 9, 3, 3)
    with pytest.raises(sqlite3.IntegrityError):
        repo.conn.execute("INSERT INTO feel_tag VALUES (?, 'malaise', 2)", (entry.id,))
    with pytest.raises(sqlite3.IntegrityError):
        repo.conn.execute("INSERT INTO feel_tag VALUES (?, 'cough', NULL)", (entry.id,))


def test_a_fine_row_is_not_flagged_and_a_low_one_is():
    assert not row(energy=3, mood=3).flagged
    assert row(energy=2).flagged
    assert row(symptoms={Symptom.hurt: 1}).flagged
    assert row(note="odd").flagged


# --- the route --------------------------------------------------------------------


def test_a_feel_comes_back_in_the_rebuilt_panel(client):
    panel = client.post("/api/feel", json={
        "date": DATE, "hour": 17, "energy": 2, "mood": 3, "tired": ["malaise"],
        "symptoms": {"headache": 2},
    }).json()
    [entry] = panel["feel_today"]
    assert (entry["energy"], entry["tired"], entry["symptoms"]) == (2, ["malaise"], {"headache": 2})
    assert panel["feel"]["lookback"]["entry"]["id"] == entry["id"]


def test_a_kind_against_its_scale_is_a_422(client):
    response = client.post("/api/feel", json={
        "date": DATE, "hour": 17, "energy": 4, "mood": 3, "tired": ["sleepy"],
    })
    assert response.status_code == 422


def test_deleting_a_feel_removes_it(client):
    panel = client.post("/api/feel", json={"date": DATE, "hour": 9, "energy": 3, "mood": 3}).json()
    entry_id = panel["feel_today"][0]["id"]
    panel = client.post("/api/feel/delete", json={"date": DATE, "entry_id": entry_id}).json()
    assert panel["feel_today"] == []


# --- recovery --------------------------------------------------------------------


def test_a_feel_is_recoverys_seventh_input():
    score = health.recovery_score([], [], [], [], [], [], TODAY, [row(energy=2, mood=2)])
    [feel_input] = [i for i in score.inputs if i.key == "feel"]
    assert feel_input.state is InputState.scored
    assert feel_input.value == 25.0
    assert feel_input.reading == "energy 2 · mood 2 at 17:00"


def test_no_feel_today_is_not_logged_rather_than_zero():
    score = health.recovery_score([], [], [], [], [], [], TODAY, [row(days_ago=1)])
    [feel_input] = [i for i in score.inputs if i.key == "feel"]
    assert feel_input.state is InputState.not_logged
    assert feel_input.value is None


def test_after_a_fortnight_feel_is_scored_against_your_own_normal():
    """A 3-and-3 is only average if that is what your days are: against a
    month of 4s, it is a bad day."""
    history = [row(energy=4, mood=4 if n % 2 else 3, days_ago=n) for n in range(1, 20)]
    today = [row(energy=3, mood=3)]
    # Young: as answered, and a 3-and-3 is the middle of the scale.
    assert health.feel_score(today, TODAY) == 50.0
    assert health.feel_score(history[:10] + today, TODAY) == 50.0
    # A fortnight of better days behind it, and it is well under your normal.
    assert health.feel_score(history + today, TODAY) < 50.0


# --- the pings ----------------------------------------------------------------------


WINDOWS = FeelSettings().windows
MIDDAY, EVENING = (w.span for w in WINDOWS)


def test_an_exact_feel_answers_its_window_and_its_grace():
    start, end = EVENING
    assert feel.answers(row(hour=17, minute=0), start, end)
    assert feel.answers(row(hour=20, minute=45), start, end)
    assert not feel.answers(row(hour=21, minute=5), start, end)
    assert not feel.answers(row(hour=16, minute=59), start, end)


def test_a_remembered_hour_answers_if_any_minute_of_it_does():
    """The 8pm row logged against a 7:58 ping is an answer."""
    start, end = EVENING
    assert feel.answers(row(hour=20), start, end)
    assert feel.answers(row(hour=16), *MIDDAY) is False
    assert feel.answers(row(hour=11), *MIDDAY)


def test_a_drawn_minute_leaves_time_to_answer():
    rng = random.Random(7)
    for _ in range(200):
        for due, window in zip(feel.draw_dues(WINDOWS, rng), WINDOWS, strict=True):
            start, end = window.span
            at = int(due[:2]) * 60 + int(due[3:])
            assert start <= at <= end - feel.ANSWER_LEAD_MIN


def test_a_ping_reads_answered_sent_waiting_or_missed():
    drawn = {0: ("12:10", "12:10"), 1: ("18:40", None)}
    at_six = feel.ping_states(WINDOWS, drawn, [], 18 * 60)
    assert [p.state for p in at_six] == ["missed", "waiting"]
    at_seven = feel.ping_states(WINDOWS, {**drawn, 1: ("18:40", "18:41")}, [], 19 * 60)
    assert at_seven[1].state == "sent"
    answered = feel.ping_states(WINDOWS, drawn, [row(hour=13)], 18 * 60)
    assert answered[0].state == "answered"
    over = feel.ping_states(WINDOWS, drawn, [], None)
    assert [p.state for p in over] == ["missed", "missed"]


def test_the_drawn_minute_never_reaches_the_panel():
    """A ping is only a surprise if nobody can read when it is due."""
    [first, _] = feel.ping_states(WINDOWS, {0: ("12:10", None)}, [], 9 * 60)
    assert "12:10" not in first.model_dump_json()


def pings_on(settings):
    return settings.model_copy(update={"feel": settings.feel.model_copy(update={"pings": True})})


def test_a_tick_sends_each_ping_once_at_its_minute(settings, repo):
    cfg = pings_on(settings)
    sent: list[dict] = []

    def send(_cfg, _repo, body):
        sent.append(body)
        return 1

    repo.draw_pings(TODAY, ["12:00", "17:30"])
    at = dt.datetime.combine(TODAY, dt.time(11, 59))
    assert feel.tick(cfg, repo, at, send) == 0
    assert feel.tick(cfg, repo, at.replace(hour=12, minute=0), send) == 1
    assert feel.tick(cfg, repo, at.replace(hour=12, minute=5), send) == 0
    assert [body["tag"] for body in sent] == [f"feel-{DATE}-0"]
    assert repo.feel_pings(TODAY)[0] == ("12:00", "12:00")


def test_a_ping_already_answered_is_never_sent(settings, repo):
    """You said how you are; being asked again is the nag this is built not to be."""
    cfg = pings_on(settings)
    repo.draw_pings(TODAY, ["12:30", "17:30"])
    repo.add_feel(TODAY, 12, 3, 3, minute=0)
    at = dt.datetime.combine(TODAY, dt.time(12, 40))
    assert feel.tick(cfg, repo, at, lambda *_: pytest.fail("pinged")) == 0


def test_a_restart_keeps_the_morning_draw(settings, repo):
    cfg = pings_on(settings)
    at = dt.datetime.combine(TODAY, dt.time(7, 0))
    feel.tick(cfg, repo, at, lambda *_: 0)
    first = repo.feel_pings(TODAY)
    repo.draw_pings(TODAY, ["11:30", "17:00"])
    feel.tick(cfg, repo, at, lambda *_: 0)
    assert repo.feel_pings(TODAY) == first


def test_a_late_laptop_sends_while_the_window_is_open_and_not_after(settings, repo):
    cfg = pings_on(settings)
    repo.draw_pings(TODAY, ["12:00", "17:30"])
    asleep_through = dt.datetime.combine(TODAY, dt.time(14, 50))
    assert feel.tick(cfg, repo, asleep_through, lambda *_: 0) == 0
    assert feel.tick(cfg, repo, asleep_through.replace(hour=19, minute=10), lambda *_: 0) == 1


def test_windows_must_be_in_order_and_apart():
    with pytest.raises(ValidationError):
        FeelSettings(windows=[
            FeelWindow(name="a", start="12:00", end="15:00"),
            FeelWindow(name="b", start="14:00", end="18:00"),
        ])
    with pytest.raises(ValidationError):
        FeelSettings(windows=[FeelWindow(name="a", start="12:00", end="12:10")])


def test_pings_off_leaves_the_day_without_them(settings, repo):
    panel = feel.panel(settings, repo, TODAY, feel.Context(), [], week=0, prev_week=0, rhr_run=0)
    assert panel.pings == []


# --- web push ------------------------------------------------------------------------


def test_the_key_is_made_once_and_handed_over_as_base64url(settings, repo):
    pytest.importorskip("py_vapid")
    cfg = pings_on(settings)
    first = feel.status(cfg, repo).public_key
    raw = base64.urlsafe_b64decode(first + "=" * (-len(first) % 4))
    assert len(raw) == 65 and raw[0] == 4  # an uncompressed P-256 point
    assert feel.status(cfg, repo).public_key == first


def test_pings_off_says_so_rather_than_handing_out_a_key(settings, repo):
    status = feel.status(settings, repo)
    assert status.public_key is None
    assert "off" in status.reason


def test_a_subscription_keeps_the_page_it_came_from(client, settings):
    body = {"endpoint": "https://web.push.apple.com/abc123", "expirationTime": None,
            "keys": {"p256dh": "B" * 87, "auth": "x" * 22}}
    reply = client.post(f"/api/push/subscribe?date={DATE}", json=body,
                        headers={"origin": "https://laptop.example.ts.net"})
    assert reply.status_code == 200
    assert reply.json()["subscriptions"] == 1


def test_a_phone_the_push_service_says_is_gone_is_dropped(settings, repo, monkeypatch):
    pywebpush = pytest.importorskip("pywebpush")
    cfg = pings_on(settings)
    repo.add_push_subscription("https://push.example/gone", "B" * 87, "x" * 22, None, TODAY)
    repo.add_push_subscription("https://push.example/here", "B" * 87, "x" * 22, None, TODAY)

    class Gone:
        status_code = 410

    def webpush(subscription_info, **_):
        if subscription_info["endpoint"].endswith("gone"):
            raise pywebpush.WebPushException("gone", response=Gone())

    monkeypatch.setattr(pywebpush, "webpush", webpush)
    assert feel.send_all(cfg, repo, {"title": "t"}) == 1
    assert [s["endpoint"] for s in repo.push_subscriptions()] == ["https://push.example/here"]


# --- what came before -----------------------------------------------------------------


def sessions(loads: dict[int, int]) -> list[d.TrainingSession]:
    """Sessions whose load is `rpe x minutes` = the given figure, days ago."""
    return [
        d.TrainingSession(id=n + 1, date=TODAY - dt.timedelta(days=n), kind=SessionKind.boxing,
                          rpe=10, minutes=load // 10)
        for n, load in loads.items() if load
    ]


def spread(center: float, days: int = 25) -> list[tuple[dt.date, float]]:
    """A series with a real spread around `center`, oldest first, ending
    yesterday -- today's reading is the caller's to add."""
    return [(TODAY - dt.timedelta(days=days - n), center + (n % 5 - 2)) for n in range(days)]


def two_hard_days() -> feel.Context:
    """The afternoon he asked for this: two heavy days, a resting heart rate
    that has not come down, and water not logged since the morning."""
    base = {n: 200 for n in range(3, 40)}
    ctx = feel.Context(
        sessions=sessions({**base, 1: 900, 2: 650}),
        rhr=[*spread(40.0), (TODAY - dt.timedelta(days=1), 45.0), (TODAY, 44.0)],
        drinks=[d.DrinkLog(id=1, date=TODAY, hour=10, amount=2)],
    )
    ctx.rhr = sorted(dict(ctx.rhr).items())
    ctx.load_window(TODAY)
    return ctx


def test_malaise_suspects_the_heart_and_the_load_first():
    ctx = two_hard_days()
    look = feel.lookback(ctx, [row(energy=2, tired=[Tired.malaise])])
    keys = look.suspects["malaise"]
    loud = [s.key for s in look.signals if s.key in keys and s.dev >= 1]
    assert {"rhr", "load2"} <= set(loud)
    assert [s.dev for s in look.signals] == sorted((s.dev for s in look.signals), reverse=True)


def test_a_dry_afternoon_is_gold_because_it_may_be_the_log():
    ctx = two_hard_days()
    look = feel.lookback(ctx, [row(symptoms={Symptom.thirsty: 1})])
    [fluid] = [s for s in look.signals if s.key == "fluid"]
    assert fluid.tone == "check"
    assert fluid.value == "nothing since 10:00"


def test_other_has_no_suspects_and_is_ranked_on_everything():
    look = feel.lookback(two_hard_days(), [row(note="metal taste")])
    assert "other" in look.flags
    assert "other" not in look.suspects


def test_a_fine_day_has_nothing_to_look_back_from():
    assert feel.lookback(two_hard_days(), [row(energy=4, mood=4)]) is None


def test_the_flag_line_names_a_cold_when_the_heart_agrees():
    look = feel.lookback(two_hard_days(), [row(energy=2, tired=[Tired.malaise])])
    assert "a cold" in feel.flag_line(look, rhr_run=2)
    assert "loudest" in feel.flag_line(look, rhr_run=0)


def test_the_landing_line_puts_the_week_beside_the_energy():
    assert feel.landing_line([row(energy=2)], 2193, 840) == (
        "this week is 2.6× last · energy 2/5 today → landing hard"
    )
    assert feel.landing_line([], 2193, 840) is None


def test_counts_wait_for_enough_flags():
    """One flag is a story; the counts start at `MIN_FLAGS`."""
    ctx = two_hard_days()
    few = [row(hour=9, days_ago=n, energy=2, tired=[Tired.malaise]) for n in range(1, 4)]
    today = [row(energy=2, tired=[Tired.malaise])]
    assert feel.lookback(ctx, today, few + today).counts == {}
    many = [row(hour=9, days_ago=n, energy=2, tired=[Tired.malaise]) for n in range(1, 12)]
    look = feel.lookback(ctx, today, many + today)
    assert look.flags["malaise"] == 12
    assert {c.key for c in look.counts["malaise"]} <= set(feel.SUSPECTS["malaise"])
