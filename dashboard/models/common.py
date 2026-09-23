"""Shared scalars, enums, and the model base.

Everything persisted or accepted over HTTP derives from `Base`. The strictness
here is deliberate: `extra="forbid"` is what turns a malformed LLM generation
into a loud ValidationError at the boundary, instead of a half-populated dict
that only fails three layers later in the browser.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import StrEnum
from types import MappingProxyType
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

ClockTime = Annotated[str, Field(pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$")]
"""A local wall-clock time, `HH:MM`, 24h -- the browser's, never this
process's. Exactly the form an `<input type=time>` hands back, and the form the
CHECKs in migration 37 accept, so nothing between the phone and the table has
to translate it."""


def clock_minutes(value: str) -> int:
    """`07:30` -> 450. Only ever handed a `ClockTime`, so it does not guard."""
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)


def span_hours(start: str, end: str) -> float:
    """Hours from one clock time to the next occurrence of another.

    Modulo a day, because a night crosses midnight more often than not: into
    bed at 23:40 and up at 07:10 is 7.5, and so is 00:10 to 07:40. A span of
    zero is a whole day or nothing, and callers refuse it rather than guess."""
    return ((clock_minutes(end) - clock_minutes(start)) % 1440) / 60


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


class ProblemKind(StrEnum):
    """What a stored problem is, frozen on the row.

    Narrower than `DayMode` on purpose, and the two are not the same list. The
    mode says what a *day* asked for -- and `proof`, `proof-long` and
    `proof-showcase` ask for three different problems out of the same book by
    three different rules. What comes back is one kind of object, and it is the
    object the panel renders and the scorer prices.
    """

    compute = "compute"
    """A numeric answer, marked right or wrong."""

    proof = "proof"
    """An argument, self-reported against a rubric."""


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
    """The watch's sleep *window* -- detected onset to detected wake, awake
    minutes inside it included. Misnamed: the watch cannot see you lie down,
    so the half hour before sleep arrives is never in it. What you actually
    spent in bed is `Readiness.bed`/`woke`, said on the check-in (migration
    37); this series is kept under its old name because renaming a metric is a
    table rebuild and nothing renders it."""

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
    """What the watch's hydration glyph was tapped for, in ml.

    Kept, and no longer read. `sweat_loss_ml` supersedes it as the watch's
    hydration input: the glyph is something you remember to tap and the sweat
    figure is something the watch computes, and only one of those two survives
    a February. The column stays because the rows behind it are true readings
    of the days they describe, and dropping a series to stop consulting it is
    how a log loses the ability to answer a question asked later.

    A zero day is never written: absent means "not logged", and that is
    different from "drank nothing"."""

    stress = "stress"
    """Garmin's 0-100 all-day stress average, off the same daily summary that
    already carries calories out and Body Battery.

    An input to the recovery score rather than a thing to act on. It is derived
    from HRV across the whole day, so it moves for the reasons the overnight
    number moves and for several more besides -- which is the argument for
    letting it weigh on a score and never letting it write a sentence."""

    vo2max = "vo2max"
    """ml/kg/min, as the watch estimates it.

    Updates only after an outdoor run with heart rate, so most days produce
    nothing at all. That is the metric behaving correctly rather than a sync
    that failed: a VO2max recomputed on a day you did not run would be the
    watch repeating itself, and the series is more honest with gaps in it than
    with a flat line drawn across them."""

    sweat_loss_ml = "sweat_loss_ml"
    """Millilitres the watch reckons were sweated out, summed over the day's
    activities.

    The FR165 reports it per activity, so it exists on days you trained and is
    absent on days you did not -- and absent is the right answer there, not
    zero. This is the hydration input `hydration_ml` used to be, and a better
    one for the reason that decides most things on this tab: nobody has to
    remember to produce it."""

    vert_jump = "vert_jump"
    """Vertical jump, inches. Typed by hand, on testing days only.

    Best of three, which is how a vertical is quoted everywhere else and
    therefore the only version comparable with a number from a gym. Never on
    the daily form: an empty box every morning reads as a missed log, and a
    metric measured every six weeks cannot share a surface with one measured
    every day without one of them lying about the other."""


class EntrySource(StrEnum):
    """Who wrote a health row.

    The whole reason this exists: `manual` outranks `garmin`, so a sync may
    never overwrite a correction you made by hand. See `Repository.log_health`.
    """

    manual = "manual"
    garmin = "garmin"


class InputState(StrEnum):
    """Why one input to a score is or is not carrying a number.

    This enum exists because the laptop stopped being a logging surface. While
    every metric had a box on the morning panel, a missing reading was "a hand
    entry that may still arrive" and rendering it as nothing was fine -- you
    knew what you had not typed. Now nothing gets typed, so a hole is a hole,
    and the panel has to say *which kind* out loud.

    The distinction that costs the most to get wrong is the first two against
    each other. A night the watch spent on the charger and a rest day produce
    the same absence, and one of them is a fact about your training while the
    other is a fact about your watch. Rendering either as empty -- or worse, as
    zero -- makes them the same thing on screen.
    """

    scored = "scored"
    """A reading, and enough behind it to say what the reading means."""

    no_watch_data = "no_watch_data"
    """The watch supplies this one and there is no row for the day. A charger
    night, a sync that has been failing, or a watch left on the side."""

    no_baseline = "no_baseline"
    """Readings exist; a comparison does not. HRV and resting heart rate are
    only meaningful against a fortnight of your own, so the first weeks of a
    series read as this rather than as a score computed off four numbers."""

    not_logged = "not_logged"
    """A hand-kept log that was not kept. Meals, drinks and the in-and-out log
    all read as this, and it is deliberately not the same word as the watch's
    absence: one of them is fixed by opening the phone."""

    not_asked = "not_asked"
    """Nothing to measure against today. A rest day prescribes no blocks, so
    prescribed-against-logged has no denominator -- which is a complete answer
    and not a gap."""


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

    sport = "sport"
    """A game, and the one kind here that nobody prescribed.

    Two hours of sand volleyball is real load -- it costs more to recover from
    than the twelve-minute neck circuit it used to be filed beside -- and it is
    load the plan did not ask for, which is the fact worth being able to see.
    Folding it into `other` buried it in a bucket of accessory work; folding it
    into `conditioning` would have inflated the volume the camp plan is
    measured against with work that has no intervals and no prescription behind
    it.

    So it is its own word, and nothing in `training.toml` resolves here. That
    asymmetry is the point: a week whose load came from games rather than from
    the plan reads differently, and until this existed it did not read at all.
    """

    other = "other"


SESSION_FOCUS: Mapping[str, SessionKind] = MappingProxyType(
    {
        # --- boxing ---
        "boxing.shadowboxing": SessionKind.boxing,
        "boxing.bag": SessionKind.boxing,
        "boxing.mitts": SessionKind.boxing,
        "boxing.drills": SessionKind.boxing,
        "boxing.class": SessionKind.boxing,
        # --- sparring ---
        "sparring.full": SessionKind.sparring,
        "sparring.touch": SessionKind.sparring,
        # --- cardio, which is two kinds wearing one word ---
        "cardio.hiit.core": SessionKind.conditioning,
        "cardio.hiit.lower": SessionKind.conditioning,
        "cardio.hiit.upper": SessionKind.conditioning,
        "cardio.hiit.full": SessionKind.conditioning,
        "cardio.hiit.interval_run": SessionKind.conditioning,
        "cardio.steady.incline": SessionKind.roadwork,
        "cardio.steady.run": SessionKind.roadwork,
        "cardio.steady.stairmaster": SessionKind.roadwork,
        "cardio.steady.elliptical": SessionKind.roadwork,
        # --- strength ---
        "strength.vjp.lower_french_contrast": SessionKind.strength,
        "strength.vjp.upper_french_contrast": SessionKind.strength,
        "strength.vjp.lower_concentrics": SessionKind.strength,
        "strength.vjp.upper_concentrics": SessionKind.strength,
        "strength.vjp.potentiation": SessionKind.strength,
        "strength.general.1x20": SessionKind.strength,
        "strength.general.upper": SessionKind.strength,
        "strength.general.lower": SessionKind.strength,
        "strength.general.full": SessionKind.strength,
        # --- games, which are load nobody prescribed ---
        #
        # `volleyball` is the indoor court and `sand_volleyball` is the beach,
        # and they are two leaves rather than one because they are two
        # different afternoons: sand is roughly twice the work for the same
        # scoreline, and a leaf that could not tell them apart would be
        # throwing that away at the only moment it is recoverable.
        "sport.volleyball": SessionKind.sport,
        "sport.sand_volleyball": SessionKind.sport,
        "sport.basketball": SessionKind.sport,
        "sport.football": SessionKind.sport,
        "sport.throw": SessionKind.sport,
        # --- everything the load model must not fold into strength ---
        "other.neck": SessionKind.other,
        "other.core": SessionKind.other,
        "other.mobility": SessionKind.other,
        "other.walk": SessionKind.other,
        "other.rehab": SessionKind.other,
    }
)
"""Every leaf of the session tree, and the `SessionKind` each one lands in.

