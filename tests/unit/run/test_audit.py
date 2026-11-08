from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from guard.run.audit import canonical_json, code_version, inputs_hash


class Colour(enum.Enum):
    RED = "red"


@dataclass(frozen=True)
class Inner:
    amount: Decimal
    when: date


@dataclass(frozen=True)
class Outer:
    inner: Inner
    colour: Colour
    tags: tuple[str, ...]


def test_canonical_json_is_sorted_compact_and_typed() -> None:
    obj = Outer(Inner(Decimal("10.50"), date(2026, 11, 11)), Colour.RED, ("b", "a"))
    assert canonical_json(obj) == (
        '{"colour":"red","inner":{"amount":"10.50","when":"2026-11-11"},"tags":["b","a"]}'
    )


def test_key_order_and_set_order_do_not_matter() -> None:
    a = {"x": 1, "y": {"b": 2, "a": 3}, "s": frozenset({"q", "p"})}
    b = {"s": frozenset({"p", "q"}), "y": {"a": 3, "b": 2}, "x": 1}
    assert canonical_json(a) == canonical_json(b)
    assert inputs_hash(a) == inputs_hash(b)


def test_hash_changes_when_an_input_changes() -> None:
    assert inputs_hash({"v": Decimal("1")}) != inputs_hash({"v": Decimal("1.0")})
    assert len(inputs_hash({})) == 64


def test_datetimes_are_iso() -> None:
    assert canonical_json({"t": datetime(2026, 11, 11, 12, tzinfo=UTC)}) == (
        '{"t":"2026-11-11T12:00:00+00:00"}'
    )


def test_non_finite_floats_and_unknown_types_are_rejected() -> None:
    with pytest.raises(ValueError):
        canonical_json({"x": float("nan")})
    with pytest.raises(TypeError):
        canonical_json({"x": object()})


def test_code_version_is_stable_text() -> None:
    assert code_version() == code_version()
    assert len(code_version()) == 16
