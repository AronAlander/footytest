"""The motion system's guarantees.

Charts draw themselves in as they scroll into view. The animation itself
is checked in a browser; what is pinned here are the promises that keep it
from ever hiding the data: nothing is hidden for a reader who has asked
for less motion, nothing waits on an animation finishing to become
visible, a printout shows everything, and no figure is ever held back.
"""
import re

import build_report

CSS = build_report.CSS
JS = build_report.MOTION_JS


def _block(css, start):
    """The balanced {...} body of the at-rule beginning at `start`."""
    i, depth = css.index("{", start), 0
    for j in range(i, len(css)):
        depth += {"{": 1, "}": -1}.get(css[j], 0)
        if depth == 0:
            return css[i + 1:j]
    raise ValueError("unbalanced")


def _hiding_rules(css):
    return [m.start() for m in re.finditer(r"\.motion \.block:not\(\.in\)", css)]


def test_every_rule_that_hides_sits_behind_the_reduced_motion_guard():
    guard = CSS.index("@media (prefers-reduced-motion: no-preference)")
    body_start = CSS.index("{", guard)
    body = _block(CSS, guard)
    body_end = body_start + len(body) + 1
    printed = CSS.index("@media print")
    hides = [p for p in _hiding_rules(CSS) if not printed < p < printed + 200]
    assert hides, "no hiding rules found at all"
    assert all(body_start < p < body_end for p in hides)


def test_a_reader_who_wants_less_motion_never_gets_the_class():
    assert JS.index("prefers-reduced-motion: reduce") < JS.index("classList.add('motion')")
    assert JS.index("IntersectionObserver' in window") < JS.index("classList.add('motion')")


def test_a_printout_shows_everything():
    body = _block(CSS, CSS.index("@media print"))
    assert "opacity: 1 !important" in body and "clip-path: none !important" in body


def test_whatever_is_hidden_is_released_by_the_done_state():
    """Visible must not depend on a transition finishing."""
    hidden = set(re.findall(r"\.(hist-bar|h2h-bar|dm-bar|ms-bar|pcell > i)",
                            CSS[CSS.index(".motion .block:not(.in) :is(.hist-bar"):]
                            .split("}")[0]))
    done = CSS[CSS.index(".motion .block.done"):].split("{")[0]
    for sel in hidden:
        assert sel in done, f"{sel} can be hidden but is never released"
    assert "svg circle" in done
    assert "classList.add('done')" in JS


def test_the_prediction_bar_is_never_hidden():
    """Its percentages sit inside its segments; hiding it hides figures."""
    motion = CSS[CSS.index("/* ---- motion"):CSS.index("@media print")]
    assert ".prob" not in motion


def test_a_dashed_line_keeps_its_dashes():
    """The dashed start of a form curve means something."""
    assert "strokeDasharray !== 'none') return" in JS


def test_the_script_loads_in_the_head_once():
    src = open(build_report.__file__, encoding="utf-8").read()
    assert src.count("<script>{MOTION_JS}</script></head>") == 1
