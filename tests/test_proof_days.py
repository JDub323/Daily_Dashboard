"""The week's shape: proof days, the two-day problem, and Sunday.

The old week asked for the same thing seven times and converged on one tier.
What is pinned here is the machinery that stops that: which day asks for what,
that a proof is priced differently from an answer, that the two-day problem is
genuinely two days rather than one with a delay, and that none of it touches
the ratings the calibrated days are pitched against.

`TODAY` from conftest is 2026-07-26, a Sunday -- which makes it the reading day
and every weekday below an offset from it.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from dashboard import batch, content, llm, scoring
from dashboard.db.repository import PROOF_AREA
from dashboard.models import domain as d
from dashboard.models.common import DayMode, MathOutcome, MathStatus, ProblemKind, Track

from .conftest import TODAY

SUNDAY = TODAY
MONDAY = TODAY + dt.timedelta(days=1)
WEDNESDAY = TODAY + dt.timedelta(days=3)
THURSDAY = TODAY + dt.timedelta(days=4)
FRIDAY = TODAY + dt.timedelta(days=5)
SATURDAY = TODAY + dt.timedelta(days=6)


@pytest.fixture
def proof(repo) -> d.MathProblem:
    """A two-hint proof problem out of the book, with a rubric."""
    return repo.add_problem(
        d.MathProblem(
            source="P&B 199",
            tier="putnam-early",
            anchor=1750,
            statement=r"Prove that $x^3 - 3x + 1$ is irreducible over $\mathbb{Q}$.",
            kind=ProblemKind.proof,
            answer="Rational root test, then Gauss.",
            hints=["what would a rational root have to be", "reduce mod 2"],
            rubric=["ruled out rational roots", "handled the quadratic factor case"],
            location="2.2.7 Irreducible Polynomials, p. 80",
            model="test",
            generated_at=dt.datetime(2026, 7, 26, 3, 0),
            topic="polynomial identities, divisibility, and irreducibility",
            area="algebra",
        )
    )


# --- the week --------------------------------------------------------------


def test_the_week_has_the_shape_the_config_names(settings):
    modes = [settings.math.mode_for(SUNDAY + dt.timedelta(days=i)) for i in range(7)]
    assert modes == [
        DayMode.reading,
        DayMode.compute,
        DayMode.compute,
        DayMode.proof,
        DayMode.proof_long,
        DayMode.proof_long,
        DayMode.proof_showcase,
    ]


def test_only_thursday_carries_and_only_friday_is_day_two(settings):
    assert settings.math.carries_over(THURSDAY)
    assert not settings.math.carries_over(FRIDAY)
    assert settings.math.is_second_day(FRIDAY)
    assert not settings.math.is_second_day(THURSDAY)
    # Saturday is proof-showcase, so the pair cannot run on past Friday.
    assert not settings.math.is_second_day(SATURDAY)


def test_a_proof_day_has_no_reference_time(settings):
    """The speed bonus would pay for hurrying through the one day of the week
    that exists for not hurrying."""
    assert settings.math.par_for("putnam-early") == 40
    assert settings.math.par_for("putnam-early", DayMode.proof_long) is None
    assert settings.math.par_for("putnam-early", DayMode.proof_showcase) is None
    assert settings.math.par_for("aime-late", DayMode.compute) == 20


# --- what a proof is worth -------------------------------------------------


def test_the_proof_ladder_lands_where_the_compute_ladder_does():
    solved = [scoring.proof_score(MathOutcome.solved, h) for h in (0, 1, 2)]
    assert solved == [1.0, 0.7, 0.1]
    # A fully-hinted proof is worth what a fully-hinted compute problem is.
    assert solved[-1] == scoring.math_score(MathOutcome.solved, 3)


def test_a_partial_proof_is_half_and_never_goes_negative():
    assert scoring.proof_score(MathOutcome.partial, 0) == 0.5
    assert scoring.proof_score(MathOutcome.partial, 1) == 0.2
    # 0.5 - 0.9 is negative, and the score column forbids that. Clamped rather
    # than allowed to mean "worse than not doing it".
    assert scoring.proof_score(MathOutcome.partial, 2) == 0.0
    assert scoring.proof_score(MathOutcome.failed, 0) == 0.0


def test_the_day_one_bonus_is_large_and_capped(repo):
    """~+58 mature, ~+78 early, and never more than a first-ever result."""
    mature = scoring.early_solve_multiplier(24)
    k, after = scoring.timed_elo(1500, 1750, 1.0, 24, mature)
    assert (k, after - 1500) == (72, 58)

    fresh = scoring.early_solve_multiplier(0)
    k, after = scoring.timed_elo(1500, 1750, 1.0, 0, fresh)
    assert k == scoring.K_BONUS_CEILING
    assert after - 1500 == 78


# --- which ratings move ----------------------------------------------------


def test_a_proof_rates_into_proofs_and_nothing_else(repo, proof):
    """The reason `PROOF_AREA` exists: honest proof scores must not drag the
    numbers that pitch Monday's difficulty."""
    repo.open_math_attempt(WEDNESDAY, proof.id)
    pooled_before = repo.current_rating(Track.math)
    algebra_before = repo.current_rating(Track.math, "algebra")

    repo.finish_math(WEDNESDAY, MathOutcome.solved)

    rows = [tuple(r) for r in repo.conn.execute("SELECT area FROM rating_event WHERE track='math'")]
    assert rows == [(PROOF_AREA,)]
    assert repo.current_rating(Track.math) == pooled_before
    assert repo.current_rating(Track.math, "algebra") == algebra_before
    assert repo.current_rating(Track.math, PROOF_AREA) > pooled_before


