"""Guardrails applied to every draft of advice. ``advise`` is the only path to shown advice."""

from guard.guardrails.checks import advise, apply_guardrails

__all__ = ["advise", "apply_guardrails"]
