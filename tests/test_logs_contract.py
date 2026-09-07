"""The vocabularies in `web/app/logs.js` and the ones in Python are the same.

There is no way to make them literally one thing -- the tree has to be a table
on a screen and an enum in a request model, and those are two languages. What
there is instead is this file, which reads the JavaScript with Node and asserts
that every list in it matches its counterpart.

It earns its keep because of *how* a drift here fails. Nothing throws. The
phone offers a preset the request model has never heard of, the tap looks
exactly like every other tap, the entry goes into the queue -- and the queue is
a record with no second copy of anything in it, so the write comes back a 422
from a kitchen, at which point the meal is either re-entered by hand or lost.
That is the failure this suite exists to prevent (see `queue.js`: "send a meal
twice before losing one"), and a vocabulary that only one side knows is the
cheapest possible way to cause it.

Node rather than a regex over the source. A regex would pass on a file that
does not parse, which is the other half of the same failure -- and running the
module is the only way to check `SESSION_TREE`, whose leaves are what
`SESSION_FOCUS` holds and whose *shape* is what decides them.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from dashboard.models.common import (
    SESSION_FOCUS,
    DrinkName,
    DrinkTag,
    MealFlag,
    MealPreset,
)

ROOT = Path(__file__).resolve().parent.parent

# What to ask the module for. Written as one script so the whole contract costs
# a single Node start rather than one per assertion -- these run on a laptop
# that also has a dashboard on it.
SCRIPT = """
const m = await import("./web/app/logs.js");

const leaves = [];
const walk = (nodes, path, kind) => {
  for (const node of nodes) {
    const here = node.kind ?? kind;
    if (node.children?.length) walk(node.children, [...path, node.value], here);
    else leaves.push([[...path, node.value].join("."), here]);
  }
};
walk(m.SESSION_TREE, [], null);

console.log(JSON.stringify({
  tags: m.DRINK_TAGS.map(([, value]) => value),
  names: [...m.DRINK_NAMES.values()].flat().map(([value]) => value),
  presets: m.MEAL_PRESETS.map(([value]) => value),
  flags: m.MEAL_FLAGS.map(([, value]) => value),
  focus: Object.fromEntries(leaves),
  combinations: [...m.DRINK_NAMES.entries()].map(([key, names]) => [key, names.length]),
}));
"""


@pytest.fixture(scope="module")
def tables() -> dict:
    node = shutil.which("node")
    if node is None:  # pragma: no cover - a machine without Node
        pytest.skip("node is not on PATH")
    done = subprocess.run(
        [node, "--input-type=module", "-e", SCRIPT],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_the_drink_tags_are_the_same_six(tables: dict) -> None:
    """Order included. `canonical_tags` sorts by `DrinkTag` order and the phone
    sorts by the table's, so a row written on the phone and a row written by a
    script have to agree about what `sugar + caffeine` is called -- and the
    name lookup is keyed on exactly that string."""
    assert tables["tags"] == [tag.value for tag in DrinkTag]


def test_every_offered_drink_name_exists(tables: dict) -> None:
    assert sorted(tables["names"]) == sorted(name.value for name in DrinkName)


def test_no_drink_name_is_offered_twice(tables: dict) -> None:
    """A name under two tag combinations would be two drinks with one label,
    and no way to tell afterwards which of the two a row was."""
    assert len(tables["names"]) == len(set(tables["names"]))


def test_no_combination_offers_more_than_three_names(tables: dict) -> None:
    """The cap is a design rule, not an accident of the longest row. A fourth
    chip is a menu, and this row exists precisely because the tag row above it
    does not have to be read."""
    too_many = [key for key, count in tables["combinations"] if count > 3]
    assert not too_many


def test_every_meal_preset_exists(tables: dict) -> None:
    assert sorted(tables["presets"]) == sorted(preset.value for preset in MealPreset)


def test_the_meal_flags_are_the_same_four(tables: dict) -> None:
    assert tables["flags"] == [flag.value for flag in MealFlag]


def test_every_leaf_of_the_tree_is_a_known_focus(tables: dict) -> None:
    """Both directions. A leaf the server has not heard of is a 422 from a gym;
    a focus with no leaf is a value nothing can ever write, which is how a
    vocabulary rots."""
    assert set(tables["focus"]) == set(SESSION_FOCUS)


def test_every_leaf_lands_in_the_kind_the_server_expects(tables: dict) -> None:
    """The invariant `SessionRequest` enforces, checked against the tree that
    produces the bodies. This is the one that would let the phone post
    `cardio.steady.run` as boxing."""
    assert tables["focus"] == {key: kind.value for key, kind in SESSION_FOCUS.items()}


def test_every_leaf_reaches_a_kind(tables: dict) -> None:
    """`cardio` carries no kind of its own, so a leaf under it inherits from
    `hiit` or `steady`. A branch that inherited nothing would draw a form whose
    commit button can never light."""
    assert all(kind is not None for kind in tables["focus"].values())