`kind` answers "how much of X did I do" and is what `daily_load` and the camp
plan are written against; it is unchanged and stays the only thing scored.
This is the level below it -- what the session actually *was* -- and it is
identity in exactly the sense `DrinkName` and `MealSlug` are: grouped by,
never summed.

**The mapping is the invariant, not the list.** A leaf determines its kind, so
`cardio.steady.run` posted with `kind: boxing` is a contradiction the request
model refuses (`SessionRequest.consistent`). Without that check the two fields
would be free to disagree on the same row, and the disagreement would be
invisible -- the load model reads `kind` and would never look at the leaf that
says otherwise.

The paths are dotted because `other` and `full` and `upper` each appear under
several branches, and a flat token set could not tell those apart. They are
also why the umbrella words can differ from the stored kinds: `cardio` is not
a `SessionKind` and never becomes one -- it is one word on a screen that
resolves, one level down, into `conditioning` or `roadwork` depending on
whether the work had intervals in it. That is the distinction the load model
has always drawn and the one nobody wants to be asked for in those words at
6am.

`roadwork` consequently now means steady-state aerobic work rather than
specifically running, which is what the word means in a boxing gym anyway. An
elliptical session stores as roadwork; the leaf is what says it was an
elliptical.

Not a CHECK on the table -- see migration 33 and `plan_key` before it, which
made the same call for the same reason: this is a vocabulary that lives in a
file and gets edited, so freezing it into the schema means a migration every
time a machine is added to the gym.
"""


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


class DrinkTag(StrEnum):
    """What was in it -- and a drink can be several of these at once.

    These were five mutually exclusive *kinds* until it became clear that a lot
    of what actually gets drunk is two of them: coffee with sugar in it, a
    sports drink that is salt and sugar both. One column forced a choice
    between two true answers, and the one that got recorded was whichever the
    thumb reached first -- so the sugar in the coffee was simply never written
    down.

    Water is not a tag. Every drink here is water with something in it, which
    makes `water` the one label that would be true of all of them and
    informative about none. A plain glass is a row with no tags, and that is
    exactly what an untagged row means read back.

    `gatorade` is gone the same way. It was `electrolyte` and `sugar` together
    -- that was the entire distinction between it and electrolyte water -- and
    now that a row can be both, keeping the word would be inviting two
    spellings of one drink to disagree. The rows already logged as gatorade
    carry both tags; see the migration.

    `caffeine` counts as fluid despite the folklore. The diuretic effect is
    real and smaller than the water it arrives in; it is a tag because the
    caffeine is worth counting, not because the water in it is suspect.

    It was called `coffee` until `carbonated` arrived beside it, and the rename
    is the same mistake being corrected a second time. A tag naming a *drink*
    rather than a *substance* has nowhere to put the thing that is not that
    drink: an energy drink is caffeine and is not coffee, so under the old name
    it was either filed as coffee -- a lie the caffeine mean then averaged --
    or left untagged, which dropped its caffeine out of the score altogether.
    The word `coffee` still exists; it moved down a level to `DrinkName`, which
    is where the identity of a drink belongs and where it cannot be mistaken
    for a quantity of a substance.

    `carbonated` is the newest, and the only tag nothing scores yet. That is
    deliberate rather than an omission: it costs nothing to record and cannot
    be recovered afterwards, and it is what separates a beer from a shot and a
    soda from a juice without either of those having to become a tag of its
    own.

    `alcohol` is the odd one out twice over: it is the only tag that changes
    what `amount` means (see `DrinkLog.amount`), and the only one that takes
    the row out of the day's fluid entirely -- whatever else is on it, a rum
    and coke hydrates you by nothing.
    """

    milk = "milk"
    sugar = "sugar"
    electrolyte = "electrolyte"
    caffeine = "caffeine"
    alcohol = "alcohol"
    carbonated = "carbonated"


def canonical_tags(tags: Iterable[str]) -> list[DrinkTag]:
    """One drink's tags, deduplicated and put in `DrinkTag` order.

    The order is decided here so that a row written by the phone and a row
    written by a script read back identically, and `sugar + coffee` is never
    also stored as `coffee + sugar`. A set is the honest type and a poor thing
    to send over JSON, so the canonical *list* is what crosses the wire.
    """
    order = list(DrinkTag)
    return sorted({DrinkTag(tag) for tag in tags}, key=order.index)


class DrinkName(StrEnum):
    """What the drink actually was -- as opposed to `DrinkTag`, which is what
    was in it.

    The distinction is the whole reason there are two of these and it is worth
    being exact about, because collapsing them is the mistake that produced
    `gatorade` and then `coffee` and would produce a third one next year.

    A **tag is composition**. It is summed, averaged and scored: `day_fluid_ml`
    adds up everything that is not alcohol, `caffeine_mean` averages what is
    caffeinated, `alcohol_week` counts standard drinks. A tag has to be true of
    a *part* of the drink, and every drink carrying it has to be summable with
    every other one.

    A **name is identity**. Nothing sums it and nothing ever will. It exists to
    be grouped by -- "how many energy drinks in October" -- and to make a row
    legible a year later, when `sugar + carbonated + caffeine` is technically
    complete and tells you nothing about whether that was a Coke or a Celsius.

    So a name is never also a tag, and no reading in `health.py` may ever look
    at one. The moment a name is scored it has become a tag with a second
    spelling, and the two are then free to disagree about the same drink.

    Nothing here is required. A drink with tags and no name is the ordinary
    case and reads as "some drink like that" -- which is what "other" means on
    the phone, where declining to answer is a real answer and costs no tap.

    The list grows. It is not a CHECK on the table for exactly that reason --
    see migration 33 -- so adding a drink is an edit here and in the tree in
    `web/app/logs.js`, and no migration at all.
    """

    # carbonated
    sparkling_water = "sparkling_water"
    diet_soda = "diet_soda"

    # sugar
    oj = "oj"
    lemonade = "lemonade"
    juice = "juice"

    # sugar + carbonated
    soda = "soda"
    ginger_ale = "ginger_ale"

    # caffeine, and what gets added to it
    coffee = "coffee"
    latte = "latte"
    sweet_coffee = "sweet_coffee"
    mocha = "mocha"
    frappe = "frappe"
    diet_energy_drink = "diet_energy_drink"
    zevia = "zevia"
    energy_drink = "energy_drink"
    cola = "cola"

    # milk
    lowfat_milk = "lowfat_milk"
    whole_milk = "whole_milk"
    protein_shake = "protein_shake"
    chocolate_milk = "chocolate_milk"

    # electrolyte
    electrolyte_tab = "electrolyte_tab"
    gatorade = "gatorade"

    # alcohol
    liquor = "liquor"
    dry_wine = "dry_wine"
    beer = "beer"
    hard_seltzer = "hard_seltzer"
    cocktail = "cocktail"
    sweet_wine = "sweet_wine"
    rum_and_coke = "rum_and_coke"
    cider = "cider"


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


MealSlug = Annotated[
    str,
    Field(
        min_length=2,
        max_length=64,
        pattern=r"^[a-z0-9]+(_[a-z0-9]+)*(\.[a-z0-9]+(_[a-z0-9]+)*)?$",
    ),
]
"""What the meal was -- the identity half of the meal log, and the counterpart
of `DrinkName` (read that docstring for why identity and composition are kept
apart). Nothing scores one; `diet_quality` reads `quality` and the energy
balance reads `size`, both of which a name merely *fills in*.

