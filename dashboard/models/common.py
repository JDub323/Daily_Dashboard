"""Shared scalars, enums, and the model base.

Everything persisted or accepted over HTTP derives from `Base`. The strictness
here is deliberate: `extra="forbid"` is what turns a malformed LLM generation
into a loud ValidationError at the boundary, instead of a half-populated dict
that only fails three layers later in the browser.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class Base(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# --- scalars ---------------------------------------------------------------

Score = Annotated[float, Field(ge=0.0, le=1.0)]
Rating = Annotated[int, Field(ge=100, le=4000)]

# HttpUrl admits only http/https. That is the whole fix for the paper-queue
# hole -- a pasted `javascript:` URL can no longer reach the DOM, because it
# cannot get past the request model. Escaping stops being something the
# renderer has to remember.
Url = HttpUrl


# --- enums -----------------------------------------------------------------


class Track(StrEnum):
    """The elo-rated activities. One rating_event table serves all of them."""

    math = "math"
    chess = "chess"

    spelling = "spelling"
    """The spelling bee. Five results a day, one per word, each against that
    word's tier anchor -- so K falls faster here than anywhere else, which is
    right: five independent trials a day is a great deal more evidence per day
    than one problem."""

    investigate = "investigate"
    """The politics class's daily investigation, rated per subject folder in
    `area`. Its own track rather than a third area of math because `n` -- and
    so K -- must be counted separately: a year of math results should not make
    the investigation rating confident."""


class Provenance(StrEnum):
    measured = "measured"
    """Produced by a real attempt; the rating trajectory is trustworthy."""

    reconstructed = "reconstructed"
    """Imported from state.json, where the rating was interpolated by the old
    _backfill_ratings rather than measured. The date/score/ref on these rows
    are genuine; rating_before/rating_after are not, and are stored NULL."""

    lapsed = "lapsed"
    """Auto-scored: the problem was drawn and the day ended with no mark.

    The rating change is real and the numbers are trustworthy, so unlike
    `reconstructed` these rows carry ratings. What they are not is *evidence
    about skill* -- nobody attempted anything -- so they are excluded from the
    K-factor's `n` and from `success_rate`. A day you ghosted should cost you
    rating without also making the estimate more confident."""

    late = "late"
    """A lapsed day submitted afterwards, scored at `late_math_score`.

    Evidence about skill -- you did the problem -- so unlike `lapsed` it counts
    toward the K-factor's `n`. It is excluded from `success_rate`, which
    thresholds at score > 0.5: a late score is deflated by design and tops out
    at 0.40, so every late solve would read as a failure and catching up would
    look like getting worse."""

    unlapsed = "unlapsed"
    """Bookkeeping: hands back exactly what a lapse took, before its `late`
    result is applied.

    Submitting late replaces the lapse rather than stacking on it, and the log
    is append-only -- so the lapse row stays and this row cancels it. Excluded
    from `n` and from `success_rate` alike: nothing was attempted here, and
    `score`/`k` on the row are the lapse's own values carried over only because
    the columns are NOT NULL. Nothing reads them."""

    theorem = "theorem"
    """A graded theorem recall, scored `(explained + applied) / 2`.

    Rated against an opponent equal to the current rating, so the expected
    score is exactly 0.5 and the delta is `k * (score - 0.5)` -- with K=6 that
    is +3, 0 or -3, which is the whole spec, expressed in the elo arithmetic
    rather than bolted on beside it.

    Real numbers, so the rows carry ratings. Excluded from `n` all the same:
    recalling what a theorem says is a different skill from solving with it
    under time pressure, and one row a day would drive K to its floor inside
    two months -- after which nothing a *problem* did could move the rating
    much. Confidence about problem solving has to be bought with problems.

    Excluded from `success_rate` too, which reads "how often do you solve the
    daily problem". Recall is not that question, and a 0.5 cannot clear its
    threshold anyway."""


class Deck(StrEnum):
    """Which deck a recall card belongs to.

    One table, two decks, because the *machinery* is identical: a card you
    curate as you meet it, drawn once a day by how long since it last came up,
    answered in two boxes before anything is revealed, graded, and worth +/-3.
    Only three things differ, and all three are parameters rather than code --
    which rating track the result lands on, what a card is generated from, and
    what the grader is told it is looking at.
    """

    math = "math"
    """Theorems. Rated into the math track's area buckets."""

    argument = "argument"
    """Argument patterns and fallacies -- principal-agent, tragedy of the
    commons, base-rate neglect. Rated into the investigate track."""


