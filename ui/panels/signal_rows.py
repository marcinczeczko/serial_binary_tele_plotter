"""
Signals as the Signals pane lists them (R9.4): one row per left/right pair.

A robot has two of most things, and the pane showed `L: Measurement` and `R: Measurement`
as two rows each (34 rows for the bundled PID frame). A pair is found by its label's
`L: ` / `R: ` prefix, else by its key's `left_` / `right_` prefix, and only within one
lane (a copy moved to another lane stands alone there). No config key: the bundled
profile pairs by its names (spec open question 1, decided in R9.4). Qt-free.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

LABEL_SIDES = (("L: ", "left"), ("R: ", "right"))
KEY_SIDES = (("left_", "left"), ("right_", "right"))


@dataclass(frozen=True)
class SignalRow:
    """One row: a name and the signal drawn left and/or right (at least one)."""

    name: str
    left: str | None = None
    right: str | None = None

    @property
    def members(self) -> list[str]:
        return [sid for sid in (self.left, self.right) if sid is not None]


def side_of(sid: str, sig: Mapping[str, Any]) -> tuple[str | None, str, str]:
    """(side or None, pairing base, shown name) of one signal."""
    label = str(sig.get("label", sid))
    for prefix, side in LABEL_SIDES:
        if label.startswith(prefix):
            name = label[len(prefix) :].strip()
            return side, "label:" + name.lower(), name
    for prefix, side in KEY_SIDES:
        if sid.startswith(prefix):
            return side, "key:" + sid[len(prefix) :], label
    return None, "", label


def pair_rows(sids: Iterable[str], signals: Mapping[str, Mapping[str, Any]]) -> list[SignalRow]:
    """
    The rows of one lane, in the order their first signal appears. A signal pairs with the
    first signal of the other side that has the same base; the rest stand alone.
    """
    rows: list[SignalRow] = []
    open_rows: dict[tuple[str, str], int] = {}  # (base, side still missing) -> row index
    for sid in sids:
        side, base, name = side_of(sid, signals.get(sid, {}))
        if side is None:
            rows.append(SignalRow(name, left=sid))
            continue
        other = "right" if side == "left" else "left"
        index = open_rows.pop((base, side), None)
        if index is not None:  # the other side came first and waits for this one
            row = rows[index]
            rows[index] = SignalRow(
                row.name,
                left=sid if side == "left" else row.left,
                right=sid if side == "right" else row.right,
            )
            continue
        rows.append(SignalRow(name, **{side: sid}))
        open_rows[(base, other)] = len(rows) - 1
    return rows


def lane_title(label: str) -> str:
    """A lane header as a scope writes it: `Speed [rps]` -> `SPEED rps`."""
    text = label.strip()
    if text.endswith("]") and "[" in text:
        name, unit = text[:-1].split("[", 1)
        return f"{name.strip().upper()} {unit.strip()}".strip()
    return text.upper()
