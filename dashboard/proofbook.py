"""The proof textbook: its table of contents, and how a topic finds a section.

Wednesday through Saturday come out of one book -- *Putnam and Beyond* by
Gelca and Andreescu -- rather than off the open web, and that single fact is
what this module exists to make safe.

The failure it prevents is specific. A model asked at 3am for "an easier proof
problem from Gelca-Andreescu on polynomials", with no book in front of it,
writes a problem that reads exactly like one from the book and is not in it:
a drifted statement, an invented problem number, and an official solution that
was never printed. Nothing downstream can tell the difference, because
`verified_url` -- the one provenance check the math batch had -- does not exist
for a textbook.

So the generator is never asked to *recall* the book. It is handed a page
range and told to read it. This module is the thing that knows which pages:

    index (once)   parse the PDF's table of contents into `proofbook.json`
    sections_for   topic -> the sections of the book it should be drawn from

The index is committed; the PDF is not (it is 9.7MB and not ours to
redistribute -- see .gitignore). That split is deliberate. Nothing at runtime
opens the PDF, so nothing at runtime needs `pypdf`, and a machine without the
book still boots, still serves Monday and Tuesday, and says what is missing on
the proof days.

`pypdf` is imported inside `build_index` for the reason `garminconnect` and
`cryptography` are imported lazily elsewhere: it is needed by one command that
runs roughly never, and a venv that predates it must not fail to start.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from . import logs
from .models import domain as d

log = logs.get(__name__)

# --- reading the index -----------------------------------------------------


def load(path: Path) -> d.ProofBook | None:
    """The committed index, or None if it is missing or unreadable.

    None rather than an exception, and the caller degrades: no index means the
    proof days cannot name a page range, which is a batch that says so at 3am
    rather than a dashboard that will not start at 8.
    """
    if not path.exists():
        return None
    try:
        return d.ProofBook(**json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, ValueError):
        log.exception("could not read the proof-textbook index at %s", path)
        return None


STOPWORDS = frozenset(
    {
        "a",
        "about",
        "and",
        "are",
        "as",
        "at",
        "by",
        "for",
        "from",
        "in",
        "into",
        "of",
        "on",
        "or",
        "the",
        "to",
        "with",
        "problem",
        "problems",
        "theory",
        "technique",
        "techniques",
        "method",
        "methods",
    }
)
"""Words that carry no matching signal.

Small on purpose. "theory" is in it because "number theory" and "finite group
theory" would otherwise score a point against each other on their least
informative word, which is exactly the kind of match that puts a group theory
week in front of the number theory chapter.
"""

# Deliberately absent: a synonym table. An earlier version had one, mapping
# "congruences" to "modular" and a dozen more like it, to make the word match
# below land on the right section more often. It got nine of the twenty-five
# rotation topics wrong anyway, and the ones it got wrong were not obviously
# wrong -- "polynomial roots, Vieta, and symmetric functions" matched *Equations
# with Functions as Unknowns*, which is a real section about something else.
# The index made the honest fix available: name the section in config, where it
# is one line, visible, and checkable against the contents page. The matching
# below is now only the fallback for a topic nobody has mapped yet.


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", text.casefold()) if w not in STOPWORDS and len(w) > 2}


def sections_for(
    book: d.ProofBook,
    topic: str,
    area: str | None = None,
    *,
    override: str | None = None,
    limit: int = 3,
) -> list[d.ProofSection]:
    """The sections a week's proof problems should be drawn from, best first.

    Three passes, in decreasing order of how much they are trusted:

    1. `override` -- a section number from `[math.proof.sections]` in the
       config. Exact, and the escape hatch for a week the words get wrong.
    2. Word overlap between the rotation topic and the section title. Cheap,
       and right most of the time because both name the same mathematics.
    3. The chapter for the week's broad area. The fallback that is always
       available, and the reason a topic with no section in this book -- "digits,
       base representations, and floor functions" has no chapter of its own --
       still gets a usable week instead of an empty one.

    Returns at most `limit`, because the generator is choosing a problem, not
    reading the book: a week pointed at eight sections is a week pointed at a
    chapter, and the point of the index is to be narrower than that.
    """
    sections = [s for c in book.chapters for s in c.sections]
    if override:
        exact = [s for s in sections if s.number == override or s.number.startswith(override + ".")]
        if exact:
            return exact[:limit]
        log.warning("[math.proof.sections] names %r, which is not in the index", override)

    wanted = _words(topic)
    scored = [(len(wanted & _words(f"{s.parent or ''} {s.title}")), s) for s in sections]
    hits = sorted((x for x in scored if x[0]), key=lambda x: (-x[0], x[1].number))
    if hits:
        return [s for _, s in hits[:limit]]

    chapter = chapter_for_area(book, area)
    return chapter.sections[:limit] if chapter else []


AREA_CHAPTERS = {
    "algebra": "2",
    "analysis": "3",
    "geometry": "4",
    "number theory": "5",
    "combinatorics": "6",
    "probability": "6",
}
"""[math.areas] bucket -> the chapter of this book that covers it.