class DayMode(StrEnum):
    """What a given weekday asks of you, from `[math.week]` in the config.

    The week used to have one shape -- a calibrated problem, every day, with
    Saturday swapped for a famous one by a constant in `content.py`. Five days
    of that converged on a single tier: the ladder is centred on the rating and
    the nearest rung is served, which is a thermostat, and a thermostat's whole
    job is to remove variance. Every batch generated between 2026-08-20 and
    2026-08-28 offered the same three tiers.

    So the week is now the unit, and its shape is config rather than code.
    """

    compute = "compute"
    """A calibrated ladder with a numeric answer -- the old behaviour, kept for
    Monday and Tuesday. Still a thermostat, and that is fine at two days a week
    rather than five."""

    proof = "proof"
    """One proof problem from the proof textbook, on the easier side: enough
    ingenuity to need playing with, not enough to eat the evening."""

    proof_long = "proof-long"
    """The two-day problem. Day one has no hints at all; day two has two, the
    second of which more or less gives it away. Solving it on day one is worth
    a large multiple of an ordinary result -- see `scoring.EARLY_SOLVE_BONUS`."""

    proof_showcase = "proof-showcase"
    """A famous proof, chosen on elegance and not on the rating."""

    reading = "reading"
    """No problem at all. A reading plan in half-hour segments, of which you
    are expected to do one or two -- unrated, and ticked by doing any of it."""

    @property
    def is_proof(self) -> bool:
        return self in (DayMode.proof, DayMode.proof_long, DayMode.proof_showcase)


class MathStatus(StrEnum):
    open = "open"
    solved = "solved"
    failed = "failed"

    partial = "partial"
    """A proof with the idea in it and a gap somewhere. Scores 0.5 before
    hints.

    Only reachable on a proof day, and it exists because the binary the compute
    days use is a bad description of a proof: "did you get 137" has an answer,
    "did you prove it" mostly has three. The rubric the batch writes from the
    book's own solution is what keeps this from being a mood -- the checkpoints
    are on screen when the button is pressed."""

    carried = "carried"
    """Day one of a two-day problem, rolled to tomorrow. Unscored and unrated:
    the result lands once, on day two.

    Reached by pressing "keep going tomorrow", and also by doing nothing at all
    -- `reconcile_math_lapses` carries an unmarked day-one attempt rather than
    lapsing it, because forgetting to press a button should not cost a full
    result on a problem you were meant to still be holding."""

    lapsed = "lapsed"
    """Drawn but never marked, and the day is over. Scored as a failure --
    only reachable through `Repository.lapse_math`, never from the client."""


class ChessStatus(StrEnum):
    open = "open"
    done = "done"


class MathOutcome(StrEnum):
    """What the client reports. The score is derived server-side."""

    solved = "solved"
    failed = "failed"

    partial = "partial"
    """Proof days only; see `MathStatus.partial`. Rejected on a compute day at
    the API boundary rather than silently scored, because a numeric answer that
    is half right is just wrong."""

    carried = "carried"
    """Day one of a two-day problem, ending the day without a result."""


class ChessOutcome(StrEnum):
    """The three self-report buttons that replaced the inline board."""

    clean = "clean"
    messy = "messy"
    failed = "failed"


class PaperStatus(StrEnum):
    surfaced = "surfaced"
    """Today's triage candidate, not yet acted on."""

    queued = "queued"
    read = "read"
    skipped = "skipped"

    cleared = "cleared"
    """Was in the queue, since read out of it."""


class HealthMetric(StrEnum):
    """The daily scalars -- one number per metric per day.

    Only `weight` is typed by hand for good. The rest are what a Garmin sync
    fills in; until it exists they are typed too, which is why every one of
    them is a plain number rather than something only an import could produce.
    """

    weight = "weight"
    """lb, first thing, before anything else."""

    sleep_hours = "sleep_hours"
    """Hours actually asleep, as the watch scores it."""

    time_in_bed = "time_in_bed"
    """Hours between lights-out and getting up. Kept apart from `sleep_hours`
    because the gap between the two *is* the signal -- nine in bed and six
    asleep is a different problem from six of each."""

    sleep_score = "sleep_score"
    """Garmin's 0-100. Was called `sleep` before the units were pinned down."""

    hrv = "hrv"
    """Overnight average, ms."""

    resting_hr = "resting_hr"
    body_battery = "body_battery"
    """At wake. Watched, not acted on -- the formula behind it is Garmin's."""

    calories_out = "calories_out"
    """Everything burned in the day, BMR included -- Garmin's
    `totalKilocalories`. Half of the energy balance; the other half is the
    meal log, and neither is worth much without the other."""

    hydration_ml = "hydration_ml"
    """What the watch's hydration glyph was tapped for, in ml. Logged by hand
    on the wrist, so it undercounts by however much you forgot -- which is why
    it is read *beside* urine colour rather than instead of it. A zero day is
    never written: absent means "not logged", and that is different from
    "drank nothing"."""


