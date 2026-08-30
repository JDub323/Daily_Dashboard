r"""A recall answer, as a file an editor can open.

Theorem recall asks two questions and gives you two textareas. A textarea is
where the LaTeX half of that goes wrong: `$|\langle x,y\rangle| \le \|x\|\,\|y\|$`
is a line you proofread character by character, and proofreading is the thing
a browser text box is worst at. `dashboard/term.py` already forks a real
editor onto a pty for the investigation write-up, and the reasoning there --
an emulation is always a subset, and the subset you notice missing is whichever
motion you reached for without thinking -- applies here twice over.

So this module is the file that editor opens. Two sections in one buffer, not
one buffer each: the answer is a single act of recall, and quitting the editor
halfway through to reopen it on the other box is exactly the interruption the
panel exists to avoid. A marker line separates them, and the marker is the
whole format:

    >>> the theorem
    ...
    >>> the problem
    ...

[Invariant] `render` and `split` are inverses on the text between the markers.
The panel seeds the file from what is in its boxes and fills its boxes from
what is in the file, so a draft goes box -> file -> box repeatedly over one
sitting; a lossy step anywhere in that loop would eat a sentence a keystroke
at a time and look like nothing at all until the answer was submitted.

Nothing here is ever submitted. This is a scratchpad for the boxes, not a
document the way an investigation's file is -- the server never reads it as an
answer, and what gets graded is still what the panel posts.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

#: What each section is called. The panel's own two labels, so the buffer says
#: which box you are in using the words that are on screen beside it.
THEOREM = "the theorem"
PROBLEM = "the problem"

#: The two section markers, and the format in its entirety.
#:
#: `>>>` rather than a markdown heading because this file is not markdown and
#: pretending otherwise would invite a `#` of your own to be read as structure.
#: Three characters no answer opens a line with, at the start of a line.
EXPLANATION = f">>> {THEOREM}"
APPLICATION = f">>> {PROBLEM}"

#: A marker, wherever it starts a line. Anchored per-line rather than matched
#: loosely: a `>>>` inside an answer is a quoted prompt, and only a line that
#: *begins* with one is structure.
#:
#: The `>`s are allowed to drift apart because `>>>` is also markdown for a
#: thrice-nested blockquote, and a formatter run over this file on save would
#: write it back as `> > >`. Nothing does that today — conform.nvim is
#: installed with format-on-save commented out and no markdown formatter — but
#: the day that changed, the whole answer would come back in the first box and
#: nothing would say why.
_MARKER = re.compile(rf"^>(?:\s*>){{2}}\s*(?P<label>{THEOREM}|{PROBLEM})[^\S\n]*$", re.M)


def file_for(root: Path, kind: str, day: dt.date) -> Path:
    """Where one day's scratch buffer lives.

    Named by the day rather than by the card, because there is one draw a day
    and a directory of dates is one you can read. `kind` keeps the two decks
    that share this shape -- theorem recall and the argument patterns -- out of
    each other's file on a day you do both.
    """
    return root / f"{kind}-{day.isoformat()}.md"


def render(header: str, explanation: str = "", application: str = "") -> str:
    """The two answers as one buffer, with the header above both.

    The header is a comment in no language: it is the card's name and topic,
    and it is there so a file opened from a real terminal a week later says
    what it was an answer to. `split` drops everything above the first marker,
    which is what makes it safe to put anything at all up there.
    """
    return (
        f"{header}\n\n"
        f"{EXPLANATION}\n{explanation.strip()}\n\n"
        f"{APPLICATION}\n{application.strip()}\n"
    )


def header_for(name: str, topic: str, day: dt.date) -> str:
    """The two lines above the first marker. Never read back."""
    return f"# {name} — {topic}\n# drawn {day.isoformat()}, and not submitted from here"


def split(text: str) -> tuple[str, str]:
    """The buffer back into its two boxes.

    Keyed on which markers are *present*, not on which sections have anything
    in them -- a marker with nothing under it is an unanswered box, and reading
    it as a missing marker is how a freshly seeded file comes back with its own
    scaffolding in the first textarea.

    Degrades rather than raising, and degrades in the direction that keeps the
    text. A file whose markers were all deleted is not corrupt -- it is a file
    somebody typed in -- so the whole of it becomes the first answer, in a box
    that is on screen and editable. Refusing it would be refusing to hand back
    words that are sitting right there on disk.

    Only the first occurrence of each marker counts, so an answer that quotes
    one further down keeps it as text, and the markers' order in the file does
    not matter: they name the boxes rather than numbering them.
    """
    marks = list(_MARKER.finditer(text))
    if not marks:
        return text.strip(), ""

    sections: dict[str, str] = {}
    for i, match in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        sections.setdefault(match.group("label"), text[match.end() : end].strip())
    return sections.get(THEOREM, ""), sections.get(PROBLEM, "")


def ensure_file(
    path: Path, header: str, explanation: str = "", application: str = ""
) -> Path:
    """Put the boxes on disk for the editor to open.

    Not `investigate.ensure_file`'s rule, and the difference is deliberate. A
    write-up is a document the server may only ever create; this is a
    scratchpad the panel owns both ends of, and the panel is the newer of the
    two whenever it has anything to say -- its boxes were last filled *from*
    this file when the editor exited.

    So: seed whenever there is a draft to seed with, and otherwise leave what
    is on disk alone. The second half is the recovery case -- if the read-back
    after `:wq` failed, the boxes are empty and the file is the only copy of
    what was typed, and reopening the editor has to show it rather than
    overwrite it with two empty sections.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if explanation.strip() or application.strip() or not path.exists():
        path.write_text(render(header, explanation, application), encoding="utf-8")
    return path


def read_file(path: Path) -> tuple[str, str]:
    """The two boxes, as the file has them. Empty if it is not there.

    A missing file means the editor never opened -- the state the panel is
    already drawing -- so it is not an error here any more than it is there.
    """
    try:
        return split(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return "", ""
