"""The stated-interest profile.

Chosen over learning from clicks, and these tests pin the reasons: it works
with no history at all, it is wrong only in ways you can read, and the file
being absent is a normal state rather than an error.
"""

from __future__ import annotations

from dashboard import interests

PROFILE = """
Some prose about what I care about, which the scorer ignores.

# Interested in
- mechanistic interpretability
- evaluation and benchmarks

## Not interested in
- federated learning
- medical imaging
"""


def test_bullets_bucket_by_the_heading_above_them():
    p = interests.parse(PROFILE)
    assert p.include == ["mechanistic interpretability", "evaluation and benchmarks"]
    assert p.exclude == ["federated learning", "medical imaging"]


def test_prose_outside_bullets_is_not_a_term():
    """It is there for a human, and eventually for a model as prompt context.
    Treating a paragraph as a search term would match almost nothing."""
    assert "some prose" not in " ".join(interests.parse(PROFILE).include)


def test_a_wanted_term_outranks_an_unmentioned_one():
    p = interests.parse(PROFILE)
    assert p.score("Mechanistic interpretability of transformers") > p.score("A paper on optics")


def test_an_unwanted_term_sinks_below_an_unmentioned_one():
    """An opt-out you took the trouble to write should beat an opt-in you did
    not -- otherwise excluding something merely fails to promote it."""
    p = interests.parse(PROFILE)
    assert p.score("Federated learning at scale") < p.score("A paper on optics")


def test_matching_is_case_insensitive_and_spans_fields():
    p = interests.parse(PROFILE)
    assert p.score("Untitled", "we evaluate on EVALUATION AND BENCHMARKS") > 0


def test_an_empty_profile_leaves_the_order_alone():
    """No file means arXiv's own ordering, which is newest first."""
    order = ["c", "a", "b"]
    assert interests.rank(order, interests.Interests(), text_of=lambda x: (x,)) == order


def test_ranking_is_stable_for_equal_scores():
    """Anything the profile is silent about keeps the order it arrived in, so
    recency survives as the tie-break."""
    p = interests.Interests(include=["zebra"])
    order = ["c", "a", "b"]
    assert interests.rank(order, p, text_of=lambda x: (x,)) == order


def test_ranking_promotes_a_match_without_reordering_the_rest():
    p = interests.parse(PROFILE)
    items = ["optics", "medical imaging", "mechanistic interpretability", "topology"]
    assert interests.rank(items, p, text_of=lambda x: (x,)) == [
        "mechanistic interpretability",
        "optics",
        "topology",
        "medical imaging",
    ]


def test_a_missing_file_is_an_empty_profile_not_an_error(tmp_path):
    assert interests.load(tmp_path / "nope.md").empty


def test_markdown_emphasis_is_stripped_from_a_term():
    assert interests.parse("# Want\n- **sparse autoencoders**\n").include == ["sparse autoencoders"]


def test_several_negative_headings_are_recognised():
    text = "# Avoid\n- a\n# Skip\n- b\n# Want\n- c\n"
    p = interests.parse(text)
    assert p.exclude == ["a", "b"] and p.include == ["c"]
