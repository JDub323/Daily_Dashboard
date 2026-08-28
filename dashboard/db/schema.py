"""Ordered, idempotent migrations.

`PRAGMA user_version` is the only thing that decides what has run. Migrations
are append-only: never edit a migration that has shipped, add a new one.

This replaces `_migrate`, which was a pile of `setdefault` calls plus
`_backfill_ratings` -- unordered, and with no way to know whether it had
already run, so it re-fabricated rating history on every single load.

All tables are STRICT: SQLite's default type affinity would happily store the
string "banana" in an INTEGER column, which is precisely the class of silent
corruption this rewrite exists to remove.
"""

from __future__ import annotations

import sqlite3

# Deliberately absent: any retention/pruning DELETE. The plan calls for
# bounding the unbounded tables, but the cutoff is unspecified and `daylog`
# and `day_completion` feed streak arithmetic, so a guessed cutoff silently
# breaks a streak. Pruning lands as its own migration once a policy is set.

MIGRATIONS: list[tuple[int, str]] = [
    (
        1,
        """
        -- One append-only log for both rated tracks. There is no `rating`
        -- column anywhere in this schema; the current rating is derived.
        CREATE TABLE rating_event (
            id             INTEGER PRIMARY KEY,
            track          TEXT    NOT NULL CHECK (track IN ('math', 'chess')),
            date           TEXT    NOT NULL CHECK (date IS strftime('%Y-%m-%d', date)),
            ref            TEXT    NOT NULL,
            opponent       INTEGER NOT NULL CHECK (opponent BETWEEN 100 AND 4000),
            score          REAL    NOT NULL CHECK (score BETWEEN 0.0 AND 1.0),
            k              INTEGER NOT NULL CHECK (k >= 1),
            rating_before  INTEGER,
            rating_after   INTEGER,
            provenance     TEXT    NOT NULL DEFAULT 'measured'
                                   CHECK (provenance IN ('measured', 'reconstructed')),
            -- The two invariants that keep fabricated history honest:
            -- ratings are present or absent together, and absent exactly
            -- when the row is reconstructed.
            CHECK ((rating_before IS NULL) = (rating_after IS NULL)),
            CHECK ((rating_after IS NULL) = (provenance = 'reconstructed'))
        ) STRICT;

        CREATE INDEX rating_event_track_id ON rating_event (track, id);

        CREATE TABLE math_problem (
            id            INTEGER PRIMARY KEY,
            source        TEXT    NOT NULL UNIQUE,   -- replaces math.seen
            tier          TEXT    NOT NULL,
            anchor        INTEGER NOT NULL CHECK (anchor BETWEEN 100 AND 4000),
            statement     TEXT    NOT NULL,
            answer        TEXT    NOT NULL,
            hints         TEXT    NOT NULL,          -- JSON, exactly 3
            verified_url  TEXT,
            model         TEXT    NOT NULL,
            generated_at  TEXT    NOT NULL
        ) STRICT;

        CREATE TABLE math_attempt (
            date            TEXT    PRIMARY KEY
                                    CHECK (date IS strftime('%Y-%m-%d', date)),
            problem_id      INTEGER NOT NULL REFERENCES math_problem (id),
            hints_revealed  INTEGER NOT NULL DEFAULT 0
                                    CHECK (hints_revealed BETWEEN 0 AND 3),
            status          TEXT    NOT NULL DEFAULT 'open'
                                    CHECK (status IN ('open', 'solved', 'failed')),
            score           REAL    CHECK (score IS NULL OR score BETWEEN 0.0 AND 1.0),
            CHECK ((status = 'open') = (score IS NULL))
        ) STRICT;

        -- pgn / solution / initialPly are gone with the inline board. Only
        -- `rating` still matters, as the elo opponent.
        CREATE TABLE chess_puzzle (
            id      TEXT    PRIMARY KEY,
            rating  INTEGER NOT NULL CHECK (rating BETWEEN 100 AND 4000),
            themes  TEXT    NOT NULL DEFAULT '[]',   -- JSON
            url     TEXT    NOT NULL
        ) STRICT;

        CREATE TABLE chess_attempt (
            date       TEXT PRIMARY KEY
                            CHECK (date IS strftime('%Y-%m-%d', date)),
            puzzle_id  TEXT NOT NULL REFERENCES chess_puzzle (id),
            status     TEXT NOT NULL DEFAULT 'open'
                            CHECK (status IN ('open', 'done')),
            score      REAL CHECK (score IS NULL OR score IN (0.0, 0.4, 1.0)),
            CHECK ((status = 'open') = (score IS NULL))
        ) STRICT;

        CREATE TABLE word_of_day (
            date        TEXT PRIMARY KEY
                             CHECK (date IS strftime('%Y-%m-%d', date)),
            word        TEXT NOT NULL,
            pos         TEXT NOT NULL,
            definition  TEXT NOT NULL,
            sentences   TEXT NOT NULL DEFAULT '[]',  -- JSON
            grade       TEXT                          -- JSON, NULL until graded
        ) STRICT;

        CREATE TABLE wordle_result (
            date       TEXT    PRIMARY KEY
                               CHECK (date IS strftime('%Y-%m-%d', date)),
            puzzle_no  INTEGER,                       -- "1,861" normalised to int
            guesses    INTEGER CHECK (guesses IS NULL OR guesses BETWEEN 1 AND 6),
            won        INTEGER NOT NULL CHECK (won IN (0, 1)),
            grid       TEXT    NOT NULL DEFAULT '[]', -- JSON
            CHECK (won = 0 OR guesses IS NOT NULL)    -- a win has a guess count
        ) STRICT;

        CREATE TABLE pokedoku_result (
            date     TEXT    PRIMARY KEY
                             CHECK (date IS strftime('%Y-%m-%d', date)),
            correct  INTEGER NOT NULL CHECK (correct BETWEEN 0 AND 9),
            score    INTEGER CHECK (score IS NULL OR score >= 0),
            grid     TEXT    NOT NULL DEFAULT '[]'    -- JSON
        ) STRICT;

        CREATE TABLE theorem_card (
            id         INTEGER PRIMARY KEY,
            name       TEXT    NOT NULL,
            statement  TEXT    NOT NULL,
            topic      TEXT    NOT NULL,
            due        TEXT    NOT NULL
                               CHECK (due IS strftime('%Y-%m-%d', due)),
            interval   INTEGER NOT NULL DEFAULT 1 CHECK (interval >= 1),
            ease       REAL    NOT NULL DEFAULT 2.5
                               CHECK (ease BETWEEN 1.3 AND 3.0)
        ) STRICT;

        CREATE INDEX theorem_card_due ON theorem_card (due);

        CREATE TABLE theorem_review (
            id              INTEGER PRIMARY KEY,
            card_id         INTEGER NOT NULL REFERENCES theorem_card (id) ON DELETE CASCADE,
            date            TEXT    NOT NULL
                                    CHECK (date IS strftime('%Y-%m-%d', date)),
            quality         INTEGER NOT NULL CHECK (quality BETWEEN 1 AND 4),
            interval_after  INTEGER NOT NULL CHECK (interval_after >= 1),
            ease_after      REAL    NOT NULL CHECK (ease_after BETWEEN 1.3 AND 3.0)
        ) STRICT;

        -- current / queue / archive were three lists holding one entity.
        CREATE TABLE paper (
            id           INTEGER PRIMARY KEY,
            title        TEXT    NOT NULL,
            link         TEXT    NOT NULL,
            summary      TEXT,
            published    TEXT,
            status       TEXT    NOT NULL
                                 CHECK (status IN ('surfaced', 'queued', 'read',
                                                   'skipped', 'cleared')),
            surfaced_on  TEXT,
            added        TEXT    NOT NULL,
            queue_pos    INTEGER,
            CHECK ((status = 'queued') = (queue_pos IS NOT NULL))
        ) STRICT;

        CREATE INDEX paper_queue ON paper (status, queue_pos);

        CREATE TABLE health_entry (
            metric  TEXT NOT NULL CHECK (metric IN ('weight', 'sleep')),
            date    TEXT NOT NULL
                         CHECK (date IS strftime('%Y-%m-%d', date)),
            value   REAL NOT NULL CHECK (value > 0),
            PRIMARY KEY (metric, date)     -- find-then-mutate becomes UPSERT
        ) STRICT;

        CREATE TABLE daylog (
            date  TEXT PRIMARY KEY
                       CHECK (date IS strftime('%Y-%m-%d', date)),
            text  TEXT NOT NULL
        ) STRICT;

        -- `pct` is not stored: it is done/total, and storing it alongside
        -- them let the three disagree.
        CREATE TABLE day_completion (
            date   TEXT    PRIMARY KEY
                           CHECK (date IS strftime('%Y-%m-%d', date)),
            done   INTEGER NOT NULL CHECK (done >= 0),
            total  INTEGER NOT NULL CHECK (total > 0),
            CHECK (done <= total)
        ) STRICT;

        CREATE TABLE topic_info (
            topic     TEXT PRIMARY KEY,
            textbook  TEXT NOT NULL,   -- JSON
            theorems  TEXT NOT NULL    -- JSON
        ) STRICT;
        """,
    ),
    (
        2,
        """
        -- Adds the `lapsed` state: a problem drawn and never marked,
        -- auto-scored as a failure once the day is over.
        --
        -- Both changes are CHECK constraints, which SQLite cannot ALTER, so
        -- this is the 12-step rebuild. Neither table is referenced by a
        -- foreign key, so the drop-and-rename is safe with foreign_keys ON;
        -- the copies are `SELECT` by explicit column list rather than `*` so
        -- a future column added to one and not the other fails loudly here
        -- instead of shifting values silently.

        CREATE TABLE rating_event_v2 (
            id             INTEGER PRIMARY KEY,
            track          TEXT    NOT NULL CHECK (track IN ('math', 'chess')),
            date           TEXT    NOT NULL CHECK (date IS strftime('%Y-%m-%d', date)),
            ref            TEXT    NOT NULL,
            opponent       INTEGER NOT NULL CHECK (opponent BETWEEN 100 AND 4000),
            score          REAL    NOT NULL CHECK (score BETWEEN 0.0 AND 1.0),
            k              INTEGER NOT NULL CHECK (k >= 1),
            rating_before  INTEGER,
            rating_after   INTEGER,
            provenance     TEXT    NOT NULL DEFAULT 'measured'
                                   CHECK (provenance IN ('measured', 'reconstructed',
                                                         'lapsed')),
            -- Unchanged, and still true of a lapse: only `reconstructed`
            -- fabricated its trajectory, so only `reconstructed` stores NULL.
            CHECK ((rating_before IS NULL) = (rating_after IS NULL)),
            CHECK ((rating_after IS NULL) = (provenance = 'reconstructed'))
        ) STRICT;

        INSERT INTO rating_event_v2
            (id, track, date, ref, opponent, score, k,
             rating_before, rating_after, provenance)
        SELECT id, track, date, ref, opponent, score, k,
               rating_before, rating_after, provenance
        FROM rating_event;

        DROP TABLE rating_event;
        ALTER TABLE rating_event_v2 RENAME TO rating_event;
        CREATE INDEX rating_event_track_id ON rating_event (track, id);

        CREATE TABLE math_attempt_v2 (
            date            TEXT    PRIMARY KEY
                                    CHECK (date IS strftime('%Y-%m-%d', date)),
            problem_id      INTEGER NOT NULL REFERENCES math_problem (id),
            hints_revealed  INTEGER NOT NULL DEFAULT 0
                                    CHECK (hints_revealed BETWEEN 0 AND 3),
            status          TEXT    NOT NULL DEFAULT 'open'
                                    CHECK (status IN ('open', 'solved', 'failed',
                                                      'lapsed')),
            score           REAL    CHECK (score IS NULL OR score BETWEEN 0.0 AND 1.0),
            -- A lapse is closed and carries score 0.0, so "open iff unscored"
            -- survives the new state unchanged.
            CHECK ((status = 'open') = (score IS NULL))
        ) STRICT;

        INSERT INTO math_attempt_v2 (date, problem_id, hints_revealed, status, score)
        SELECT date, problem_id, hints_revealed, status, score FROM math_attempt;

        DROP TABLE math_attempt;
        ALTER TABLE math_attempt_v2 RENAME TO math_attempt;
        """,
    ),
    (
        3,
        """
        -- Theorems stop being a spaced-repetition queue and become a deck you
        -- curate by hand, drawn from once a day.
        --
        -- `due`/`interval`/`ease` go: SM-2 decided *when* a card came back,
        -- and the replacement decides *how likely* it is to, from one number
        -- (days since it last appeared) computed from the draw log. Keeping
        -- the columns would leave two schedulers in the schema and one of
        -- them a lie.
        --
        -- `added` inherits the old `due`, which for a never-reviewed card was
        -- exactly its creation date -- the only cards that exist here.

        CREATE TABLE theorem_card_v2 (
            id         INTEGER PRIMARY KEY,
            name       TEXT    NOT NULL,
            statement  TEXT    NOT NULL,
            topic      TEXT    NOT NULL,
            added      TEXT    NOT NULL
                               CHECK (added IS strftime('%Y-%m-%d', added)),
            -- Retired, not deleted: an archived card stops being drawn but
            -- keeps its history, so the deck can be pruned without lying
            -- about what you have seen.
            archived   INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0, 1))
        ) STRICT;

        INSERT INTO theorem_card_v2 (id, name, statement, topic, added)
        SELECT id, name, statement, topic, due FROM theorem_card;

        -- One draw per day, and the date is the key: that is what makes a
        -- reload idempotent. Without it every GET rerolls the day's theorem,
        -- which is both a different card each refresh and a corrupt recency
        -- history.
        CREATE TABLE theorem_draw (
            date      TEXT    PRIMARY KEY
                              CHECK (date IS strftime('%Y-%m-%d', date)),
            card_id   INTEGER NOT NULL REFERENCES theorem_card_v2 (id)
                              ON DELETE CASCADE,
            -- NULL until you say. A drawn-but-unanswered card still counts as
            -- having appeared -- you saw the name.
            recalled  INTEGER CHECK (recalled IS NULL OR recalled IN (0, 1))
        ) STRICT;

        CREATE INDEX theorem_draw_card ON theorem_draw (card_id, date);

        -- The review log folds into the draw log: a graded review was an
        -- appearance, and `good`/`easy` (3-4) was a recall. GROUP BY date
        -- keeps the one-draw-per-day key from a history that allowed several.
        INSERT INTO theorem_draw (date, card_id, recalled)
        SELECT date, card_id, quality >= 3 FROM theorem_review GROUP BY date;

        DROP TABLE theorem_review;
        DROP TABLE theorem_card;
        ALTER TABLE theorem_card_v2 RENAME TO theorem_card;
        """,
    ),
    (
        4,
        """
        -- Per-area math ratings, alongside the pooled one rather than
        -- replacing it.
        --
        -- `area` NULL means "the pooled math rating" -- every row that exists
        -- today, unchanged. That is the whole trick: `current_rating('math')`
        -- gains `AND area IS NULL` and returns exactly what it returned
        -- before, so the lineage this table exists to protect is not touched.
        -- A result now writes two rows, one pooled and one for its area, and
        -- the two ratings evolve independently.
        --
        -- ADD COLUMN rather than the 12-step rebuild: the new column is
        -- nullable with no default, so there is nothing to backfill and no
        -- CHECK to retrofit onto existing rows. rating_event is the one table
        -- in this schema that cannot be regenerated, so it gets the smallest
        -- change that works.
        ALTER TABLE rating_event ADD COLUMN area TEXT;

        CREATE INDEX rating_event_area ON rating_event (track, area, id);

        -- What the problem was generated for. Nullable, and NULL on every
        -- problem that predates this: the topic they were drawn under can be
        -- guessed from the date and the rotation, but a guess written into a
        -- column reads afterwards as a record. The rotation is config and can
        -- be edited, which is exactly what makes the guess unreliable.
        ALTER TABLE math_problem ADD COLUMN topic TEXT;

        -- The resolved bucket, frozen here rather than looked up at scoring
        -- time -- the same rule `opponent` already follows. [math.areas] is
        -- config; if a topic is later reassigned to another area, the results
        -- it has already produced must stay where they were earned.
        ALTER TABLE math_problem ADD COLUMN area TEXT;
        """,
    ),
    (
        5,
        """
        -- Stop showing the same link twice.

        -- Every news link that has been put on screen, and when. Not the
        -- headline: the same URL under a re-written title is the same story,
        -- and the title is what changes when a desk updates a piece.
        CREATE TABLE seen_link (
            link        TEXT PRIMARY KEY,
            first_seen  TEXT NOT NULL
                             CHECK (first_seen IS strftime('%Y-%m-%d', first_seen)),
            last_seen   TEXT NOT NULL
                             CHECK (last_seen IS strftime('%Y-%m-%d', last_seen))
        ) STRICT;

        -- The window query is `last_seen >= ?`, and pruning is the same
        -- predicate reversed.
        CREATE INDEX seen_link_last ON seen_link (last_seen);

        -- Collapse papers already duplicated before the constraint existed.
        -- Two ways in: arXiv served the same top entry on two days (one row
        -- each), and `papers_today` raced itself -- check-then-insert with no
        -- constraint, so two tabs both saw "nothing surfaced today" and both
        -- inserted.
        --
        -- Keep the most advanced status per link, because that is the row
        -- carrying a decision: `read` beats `cleared` beats `queued` beats
        -- `skipped` beats an untouched `surfaced`. Ties go to the earliest
        -- sighting, so `surfaced_on` records when it first came up.
        DELETE FROM paper WHERE id NOT IN (
            SELECT id FROM (
                SELECT id, ROW_NUMBER() OVER (
                    PARTITION BY link
                    ORDER BY CASE status
                                 WHEN 'read'    THEN 0
                                 WHEN 'cleared' THEN 1
                                 WHEN 'queued'  THEN 2
                                 WHEN 'skipped' THEN 3
                                 ELSE 4
                             END,
                             surfaced_on IS NULL, surfaced_on, id
                ) AS rn
                FROM paper
            ) WHERE rn = 1
        );

        CREATE UNIQUE INDEX paper_link ON paper (link);

        -- One surfaced candidate per day, enforced rather than assumed. This
        -- is what actually closes the race: the loser of two concurrent
        -- inserts now fails instead of adding a second row for the day.
        -- Partial, because a pasted queue entry has no surfaced_on and any
        -- number of those may share the NULL.
        CREATE UNIQUE INDEX paper_surfaced_day ON paper (surfaced_on)
            WHERE surfaced_on IS NOT NULL;
        """,
    ),
    (
        6,
        """
        -- Late submission: a lapsed day you actually went back and did.
        --
        -- Two new provenances, so another CHECK rebuild of rating_event --
        -- the same 12-step dance migration 2 did to add 'lapsed', and for the
        -- same reason. Migration 4 avoided a rebuild here on the grounds that
        -- this is the one table that cannot be regenerated; that argument is
        -- about *recomputing* the rows, and this copies them verbatim by
        -- explicit column list, `area` included. Both indexes come back.
        --
        --   'late'      the result itself, scored by `late_math_score`
        --   'unlapsed'  hands back exactly what the lapse took, so the late
        --               result replaces it instead of stacking on top
        --
        -- Replacing rather than stacking is what makes the stated rule true:
        -- being late costs what two hints cost. Stacking would have charged
        -- the full lapse *and* the deflated score, which is a much steeper
        -- penalty than the one advertised.

        CREATE TABLE rating_event_v3 (
            id             INTEGER PRIMARY KEY,
            track          TEXT    NOT NULL CHECK (track IN ('math', 'chess')),
            date           TEXT    NOT NULL CHECK (date IS strftime('%Y-%m-%d', date)),
            ref            TEXT    NOT NULL,
            opponent       INTEGER NOT NULL CHECK (opponent BETWEEN 100 AND 4000),
            score          REAL    NOT NULL CHECK (score BETWEEN 0.0 AND 1.0),
            k              INTEGER NOT NULL CHECK (k >= 1),
            rating_before  INTEGER,
            rating_after   INTEGER,
            provenance     TEXT    NOT NULL DEFAULT 'measured'
                                   CHECK (provenance IN ('measured', 'reconstructed',
                                                         'lapsed', 'late', 'unlapsed')),
            area           TEXT,
            -- Unchanged, and still true of both new rows: only
            -- `reconstructed` fabricated its trajectory, so only
            -- `reconstructed` stores NULL ratings.
            CHECK ((rating_before IS NULL) = (rating_after IS NULL)),
            CHECK ((rating_after IS NULL) = (provenance = 'reconstructed'))
        ) STRICT;

        INSERT INTO rating_event_v3
            (id, track, date, ref, opponent, score, k,
             rating_before, rating_after, provenance, area)
        SELECT id, track, date, ref, opponent, score, k,
               rating_before, rating_after, provenance, area
        FROM rating_event;

        DROP TABLE rating_event;
        ALTER TABLE rating_event_v3 RENAME TO rating_event;
        CREATE INDEX rating_event_track_id ON rating_event (track, id);
        CREATE INDEX rating_event_area ON rating_event (track, area, id);

        -- Finding the lapse to cancel is a per-day lookup, and the archive
        -- reads the whole span at once.
        CREATE INDEX rating_event_date ON rating_event (track, date);
        """,
    ),
    (
        7,
        """
        -- How long the problem took, self-reported in whole minutes.
        --
        -- ADD COLUMN rather than the 12-step rebuild, on migration 4's
        -- reasoning: nullable, no default, nothing to backfill. NULL is not
        -- "zero minutes" but "no time was given", which is every attempt made
        -- before today and every one made without a timer since -- and
        -- `time_multiplier` returns 1.0 for it, so an untimed solve scores
        -- exactly what it scored before this column existed.
        --
        -- SQLite accepts a CHECK on an added column (verified, not assumed).
        -- The upper bound is a day: this is a number typed into a box, and
        -- the failure mode worth catching is a stray digit turning a 30 into
        -- a 300 and quietly floor-ing a solve that deserved a bonus.
        ALTER TABLE math_attempt ADD COLUMN minutes INTEGER
            CHECK (minutes IS NULL OR minutes BETWEEN 1 AND 1440);
        """,
    ),
    (
        8,
        """
        -- How to say the word of the day.
        --
        -- Two columns rather than one: `pronunciation` is IPA, which is exact
        -- and unreadable without training, and `respelling` is the same sound
        -- written for someone who does not read IPA. Dictionaries print both,
        -- and the pair costs nothing -- it is two more fields on a generation
        -- that was already being paid for.
        --
        -- Both nullable and both NULL on every existing row. A word already
        -- generated is not regenerated to fill them in: the row is what was
        -- served that day, and paying for a second call to backfill a field
        -- onto a word from last week would be spending real money to change
        -- the historical record.
        ALTER TABLE word_of_day ADD COLUMN pronunciation TEXT;
        ALTER TABLE word_of_day ADD COLUMN respelling TEXT;
        """,
    ),
    (
        9,
        """
        -- Theorem recall becomes something you write, and something it costs
        -- you to get wrong.
        --
        -- The old answer was one bit you awarded yourself after pressing
        -- reveal, which is not recall -- the statement was on screen before
        -- the judgement was made. Now you write two things before anything is
        -- revealed: the theorem in your own words, and a problem you needed it
        -- for. An LLM grades each, and the pair moves the rating.
        --
        -- ADD COLUMN rather than a rebuild, as migration 7 did: every column
        -- here is new and nullable, and SQLite allows a CHECK on an added
        -- column. `recalled` stays and keeps its meaning -- did you know the
        -- theorem -- so the archive's "recalled 3x" counts the same thing it
        -- always did. It is now set from `explained` instead of by hand.

        ALTER TABLE theorem_draw ADD COLUMN explanation TEXT;
        ALTER TABLE theorem_draw ADD COLUMN application TEXT;
        ALTER TABLE theorem_draw ADD COLUMN explained INTEGER
            CHECK (explained IS NULL OR explained IN (0, 1));
        ALTER TABLE theorem_draw ADD COLUMN applied INTEGER
            CHECK (applied IS NULL OR applied IN (0, 1));
        ALTER TABLE theorem_draw ADD COLUMN note TEXT;

        -- A sixth provenance, so another CHECK rebuild of rating_event -- the
        -- same dance migrations 2 and 6 did, copying every column verbatim by
        -- explicit list. All three indexes come back.
        --
        --   'theorem'  a recall result, scored (explained + applied) / 2
        --
        -- It is in NOT_ATTEMPTS on the Python side, and that is the whole
        -- reason it needs its own name rather than riding in as 'measured'.
        -- The rating change is real, so unlike 'reconstructed' these rows
        -- carry ratings. What they are not is evidence about *problem
        -- solving*: remembering what Cauchy-Schwarz says is a different skill
        -- from using it under time pressure, and letting a daily recall count
        -- toward `n` would collapse K -- 96/(1 + n/8) reaches its floor in
        -- about two months at one row a day -- so a year of theorem cards
        -- would quietly stop math results from moving the rating at all.
        -- Confidence in a problem-solving rating has to be bought with
        -- problems.

        CREATE TABLE rating_event_v4 (
            id             INTEGER PRIMARY KEY,
            track          TEXT    NOT NULL CHECK (track IN ('math', 'chess')),
            date           TEXT    NOT NULL CHECK (date IS strftime('%Y-%m-%d', date)),
            ref            TEXT    NOT NULL,
            opponent       INTEGER NOT NULL CHECK (opponent BETWEEN 100 AND 4000),
            score          REAL    NOT NULL CHECK (score BETWEEN 0.0 AND 1.0),
            k              INTEGER NOT NULL CHECK (k >= 1),
            rating_before  INTEGER,
            rating_after   INTEGER,
            provenance     TEXT    NOT NULL DEFAULT 'measured'
                                   CHECK (provenance IN ('measured', 'reconstructed',
                                                         'lapsed', 'late', 'unlapsed',
                                                         'theorem')),
            area           TEXT,
            -- Unchanged, and still true of a theorem row: only
            -- `reconstructed` fabricated its trajectory, so only
            -- `reconstructed` stores NULL ratings.
            CHECK ((rating_before IS NULL) = (rating_after IS NULL)),
            CHECK ((rating_after IS NULL) = (provenance = 'reconstructed'))
        ) STRICT;

        INSERT INTO rating_event_v4
            (id, track, date, ref, opponent, score, k,
             rating_before, rating_after, provenance, area)
        SELECT id, track, date, ref, opponent, score, k,
               rating_before, rating_after, provenance, area
        FROM rating_event;

        DROP TABLE rating_event;
        ALTER TABLE rating_event_v4 RENAME TO rating_event;
        CREATE INDEX rating_event_track_id ON rating_event (track, id);
        CREATE INDEX rating_event_area ON rating_event (track, area, id);
        CREATE INDEX rating_event_date ON rating_event (track, date);
        """,
    ),
    (
        10,
        """
        -- The daily debate: a third rated track, and the two tables behind it.
        --
        -- A fifth CHECK rebuild of rating_event, for the same reason as
        -- migrations 2, 6 and 9 and by the same 12-step dance: `track` gains
        -- 'debate'. Every column is copied verbatim by explicit list -- this
        -- is still the one table in the schema that cannot be regenerated, so
        -- it is copied, never recomputed. All three indexes come back.
        --
        -- `area` carries the issue slug, exactly as it carries the
        -- mathematical area for a math row, so per-issue debate ratings need
        -- no new column and no new query: `current_rating('debate', slug)`
        -- already works. A scored debate writes two rows, pooled and issue,
        -- which is the shape `_close_math` established.

        CREATE TABLE rating_event_v5 (
            id             INTEGER PRIMARY KEY,
            track          TEXT    NOT NULL CHECK (track IN ('math', 'chess', 'debate')),
            date           TEXT    NOT NULL CHECK (date IS strftime('%Y-%m-%d', date)),
            ref            TEXT    NOT NULL,
            opponent       INTEGER NOT NULL CHECK (opponent BETWEEN 100 AND 4000),
            score          REAL    NOT NULL CHECK (score BETWEEN 0.0 AND 1.0),
            k              INTEGER NOT NULL CHECK (k >= 1),
            rating_before  INTEGER,
            rating_after   INTEGER,
            provenance     TEXT    NOT NULL DEFAULT 'measured'
                                   CHECK (provenance IN ('measured', 'reconstructed',
                                                         'lapsed', 'late', 'unlapsed',
                                                         'theorem')),
            area           TEXT,
            CHECK ((rating_before IS NULL) = (rating_after IS NULL)),
            CHECK ((rating_after IS NULL) = (provenance = 'reconstructed'))
        ) STRICT;

        INSERT INTO rating_event_v5
            (id, track, date, ref, opponent, score, k,
             rating_before, rating_after, provenance, area)
        SELECT id, track, date, ref, opponent, score, k,
               rating_before, rating_after, provenance, area
        FROM rating_event;

        DROP TABLE rating_event;
        ALTER TABLE rating_event_v5 RENAME TO rating_event;
        CREATE INDEX rating_event_track_id ON rating_event (track, id);
        CREATE INDEX rating_event_area ON rating_event (track, area, id);
        CREATE INDEX rating_event_date ON rating_event (track, date);

        -- One session a day, date-keyed -- the rule every daily table here
        -- follows, and the thing that makes a reload idempotent.
        --
        -- `issue`, `subarea`, `angle`, `format` and `anchor` are all frozen
        -- here at open time rather than recomputed from config at read time.
        -- That is the same decision `math_problem.area` and
        -- `rating_event.opponent` record: the rotation is editable, and a
        -- session must go on meaning what it meant when it was argued.
        CREATE TABLE debate_session (
            date        TEXT    PRIMARY KEY
                                CHECK (date IS strftime('%Y-%m-%d', date)),
            issue       TEXT    NOT NULL,
            subarea     TEXT    NOT NULL,
            angle       TEXT    NOT NULL,
            motion      TEXT    NOT NULL,
            format      TEXT    NOT NULL,
            side        TEXT    NOT NULL CHECK (side IN ('pro', 'con')),
            anchor      INTEGER NOT NULL CHECK (anchor BETWEEN 100 AND 4000),

            -- Was the side assigned against the stated position. Stored rather
            -- than re-derived from the weekday, because `contra_days` is
            -- config and the bonus is already baked into `anchor`.
            contra      INTEGER NOT NULL DEFAULT 0 CHECK (contra IN (0, 1)),

            -- What you thought before, and after. The pair is the only
            -- instrument here that measures being *right* rather than being
            -- persuasive, which is why it is worth two columns and a
            -- confidence each.
            position    TEXT,
            confidence  INTEGER CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 100),
            position_after   TEXT,
            confidence_after INTEGER
                        CHECK (confidence_after IS NULL OR confidence_after BETWEEN 0 AND 100),

            readings    TEXT    NOT NULL DEFAULT '[]',  -- JSON, frozen at open
            status      TEXT    NOT NULL DEFAULT 'prep'
                                CHECK (status IN ('prep', 'briefed', 'scored', 'lapsed')),
            briefed_on  TEXT    CHECK (briefed_on IS NULL
                                       OR briefed_on IS strftime('%Y-%m-%d', briefed_on)),
            scorecard   TEXT,                           -- JSON, NULL until scored
            score       REAL    CHECK (score IS NULL OR score BETWEEN 0.0 AND 1.0),

            -- Open iff unscored, the same invariant math_attempt carries. A
            -- lapse is closed and carries 0.0, so it satisfies this too.
            CHECK ((score IS NULL) = (status IN ('prep', 'briefed'))),
            -- A session cannot have been briefed without a date to count the
            -- grace period from.
            CHECK ((briefed_on IS NULL) = (status = 'prep'))
        ) STRICT;

        -- The dossier, as rows. `dossiers/<issue>.md` is rendered from this
        -- and is never read back: the database is the record, the markdown is
        -- there to be grepped.
        CREATE TABLE debate_fact (
            id      INTEGER PRIMARY KEY,
            issue   TEXT    NOT NULL,
            date    TEXT    NOT NULL CHECK (date IS strftime('%Y-%m-%d', date)),
            claim   TEXT    NOT NULL CHECK (length(claim) >= 3),

            -- Free text, not a URL column. Usually one -- the panel offers the
            -- day's readings as one click -- but a fact can come from a book,
            -- and a schema that could not hold "Borjas, Immigration Economics,
            -- ch. 4" would be one you route around by pasting a search link.
            source  TEXT    NOT NULL CHECK (length(source) >= 4),
            stance  TEXT    NOT NULL CHECK (stance IN ('for', 'against', 'context'))
        ) STRICT;

        CREATE INDEX debate_fact_issue ON debate_fact (issue, id);
        CREATE INDEX debate_fact_date ON debate_fact (date);
        """,
    ),
    (
        11,
        """
        -- Phase 2: the overnight batch, and the week's reading.
        --
        -- No CHECK rebuild this time. Every column here is new and nullable or
        -- defaulted, and SQLite accepts a CHECK on an added column -- so this
        -- is migration 7's shape rather than migration 10's, and rating_event
        -- is not touched at all.

        -- Where today's motion came from. NULL on every session opened before
        -- this, which is honest: those were opened from the template frame,
        -- but recording that retroactively would be writing a fact from an
        -- inference. NULL reads as "before this was tracked".
        ALTER TABLE debate_session ADD COLUMN motion_source TEXT
            CHECK (motion_source IS NULL OR motion_source IN ('template', 'batch'));

        -- What makes the motion live right now: the bill number, the ruling,
        -- the date. Only the batch produces one.
        ALTER TABLE debate_session ADD COLUMN motion_note TEXT;
        ALTER TABLE debate_session ADD COLUMN motion_url TEXT;

        -- The week's reading, one row per item.
        --
        -- Rows rather than a JSON blob on the session, because two of the
        -- three things wanted from them are per-item and stateful: whether an
        -- article has been read, and which of them the day is still waiting
        -- on. A blob would mean rewriting the whole list to tick one box.
        --
        -- Keyed by the Sunday the week opens on, not by the day it is read.
        -- The list is a property of the week -- that is what makes
        -- regenerating it idempotent, and what lets Wednesday still see what
        -- Sunday was given.
        CREATE TABLE debate_reading (
            id          INTEGER PRIMARY KEY,
            week_start  TEXT    NOT NULL
                                CHECK (week_start IS strftime('%Y-%m-%d', week_start)),
            issue       TEXT    NOT NULL,
            subarea     TEXT    NOT NULL,

            kind        TEXT    NOT NULL CHECK (kind IN ('article', 'book')),
            title       TEXT    NOT NULL CHECK (length(title) >= 2),
            url         TEXT,
            author      TEXT,
            publisher   TEXT,
            published   TEXT    CHECK (published IS NULL
                                       OR published IS strftime('%Y-%m-%d', published)),
            summary     TEXT,

            -- 'left' / 'right' / 'neither' for a book; NULL for an article.
            -- A book is on a side of the argument, not of tonight's motion.
            lean        TEXT    CHECK (lean IS NULL OR lean IN ('left', 'right', 'neither')),

            -- Books are always optional; articles are required unless the
            -- generator says otherwise. A table CHECK below ties the two
            -- together so a required book cannot be written at all -- 100+
            -- book recommendations over a cycle is already the part most
            -- likely to go unread, and one that could block a day is worse.
            required    INTEGER NOT NULL DEFAULT 1 CHECK (required IN (0, 1)),

            -- NULL until ticked. A date rather than a flag: "when did I read
            -- this" is the question the dossier will want later, and a
            -- boolean cannot answer it.
            read_on     TEXT    CHECK (read_on IS NULL
                                       OR read_on IS strftime('%Y-%m-%d', read_on)),

            -- Table constraints, and they come last because SQLite says so:
            -- once a table constraint appears, no further column definition
            -- may follow it. (Putting the `required` CHECK beside the column
            -- it talks about failed here with a syntax error on the next
            -- column, which is not what the message says it is.)
            CHECK (kind = 'article' OR required = 0),

            -- One row per item per week. Regenerating a list therefore
            -- upserts rather than duplicating, and a re-run of the 3am task
            -- cannot double the week's required reading.
            UNIQUE (week_start, kind, title)
        ) STRICT;

        CREATE INDEX debate_reading_week ON debate_reading (week_start, kind, id);
        """,
    ),
    (
        12,
        """
        -- Phase 3: the theorem deck becomes two decks.
        --
        -- Argument patterns -- principal-agent, tragedy of the commons,
        -- base-rate neglect -- want exactly the machinery the theorem deck
        -- already has: curate a card when you meet one, draw one a day
        -- weighted by how long since it last appeared, answer two boxes before
        -- anything is revealed, get graded, move +/-3. Building a parallel set
        -- of tables for that would be copying a scheduler, a draw log and a
        -- grader to change which rating track the answer lands on.
        --
        -- So: a `deck` column on both tables, defaulting to 'math', which is
        -- what every existing row is.

        ALTER TABLE theorem_card ADD COLUMN deck TEXT NOT NULL DEFAULT 'math'
            CHECK (deck IN ('math', 'argument'));

        -- `theorem_draw` needs its PRIMARY KEY widened from (date) to
        -- (date, deck), so this one is the 12-step rebuild. One draw per deck
        -- per day: both decks must be able to come up on the same morning, and
        -- with the old key the second would silently lose to the first --
        -- `record_draw` inserts ON CONFLICT DO NOTHING and re-reads, so the
        -- symptom would have been the argument card quietly never appearing.
        CREATE TABLE theorem_draw_v2 (
            date         TEXT    NOT NULL
                                 CHECK (date IS strftime('%Y-%m-%d', date)),
            deck         TEXT    NOT NULL DEFAULT 'math'
                                 CHECK (deck IN ('math', 'argument')),
            card_id      INTEGER NOT NULL REFERENCES theorem_card (id)
                                 ON DELETE CASCADE,
            recalled     INTEGER CHECK (recalled IS NULL OR recalled IN (0, 1)),
            explanation  TEXT,
            application  TEXT,
            explained    INTEGER CHECK (explained IS NULL OR explained IN (0, 1)),
            applied      INTEGER CHECK (applied IS NULL OR applied IN (0, 1)),
            note         TEXT,
            PRIMARY KEY (date, deck)
        ) STRICT;

        INSERT INTO theorem_draw_v2
            (date, deck, card_id, recalled, explanation, application,
             explained, applied, note)
        SELECT date, 'math', card_id, recalled, explanation, application,
               explained, applied, note
        FROM theorem_draw;

        DROP TABLE theorem_draw;
        ALTER TABLE theorem_draw_v2 RENAME TO theorem_draw;

        -- The recency query is per deck now, so the index leads with it.
        CREATE INDEX theorem_draw_card ON theorem_draw (deck, card_id, date);
        """,
    ),
    (
        13,
        """
        -- The spelling bee: five words a day, spoken, on their own elo track.
        --
        -- The sixth CHECK rebuild of rating_event, by the same 12-step dance
        -- as migrations 2, 6, 9, 10 and 12, copying every column verbatim by
        -- explicit list. `track` gains 'spelling'. All three indexes come back.

        CREATE TABLE rating_event_v6 (
            id             INTEGER PRIMARY KEY,
            track          TEXT    NOT NULL
                                   CHECK (track IN ('math', 'chess', 'debate', 'spelling')),
            date           TEXT    NOT NULL CHECK (date IS strftime('%Y-%m-%d', date)),
            ref            TEXT    NOT NULL,
            opponent       INTEGER NOT NULL CHECK (opponent BETWEEN 100 AND 4000),
            score          REAL    NOT NULL CHECK (score BETWEEN 0.0 AND 1.0),
            k              INTEGER NOT NULL CHECK (k >= 1),
            rating_before  INTEGER,
            rating_after   INTEGER,
            provenance     TEXT    NOT NULL DEFAULT 'measured'
                                   CHECK (provenance IN ('measured', 'reconstructed',
                                                         'lapsed', 'late', 'unlapsed',
                                                         'theorem')),
            area           TEXT,
            CHECK ((rating_before IS NULL) = (rating_after IS NULL)),
            CHECK ((rating_after IS NULL) = (provenance = 'reconstructed'))
        ) STRICT;

        INSERT INTO rating_event_v6
            (id, track, date, ref, opponent, score, k,
             rating_before, rating_after, provenance, area)
        SELECT id, track, date, ref, opponent, score, k,
               rating_before, rating_after, provenance, area
        FROM rating_event;

        DROP TABLE rating_event;
        ALTER TABLE rating_event_v6 RENAME TO rating_event;
        CREATE INDEX rating_event_track_id ON rating_event (track, id);
        CREATE INDEX rating_event_area ON rating_event (track, area, id);
        CREATE INDEX rating_event_date ON rating_event (track, date);

        -- One round a day, date-keyed, like every other daily table here --
        -- which is what makes a reload idempotent rather than a reroll.
        --
        -- `words` is the drawn list frozen at open time: word, tier and
        -- anchor, exactly as `math_problem` freezes its anchor. The pool and
        -- the tier table are both editable, and a round has to go on being
        -- scored against what it was drawn as.
        CREATE TABLE spelling_round (
            date     TEXT    PRIMARY KEY
                             CHECK (date IS strftime('%Y-%m-%d', date)),
            words    TEXT    NOT NULL,           -- JSON, frozen at draw time
            answers  TEXT    NOT NULL DEFAULT '[]',  -- JSON, what you typed
            correct  INTEGER CHECK (correct IS NULL OR correct >= 0),
            status   TEXT    NOT NULL DEFAULT 'open'
                             CHECK (status IN ('open', 'done')),
            -- Open iff unscored, the invariant every attempt table here has.
            CHECK ((status = 'open') = (correct IS NULL))
        ) STRICT;

        -- Every word ever served, and how it went. This is the draw's memory:
        -- never-seen words go first, then ones you have missed and are due
        -- again, then whatever is least recent. Keyed by the word itself, so
        -- editing spelling.json cannot orphan a history.
        CREATE TABLE spelling_seen (
            word        TEXT    PRIMARY KEY,
            tier        TEXT    NOT NULL,
            first_seen  TEXT    NOT NULL
                                CHECK (first_seen IS strftime('%Y-%m-%d', first_seen)),
            last_seen   TEXT    NOT NULL
                                CHECK (last_seen IS strftime('%Y-%m-%d', last_seen)),
            asked       INTEGER NOT NULL DEFAULT 0 CHECK (asked >= 0),
            missed      INTEGER NOT NULL DEFAULT 0 CHECK (missed >= 0),
            CHECK (missed <= asked)
        ) STRICT;

        CREATE INDEX spelling_seen_tier ON spelling_seen (tier, last_seen);
        """,
    ),
    (
        14,
        """
        -- The portfolio lock: Windows Hello credentials, public halves only.
        --
        -- No rebuild and nothing else touched -- this is a new table on its
        -- own, and rating_event is not involved.
        --
        -- Storing this in `dashboard.db` rather than a file beside it is on
        -- purpose. There is no secret in the row: WebAuthn's private key never
        -- leaves the TPM, and everything here is what the browser would hand
        -- to any site that asked. What the database gives it is the gitignore
        -- entry that already exists and the backup story the rest of the
        -- personal data already has -- a credential in an untracked JSON file
        -- beside it would be one more thing to remember.
        --
        -- Several rows are allowed. One laptop needs one, but "enrol the
        -- desktop as well" should not mean deleting the credential that
        -- currently works and hoping the new one takes.
        CREATE TABLE webauthn_credential (
            -- base64url, as the browser reports it. TEXT rather than BLOB so
            -- the allowCredentials list is a straight SELECT with no encoding
            -- step between the database and the page.
            id          TEXT    PRIMARY KEY,
            public_key  BLOB    NOT NULL,   -- SPKI DER, from getPublicKey()
            alg         INTEGER NOT NULL,   -- COSE identifier: -7, -8 or -257

            -- The authenticator's own counter, for detecting a cloned
            -- credential. Windows Hello reports a constant 0 and the spec
            -- allows that, so this is frequently never anything else -- see
            -- webauthn.verify_assertion, which only treats a *regression* as
            -- evidence.
            sign_count  INTEGER NOT NULL DEFAULT 0 CHECK (sign_count >= 0),

            label       TEXT    NOT NULL DEFAULT '',
            created     TEXT    NOT NULL
                                CHECK (created IS strftime('%Y-%m-%d', created)),
            last_used   TEXT    CHECK (last_used IS NULL
                                       OR last_used IS strftime('%Y-%m-%d', last_used))
        ) STRICT;
        """,
    ),
    (
        15,
        """
        -- The trick of the day: one identity or technique from the week's
        -- topic, taught by a set of problems.
        --
        -- No rebuild of rating_event, and that is the design rather than an
        -- economy. A trick result rates on the *math* track in the `tricks`
        -- area, which is deliberately not one of the [math.areas] buckets --
        -- the same move `ARGUMENT_AREA` makes on the debate track, for the same
        -- two reasons. It keeps the result out of the per-area strip and the
        -- retrospective chart, both of which iterate the configured areas; and
        -- because `area IS NULL` is the pooled rating, writing only an area row
        -- leaves the headline math rating untouched. Applying eight problems a
        -- day to the number that means "how well do you solve competition
        -- problems" would drive its K to the floor inside a fortnight and stop
        -- the problems themselves from moving it, which is exactly the argument
        -- migration 9 made about theorem recalls.

        CREATE TABLE trick (
            id         INTEGER PRIMARY KEY,
            name       TEXT    NOT NULL,
            statement  TEXT    NOT NULL,
            idea       TEXT    NOT NULL DEFAULT '',
            topic      TEXT    NOT NULL,

            -- 'technique' is the fallback for a week whose topic has no
            -- computational shortcut in it. Recorded rather than smoothed over,
            -- so it is visible how often the coupling to the rotation is
            -- costing the day its trick.
            kind       TEXT    NOT NULL DEFAULT 'trick'
                               CHECK (kind IN ('trick', 'technique')),
            added      TEXT    NOT NULL
                               CHECK (added IS strftime('%Y-%m-%d', added))
        ) STRICT;

        -- No repeats, ever. Case-insensitive because the do-not-repeat list is
        -- a prompt payload and a model that has been told about "Vieta's
        -- formulas" will happily offer "Vieta's Formulas" back.
        CREATE UNIQUE INDEX trick_name ON trick (name COLLATE NOCASE);

        -- One round a day, date-keyed like every other daily table here. That
        -- matters more than usual: rerolling this one costs a generation, not
        -- just a different puzzle.
        --
        -- `problems` is the whole set frozen at draw time -- prompts, answers,
        -- accepted alternatives and worked lines -- for the reason
        -- `spelling_round.words` is frozen: the round has to go on being marked
        -- against what it was actually asked as.
        CREATE TABLE trick_round (
            date         TEXT    PRIMARY KEY
                                 CHECK (date IS strftime('%Y-%m-%d', date)),
            trick_id     INTEGER NOT NULL REFERENCES trick (id),

            -- The older trick that one problem in the set reaches back to.
            -- NULL on the first day and whenever the draw found nothing off
            -- cooldown -- the set is then simply one problem shorter on the
            -- new trick's own account.
            callback_id  INTEGER REFERENCES trick (id),

            problems     TEXT    NOT NULL,              -- JSON, frozen at draw
            answers      TEXT    NOT NULL DEFAULT '[]', -- JSON, what you typed
            correct      INTEGER CHECK (correct IS NULL OR correct >= 0),
            status       TEXT    NOT NULL DEFAULT 'open'
                                 CHECK (status IN ('open', 'done')),

            -- The optional problem, kept apart from the marked set. It is
            -- free text graded by a model rather than a string compare, and it
            -- can be answered after the rest of the set is closed -- so it
            -- cannot ride in `answers` without making "the round is done" mean
            -- two different things.
            challenge        TEXT,
            challenge_ok     INTEGER CHECK (challenge_ok IS NULL
                                            OR challenge_ok IN (0, 1)),
            challenge_note   TEXT,

            -- Open iff unscored, the invariant every attempt table here has.
            CHECK ((status = 'open') = (correct IS NULL)),
            -- A graded challenge has an answer behind it.
            CHECK (challenge_ok IS NULL OR challenge IS NOT NULL)
        ) STRICT;

        -- "Which trick has gone longest without a callback" is the draw, and it
        -- reads this column.
        CREATE INDEX trick_round_callback ON trick_round (callback_id, date);
        """,
    ),
    (
        16,
        """
        -- Every time the quick-add box was told it guessed the wrong calendar.
        --
        -- This table is the training set that the account's own history cannot
        -- provide. The history is free but noisy: `Meals` holds "Learn Python"
        -- and "Schedule Week", `Homework` holds six birthdays and "do taxes",
        -- so a model fitted on it alone learns filing habits that are not
        -- really habits. A correction is the one label that was given
        -- deliberately, about a phrasing that actually gets typed, so these
        -- rows carry more weight in the fit than the history does.
        --
        -- `predicted` is kept alongside `chosen` rather than being overwritten
        -- by it: the pair is the only record of whether the classifier is
        -- getting better, and a table of right answers with the wrong ones
        -- discarded cannot answer that.
        CREATE TABLE calendar_correction (
            id          INTEGER PRIMARY KEY,
            created     TEXT    NOT NULL
                                CHECK (created IS strftime('%Y-%m-%dT%H:%M:%S', created)),
            -- What was typed, minus the date and time -- the same string the
            -- classifier saw, so a row can be replayed exactly.
            text        TEXT    NOT NULL CHECK (length(text) BETWEEN 1 AND 500),
            predicted   TEXT,
            chosen      TEXT    NOT NULL CHECK (length(chosen) BETWEEN 1 AND 200),
            source      TEXT    NOT NULL
                                CHECK (source IN ('rule', 'model', 'correction', 'fallback'))
        ) STRICT;

        -- The fit reads the whole table on every retrain, newest first.
        CREATE INDEX calendar_correction_created ON calendar_correction (created);
        """,
    ),
    (
        17,
        """
        -- The health tab becomes a training log, because the watch changed and
        -- the question changed with it. Two numbers on two charts is not
        -- something you can act on; "your load is 60% of your four-week norm"
        -- is.
        --
        -- `metric` is part of the table definition, so widening it is a rebuild
        -- rather than an ALTER -- the same dance rating_event has done twice.
        --
        -- `source` is not decoration. It carries the one rule that keeps a
        -- later import from being destructive: a hand-typed row is never
        -- overwritten by a sync. Without it, correcting a bad sleep reading
        -- means correcting it again tomorrow, and the morning after that.
        CREATE TABLE health_entry_v2 (
            metric  TEXT NOT NULL CHECK (metric IN (
                        'weight', 'sleep_hours', 'time_in_bed',
                        'sleep_score', 'hrv', 'resting_hr', 'body_battery')),
            date    TEXT NOT NULL
                         CHECK (date IS strftime('%Y-%m-%d', date)),
            value   REAL NOT NULL CHECK (value > 0),
            source  TEXT NOT NULL DEFAULT 'manual'
                         CHECK (source IN ('manual', 'garmin')),
            PRIMARY KEY (metric, date)     -- find-then-mutate becomes UPSERT
        ) STRICT;

        -- `sleep` becomes `sleep_score`. It always was the score -- the panel
        -- that wrote it was labelled "sleep score" and charted a 0-100 number
        -- -- so this is a rename, and the rows carry across. The name is
        -- changed because `sleep_hours` now sits beside it, and two columns
        -- where one is called "sleep" and the other "sleep hours" is an
        -- invitation to put hours in the wrong one.
        INSERT INTO health_entry_v2 (metric, date, value, source)
            SELECT CASE metric WHEN 'sleep' THEN 'sleep_score' ELSE metric END,
                   date, value, 'manual'
              FROM health_entry;

        DROP TABLE health_entry;
        ALTER TABLE health_entry_v2 RENAME TO health_entry;

        -- The morning questionnaire: the Hooper index plus motivation.
        --
        -- Every item runs 1 = worst, 5 = best, INCLUDING the three published
        -- the other way round (fatigue, soreness and stress are normally
        -- 1 = none). Uniform direction is worth diverging from the literature
        -- for: a scale where four items agree and one is inverted is a sign
        -- error waiting to happen, and it would happen silently -- the
        -- composite would still be a plausible number, just wrong. The panel
        -- labels carry the direction so the answer matches the intent.
        CREATE TABLE readiness (
            date           TEXT PRIMARY KEY
                                CHECK (date IS strftime('%Y-%m-%d', date)),
            sleep_quality  INTEGER NOT NULL CHECK (sleep_quality BETWEEN 1 AND 5),
            fatigue        INTEGER NOT NULL CHECK (fatigue       BETWEEN 1 AND 5),
            soreness       INTEGER NOT NULL CHECK (soreness      BETWEEN 1 AND 5),
            stress         INTEGER NOT NULL CHECK (stress        BETWEEN 1 AND 5),
            motivation     INTEGER NOT NULL CHECK (motivation    BETWEEN 1 AND 5)
        ) STRICT;

        -- Sessions, and deliberately many per day -- a two-a-day is an
        -- ordinary week in camp, and PRIMARY KEY (date) would have the
        -- evening's sparring quietly replace the morning's run. Load sums over
        -- the day.
        --
        -- rpe x minutes is the point of this table. It is the load metric that
        -- survives contact with a sport the watch cannot read: wrist heart rate
        -- under wraps and gloves is not something a training decision should
        -- rest on.
        CREATE TABLE training_session (
            id       INTEGER PRIMARY KEY,
            date     TEXT NOT NULL
                          CHECK (date IS strftime('%Y-%m-%d', date)),
            kind     TEXT NOT NULL CHECK (kind IN (
                          'boxing', 'sparring', 'roadwork', 'strength',
                          'conditioning', 'other')),
            rpe      INTEGER NOT NULL CHECK (rpe     BETWEEN 1 AND 10),
            minutes  INTEGER NOT NULL CHECK (minutes BETWEEN 1 AND 600),
            note     TEXT CHECK (note IS NULL OR length(note) BETWEEN 1 AND 200),
            -- NULL when hand-logged; the import's idempotency key when it is
            -- not, so re-running a sync cannot double-count a session.
            garmin_activity_id INTEGER UNIQUE
        ) STRICT;

        -- Every read here is a window ending today: "the last 28 days".
        CREATE INDEX training_session_date ON training_session (date);
        """,
    ),
    (
        18,
        """
        -- The day splits into two layers: a required core the ring counts, and
        -- everything else, which now pays points instead of being owed.
        --
        -- `bonus` is stored rather than derived because the points come from
        -- *today's* chips. A month from now the client may weigh the optional
        -- half differently, or not offer half of it, and what a given day
        -- actually earned would be unrecoverable -- the same reason `done` and
        -- `total` sit in the row instead of being recomputed from the panels.
        --
        -- Defaulted to 0, which reads every pre-existing row as "core only".
        -- That is not a lie about those days: they were counted under a
        -- denominator that already included the optional work.
        --
        -- No upper CHECK. `total` bounds the required half; the optional half
        -- has no ceiling worth freezing into the schema.
        ALTER TABLE day_completion ADD COLUMN bonus INTEGER NOT NULL DEFAULT 0
            CHECK (bonus >= 0);
        """,
    ),
    (
        19,
        """
        -- Which prescribed block this session answers, or NULL for one logged
        -- freehand. `training.toml` says what the day asked for; this is the
        -- only place the two layers touch.
        --
        -- TEXT and not a foreign key, and that is not laziness: the plan lives
        -- in a file that is hand-edited and regenerated from a spreadsheet, so
        -- there is no table to point at and no referential integrity to be had.
        -- What there is instead is a key shaped `<date>:<token>` -- no ordinal,
        -- no weekday -- so the one edit actually expected (moving the neck
        -- workout to the other end of the day) leaves rows already written
        -- pointing at a key that simply no longer appears. That is correct:
        -- the key is provenance, a record of what the plan said at the time,
        -- not a live pointer into a file that has since changed.
        ALTER TABLE training_session ADD COLUMN plan_key TEXT
            CHECK (plan_key IS NULL OR length(plan_key) BETWEEN 3 AND 64);

        -- One session per prescribed block. Partial, because NULL is the
        -- ordinary case -- freehand logging stays unrestricted, and two-a-days
        -- that answer no plan are still two rows.
        --
        -- This is the guard that makes the widgets idempotent: a card
        -- double-submitted, or resubmitted after a rollover, cannot quietly
        -- log the same practice twice and double its load for the day.
        CREATE UNIQUE INDEX training_session_plan_key
            ON training_session (plan_key) WHERE plan_key IS NOT NULL;
        """,
    ),
    (
        20,
        """
        -- What goes in and what comes out, plus the two scalars the watch can
        -- fill in that the old CHECK had no room for.
        --
        -- `metric` is part of the table definition, so widening it is a rebuild
        -- -- the same dance migrations 2, 6, 9, 10, 12 and 17 did. Every row
        -- carries across verbatim, `source` included, so a hand correction made
        -- before today still outranks tonight's sync afterwards.
        CREATE TABLE health_entry_v3 (
            metric  TEXT NOT NULL CHECK (metric IN (
                        'weight', 'sleep_hours', 'time_in_bed',
                        'sleep_score', 'hrv', 'resting_hr', 'body_battery',
                        'calories_out', 'hydration_ml')),
            date    TEXT NOT NULL
                         CHECK (date IS strftime('%Y-%m-%d', date)),
            value   REAL NOT NULL CHECK (value > 0),
            source  TEXT NOT NULL DEFAULT 'manual'
                         CHECK (source IN ('manual', 'garmin')),
            PRIMARY KEY (metric, date)
        ) STRICT;

        INSERT INTO health_entry_v3 (metric, date, value, source)
            SELECT metric, date, value, source FROM health_entry;

        DROP TABLE health_entry;
        ALTER TABLE health_entry_v3 RENAME TO health_entry;

        -- One row per visit. Many per day by definition, and not a scalar:
        -- the whole signal is in the *pattern* across a day, so an average
        -- would throw away the only thing worth reading.
        --
        -- `hour` and not a timestamp. Rounded to the hour is what can be
        -- answered truthfully from memory thirty seconds later, and a minute
        -- field would invite a precision the log does not have -- as well as
        -- another keystroke, which is the budget this whole feature is built
        -- against. It is the browser's hour, never this process's, for the
        -- same reason `date` is: [Invariant 1].
        --
        -- One table for both kinds rather than two, because every read here
        -- is "what happened today" in time order and two tables would have
        -- every one of them be a UNION. The CHECK at the bottom is what keeps
        -- that from being sloppy: each kind carries exactly its own field and
        -- exactly one of them is ever non-NULL, so there is no row that can
        -- describe the colour of a stool or the solidity of a urination.
        CREATE TABLE bathroom_log (
            id        INTEGER PRIMARY KEY,
            date      TEXT NOT NULL
                           CHECK (date IS strftime('%Y-%m-%d', date)),
            hour      INTEGER NOT NULL CHECK (hour BETWEEN 0 AND 23),
            kind      TEXT NOT NULL CHECK (kind IN ('stool', 'urine')),
            solidity  TEXT CHECK (solidity IS NULL
                                  OR solidity IN ('liquid', 'soft', 'solid')),
            color     TEXT CHECK (color IS NULL
                                  OR color IN ('clear', 'light', 'yellow', 'darker')),
            volume    TEXT NOT NULL CHECK (volume IN ('small', 'medium', 'large')),
            CHECK (
                (kind = 'stool' AND solidity IS NOT NULL AND color IS NULL)
             OR (kind = 'urine' AND color IS NOT NULL AND solidity IS NULL)
            )
        ) STRICT;

        CREATE INDEX bathroom_log_date ON bathroom_log (date);

        -- Meals. Same shape and the same reasoning: many a day, an hour rather
        -- than a timestamp, no upsert -- a second lunch is a second row.
        --
        -- `quality` is how *processed* it was, 1 = a burger, 5 = chicken and
        -- rice and something green. Not calories (that is `size`), not
        -- macros, not a diet score. One axis, because it is answered from a
        -- kitchen with one hand.
        CREATE TABLE meal_log (
            id       INTEGER PRIMARY KEY,
            date     TEXT NOT NULL
                          CHECK (date IS strftime('%Y-%m-%d', date)),
            hour     INTEGER NOT NULL CHECK (hour BETWEEN 0 AND 23),
            size     TEXT NOT NULL CHECK (size IN ('small', 'medium', 'large')),
            quality  INTEGER NOT NULL CHECK (quality BETWEEN 1 AND 5),
            note     TEXT CHECK (note IS NULL OR length(note) BETWEEN 1 AND 200)
        ) STRICT;

        CREATE INDEX meal_log_date ON meal_log (date);
        """,
    ),
    (
        21,
        """
        -- The todo list. One row per *occurrence*, never per repetition:
        -- "every tuesday" writes twelve rows and twelve Google events, because
        -- `gcalcli add` has no recurrence flag and because deleting one week's
        -- reading has to leave the other weeks standing. `series` ties them
        -- back together for display; it is a grouping key, not a parent row.
        CREATE TABLE todo (
            id          INTEGER PRIMARY KEY,
            created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),

            -- What was typed, before aliases were expanded. Kept so the box
            -- can put a mistaken line back the way it was written rather than
            -- the way it was understood.
            raw         TEXT    NOT NULL,
            title       TEXT    NOT NULL CHECK (length(title) BETWEEN 1 AND 300),
            subject     TEXT,   -- the expanded alias, or NULL for a plain task
            calendar    TEXT    NOT NULL,

            due_date    TEXT    NOT NULL
                                CHECK (due_date IS strftime('%Y-%m-%d', due_date)),
            -- NULL together: an all-day item is the normal case and a timed
            -- one is the exception, so the absence of a clock is the default
            -- rather than a sentinel hour.
            start_time  TEXT    CHECK (start_time IS NULL OR length(start_time) = 5),
            end_time    TEXT    CHECK (end_time IS NULL OR length(end_time) = 5),

            series      TEXT,   -- shared by every occurrence of one repetition

            -- The handle on the Google event. `event_id` is decoded out of the
            -- URL that `gcalcli add --details url` prints; either may be NULL
            -- when that decode fails, and the pair being NULL is precisely the
            -- condition under which completing an item cannot delete anything
            -- and says so instead of guessing at a search string.
            event_id    TEXT,
            event_url   TEXT,

            done_at     TEXT,
            -- Whether the Google event is gone. Separate from `done_at`
            -- because the two genuinely come apart: the item is finished the
            -- moment it is ticked, and the delete is a network call that can
            -- fail afterwards without un-finishing it.
            event_deleted INTEGER NOT NULL DEFAULT 0 CHECK (event_deleted IN (0, 1)),

            -- Table-level, and therefore last: SQLite allows no column
            -- definitions after the first table constraint.
            CHECK ((start_time IS NULL) = (end_time IS NULL))
        ) STRICT;

        CREATE INDEX todo_due ON todo (due_date);
        CREATE INDEX todo_open ON todo (done_at, due_date);
        CREATE INDEX todo_series ON todo (series) WHERE series IS NOT NULL;
        """,
    ),
    (
        22,
        """
        -- The spelling tiers were renamed: everyday/tricky/nasty/brutal became
        -- easy/medium/difficult/impossible. The names were always arbitrary
        -- labels on four fixed anchors, so nothing about the scale moves --
        -- but the old names are written into two places that outlive
        -- config.toml, and both have to follow or the draw quietly breaks.
        --
        -- `spelling_seen.tier` is the one that would break it. The draw fills
        -- each rung from `[w for w in pool if w.tier == rung]` and then reads
        -- this table for cooldowns; a pool relabelled `easy` against a history
        -- still saying `everyday` is not an error, it is every word looking
        -- never-seen, which would re-ask the whole pool from the top.
        UPDATE spelling_seen SET tier = 'easy'       WHERE tier = 'everyday';
        UPDATE spelling_seen SET tier = 'medium'     WHERE tier = 'tricky';
        UPDATE spelling_seen SET tier = 'difficult'  WHERE tier = 'nasty';
        UPDATE spelling_seen SET tier = 'impossible' WHERE tier = 'brutal';

        -- `spelling_round.words` is the drawn list frozen as JSON, and it is
        -- frozen precisely so a round goes on being scored as it was drawn.
        -- The anchor travels in that JSON beside the tier, so the rewrite here
        -- is cosmetic -- it keeps a finished round from displaying a tier name
        -- that no longer exists anywhere else.
        UPDATE spelling_round SET words =
            replace(replace(replace(replace(words,
                '"everyday"', '"easy"'),
                '"tricky"',   '"medium"'),
                '"nasty"',    '"difficult"'),
                '"brutal"',   '"impossible"');
        """,
    ),
    (
        23,
        """
        -- The investigation pool: things to look into, drawn one a day.
        --
        -- The class hands out a list of them; this is where a list lives until
        -- a day picks something off it. One row per topic, and the row outlives
        -- the day that drew it: `used_on` is set when the write-up is graded,
        -- not when the topic is drawn. That is the whole of the "returns to the
        -- pool" rule -- a day that draws and then writes nothing leaves
        -- `used_on` NULL, so tomorrow's draw sees the topic again and there is
        -- nothing to reconcile at midnight. Compare `debate_session`, which
        -- needed a lapse sweep precisely because it recorded the claim on the
        -- day rather than leaving the pool alone.
        CREATE TABLE investigation (
            id         INTEGER PRIMARY KEY,
            added_on   TEXT NOT NULL
                            CHECK (added_on IS strftime('%Y-%m-%d', added_on)),
            topic      TEXT NOT NULL CHECK (length(topic) BETWEEN 3 AND 300),

            -- Investigated and graded. NULL means still in the pool.
            used_on    TEXT CHECK (used_on IS NULL
                                   OR used_on IS strftime('%Y-%m-%d', used_on)),

            -- Struck off by hand: gone stale, superseded by a later list, or
            -- simply not worth a day. Deliberately not the same column as
            -- `used_on` -- the two say opposite things about whether the topic
            -- was ever looked into, and collapsing them would make the pool's
            -- own history a lie.
            cleared_on TEXT CHECK (cleared_on IS NULL
                                   OR cleared_on IS strftime('%Y-%m-%d', cleared_on))
        ) STRICT;

        -- "Open" is read by the draw, counted by the panel and checked against
        -- by the paste box, so the predicate is written once here rather than
        -- three times over in Python.
        CREATE INDEX investigation_pool ON investigation (id)
            WHERE used_on IS NULL AND cleared_on IS NULL;

        -- Case-folded, and scoped to the open pool. Pasting Monday's list again
        -- on Tuesday should be a no-op, but a topic investigated in September
        -- may fairly come round again in November -- so the constraint has to
        -- lapse when the row does.
        CREATE UNIQUE INDEX investigation_open_topic ON investigation (lower(topic))
            WHERE used_on IS NULL AND cleared_on IS NULL;

        -- One draw a day, keyed by the date, so a reload cannot reroll it --
        -- the contract `theorem_draw` and `debate_session` already hold.
        CREATE TABLE investigation_day (
            date             TEXT PRIMARY KEY
                                  CHECK (date IS strftime('%Y-%m-%d', date)),
            investigation_id INTEGER NOT NULL REFERENCES investigation (id),
            drawn_at         TEXT NOT NULL
                                  DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
        ) STRICT;
        """,
    ),
    (
        24,
        """
        -- Where the write-up for a topic lives, relative to [paths].
        -- investigations.
        --
        -- On the topic, not on the day, and that is the whole point. A topic
        -- drawn on Tuesday and left unwritten goes back in the pool; when it
        -- comes round again a fortnight later the paragraphs started on
        -- Tuesday are still in the file and the editor opens on them. Keying
        -- the file by date would have made that a new empty buffer.
        ALTER TABLE investigation ADD COLUMN path TEXT;

        -- What was submitted that day, and when.
        --
        -- A snapshot, deliberately duplicating the file. The file goes on
        -- being editable forever -- it is a file -- and a grade has to stay
        -- attached to the text that earned it, so this column is what the
        -- prompt quotes and what the ballot answers. The file is the source;
        -- this is the submission.
        ALTER TABLE investigation_day ADD COLUMN write_up TEXT;
        ALTER TABLE investigation_day ADD COLUMN written_at TEXT;
        """,
    ),
]

SCHEMA_VERSION = max(v for v, _ in MIGRATIONS)


def migrate(conn: sqlite3.Connection) -> int:
    """Apply every migration newer than the file's user_version.

    Each migration runs inside its own transaction together with the
    user_version bump, so a failure part-way leaves the version untouched and
    the migration is retried whole on the next start rather than half-applied.
    """
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    applied = 0
    for version, sql in sorted(MIGRATIONS):
        if version <= current:
            continue
        # BEGIN/COMMIT live inside the script rather than around it:
        # executescript issues an implicit COMMIT for any pending transaction
        # before it runs, so an outer BEGIN would be discarded and the DDL
        # would land unprotected. PRAGMA takes no bound parameter, but the
        # version is an int literal from this module, never user input.
        script = f"BEGIN;\n{sql}\nPRAGMA user_version = {int(version)};\nCOMMIT;"
        try:
            conn.executescript(script)
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        applied += 1
    return applied
