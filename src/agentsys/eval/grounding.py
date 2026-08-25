"""Is every figure in the answer traceable to something a tool returned?

This generalizes the check inside
`test_synthesis_does_not_fabricate_data_beyond_what_subtasks_actually_retrieved`
into a reusable function, because the failure it guards against is this
project's most dangerous one and it deserves to be measured on every run
rather than asserted on one hard-coded case.

The bug, from the README: synthesis needed two quarters, only one had been
queried, and it filled the gap with a plausible figure formatted to look
like a real citation. It happened to land on the correct value, which made
it worse, not better.

THIS IS A SIGNAL, NOT A VERDICT.

A correct answer can legitimately contain a number no tool ever returned --
a computed growth rate, a sum, a rounded restatement. So a naive "every
number must appear in a tool output" check produces false positives on
exactly the tasks the system is best at. Rather than pretend otherwise,
`certain` says whether the heuristic can settle the question on its own:

    certain=True   nothing derived could explain these figures -> real signal
    certain=False  the run computed things; a judge should settle it

That is the same discipline as flagging a keyword-heuristic verdict for
review instead of reporting it as fact -- an over-confident fabrication
detector is itself a kind of fabrication.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Tools whose whole job is producing values that were never "retrieved".
# Their presence means an unmatched figure has an innocent explanation.
_DERIVING_TOOLS = {"code_execution", "generate_tweet", "delegate_subagent"}

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def normalize(figure: str) -> str:
    """'$9,600,000.00' and '9600000' are the same number written two ways.
    Strip the formatting so a real citation isn't reported as invented purely
    because the answer used thousands separators."""
    # Keep only digits and the decimal point. figures_in's regex starts at a
    # digit so it never passes a currency symbol through, but this is a
    # public helper and a caller comparing "$9,600,000.00" to "9600000"
    # should get equality rather than a surprise.
    cleaned = "".join(ch for ch in figure if ch.isdigit() or ch == ".")
    if "." in cleaned:
        cleaned = cleaned.rstrip("0").rstrip(".")
    return cleaned or "0"


def is_significant(figure: str) -> bool:
    """Filter out the noise that makes this check useless if included.

    Small integers are everywhere in prose ('3 steps', 'the 2 quarters'),
    and years are almost never the fabrication anyone cares about. What
    matters is figures with enough specificity that inventing one is a lie
    with content: large values, or anything with a decimal part.
    """
    value = normalize(figure)
    if "." in value:
        return True
    if not value.isdigit():
        return False
    if len(value) == 4 and value.startswith(("19", "20")):
        return False  # a year
    return len(value) >= 4


def figures_in(text: str) -> set[str]:
    return {normalize(m) for m in _NUMBER.findall(text or "") if is_significant(m)}


def figures_in_outputs(tool_outputs: list[dict]) -> set[str]:
    """Every figure any tool returned, from anywhere in its output. Flattened
    without regard to structure on purpose: the question is only "did this
    number come from somewhere real", not which column it sat in."""
    found: set[str] = set()
    for output in tool_outputs:
        found |= figures_in(_stringify(output))
    return found


def _stringify(value) -> str:
    if isinstance(value, dict):
        return " ".join(_stringify(v) for v in value.values())
    if isinstance(value, (list, tuple, set)):
        return " ".join(_stringify(v) for v in value)
    return str(value)


@dataclass
class GroundingReport:
    ungrounded: list[str] = field(default_factory=list)
    grounded: list[str] = field(default_factory=list)
    certain: bool = True

    @property
    def looks_fabricated(self) -> bool:
        """Only claims fabrication when the heuristic can actually settle it.
        An uncertain report is a prompt to look, not a finding."""
        return bool(self.ungrounded) and self.certain

    def to_dict(self) -> dict:
        return {
            "ungrounded": self.ungrounded,
            "grounded": self.grounded,
            "certain": self.certain,
            "looks_fabricated": self.looks_fabricated,
        }


def check_grounding(
    final_output: str | None,
    tool_outputs: list[dict],
    tools_used: list[str] | None = None,
) -> GroundingReport:
    """Which significant figures in the answer no tool result can account for.

    `tools_used` decides `certain`: if anything that computes values ran, an
    unmatched figure is probably derived rather than invented, and this
    reports that it cannot tell rather than guessing.
    """
    quoted = figures_in(final_output or "")
    if not quoted:
        return GroundingReport(certain=True)

    available = figures_in_outputs(tool_outputs)
    ungrounded = sorted(quoted - available)
    grounded = sorted(quoted & available)
    derived = bool(set(tools_used or []) & _DERIVING_TOOLS)

    return GroundingReport(
        ungrounded=ungrounded,
        grounded=grounded,
        # No tool ran at all and the answer still quotes specific figures:
        # nothing could have produced them, so this one IS certain.
        certain=not derived,
    )