def test_a_compute_problem_still_rates_pooled_and_area(repo, problem):
    """Unchanged, and the point of pinning it: the proofs bucket is an
    addition, not a rerouting."""
    repo.conn.execute("UPDATE math_problem SET area = 'analysis' WHERE id = ?", (problem.id,))
    repo.open_math_attempt(MONDAY, problem.id)
    repo.finish_math(MONDAY, MathOutcome.solved)

    areas = [r[0] for r in repo.conn.execute("SELECT area FROM rating_event WHERE track='math'")]
    assert areas == [None, "analysis"]


def test_partial_is_refused_on_a_problem_that_has_an_answer(repo, problem):
    repo.open_math_attempt(MONDAY, problem.id)
    with pytest.raises(ValueError, match="proof outcome"):
        repo.finish_math(MONDAY, MathOutcome.partial)


# --- the two-day problem ---------------------------------------------------


def test_day_one_offers_no_hints_and_day_two_offers_two(settings, proof):
    assert content.hints_available(settings, THURSDAY, proof) == 0
    assert content.hints_available(settings, FRIDAY, proof) == 2
    # A Wednesday proof is not gated -- it is one day's work.
    assert content.hints_available(settings, WEDNESDAY, proof) == 2


def test_carrying_writes_no_rating_event(repo, proof):
    repo.open_math_attempt(THURSDAY, proof.id)
    before = repo.current_rating(Track.math, PROOF_AREA)

    carried = repo.carry_math(THURSDAY)

    assert carried.status is MathStatus.carried
    assert carried.score is None
    assert repo.current_rating(Track.math, PROOF_AREA) == before
    assert repo.event_count(Track.math, PROOF_AREA) == 0


def test_day_two_picks_up_the_carried_problem_without_drawing(settings, repo, proof):
    repo.open_math_attempt(THURSDAY, proof.id)
    repo.carry_math(THURSDAY)

    served = content.next_problem(settings, repo, FRIDAY)

    assert served.id == proof.id
    assert repo.math_attempt(FRIDAY).day == 2
    # One problem, two attempt rows, and only the second will be rated.
    assert repo.math_attempt(THURSDAY).problem_id == repo.math_attempt(FRIDAY).problem_id


def test_the_result_lands_once_on_day_two(repo, proof):
    repo.open_math_attempt(THURSDAY, proof.id)
    repo.carry_math(THURSDAY)
    repo.open_math_attempt(FRIDAY, proof.id, day=2)

    repo.finish_math(FRIDAY, MathOutcome.solved)

    assert repo.event_count(Track.math, PROOF_AREA) == 1
    assert repo.math_attempt(THURSDAY).score is None
    assert repo.math_attempt(FRIDAY).score == 1.0


def test_an_unmarked_day_one_carries_instead_of_lapsing(settings, repo, proof):
    """Forgetting to press "keep going tomorrow" must not cost a full result on
    a problem you were meant to still be holding."""
    repo.open_math_attempt(THURSDAY, proof.id)

    events = repo.reconcile_math_lapses(SATURDAY, settings.math.carries_over)

    assert events == []
    assert repo.math_attempt(THURSDAY).status is MathStatus.carried


