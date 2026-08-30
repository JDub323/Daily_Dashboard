"""The recall buffer: the two boxes as one file, and back again.

Everything here is about the round trip, because the round trip is the whole
risk. The panel seeds this file from its textareas and refills them from it
every time the editor exits, so a step that quietly drops a section would eat
an answer over a sitting and look like nothing until it was submitted.
"""

from __future__ import annotations

import datetime as dt

from dashboard import scratch

DAY = dt.date(2026, 8, 30)
HEADER = scratch.header_for("Cauchy–Schwarz", "inequalities", DAY)


# --- the round trip --------------------------------------------------------


def test_what_render_writes_is_what_split_reads():
    text = scratch.render(HEADER, "the dot product is at most the norms", "the [0,1] one")
    assert scratch.split(text) == (
        "the dot product is at most the norms",
        "the [0,1] one",
    )


def test_the_trip_survives_being_made_repeatedly():
    """Box -> file -> box happens once per editor session, and a sitting is
    several. A step that were lossy only on the second pass would be the
    hardest kind of bug to see."""
    pair = ("what it says", "where I needed it")
    for _ in range(5):
        pair = scratch.split(scratch.render(HEADER, *pair))
    assert pair == ("what it says", "where I needed it")


def test_the_header_never_comes_back():
    """It is there to say what the file was, a week later, in a real terminal.
    Nothing above the first marker is an answer."""
    explanation, _ = scratch.split(scratch.render(HEADER, "a", "b"))
    assert "Cauchy" not in explanation
    assert "drawn" not in explanation


def test_latex_goes_through_untouched():
    """The reason the editor exists at all. A backslash that survived the
    textarea and not the file would be worse than no editor."""
    source = r"$|\langle x,y\rangle| \le \|x\|\,\|y\|$"
    assert scratch.split(scratch.render(HEADER, source, "b"))[0] == source


def test_blank_sections_stay_blank_rather_than_becoming_the_header():
    assert scratch.split(scratch.render(HEADER)) == ("", "")


def test_a_multi_paragraph_answer_keeps_its_blank_lines():
    """Only the outer whitespace is stripped. A paragraph break inside an
    answer is the author's, and re-rendering must not close it up."""
    long = "first line\n\nsecond paragraph\n  indented continuation"
    assert scratch.split(scratch.render(HEADER, long, "b"))[0] == long


# --- files that came back wrong --------------------------------------------


def test_a_file_with_no_markers_at_all_becomes_the_first_answer():
    """Deleting the markers is a thing a person does. The words are on disk
    and the panel has two visible boxes to fix them in, so handing everything
    to the first one keeps the text; raising would throw it away."""
    assert scratch.split("I wiped the whole thing and typed this") == (
        "I wiped the whole thing and typed this",
        "",
    )


def test_only_the_second_marker_survived_and_its_answer_is_not_lost():
    text = f"stray preamble\n{scratch.APPLICATION}\nthe olympiad one\n"
    explanation, application = scratch.split(text)
    assert "the olympiad one" in (explanation + application)


def test_a_marker_quoted_inside_an_answer_stays_text():
    """Only the first of each counts. An answer that talks about the format
    should not be able to split itself in half."""
    quoted = f"I wrote {scratch.EXPLANATION} at the top by mistake"
    explanation, application = scratch.split(scratch.render(HEADER, quoted, "b"))
    assert explanation == quoted
    assert application == "b"


def test_sections_in_the_wrong_order_still_land_in_the_right_boxes():
    """The markers name the boxes; their order in the file does not."""
    text = f"{scratch.APPLICATION}\nwhere\n\n{scratch.EXPLANATION}\nwhat\n"
    assert scratch.split(text) == ("what", "where")


# --- the file on disk ------------------------------------------------------


def test_a_draft_seeds_the_file(tmp_path):
    path = scratch.file_for(tmp_path, "theorem", DAY)
    scratch.ensure_file(path, HEADER, "typed in the browser", "")
    assert scratch.read_file(path) == ("typed in the browser", "")


def test_the_day_is_in_the_name_and_the_deck_is_too(tmp_path):
    """Two decks share this shape, and doing both on one day must not put one
    answer in the other's file."""
    theorem = scratch.file_for(tmp_path, "theorem", DAY)
    pattern = scratch.file_for(tmp_path, "pattern", DAY)
    assert theorem != pattern
    assert DAY.isoformat() in theorem.name


def test_an_empty_draft_does_not_overwrite_what_is_on_disk(tmp_path):
    """The recovery case, and the one that matters most. If the read-back
    after `:wq` failed, the boxes are empty and the file is the only copy of
    what was typed -- reopening the editor must show it, not clear it."""
    path = scratch.file_for(tmp_path, "theorem", DAY)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(scratch.render(HEADER, "an hour of typing", "and this"), encoding="utf-8")

    scratch.ensure_file(path, HEADER, "", "")

    assert scratch.read_file(path) == ("an hour of typing", "and this")


def test_whitespace_is_not_a_draft(tmp_path):
    """A textarea left holding a newline is empty, and must not count as the
    newer copy."""
    path = scratch.file_for(tmp_path, "theorem", DAY)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(scratch.render(HEADER, "the real answer", ""), encoding="utf-8")

    scratch.ensure_file(path, HEADER, "  \n ", "\t")

    assert scratch.read_file(path)[0] == "the real answer"


def test_a_draft_does_overwrite_because_the_boxes_are_the_newer_copy(tmp_path):
    """The other half of the same rule. The boxes were last filled from this
    file, so anything in them is at least as new as what it holds."""
    path = scratch.file_for(tmp_path, "theorem", DAY)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(scratch.render(HEADER, "yesterday's", ""), encoding="utf-8")

    scratch.ensure_file(path, HEADER, "what the boxes hold now", "")

    assert scratch.read_file(path)[0] == "what the boxes hold now"


def test_the_directory_is_made_if_it_is_missing(tmp_path):
    """The editor chdirs into it, and a missing cwd kills the child at exec
    with nothing on the socket to explain why."""
    root = tmp_path / "never" / "existed"
    path = scratch.file_for(root, "theorem", DAY)
    scratch.ensure_file(path, HEADER)
    assert path.exists()


def test_a_missing_file_is_two_empty_boxes(tmp_path):
    """It means the editor was never opened -- the state the panel is already
    drawing, not an error."""
    assert scratch.read_file(tmp_path / "nothing-here.md") == ("", "")


def test_a_markdown_formatter_spacing_out_the_quote_does_not_break_the_split():
    """`>>>` is a thrice-nested blockquote, and that is what a formatter would
    normalise it to. Nothing formats this file today; the cost of being wrong
    about that later is both answers silently in one box."""
    text = "> > > the theorem\nwhat\n\n>  >  > the problem\nwhere\n"
    assert scratch.split(text) == ("what", "where")