class EntrySource(StrEnum):
    """Who wrote a health row.

    The whole reason this exists: `manual` outranks `garmin`, so a sync may
    never overwrite a correction you made by hand. See `Repository.log_health`.
    """

    manual = "manual"
    garmin = "garmin"


class SessionKind(StrEnum):
    boxing = "boxing"
    """Bag, pads, drills -- the ordinary session."""

    sparring = "sparring"
    """Separate from `boxing` on purpose: it is the one that costs the most to
    recover from at the same RPE, and a camp is partly the question of how
    much of it you did."""

    roadwork = "roadwork"
    strength = "strength"
    conditioning = "conditioning"
    other = "other"


# --- what goes in and what comes out ---------------------------------------
#
# Every scale below is short on purpose, and shorter than the published one it
# is named after. Bristol has seven types and the medical charts run urine
# through eight shades; both are instruments for someone describing a symptom
# once, in a clinic, with the chart in front of them. This is a thing answered
# four to eight times a day for six months from memory, and the seven-point
# version of it would be answered as "4 or 5, call it 4" -- which is a number
# with a false precision on it rather than a finer measurement.
#
# Three and four values are what can be answered honestly in one keystroke,
# and one keystroke is the entire design constraint.


class BathroomKind(StrEnum):
    stool = "stool"
    urine = "urine"


class Stool(StrEnum):
    """Bristol, collapsed to the three answers worth telling apart.

    They map onto the chart as 6-7 / 4-5 / 1-3, and the reason to keep them is
    that each is a different message: `liquid` for several days is a gut that
    is not absorbing what you are eating, `solid` here means formed rather
    than the hard pellets Bristol's type 1 describes.
    """

    liquid = "liquid"
    soft = "soft"
    solid = "solid"


class UrineColor(StrEnum):
    """The hydration chart, four shades deep.

    `darker` is the only one that carries a warning, and it only carries it in
    a run -- see `health.dark_urine_run`, which throws away the first void of
    the day before it counts anything, because overnight urine is concentrated
    in everybody and always reads dark.
    """

    clear = "clear"
    light = "light"
    yellow = "yellow"
    darker = "darker"


class Volume(StrEnum):
    small = "small"
    medium = "medium"
    large = "large"


class DrinkKind(StrEnum):
    """The five, and the cut between them is what each one does to you.

    `electrolyte` and `gatorade` are separated on the sugar and nothing else --
    a Gatorade Zero is electrolyte water by this definition, whatever is on the
    bottle. It is the distinction that matters on a cut, where the salt is
    wanted and the two hundred calories are the thing being counted.

    `coffee` counts as fluid despite the folklore. The diuretic effect of
    caffeine is real and smaller than the water it arrives in; it is here as
    its own kind because the *caffeine* is worth counting, not because the
    water in it is suspect.

    `alcohol` is the odd one out twice over: it is the only kind whose unit is
    not twelve ounces (see `DrinkLog.amount`), and the only one that does not
    count toward the day's fluid at all.
    """

    water = "water"
    electrolyte = "electrolyte"
    gatorade = "gatorade"
    coffee = "coffee"
    alcohol = "alcohol"


class MealSize(StrEnum):
    """Roughly calories, and deliberately not calories.

    A number would be a guess wearing a lab coat; three buckets is the same
    guess with its error bars showing. `health.MEAL_KCAL` turns them back into
    numbers for the energy balance, in one place, where the assumption is
    written down and can be argued with.
    """

    small = "small"
    medium = "medium"
    large = "large"


class Game(StrEnum):
    wordle = "wordle"
    pokedoku = "pokedoku"


# Deliberately absent: `ReviewQuality`, the four SM-2 grades. The theorem
# self-check is one bit -- you remembered it or you did not -- because nothing
# reads the grade any more. A four-way scale whose only consumer is a
# histogram is a scale you will stop answering honestly.