Six areas onto six chapters, with combinatorics and probability sharing one --
which is how the book files them too ("Combinatorics and Probability"). This is
the only hand-written mapping in the module, and it is written down rather than
matched on words because an area is a fixed, six-element vocabulary: matching
it would be guessing at something already known.
"""


def chapter_for_area(book: d.ProofBook, area: str | None) -> d.ProofChapter | None:
    number = AREA_CHAPTERS.get((area or "").casefold())
    if number is None:
        return None
    return next((c for c in book.chapters if c.number == number), None)


RENDER_DPI = 160
"""Enough to read a printed page of mathematics without the file being huge.

Measured rather than guessed: at 160 the subscripts and the primes in
`\\varepsilon^{n-1}` are legible in a rendered page, and a page comes to about
200KB. At 100 the exponents start to go.
"""

MAX_RENDER = 12
"""How many pages one call will render.

A guard against `--render-pages 1-857`, which would fill a directory with a
gigabyte of PNGs and hand a model more than it can read anyway. A section of
this book is four or five pages; a chapter's solutions are consulted a page at
a time.
"""


def render_pages(
    pdf: Path, first: int, last: int, out: Path, dpi: int = RENDER_DPI
) -> list[Path]:
    """Render 1-based PDF pages to PNGs and return the files written.

    This exists because reading the book is the one step of the proof days that
    has no fallback, and the obvious route to it is not reliably available: the
    harness's own PDF reader shells out to `pdftoppm`, which is not installed
    here and is not something a 3am job can install. Text extraction is worse
    than useless for this book -- `x^{2^n}` comes out as `x2n`, which is not a
    garbled statement but a *plausible different one*, and a generator reading
    that will quietly reconstruct rather than quote.

    So the pages become images, and the model looks at the page.

    `pymupdf` is imported here rather than at module scope, like `build_index`
    above: nothing at runtime renders anything, and a venv without the extra
    must still start the server.
    """
    if first < 1 or last < first:
        raise ValueError(f"page range {first}-{last} is not a range")
    if last - first + 1 > MAX_RENDER:
        raise ValueError(
            f"{last - first + 1} pages is more than the {MAX_RENDER} this renders at once"
        )

    import pymupdf  # lazy: see the docstring

    out.mkdir(parents=True, exist_ok=True)
    # Cleared each call: these are scratch, and a stale page from last week's
    # section sitting beside this week's is exactly the confusion to avoid.
    for old in out.glob("p*.png"):
        old.unlink()

    written = []
    with pymupdf.open(str(pdf)) as doc:
        if last > len(doc):
            raise ValueError(f"{pdf.name} has {len(doc)} pages; {last} was asked for")
        for n in range(first, last + 1):
            path = out / f"p{n:04d}.png"
            doc[n - 1].get_pixmap(dpi=dpi).save(str(path))
            written.append(path)
    return written


def solution_pages(book: d.ProofBook, section: d.ProofSection) -> tuple[int, int] | None:
    """Where the printed solutions for a section's chapter live, in PDF pages.

    A range for the whole chapter rather than for the section: the solutions
    are ordered by problem number, so a chapter range plus the problem number
    is enough to find one, and the index does not have to parse 500 pages of
    solutions to say something useful.

    This is what makes the hints and the rubric honest. Both are written from
    the book's own solution rather than from the model's idea of one, which is
    the difference between a hint that unlocks the intended argument and a hint
    that unlocks a different problem.
    """
    chapter = next((c for c in book.chapters if section.number.startswith(c.number + ".")), None)
    if chapter is None or chapter.solutions_pdf_page is None:
        return None
    return (chapter.solutions_pdf_page, chapter.solutions_pdf_end_page or book.pages)


# --- building it -----------------------------------------------------------

TOC_LINE = re.compile(r"^(\d+(?:\.\d+){0,2})\s+(.+?)\s*\.{3,}\s*([\d\s]+)$")
"""`2.1.3 The Cauchy-Schwarz Inequality ......... 3 2`