def test_an_unmarked_ordinary_day_still_lapses(settings, repo, problem):
    repo.open_math_attempt(MONDAY, problem.id)

    (event,) = repo.reconcile_math_lapses(WEDNESDAY, settings.math.carries_over)

    assert event.score == scoring.LAPSE_SCORE
    assert repo.math_attempt(MONDAY).status is MathStatus.lapsed


def test_the_day_one_bonus_needs_a_genuine_unhinted_day_one(repo, proof):
    """The server asserts `early_solve` from the calendar; the repository
    checks it against the row, which is the half a client cannot fake."""
    repo.open_math_attempt(FRIDAY, proof.id, day=2)
    _, event = repo.finish_math(FRIDAY, MathOutcome.solved, early_solve=True)
    assert event.k == scoring.k_factor(0)  # day two: no bonus


# --- what the overnight file has to look like ------------------------------


def proof_file(**over) -> str:
    problem = {
        "source": "P&B 693",
        "tier": "olympiad-easy",
        "kind": "proof",
        "statement": "Prove that the midpoints of a quadrilateral form a parallelogram.",
        "answer": "Half-sums of position vectors; opposite sides are both (c-a)/2.",
        "hints": ["write each midpoint as a half-sum", "subtract adjacent midpoints"],
        "rubric": ["wrote the midpoints as half-sums", "concluded from equal-and-parallel"],
        "location": "4.1.1 Geometry / Vectors, p. 211",
    }
    problem.update(over)
    return json.dumps({"date": WEDNESDAY.isoformat(), "problems": [problem]})


def test_a_proof_batch_is_accepted_without_a_verified_url(settings):
    """A textbook problem has a page, not a URL. Requiring one -- which the
    validator did -- would have rejected every proof batch ever generated."""
    parsed, errors = batch.validate(settings, proof_file())
    assert errors == []
    assert parsed.problems[0].kind == "proof"


def test_a_proof_without_its_page_or_rubric_is_rejected(settings):
    _, errors = batch.validate(settings, proof_file(location=None, rubric=[]))
    assert any("no location" in e for e in errors)
    assert any("no rubric" in e for e in errors)


def test_a_proof_takes_two_hints_and_a_compute_problem_three(settings):
    _, errors = batch.validate(settings, proof_file(hints=["a", "b", "c"]))
    assert any("exactly 2 hints" in e for e in errors)

    _, errors = batch.validate(settings, proof_file(kind="compute"))
    assert any("exactly 3 hints" in e for e in errors)
    assert any("verified_url" in e for e in errors)
    assert any("rubric belongs to a proof" in e for e in errors)


def test_every_day_of_the_week_carries_the_schema_it_must_write(settings, repo):
    """The scheduled task's prompt must not hold a copy of the output shape.

    A schema in a prompt is a copy of `batch.py` that nothing keeps in step, and
    it goes stale silently -- the first proof day would have been generated
    against the compute shape by a task nobody remembered to edit.
    """
    week = batch.week_context(settings, repo, SUNDAY)
    for day in week["days"]:
        if not day["generate"]:
            continue
        assert day["schema"], f"{day['weekday']} carries no schema"
    shapes = {d["mode"]: d["schema"] for d in week["days"] if d["generate"]}
    assert "kind\":\"proof\"" in shapes["proof"]
    assert "verified_url" in shapes["compute"]
    assert "segments" in shapes["reading"]


# --- a problem you brought yourself ----------------------------------------


@pytest.fixture
def level_check(monkeypatch):
    """Stub the one paid call the math problem still makes."""
    seen: dict[str, object] = {"reply": {"tier": "putnam-late", "note": "a real Putnam B6."}}

    def fake(settings, system, user, *, metrics=None, **kw):
        m = metrics if metrics is not None else llm.Metrics()
        m.input_tokens += 400
        m.output_tokens += 40
        seen["user"] = user
        # `(text, metrics)`, the shape `llm.json_call` unpacks.
        return json.dumps(seen["reply"]), m

    monkeypatch.setattr(llm, "call", fake)
    return seen


