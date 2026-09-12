"""The read model: rows -> the payload the browser boots from.

`state.json` was both the storage format and the API payload, which is what
let a stale tab POST its whole world back and full-replace the file. Those are
now two different shapes, and this module is the one-way function between
them. Nothing here writes.

Every function takes `today` as an argument. The browser is the sole authority
on what day it is -- the server's `dt.date.today()` ran in WSL's timezone
while the client compared against its own, so any skew silently broke every
"done today" check.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from pathlib import Path
from typing import NamedTuple

from . import (
    content,
    garmin,
    health,
    investigate,
    scoring,
    spelling,
    trainplan,
    tricks,
)
from .config import Settings
from .db.repository import ARGUMENT_AREA, PROOF_AREA, TRICK_AREA, Repository
from .models import api
from .models import domain as d
from .models.common import (
    DayMode,
    Deck,
    EntrySource,
    Game,
    HealthMetric,
    MathOutcome,
    ProblemKind,
    Track,
)

HISTORY_LIMIT = 40
"""Rating events sent to the elo charts. The chart plots the last 40 anyway;
shipping the whole history was pure payload."""

HEALTH_LIMIT = 90
"""Days of each daily scalar sent to the charts. Was 30 -- a weight series
wants to cover a training block, not a month, because the whole reason to draw
it is to see the direction over one."""


# --- shared ----------------------------------------------------------------


def track_summary(repo: Repository, track: Track) -> api.TrackSummary:
    n = repo.event_count(track)
    return api.TrackSummary(
        rating=repo.current_rating(track),
        k_next=scoring.k_factor(n),
        measured_count=n,
        success_rate=repo.success_rate(track),
        history=repo.rating_history(track, limit=HISTORY_LIMIT),
    )


# --- panels ----------------------------------------------------------------


def area_ratings(cfg: Settings, repo: Repository) -> list[api.AreaRating]:
    """The breakdown, strongest first.

    Sorted by rating rather than by the config's slot order: the question this
    answers is "where am I weak", and the answer should be the last line, not
    something to be found by scanning.
    """
    rows = repo.area_ratings(cfg.math.area_names)
    return sorted(
        (api.AreaRating(area=a, rating=r, events=n) for a, r, n in rows),
        key=lambda x: -x.rating,
    )


def proof_rating(repo: Repository) -> api.AreaRating:
    """The `proofs` bucket, as an `AreaRating` like any other.

    Rendered as the headline on a proof day. Four days of the week move this
    number and leave the pooled one alone, so showing the pooled rating on a
    Thursday would be showing the one figure the day cannot change.
    """
    area, rating, events = repo.area_ratings([PROOF_AREA])[0]
    return api.AreaRating(area=area, rating=rating, events=events)


def math_panel(cfg: Settings, repo: Repository, today: dt.date) -> api.MathPanel:
    summary = track_summary(repo, Track.math)
    mode = cfg.math.mode_for(today)
    base = {
        "summary": summary,
        "areas": area_ratings(cfg, repo),
        "target_area": cfg.math.area_for(today),
        "mode": mode.value,
        "proof": proof_rating(repo) if mode.is_proof else None,
    }

    if mode is DayMode.reading:
        # No attempt, no rating, and the hero holds a plan instead of a
        # problem. `plan` being None on a Sunday is the honest "the overnight
        # job did not run", which the panel says rather than hiding.
        return api.MathPanel(**base, plan=repo.study_plan(today))

    attempt = repo.math_attempt(today)
    if attempt is None:
        # Day two with nothing carried is the day off a day-one solve buys.
        # Computed rather than stored: it is a fact about yesterday's row and
        # today's date, and storing it would be storing a derivation that stops
        # being true when either changes.
        free = cfg.math.is_second_day(today) and repo.carried_math_attempt(today) is None
        return api.MathPanel(**base, free_day=free)

    problem = repo.problem(attempt.problem_id)
    if problem is None:
        # The attempt's FK guarantees this cannot happen; treating it as "no
        # problem today" rather than asserting keeps a corrupted row from
        # taking the whole dashboard down.
        return api.MathPanel(**base)

    budget = content.hints_available(cfg, today, problem)
    return api.MathPanel(
        **base,
        problem=problem,
        day=attempt.day,
        hints_revealed=attempt.hints_revealed,
        hint_budget=budget,
        status=attempt.status.value,
        score=attempt.score,
        minutes=attempt.minutes,
        par_minutes=cfg.math.par_for(problem.tier, mode),
        # Only what has actually been revealed, and never more than the day
        # allows. The old client received all three hints and merely declined
        # to render them; on day one of the two-day problem that would hand
        # over tomorrow's barrier in today's payload.
        hints_visible=problem.hints[: min(attempt.hints_revealed, budget)],
    )


ARCHIVE_LIMIT = 60
"""Past problems sent to the archive. Two months of daily problems, which is
what "have I seen this before" reaches back for in practice — and the whole
statement of each ships, so the payload is the reason there is a limit."""


def math_archive(cfg: Settings, repo: Repository, today: dt.date) -> api.MathArchiveResponse:
    """Every past problem, newest first, with what it did to the rating.

    Read-only like everything here. Eligibility is *computed*, not stored: a
    day drops out of the window by the calendar moving, so a stored flag would
    be a fact that quietly stops being true.

    `cfg` is here only for the reference times: the late-submission box takes a
    solve time like the live panel does, and it has to be able to price it.
    """
    history = repo.math_history(ARCHIVE_LIMIT)
    deltas = repo.rating_deltas([attempt.date for attempt, _ in history])
    eligible = set(repo.late_eligible_dates(today))
    return api.MathArchiveResponse(
        entries=[
            api.MathArchiveEntry(
                date=attempt.date,
                problem=problem,
                status=attempt.status.value,
                score=attempt.score,
                hints_revealed=attempt.hints_revealed,
                minutes=attempt.minutes,
                # Read off the problem's kind rather than the day's mode: a
                # proof is untimed whenever it is submitted, and a late box
                # offering a speed bonus on one would price the thing the
                # proof days exist to not price.
                par_minutes=(
                    None
                    if problem.kind is ProblemKind.proof
                    else cfg.math.par_for(problem.tier)
                ),
                delta=deltas.get(attempt.date),
                late_eligible=attempt.date in eligible,
                late_score_if_solved=(
                    scoring.late_math_score(MathOutcome.solved, attempt.hints_revealed)
                    if attempt.date in eligible
                    else None
                ),
            )
            for attempt, problem in history
        ],
        late_window_days=scoring.LATE_WINDOW_DAYS,
    )


def chess_panel(repo: Repository, today: dt.date) -> api.ChessPanel:
    summary = track_summary(repo, Track.chess)
    attempt = repo.chess_attempt(today)
    if attempt is None:
        return api.ChessPanel(summary=summary)

    puzzle = repo.chess_puzzle(attempt.puzzle_id)
    if puzzle is None:
        return api.ChessPanel(summary=summary)

    return api.ChessPanel(
        summary=summary,
        puzzle_id=puzzle.id,
        puzzle_rating=puzzle.rating,
        puzzle_url=puzzle.url,
        themes=puzzle.themes,
        status=attempt.status.value,
        score=attempt.score,
    )



def spelling_panel(cfg: Settings, repo: Repository, today: dt.date) -> api.SpellingPanel:
    """Today's bee.

    Read-only, like everything here: this reports the round, it does not draw
    one. Drawing is a POST, because a boot that drew would hand you a fresh set
    of words on any morning you opened the tab and then closed it -- and unlike
    a theorem, hearing a word is the whole test.

    `marks` is recomputed from the stored answers rather than stored alongside
    them. They cannot disagree that way, and the comparison is a casefold.
    """
    summary = track_summary(repo, Track.spelling)
    base = {
        "summary": summary,
        "enabled": cfg.spelling.enabled,
    }
    round_ = repo.spelling_round(today)
    if round_ is None:
        return api.SpellingPanel(**base)

    done = round_.status != "open"
    return api.SpellingPanel(
        **base,
        words=round_.words,
        answers=round_.answers,
        marks=spelling.mark(round_.words, round_.answers) if done else [],
        correct=round_.correct,
        status=round_.status,
        delta=repo.rating_delta(Track.spelling, today) if done else None,
    )


def trick_prompts(
    round_: d.TrickRound,
    callback_name: str | None,
    *,
    marked: bool,
    reveal_challenge: bool | None = None,
) -> tuple[list[api.TrickPrompt], api.TrickPrompt | None]:
    """The day's set as the panel is allowed to see it, plus its challenge.

    [Invariant] The answers do not cross the wire until the set has been
    marked. The old shape of this mistake is in `TheoremsPanel.today`'s comment:
    a panel that receives the answer and merely declines to render it has been
    trusted to ask you a question about a value it is already holding. Here
    that would be worse than ceremonial, because the marking is a string
    comparison the server performs -- so the answers being in the page is
    exactly the answer key.

    Applied to `solution` as well, which is the half that is easy to miss: a
    worked solution is an answer with the route attached.

    The challenge is decided separately from the set, because the two close at
    different times: submitting the eight does not answer the ninth. `None`
    means "graded", which is the live panel's rule; the archive passes True for
    a day that is over, since a challenge nobody answered would otherwise stay
    hidden for ever -- there is nothing left to protect once the day it belongs
    to has gone.
    """
    marks = tricks.mark(round_.problems, round_.answers) if marked else []
    if reveal_challenge is None:
        reveal_challenge = round_.challenge_ok is not None

    def shown(problem: d.TrickProblem, i: int | None) -> api.TrickPrompt:
        graded = marked if i is not None else reveal_challenge
        return api.TrickPrompt(
            band=problem.band,
            prompt=problem.prompt,
            callback_of=callback_name if problem.band == "callback" else None,
            answer=problem.answer if graded else "",
            accept=problem.accept if graded else [],
            solution=problem.solution if graded else "",
            typed=(
                (round_.answers[i] if i is not None and i < len(round_.answers) else "")
                if i is not None
                else (round_.challenge or "")
            ),
            correct=(
                (marks[i] if i is not None and i < len(marks) else None)
                if i is not None
                else round_.challenge_ok
            ),
        )

    prompts = [shown(p, i) for i, p in enumerate(tricks.marked_problems(round_.problems))]
    challenge = tricks.challenge_of(round_.problems)
    return prompts, shown(challenge, None) if challenge else None


def tricks_panel(cfg: Settings, repo: Repository, today: dt.date) -> api.TricksPanel:
    """Today's trick.

    Read-only, like everything here: this reports the round, it does not draw
    one. Drawing is a POST — `daily.js` makes it at boot, so the set is waiting
    when the page opens, and `batched` below is what tells it whether there is
    anything to draw.
    """
    base = {
        "enabled": cfg.tricks.enabled,
        "rating": repo.current_rating(Track.math, TRICK_AREA),
        "events": repo.event_count(Track.math, TRICK_AREA),
        "total": len(repo.trick_names()),
    }
    round_ = repo.trick_round(today)
    if round_ is None:
        if not cfg.tricks.enabled:
            return api.TricksPanel(**base)
        # One stat, and it decides everything the panel does next: whether the
        # night left a file. A page load with one draws the set; a page load
        # without one says the night was missed and names the job that fixes it.
        return api.TricksPanel(**base, batched=content.trick_batch_path(cfg, today).exists())

    trick = repo.trick(round_.trick_id)
    if trick is None:
        # The round's FK guarantees this cannot happen; treating it as "no
        # trick today" rather than asserting keeps a corrupted row from taking
        # the whole dashboard down, which is `math_panel`'s call in the same
        # spot.
        return api.TricksPanel(**base)

    callback = repo.trick(round_.callback_id) if round_.callback_id else None
    marked = round_.status != "open"
    problems, challenge = trick_prompts(round_, callback.name if callback else None, marked=marked)
    return api.TricksPanel(
        **base,
        trick=trick,
        problems=problems,
        challenge=challenge,
        answers=round_.answers,
        correct=round_.correct,
        status=round_.status,
        challenge_answer=round_.challenge,
        challenge_ok=round_.challenge_ok,
        challenge_note=round_.challenge_note,
        callback_of=callback.name if callback else None,
        delta=repo.trick_delta(today),
    )


TRICK_ARCHIVE_LIMIT = 60
"""Past sets sent to the archive. Two months, matching `ARCHIVE_LIMIT` -- and
the payload is the reason there is a limit, since every prompt and every worked
solution ships whole."""


def trick_archive(repo: Repository, today: dt.date) -> api.TrickArchiveResponse:
    """Every past set, newest first, answers and solutions included.

    Dated only for the challenge. Everything else here is a record of work
    already done and means the same thing whichever day you open it on -- but
    "can this still be attempted" is relative to today, and it is the one
    question that decides whether an answer may be shown.
    """
    entries = []
    for round_, trick in repo.trick_rounds(TRICK_ARCHIVE_LIMIT):
        callback = repo.trick(round_.callback_id) if round_.callback_id else None
        # `marked=True` regardless of status: this is a record of work already
        # done, and withholding the answer the way the live panel does would
        # mean the one thing you came to the archive for is the one thing it
        # will not show. An open round can only be today's, and today's panel
        # is on screen anyway.
        #
        # The challenge is revealed on any day but today, and that is the fix
        # for a real hole rather than a nicety: nothing in the app offers a
        # past day's challenge, so one you never answered would otherwise be
        # hidden by a rule that can no longer be satisfied.
        problems, challenge = trick_prompts(
            round_,
            callback.name if callback else None,
            marked=True,
            reveal_challenge=round_.challenge_ok is not None or round_.date < today,
        )
        entries.append(
            api.TrickArchiveEntry(
                date=round_.date,
                trick=trick,
                problems=problems,
                challenge=challenge,
                correct=round_.correct,
                status=round_.status,
                delta=repo.trick_delta(round_.date),
            )
        )
    return api.TrickArchiveResponse(entries=entries)


def games_panel(repo: Repository, today: dt.date) -> api.GamesPanel:
    wordle = repo.wordle(today)
    pokedoku = repo.pokedoku(today)
    return api.GamesPanel(
        wordle_today=api.WordleLogRequest(**wordle.model_dump()) if wordle else None,
        pokedoku_today=api.PokedokuLogRequest(**pokedoku.model_dump()) if pokedoku else None,
        wordle_streak=scoring.streak(repo.played_dates(Game.wordle), today),
        pokedoku_streak=scoring.streak(repo.played_dates(Game.pokedoku), today),
        wordle_average=repo.wordle_average(),
    )


class DeckView(NamedTuple):
    """One deck's projection: today's card, the list, and what was written.

    Extracted when the argument deck arrived. The two panels differ in what
    they hang off this -- the theorem panel has no rating of its own, the
    pattern panel has no archive modal -- but the draw, the blanking invariant
    and the seen/recalled counts are the same job done twice, and doing it
    twice is how the invariant below gets broken in one of them.
    """

    today: d.TheoremCard | None
    entries: list[api.TheoremDeckEntry]
    written: api.TheoremWritten | None
    recalled: bool | None
    delta: int | None
    size: int


def deck_view(repo: Repository, today: dt.date, deck: Deck) -> DeckView:
    """Read-only, like everything here: this reports the draw, it does not make
    it. A boot that drew would mean the card of the day changed depending on
    whether you happened to open the tab -- and the module that draws has to be
    one that writes.
    """
    drawn = repo.draw(today, deck)
    last = repo.last_drawn(deck)
    counts = repo.recall_counts(deck)
    graded = drawn is not None and drawn.explained is not None

    # [Invariant] Today's statement does not cross the wire until the recall
    # has been graded. The old panel shipped it with the draw and left the
    # client to keep it hidden behind a reveal button, which meant the answer
    # was in the page the whole time you were being asked to recall it -- the
    # same mistake `MathPanel.hints_visible` exists to avoid.
    #
    # Applied to the deck as well as to `today`, and that is the half that is
    # easy to miss: the deck ships every card in full to feed the archive, so
    # withholding it from one field and sending it in the other would leave
    # the answer one click away and the invariant only apparently true.
    # Blanked rather than omitted so the payload's shape does not change with
    # the day's state; the archive says why the one card is empty.
    def shown(card: d.TheoremCard) -> d.TheoremCard:
        hidden = drawn is not None and not graded and card.id == drawn.card_id
        return card.model_copy(update={"statement": ""}) if hidden else card

    entries = [
        api.TheoremDeckEntry(
            card=shown(card),
            days_since=(today - last[card.id]).days if card.id in last else None,
            seen=counts.get(card.id, (0, 0))[0],
            recalled=counts.get(card.id, (0, 0))[1],
        )
        for card in repo.cards(deck, include_archived=True)
    ]

    written = None
    if drawn is not None and graded:
        written = api.TheoremWritten(
            explanation=drawn.explanation or "",
            application=drawn.application or "",
            explained=bool(drawn.explained),
            applied=bool(drawn.applied),
            note=drawn.note or "",
        )

    return DeckView(
        today=shown(repo.card(drawn.card_id)) if drawn else None,
        entries=entries,
        written=written,
        recalled=drawn.recalled if drawn else None,
        delta=repo.theorem_delta(drawn.date, deck) if graded and drawn else None,
        size=repo.deck_size(deck),
    )


def theorems_panel(repo: Repository, today: dt.date) -> api.TheoremsPanel:
    view = deck_view(repo, today, Deck.math)
    return api.TheoremsPanel(
        today=view.today,
        recalled=view.recalled,
        written=view.written,
        delta=view.delta,
        deck=view.entries,
        deck_size=view.size,
    )


def patterns_panel(repo: Repository, today: dt.date) -> api.PatternsPanel:
    """The argument deck, plus the rating it moves and what to add next.

    `suggested` is the answer to the real problem with any curated deck: you
    cannot add a card for a pattern you did not know existed. The last ballot
    was asked to name the ones that showed up, and the ones not already in the
    deck become buttons here.
    """
    view = deck_view(repo, today, Deck.argument)
    have = {entry.card.name.casefold() for entry in view.entries}
    latest = repo.last_investigation_ballot()
    suggested = [
        name for name in (latest.patterns if latest else []) if name.casefold() not in have
    ]
    return api.PatternsPanel(
        today=view.today,
        recalled=view.recalled,
        written=view.written,
        delta=view.delta,
        deck=view.entries,
        deck_size=view.size,
        rating=repo.current_rating(Track.investigate, ARGUMENT_AREA),
        events=repo.recall_total(Deck.argument),
        suggested=suggested,
    )


def investigate_panel(
    repo: Repository,
    today: dt.date,
    root: Path,
    scrollback: int = 1000,
    folders: list[str] | None = None,
) -> api.InvestigatePanel:
    """Today's topic, if one has been drawn, and the pool behind it.

    Read-only, like everything in this module: it reports the draw, it does not
    make one. Drawing spends a topic, and a boot payload that drew would mean
    the day's topic depended on whether the dashboard happened to be opened --
    the same rule that keeps the theorem draw off this path.

    A `investigation_day` row pointing at a topic that has since been struck off
    is possible -- draw it in the morning, clear it in the evening -- and is
    reported as drawn anyway. The day did draw it, and rewriting that after the
    fact would make the panel disagree with its own history.
    """
    day = repo.investigation_day(today)
    pool = repo.pool()
    topic = repo.investigation(day.investigation_id) if day else None

    # The file, read on every render. It is a few KB and the alternative is a
    # panel that believes a `written` flag while the buffer behind it has been
    # emptied in a terminal the app knows nothing about -- which is a state
    # this feature invites, because the file is deliberately openable from
    # outside it.
    on_disk = ""
    if topic and topic.path:
        on_disk = investigate.read_file(root / topic.path)

    # Assembled off the *snapshot*, not off the file. What you hand to the
    # grader has to be what the grade will be recorded against, and the file
    # goes on being editable in a terminal this app cannot see.
    prompt = None
    if topic is not None and day is not None and day.write_up:
        prompt = investigate.build_prompt(
            topic.topic, day.write_up, investigate.known_folders(root, folders or [])
        )

    return api.InvestigatePanel(
        today=topic,
        drawn_on=day.date if day else None,
        day=day,
        scrollback=scrollback,
        file_body=investigate.body(on_disk),
        prompt=prompt,
        rubric=[
            api.RubricLine(key=c.key, label=c.label, asks=c.asks) for c in investigate.RUBRIC
        ],
        summary=track_summary(repo, Track.investigate),
        delta=repo.rating_delta(Track.investigate, today),
        folders=sorted(
            (
                api.AreaRating(area=name, rating=rating, events=n)
                for name, rating, n in repo.area_ratings(
                    repo.investigation_folders(), Track.investigate
                )
            ),
            key=lambda x: -x.rating,
        ),
        pool=pool,
        pool_size=len(pool),
        investigated=repo.investigated_count(),
    )


def papers_panel(repo: Repository, today: dt.date) -> api.PapersPanel:
    return api.PapersPanel(
        surfaced=repo.surfaced_paper(today),
        queue=repo.queue(),
        cleared_count=repo.cleared_count(),
    )


DAYLOG_LIMIT = 180
"""Day logs sent to the history modal. Six months, which is as far back as
"what was I doing then" reaches before it becomes a different question."""


def history(cfg: Settings, repo: Repository) -> api.HistoryResponse:
    """The retrospective view: per-area elo lines and the day-log archive.

    Undated, unlike almost everything else here, and that is not an oversight.
    Nothing in it is relative to today -- a rating trajectory and a pile of
    dated log entries mean the same thing whichever day you open them on, so
    there is no "today" for the browser to be the authority on.

    Areas with no measured results are dropped rather than drawn flat. A line
    through a rating that was only ever the pooled figure standing in would
    show a trend that never happened; the panel strip already reports those as
    dimmed placeholders, and this is the same honesty in chart form.
    """
    histories = repo.area_rating_history()
    areas = [
        api.AreaSeries(area=area, points=histories[area], events=len(histories[area]))
        for area in cfg.math.area_names
        if histories.get(area)
    ]
    return api.HistoryResponse(
        # Strongest first, matching the panel strip's order so the two surfaces
        # agree about which area is which.
        areas=sorted(areas, key=lambda a: -a.points[-1][1]),
        daylogs=[
            api.DayLogEntry(date=entry.date, text=entry.text)
            for entry in repo.daylogs(DAYLOG_LIMIT)
        ],
    )


def health_series(repo: Repository) -> list[api.HealthSeries]:
    return [
        api.HealthSeries(
            metric=metric,
            points=[(e.date, e.value) for e in repo.health_series(metric, HEALTH_LIMIT)],
        )
        for metric in HealthMetric
    ]


def camp_pause(
    program: trainplan.Program,
    breaks: Sequence[d.CampBreak],
    today: dt.date,
) -> api.CampPause | None:
    """The paused state as the panel needs it, or None if the camp is running.

    The two dates this computes are the whole reason the panel can be trusted
    with a button: `resumes` is the Sunday coming back would use, and it is on
    screen *before* it is agreed to, so the week that gets re-run is a thing
    you chose rather than a thing you discovered.
    """
    entry = trainplan.paused_on(breaks, today)
    if entry is None:
        return None
    resumes = entry.resumed or trainplan.next_sunday(today)
    return api.CampPause(
        since=entry.started,
        reason=entry.reason,
        days=(today - entry.started).days + 1,
        resumes=resumes,
        committed=entry.resumed is not None,
        # Asked of the day the camp comes back on rather than of today, which
        # is the point: on that Sunday the shift has taken effect, so this is
        # the week that will actually be served -- the one it stopped in.
        restarts_week=trainplan.camp_week(program, resumes, _as_if_resumed(breaks, entry, resumes)),
    )


def _as_if_resumed(
    breaks: Sequence[d.CampBreak], entry: d.CampBreak, resumes: dt.date
) -> list[d.CampBreak]:
    """`breaks` with `entry` given the resume date it is being offered.

    Replaced rather than appended. An appended copy would leave the open row in
    the list too, and `paused_on` would find it and freeze the answer at the
    week it stopped in -- which is the right number here for the wrong reason,
    and would stop being the right number the moment anything else changed.
    """
    resumed = entry.model_copy(update={"resumed": resumes})
    return [resumed if b.id == entry.id else b for b in breaks]


def training_plan(
    cfg: Settings, repo: Repository, today: dt.date
) -> api.TrainingPlan | None:
    """The camp, as the panel needs it. None when there is no plan file.

    Note what is *not* here: anything comparing the plan to the log. The
    prescription is what was asked and the sessions are what happened, and
    keeping the join out of this function is what lets the plan be edited
    without a month of history quietly changing meaning.

    It takes a `repo` now, which it did not before, and the reason is the one
    thing the plan cannot read out of its own file: whether the camp is
    currently stopped. `trainplan.py` stays pure -- it is handed the breaks
    rather than fetching them -- and this is the seam where the file and the
    table meet.
    """
    program = trainplan.program(cfg.path("training"))
    if program is None:
        return None
    breaks = repo.camp_breaks()
    blocks = trainplan.prescription(program, today, breaks)
    return api.TrainingPlan(
        name=program.name,
        fight=program.fight,
        # Unmoved by a break, and deliberately so. The camp slides; the fight
        # does not. That the gap between them narrows by exactly the length of
        # the illness is the true and useful fact here, and the panel should go
        # on printing it in the same large type it always has.
        days_out=trainplan.days_out(program, today),
        week=trainplan.camp_week(program, today, breaks),
        weeks=program.weeks,
        today=blocks,
        groups=trainplan.groups(program, today, blocks, breaks),
        week_planned=trainplan.week_planned(program, today, breaks),
        ends=trainplan.plan_ends(program, breaks),
        paused=camp_pause(program, breaks, today),
    )


def health_panel(cfg: Settings, repo: Repository, today: dt.date) -> api.HealthPanel:
    """The health tab, assembled.

    The sessions are read whole (see `Repository.sessions`) and windowed here,
    so what crosses the wire is four weeks of daily totals rather than every
    session ever logged. `health.py` still sees the full history, which is what
    lets it refuse to answer until it has enough of one.
    """
    sessions = repo.sessions()
    entries = repo.readiness_history()

    window_start = today - dt.timedelta(days=health.CHRONIC_DAYS - 1)
    loads = health.daily_load(sessions, window_start, today)

    week = health.window_load(loads, today, health.ACUTE_DAYS)
    prev_week = health.window_load(
        loads, today - dt.timedelta(days=health.ACUTE_DAYS), health.ACUTE_DAYS
    )

    weight = repo.health_series(HealthMetric.weight, HEALTH_LIMIT)
    trend = health.weight_trend([(e.date, e.value) for e in weight])
    baseline = health.readiness_baseline(entries, today)

    # The scalars the watch fills in. Read as `(date, value)` pairs because
    # that is what every function in `health.py` takes -- the same shape the
    # weight trend has always been handed, so the overnight numbers get the
    # baseline machinery for free rather than growing their own.
    def points(metric: HealthMetric) -> list[tuple[dt.date, float]]:
        return [(e.date, e.value) for e in repo.health_series(metric, HEALTH_LIMIT)]

    sleep, in_bed = points(HealthMetric.sleep_hours), points(HealthMetric.time_in_bed)
    sleep_score = points(HealthMetric.sleep_score)
    hrv, rhr = points(HealthMetric.hrv), points(HealthMetric.resting_hr)
    battery = points(HealthMetric.body_battery)
    calories, water = points(HealthMetric.calories_out), points(HealthMetric.hydration_ml)
    stress = points(HealthMetric.stress)
    sweat = points(HealthMetric.sweat_loss_ml)
    vo2max = points(HealthMetric.vo2max)
    jump = points(HealthMetric.vert_jump)

    def latest(series: list[tuple[dt.date, float]]) -> float | None:
        """Today's reading, and only today's.

        Not "the last one on file", which is the version that reads three-day-
        old sleep as last night's every time the watch spends a night on the
        charger. An absent reading has to be absent, or the strip becomes a
        thing you have to check the date of.
        """
        return next((value for date, value in reversed(series) if date == today), None)

    hrv_base = health.scalar_baseline(hrv, today)
    rhr_base = health.scalar_baseline(rhr, today)

    # All three logs windowed rather than read whole -- four to sixteen rows a
    # day against the sessions table's one, and nothing here looks past a month.
    visits = repo.bathroom_history(health.CHRONIC_DAYS, before=today)
    meals = repo.meal_history(health.CHRONIC_DAYS, before=today)
    drinks = repo.drink_history(health.CHRONIC_DAYS, before=today)

    intake = health.intake_mean(meals, today)
    spend = health.expenditure_mean(calories, today)
    balance = intake - spend if intake is not None and spend is not None else None

    fluid = health.fluid_mean(drinks, today)
    sweat_week = health.scalar_mean(sweat, today)
    caffeine = health.caffeine_mean(drinks, today)
    alcohol = health.alcohol_week(drinks, today)

    # The plan, and how much of the *week's* prescription has been answered.
    #
    # A week rather than today, and the difference is the whole of what this
    # input is worth. The load zone's other three numbers -- the ratio, the
    # monotony, the strain -- are all seven- or twenty-eight-day windows, and a
    # daily compliance figure sitting beside them swings from 0 to 100 over the
    # course of a Tuesday: at nine in the morning nothing prescribed has been
    # logged yet, which is not a fact about your training. Read over the week
    # it is on the same clock as its neighbours and says the thing it is meant
    # to say, which is whether the plan is being followed.
    #
    # Read here rather than inside `load_score` because `health.py` has never
    # known what a `TrainingPlan` is -- it takes lists of rows and returns
    # numbers, and the whole reason it is testable is that it stayed that way.
    plan = training_plan(cfg, repo, today)
    sessions_today = repo.sessions_on(today)
    answered = {s.plan_key for s in sessions if s.plan_key}

    program = trainplan.program(cfg.path("training"))
    # The same breaks the plan above was built from, and they matter more here:
    # a paused day prescribes nothing, so a week spent ill leaves this window
    # empty rather than full of blocks nobody could have answered. `plan_total`
    # of zero already reports `not_asked` rather than a zero score -- the
    # eleven unwritten weeks taught `load_score` that -- so a paused week falls
    # into a state that was already there and needed no second name.
    breaks = repo.camp_breaks()
    owed = [
        block
        for offset in range(health.ACUTE_DAYS)
        # Extras are excluded, here as everywhere: sparring and neck are things
        # a practice sometimes contains, not things the week asked for, and a
        # compliance figure that counted them could never reach 100.
        for block in (
            trainplan.prescription(program, today - dt.timedelta(days=offset), breaks)
            if program
            else []
        )
        if not block.extra
    ]
    plan_open = sum(1 for block in owed if block.key not in answered)

    vo2 = health.vo2max_trend(vo2max, today)

    return api.HealthPanel(
        series=health_series(repo),
        readiness_today=repo.readiness(today),
        sessions_today=sessions_today,
        bathroom_today=repo.bathroom_on(today),
        meals_today=repo.meals_on(today),
        drinks_today=repo.drinks_on(today),
        loads=sorted(loads.items()),
        derived=api.HealthDerived(
            acwr=health.acwr(sessions, today),
            monotony=health.monotony(sessions, today),
            strain=health.strain(sessions, today),
            week_load=int(week),
            prev_week_load=int(prev_week),
            # The rail. Each of these is a mean over whichever of its inputs
            # could be scored honestly, and each carries its own inputs across
            # so the zoom can draw them -- see the section head in `health.py`
            # for why a panel that refused composites now ships four.
            recovery=health.recovery_score(
                hrv, rhr, sleep_score, battery, stress, entries, today
            ),
            load=health.load_score(sessions, plan_open, len(owed), jump, today),
            fuel=health.fuel_score(meals, balance, today),
            hydration=health.hydration_score(
                fluid, sweat_week, visits, caffeine, alcohol, today
            ),
            weight_ema=trend[0] if trend else None,
            weight_per_week=trend[1] if trend else None,
            readiness_week=health.readiness_mean(entries, today, health.ACUTE_DAYS),
            readiness_baseline=baseline[0] if baseline else None,
            readiness_sd=baseline[1] if baseline else None,
            readiness_dip_days=health.readiness_dip(entries, today),
            sleep_week=health.scalar_mean(sleep, today),
            sleep_last=latest(sleep),
            in_bed_last=latest(in_bed),
            sleep_score_last=latest(sleep_score),
            hrv_last=latest(hrv),
            hrv_baseline=hrv_base[0] if hrv_base else None,
            hrv_dip_days=health.scalar_run(hrv, today, above=False),
            rhr_last=latest(rhr),
            rhr_baseline=rhr_base[0] if rhr_base else None,
            rhr_spike_days=health.scalar_run(rhr, today, above=True),
            body_battery_last=latest(battery),
            stress_last=latest(stress),
            # The one reading on this panel that is deliberately not gated on
            # being today's -- an outdoor run is what moves it, so most days
            # have none and "today or nothing" would blank it all month.
            vo2max_last=vo2[0] if vo2 else None,
            vo2max_on=vo2[1] if vo2 else None,
            vo2max_change=vo2[2] if vo2 else None,
            dark_urine_days=health.dark_urine_run(visits, today),
            hydration_ml=health.hydration_mean(water, today),
            liquid_days=health.liquid_days(visits, today),
            days_since_stool=health.days_since_stool(visits, today),
            intake_est=intake,
            expenditure=spend,
            balance=balance,
            balance_lb_per_week=(
                health.implied_lb_per_week(balance) if balance is not None else None
            ),
            diet_quality=health.diet_quality(meals, today),
            fluid_ml=fluid,
            caffeine_drinks=caffeine,
            alcohol_week=alcohol,
            sweat_loss_ml=sweat_week,
            vert_jump_last=jump[-1][1] if jump else None,
            vert_jump_on=jump[-1][0] if jump else None,
            vert_jump_points=len(jump),
        ),
        plan=plan,
        garmin=garmin.status(cfg),
        synced_today=[
            metric
            for metric in HealthMetric
            if (entry := repo.health_entry(metric, today)) is not None
            and entry.source is EntrySource.garmin
        ],
    )


# --- the todo list ---------------------------------------------------------


def todo_panel(cfg: Settings, repo: Repository, today: dt.date) -> api.TodoPanel:
    """Everything outstanding, plus the last fortnight of finished work.

    Open items are unbounded backwards on purpose -- an overdue reading is
    still owed however long it has been owed, and a list that quietly drops it
    after a week is a list that helps you forget. Finished ones are windowed,
    because their only remaining job is to show the week had something in it.
    """
    return api.TodoPanel(
        items=repo.todos(include_done_since=today - dt.timedelta(days=14)),
        aliases=[
            api.TodoAlias(key=key, name=s.name, calendar=s.calendar)
            for key, s in sorted(cfg.todo.subjects.items())
        ],
        default_calendar=cfg.todo.default_calendar,
        todo_calendars=cfg.todo.todo_calendars,
        recur_weeks=cfg.todo.recur_weeks,
    )


# --- the boot payload ------------------------------------------------------


def dashboard_state(cfg: Settings, repo: Repository, today: dt.date) -> api.DashboardState:
    return api.DashboardState(
        date=today,
        math=math_panel(cfg, repo, today),
        chess=chess_panel(repo, today),
        investigate=investigate_panel(
            repo,
            today,
            cfg.path("investigations"),
            cfg.editor.scrollback,
            cfg.investigate.folders,
        ),
        patterns=patterns_panel(repo, today),
        spelling=spelling_panel(cfg, repo, today),
        tricks=tricks_panel(cfg, repo, today),
        word=repo.word(today),
        games=games_panel(repo, today),
        theorems=theorems_panel(repo, today),
        papers=papers_panel(repo, today),
        health=health_panel(cfg, repo, today),
        daylog=repo.daylog(today),
        piano_practiced=repo.piano_practiced(today),
        completion=repo.completions(),
        perfect_day_streak=scoring.streak(repo.perfect_days(), today),
    )