A slug rather than an enum since migration 38. It was an enum of eight meals
written by hand, and 38 of the first 57 meals logged against it named none of
them: the real menu is a handful of plates with small variations, and a list
that has to be edited to learn a variation is a list that stops being used. So
the vocabulary is the `meal_name` table now, and it grows from what is logged
-- see `dashboard/menu.py` for how a name earns its place and loses it.

At most one dot, and the dot is the tree: `chicken_rice.salsa` is a variation
of `chicken_rice`, exactly as `cardio.steady.run` is a leaf under `cardio`. The
parent is read off the slug rather than stored beside it, so the two cannot
disagree. The phone makes these (`mealSlug` in `logs.js`), and
`test_logs_contract.py` holds it to this pattern -- a slug the phone can make
and the server refuses is a 422 from a kitchen, which is a lost meal."""

MEAL_LABEL_MAX = 40
"""How long a typed meal name may be. The phone's box stops at the same
number; the table allows a little more so a label is never the thing a write
fails on."""


class MealFlag(StrEnum):
    """What was in it -- the composition half, and a set, exactly as
    `DrinkTag` is.

    Four, and the shortness is the design. `quality` already carries one axis
    (how processed) and `size` carries another (how much); these are the
    things neither can see, and a fifth would be a thing answered by habit
    rather than by looking at the plate.

    `snack` is the one that does work beyond being recorded. Three logged
    apples must not satisfy "eat three meals", and without this flag they
    would -- the required task counts rows. It is a flag rather than an
    inference from `size`, because a small meal during a cut is still a meal
    and would be wrongly excluded by any rule keyed on size.

    `salty` earns its place off the training rather than the diet: sodium is
    the input that decides whether the fluid actually went anywhere, and on a
    camp with a weight cut at the end of it that is a question worth being able
    to ask backwards.

    `fried` is not the same question as `quality`. Quality is *processing* --
    a fried egg cooked at home is a 4 and is fried. One is about where the food
    came from and the other about what happened to it in the pan.

    Nothing scores these yet, which is the same bet `carbonated` is: they are
    free to record now and impossible to recover later.
    """

    protein = "protein"
    fried = "fried"
    snack = "snack"
    salty = "salty"


def canonical_flags(flags: Iterable[str]) -> list[MealFlag]:
    """One meal's flags, deduplicated and put in `MealFlag` order.

    The same function `canonical_tags` is, for the same reason: order decided
    once, here, so a row written by the phone and a row written by a script
    read back identically.
    """
    order = list(MealFlag)
    return sorted({MealFlag(flag) for flag in flags}, key=order.index)


class Game(StrEnum):
    wordle = "wordle"
    pokedoku = "pokedoku"


# Deliberately absent: `ReviewQuality`, the four SM-2 grades. The theorem
# self-check is one bit -- you remembered it or you did not -- because nothing
# reads the grade any more. A four-way scale whose only consumer is a
# histogram is a scale you will stop answering honestly.