The trailing page number is matched as *digits and spaces*, not as an integer.
pdfminer-style extraction of this book's contents pages puts a space between
every digit of a two- or three-digit page number ("1 0 8" is 108), and reading
that as 1 would put chapter 3 on page one.
"""

BACKMATTER = re.compile(r"^(.+?)\s*\.{3,}\s*([\d\s]+)$")
"""The unnumbered entries at the end of the contents: the six solution
chapters, then the two indexes. Distinguished from a real section only by
having no leading number, which is why this pattern is applied second."""

PROBLEM = re.compile(r"^(\d{1,4})\.\s+[A-Z(\\$]")
"""`24. Let n be a positive integer` -- a numbered problem in the exercise
lists. The capital letter matters: without it every displayed equation that
happens to begin with a digit and a decimal point is a problem number."""

MAX_PER_SECTION = 120
"""A sanity bound on the problem-number scan. The largest exercise list in this
book is well under it, so a jump larger than this is extraction noise -- a page
number read as a problem number -- rather than a section with hundreds of
problems."""


def _page_number(raw: str) -> int | None:
    digits = raw.replace(" ", "").strip()
    return int(digits) if digits.isdigit() else None


LIGATURES = {"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl"}
SPLIT_CAP = re.compile(r"\b([B-HJ-Z]) ([a-z]{2,})")
"""`Integer-V alued` -> `Integer-Valued`.

