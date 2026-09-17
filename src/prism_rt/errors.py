"""Typed exception hierarchy for the kernel and its adapters."""

from __future__ import annotations


class PrismError(Exception):
    """Base class for all errors raised by prism_rt."""


class StoreMutationOutsideStep(PrismError):
    """Raised when session state is mutated without an active StoreTxn.

    Enforces K2/K3 (§17.1 of the architecture doc, §3.3 of the implementation
    plan): only reducers and kernel decisions running inside a kernel step
    may mutate state.
    """


class InvariantViolation(PrismError):
    """Raised when a kernel invariant check fails at runtime (a defect, not
    an expected control-flow outcome — expected rejections use the more
    specific exceptions below or a returned decision object instead)."""


class CommitGateRejected(PrismError):
    """A write call was rejected by CommitGate (G1-G9). Callers should
    generally prefer inspecting the returned `GateDecision` over catching
    this; it exists for call sites that need to fail loudly."""


class StaleReadSet(PrismError):
    """A proposal or call's read set is no longer valid against the current
    FactStore revision."""


class CodecError(PrismError):
    """Raised by adapters/codec.py when wire JSON cannot be decoded into a
    known Envelope/payload type, or an Action cannot be encoded to wire JSON."""
