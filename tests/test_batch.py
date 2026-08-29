"""The overnight batch: context, validation, and serving.

The batch replaces a $0.15-0.80 API call with a file. That makes two things
worth testing hard: that a *bad* file degrades to the old behaviour instead of
taking the math panel down, and that a *good* one is never quietly ignored --
a batch that silently fails to load costs real money the next morning.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from dashboard import batch, content, llm

from .conftest import TODAY as SUNDAY

TODAY = SUNDAY + dt.timedelta(days=1)
"""A Monday.

Everything in this file is about the *compute* day's batch — a ladder of rungs,
a rating, a do-not-repeat list. `conftest.TODAY` is a Sunday, and Sunday is the
reading day now: it draws no problem and its context carries a study plan
instead of a ladder. Shadowing the name keeps every assertion below reading as
it did, on a day that still asks the question they are asking about.
"""


def rung(source: str, tier: str = "aime-late", **kw) -> dict:
    return {
        "source": source,
        "tier": tier,
        "statement": r"Find $\dfrac{n}{100}$.",
        "answer": "196",
        "hints": ["a", "b", "c"],
        "verified_url": "https://artofproblemsolving.com/wiki/index.php/x",
        **kw,
    }


def write_batch(settings, date: dt.date, *problems: dict, **envelope) -> None:
    path = settings.path("batches") / f"{date.isoformat()}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {"date": date.isoformat(), "problems": list(problems), **envelope}
    path.write_text(json.dumps(body), encoding="utf-8")


@pytest.fixture
def no_api(monkeypatch):
    """Any API call during these tests is the failure being tested for."""

    def never(*a, **kw):
        raise AssertionError("a batch was available; nothing should have been paid for")

    monkeypatch.setattr(llm, "call", never)


# --- serving ---------------------------------------------------------------


def test_a_batch_is_served_before_the_pool_and_before_the_api(settings, repo, no_api):
    write_batch(settings, TODAY, rung("Putnam 1998 B3"))
    served = content.next_problem(settings, repo, TODAY)
    assert served.source == "Putnam 1998 B3"
    assert served.model == content.BATCHED


def test_the_rung_nearest_the_current_rating_wins(settings, repo, no_api):
    """The 3am task cannot know the rating, so it proposes a ladder and the
    dashboard picks. This is the whole reason the format holds a list."""
    tiers = sorted(settings.math.anchors, key=lambda t: settings.math.anchors[t])
    easiest, hardest = tiers[0], tiers[-1]
    write_batch(
        settings,
        TODAY,
        rung("Easy One", tier=easiest),
        rung("Hard One", tier=hardest),
    )

    rating = repo.current_rating("math")
    closer = (
        "Easy One"
        if abs(settings.math.anchors[easiest] - rating)
        <= abs(settings.math.anchors[hardest] - rating)
        else "Hard One"
    )
    assert content.next_problem(settings, repo, TODAY).source == closer


def test_a_rung_you_have_already_seen_falls_through_to_the_next(settings, repo, problem, no_api):
    """`source` is UNIQUE, so a repeat would raise on store. The ladder is
    what keeps that from costing a paid regeneration."""
    write_batch(settings, TODAY, rung(problem.source), rung("AIME 2020 II Problem 6"))
    assert content.next_problem(settings, repo, TODAY).source == "AIME 2020 II Problem 6"


def test_yesterdays_batch_is_not_served_today(settings, repo):
    """Batches are keyed by the date the browser supplies. A stale file must
    not become today's problem."""
    write_batch(settings, TODAY - dt.timedelta(days=1), rung("Yesterday P1"))
    assert content._from_batch(settings, TODAY, set(), 1500) is None


# --- degrading -------------------------------------------------------------


def test_no_batch_is_not_an_error(settings):
    assert content._from_batch(settings, TODAY, set(), 1500) is None


def test_malformed_json_falls_through_instead_of_raising(settings):
    path = settings.path("batches") / f"{TODAY.isoformat()}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ this is not json", encoding="utf-8")
    assert content._from_batch(settings, TODAY, set(), 1500) is None


def test_a_hallucinated_field_is_rejected_not_ignored(settings):
    """`extra="forbid"` on the batch models. An invented key is the cheapest
    signal that the prompt and the schema have drifted apart."""
    write_batch(settings, TODAY, rung("Putnam 1998 B3", difficulty="medium"))
    assert content._from_batch(settings, TODAY, set(), 1500) is None


def test_every_rung_already_seen_falls_through(settings, problem):
    write_batch(settings, TODAY, rung(problem.source))
    assert content._from_batch(settings, TODAY, {problem.source}, 1500) is None


def test_a_bad_batch_still_lets_the_pool_serve(settings, repo, no_api):
    """The degradation that matters: a broken file must cost nothing."""
    path = settings.path("batches") / f"{TODAY.isoformat()}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("garbage", encoding="utf-8")
    settings.path("problems").write_text(
        json.dumps([rung("Pool P1") | {"hint1": "a", "hint2": "b", "hint3": "c"}]),
        encoding="utf-8",
    )
    assert content.next_problem(settings, repo, TODAY).source == "Pool P1"


# --- validation ------------------------------------------------------------


def test_validate_accepts_a_good_batch(settings):
    raw = json.dumps({"date": TODAY.isoformat(), "problems": [rung("Putnam 1998 B3")]})
    parsed, errors = batch.validate(settings, raw)
    assert errors == []
    assert parsed is not None and parsed.problems[0].source == "Putnam 1998 B3"