Text extraction breaks a word after a capital often enough that a quarter of
this book's section titles come out wrong, and these titles are read by a
person and pasted into a prompt. `A` and `I` are excluded from the class
because they are the two capitals that are also words.
"""


def _clean_title(raw: str) -> str:
    for bad, good in LIGATURES.items():
        raw = raw.replace(bad, good)
    # `Vi `ete's Relations` -> `Viete's Relations`. Extraction renders the grave
    # accent as a free-standing backtick and splits the word around it; the
    # accent is not recoverable from the text layer, so the word is closed up
    # without it rather than left with a backtick in the middle.
    raw = re.sub(r"\s*`\s*", "", raw)
    return SPLIT_CAP.sub(r"\1\2", raw).strip()


def build_index(pdf: Path, *, toc_pages: range = range(9, 13)) -> d.ProofBook:
    """Parse the PDF's contents pages and problem lists into an index.

    Runs by hand, roughly once. `toc_pages` is 0-based into the PDF and covers
    the contents; it is a parameter rather than a constant so a differently
    paginated printing can be indexed without editing code.
    """
    from pypdf import PdfReader  # lazy: see the module docstring

    reader = PdfReader(str(pdf))
    text = [(p.extract_text() or "") for p in reader.pages]

    chapters: list[dict[str, Any]] = []
    backmatter: list[tuple[str, int]] = []
    for i in toc_pages:
        for line in text[i].splitlines():
            line = line.strip()
            m = TOC_LINE.match(line)
            if m:
                number, title, raw = m.groups()
                page = _page_number(raw)
                if page is None:
                    continue
                if "." not in number:
                    chapters.append(
                        {
                            "number": number,
                            "title": _clean_title(title),
                            "page": page,
                            "sections": [],
                        }
                    )
                elif chapters:
                    chapters[-1]["sections"].append(
                        {"number": number, "title": _clean_title(title), "page": page}
                    )
                continue
            m = BACKMATTER.match(line)
            if m and not line.lower().startswith(("contents", "index")):
                page = _page_number(m.group(2))
                if page is not None:
                    backmatter.append((_clean_title(m.group(1)), page))

    if not chapters:
        raise ValueError(f"no chapters found in {pdf} pages {toc_pages}")

    _keep_leaves(chapters)
    pages = _page_map(text)
    _fill_spans(chapters, backmatter, pages)

    # The solutions repeat the chapter titles, unnumbered, after the last
    # section. Matched by title rather than by position: position would break
    # the moment a printing adds a preface entry.
    titles = {c["title"].casefold(): c for c in chapters}
    starts = sorted(p for t, p in backmatter if t.casefold() in titles)
    for title, page in backmatter:
        chapter = titles.get(title.casefold())
        if chapter is None or page <= chapter["page"]:
            continue
        later = [p for p in starts if p > page]
        chapter["solutions_pdf_page"] = _pdf_page(pages, page)
        chapter["solutions_pdf_end_page"] = _pdf_page(pages, later[0] - 1) if later else len(text)

    _scan_problems(text, chapters)

    return d.ProofBook(
        title="Putnam and Beyond (Gelca & Andreescu, 2nd edition)",
        pdf=str(pdf).replace("\\", "/"),
        pages=len(text),
        chapters=[d.ProofChapter(**c) for c in chapters],
    )


def _keep_leaves(chapters: list[dict[str, Any]]) -> None:
    """Drop every section that has subsections, in favour of those subsections.

    See `ProofSection`: the leaves are the unit worth pointing at. A section
    that has none -- `1.1 Argument by Contradiction` -- is itself a leaf and
    stays, so "leaf" is a property of the contents rather than of the depth.

    The dropped section's title is kept on each child as `parent`, because it
    is usually the half that says what the mathematics is.
    """
    for chapter in chapters:
        titles = {s["number"]: s["title"] for s in chapter["sections"]}
        parents = {n.rsplit(".", 1)[0] for n in titles if n.count(".") == 2}
        leaves = []
        for section in chapter["sections"]:
            if section["number"] in parents:
                continue
            section["parent"] = titles.get(section["number"].rsplit(".", 1)[0])
            leaves.append(section)
        chapter["sections"] = leaves


HEAD_NUMBER = re.compile(r"^(\d{1,3})\b|\b(\d{1,3})$")
"""The printed page number in a running head.