def test_a_brought_problem_is_rated_at_the_tier_the_check_returns(settings, repo, level_check):
    problem = content.own_problem(
        settings, repo, SATURDAY, "Prove the numbered cylinder is a Fourier problem.", "cylinder"
    )

    assert problem.tier == "putnam-late"
    assert problem.anchor == settings.math.anchors["putnam-late"]
    assert problem.kind is ProblemKind.proof
    assert problem.model == content.OWN
    # Nobody wrote hints or a rubric for a problem you found. Empty rather than
    # invented is the honest state, and the panel renders it as such.
    assert problem.hints == [] and problem.rubric == []
    assert problem.answer == ""
    # Every tier offered to the check must be one it can actually be rated at.
    assert "putnam-late" in level_check["user"]


def test_a_brought_problem_rates_into_proofs(repo, settings, level_check):
    content.own_problem(settings, repo, SATURDAY, "Prove something hard enough.", "cylinder")
    pooled = repo.current_rating(Track.math)
    repo.finish_math(SATURDAY, MathOutcome.solved)

    areas = [r[0] for r in repo.conn.execute("SELECT area FROM rating_event WHERE track='math'")]
    assert areas == [PROOF_AREA]
    assert repo.current_rating(Track.math) == pooled


def test_an_unknown_tier_falls_back_rather_than_crashing(settings, repo, level_check):
    level_check["reply"] = {"tier": "impossible", "note": "n/a"}
    problem = content.own_problem(settings, repo, SATURDAY, "Prove it.", "mystery")
    assert problem.tier == settings.math.tiers_for(DayMode.proof_showcase)[0]


# --- sunday ----------------------------------------------------------------


def test_a_reading_day_draws_nothing(settings, repo):
    with pytest.raises(content.ContentError, match="reading day"):
        content.next_problem(settings, repo, SUNDAY)


def test_a_proof_day_will_not_fall_back_to_the_aime_pool(settings, repo):
    """The pool is numeric answers and three hints. Serving one on a Thursday
    would put a compute problem under the two-day rules and rate it into the
    buckets the proof days exist to leave alone."""
    settings.path("problems").write_text(
        json.dumps(
            [
                {
                    "source": "AMC 2019 P5",
                    "tier": "amc",
                    "statement": "What is $1+1$?",
                    "answer": "2",
                    "hints": ["a", "b", "c"],
                }
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(content.ContentError, match="proof day"):
        content.next_problem(settings, repo, THURSDAY)

    # ...and the same pool still serves a calibrated day.
    assert content.next_problem(settings, repo, MONDAY).source == "AMC 2019 P5"


def plan(date=SUNDAY) -> d.StudyPlan:
    return d.StudyPlan(
        date=date,
        topic="generating functions",
        book="Putnam and Beyond",
        segments=[
            d.StudySegment(title=f"segment {i}", reading=f"6.4.2, pp. 7{i}", minutes=30)
            for i in range(3)
        ],
        model="test",
        generated_at=dt.datetime(2026, 7, 26, 3, 0),
    )


def test_one_segment_ticks_the_day(repo):
    repo.add_study_plan(plan())
    assert repo.study_plan(SUNDAY).started is False

    marked = repo.mark_segment(SUNDAY, 1, done=True)

    assert marked.done == [1]
    assert marked.started is True


def test_a_skip_keeps_its_reason(repo):
    repo.add_study_plan(plan())
    marked = repo.mark_segment(SUNDAY, 0, done=False, reason="knew it")
    assert [(s.index, s.reason) for s in marked.skipped] == [(0, "knew it")]


def test_done_and_skipped_stay_disjoint(repo):
    """Skipped in the morning, done in the evening, reads as done."""
    repo.add_study_plan(plan())
    repo.mark_segment(SUNDAY, 2, done=False, reason="no time")
    marked = repo.mark_segment(SUNDAY, 2, done=True)

    assert marked.done == [2]
    assert marked.skipped == []


def test_a_segment_the_plan_does_not_have_is_refused(repo):
    repo.add_study_plan(plan())
    with pytest.raises(ValueError, match="not in"):
        repo.mark_segment(SUNDAY, 5, done=True)


def test_regenerating_a_plan_replaces_it(repo):
    """Unlike a problem, whose `source` is UNIQUE. Nothing is rated off a
    reading plan, so a second generation invalidates nothing."""
    repo.add_study_plan(plan())
    again = plan()
    again.book = "generatingfunctionology"
    stored = repo.add_study_plan(again)
    assert stored.book == "generatingfunctionology"
    assert len(repo.recent_study_plans()) == 1
