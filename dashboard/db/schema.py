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