The book alternates: `6 1 Methods of Proof` on the left-hand page and
`1.2 Mathematical Induction 7` on the right, so the number is leading on one
and trailing on the other and both spellings have to be read.
"""


def _page_map(text: list[str]) -> dict[int, int]:
    """printed page number -> 1-based PDF page, read off the running heads.

    The reason this is a map and not a subtraction is in `ProofSection`: the
    difference between the two numberings drifts from 15 to 10 across this
    book. Measuring it once and adding it everywhere would be correct for the
    first chapter and five pages wrong by the last.

    Monotonicity is the filter. Printed numbers must increase as PDF pages do,
    so a head that reads as a page number smaller than one already seen is a
    displayed equation being mistaken for a folio, and is dropped. That leaves
    gaps -- a page whose head did not parse -- which `_pdf_page` interpolates
    over rather than failing on.
    """
    found: dict[int, int] = {}
    highest = 0
    for i, page in enumerate(text):
        head = page.split("\n", 1)[0].strip()
        m = HEAD_NUMBER.search(head)
        if not m:
            continue
        printed = int(m.group(1) or m.group(2))
        if printed <= highest or printed > i + 1:
            continue
        found[printed] = i + 1
        highest = printed
    if len(found) < 100:
        raise ValueError(f"only {len(found)} running heads parsed; the index would be guesswork")
    return found


def _pdf_page(pages: dict[int, int], printed: int) -> int:
    """The PDF page for a printed one, interpolating across unparsed heads.

    A chapter's opening page carries no running head at all, so the printed
    number the contents give for a section is often one this map never saw.
    Walking back to the nearest page it did see and adding the difference is
    exact whenever the two numberings do not drift *inside* that gap, which for
    a gap of a page or two they do not.
    """
    if printed in pages:
        return pages[printed]
    earlier = [p for p in pages if p < printed]
    if not earlier:
        return printed
    nearest = max(earlier)
    return pages[nearest] + (printed - nearest)


def _fill_spans(
    chapters: list[dict[str, Any]], backmatter: list[tuple[str, int]], pages: dict[int, int]
) -> None:
    """Give every section an `end_page`, and both of its PDF pages.

    The last section of the book ends where the solutions begin, which is the
    first back-matter page after it -- so the span never runs off into 500
    pages of answers.
    """
    flat = [s for c in chapters for s in c["sections"]]
    after = min((p for _, p in backmatter), default=flat[-1]["page"] + 8)
    for section, nxt in zip(flat, [*flat[1:], {"page": after}], strict=True):
        section["end_page"] = max(section["page"], nxt["page"] - 1)
        section["pdf_page"] = _pdf_page(pages, section["page"])
        section["pdf_end_page"] = _pdf_page(pages, section["end_page"])


def _scan_problems(text: list[str], chapters: list[dict[str, Any]]) -> None:
    """Record the range of problem numbers printed in each section.

    The numbers run once through the whole book rather than restarting per
    chapter, which is what makes `P&B 512` a globally unique name for a problem
    and therefore usable as `math_problem.source` -- the column the
    do-not-repeat rule is enforced on.

    That same property is the check applied here: a candidate is only believed
    if it is larger than the last problem of the previous section and not
    absurdly larger. Extraction turns plenty of displayed equations into
    something that looks like `2. Let`, and monotonicity is what tells the two
    apart without reading the mathematics.
    """
    last = 0
    for section in (s for c in chapters for s in c["sections"]):
        found: list[int] = []
        for page in text[section["pdf_page"] - 1 : section["pdf_end_page"]]:
            for line in page.splitlines():
                m = PROBLEM.match(line.strip())
                if m:
                    n = int(m.group(1))
                    if last < n <= last + MAX_PER_SECTION:
                        found.append(n)
        if found:
            section["first_problem"] = min(found)
            section["last_problem"] = max(found)
            last = max(found)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m dashboard.proofbook",
        description="Parse the proof textbook's contents into a committed index. "
        "Needs the optional `pypdf` extra; nothing at runtime does.",
    )
    ap.add_argument("pdf", type=Path, help="the textbook PDF")
    ap.add_argument("-o", "--out", type=Path, default=Path("proofbook.json"))
    args = ap.parse_args(argv)

    try:
        book = build_index(args.pdf)
    except ImportError:
        print("pypdf is not installed: pip install -e '.[proofbook]'", file=sys.stderr)
        return 2

    args.out.write_text(book.model_dump_json(indent=2) + "\n", encoding="utf-8")
    sections = [s for c in book.chapters for s in c.sections]
    numbered = [s for s in sections if s.first_problem]
    print(
        f"OK {args.out}: {len(book.chapters)} chapters, {len(sections)} sections, "
        f"{len(numbered)} with problem numbers"
    )
    for chapter in book.chapters:
        found = [s for s in chapter.sections if s.first_problem]
        span = (
            f"problems {found[0].first_problem}–{found[-1].last_problem}"
            if found
            else "no problem numbers found"
        )
        solutions = (
            f"solutions pdf {chapter.solutions_pdf_page}–{chapter.solutions_pdf_end_page}"
            if chapter.solutions_pdf_page
            else "no solutions found"
        )
        print(
            f"  {chapter.number:>2} {chapter.title:<30} p.{chapter.page:<4} {span:<22} {solutions}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