def test_validate_survives_prose_around_the_json(settings):
    """The generator is a model writing a file unattended. It will preface."""
    raw = "Here is today's batch:\n" + json.dumps(
        {"date": TODAY.isoformat(), "problems": [rung("Putnam 1998 B3")]}
    )
    parsed, errors = batch.validate(settings, raw)
    assert errors == []
    assert parsed is not None


def test_validate_repairs_singly_escaped_latex(settings):
    r"""Same trap as the API path: `\frac` is a legal JSON escape that parses
    into a formfeed. Reuses `extract_json`, so it is repaired here too."""
    raw = (
        f'{{"date": "{TODAY.isoformat()}", "problems": [{{"source": "Putnam 1998 B3", '
        '"tier": "aime-late", '
        r'"statement": "Find $\frac{n}{2}$.", "answer": "1", "hints": ["a","b","c"], '
        '"verified_url": "https://artofproblemsolving.com/x"}]}'
    )
    parsed, errors = batch.validate(settings, raw)
    assert errors == []
    assert parsed is not None
    assert r"\frac" in parsed.problems[0].statement


def test_validate_flags_an_unknown_tier(settings):
    raw = json.dumps(
        {"date": TODAY.isoformat(), "problems": [rung("AMC 2019 P5", tier="impossible")]}
    )
    _, errors = batch.validate(settings, raw)
    assert any("unknown tier" in e for e in errors)


def test_validate_flags_a_missing_verification_url(settings):
    body = rung("AMC 2019 P5")
    del body["verified_url"]
    raw = json.dumps({"date": TODAY.isoformat(), "problems": [body]})
    _, errors = batch.validate(settings, raw)
    assert any("verified_url" in e for e in errors)


def test_validate_flags_a_repeated_rung(settings):
    """A ladder whose rungs are the same problem is not a ladder."""
    raw = json.dumps(
        {"date": TODAY.isoformat(), "problems": [rung("Same"), rung("Same", tier="amc")]}
    )
    _, errors = batch.validate(settings, raw)
    assert any("repeats a source" in e for e in errors)


def test_validate_rejects_unparseable_input(settings):
    parsed, errors = batch.validate(settings, "the model wrote an apology instead")
    assert parsed is None
    assert errors


# --- context ---------------------------------------------------------------


def test_context_carries_what_the_prompt_must_not_hardcode(settings, repo):
    """The point of `--context`: the task prompt stays static while the
    rating and the do-not-repeat list move underneath it."""
    ctx = batch.context(settings, repo, TODAY)
    assert ctx["date"] == TODAY.isoformat()
    assert ctx["topic"] == settings.math.topic_for(TODAY)
    assert ctx["rating"] == repo.current_rating("math")
    assert ctx["anchors"] == settings.math.anchors
    assert TODAY.isoformat() in ctx["output_path"]


def test_context_names_seen_problems_so_they_are_not_regenerated(settings, repo, problem):
    assert problem.source in batch.context(settings, repo, TODAY)["do_not_use"]


def test_the_do_not_repeat_list_is_windowed(settings, repo, monkeypatch):
    """The full list grows without bound and the prompt is charged by the
    token -- the same reason the API path windows it."""
    monkeypatch.setattr(repo, "seen_sources", lambda: [f"P{i:04d}" for i in range(500)])
    assert len(batch.context(settings, repo, TODAY)["do_not_use"]) == content.SEEN_WINDOW


def test_the_snapshot_never_touches_the_original(settings, repo, db):
    """The 3am job reads a copy, never the live file. Anything else risks a
    background task disturbing real data while the server has it open."""
    import tempfile
    from pathlib import Path

    original = settings.path("db")
    before = original.read_bytes()
    with batch.snapshot(original) as snap:
        assert snap.current_rating("math") == repo.current_rating("math")
    assert original.read_bytes() == before
    leaked = list(Path(tempfile.gettempdir()).glob("dashboard-batch-*"))
    assert not leaked, f"the snapshot left a copy behind: {leaked}"


def test_a_missing_database_is_a_clear_failure(settings, tmp_path):
    with pytest.raises(FileNotFoundError), batch.snapshot(tmp_path / "nope.db"):
        pass


def test_context_degrades_rather_than_dying_without_a_database(settings):
    """On a 9p mount SQLite can fail to open a perfectly readable file. The
    generator still gets the topic and the tier table, which is most of the
    instruction, and is told what it is missing."""
    ctx = batch.context_without_db(settings, TODAY, "unable to open database file")
    assert ctx["topic"] == settings.math.topic_for(TODAY)
    assert ctx["rating"] is None
    assert ctx["do_not_use"] == []
    assert len(ctx["suggested_tiers"]) == batch.LADDER
    assert "could not read the database" in ctx["degraded"]


def test_suggested_tiers_bracket_the_rating(settings):
    tiers = batch.suggested_tiers(settings, 1528)
    assert len(tiers) == batch.LADDER
    anchors = [settings.math.anchors[t] for t in tiers]
    assert anchors == sorted(anchors), "easiest first"


def test_suggested_tiers_clamp_at_both_ends(settings):
    """A rating below the easiest anchor or above the hardest still gets a
    full ladder rather than a truncated one."""
    for rating in (100, 4000):
        assert len(batch.suggested_tiers(settings, rating)) == batch.LADDER
