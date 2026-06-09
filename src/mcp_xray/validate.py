"""Validation loop (PLAN S10.5): propose-don't-assert in practice.

Synthesize/load the merged surface, re-run the M3 selection harness before and
after on the same query set, and report the delta in selection accuracy and
surface tokens. A merge that saves tokens but tanks accuracy is rejected on
evidence -- this module just produces the evidence.
"""

from __future__ import annotations

from dataclasses import dataclass

from .counting import make_counter
from .inventory import Inventory
from .probes.base import RunContext
from .probes.noise import NoiseProbe


@dataclass
class ValidationDelta:
    surface_tokens_before: int
    surface_tokens_after: int
    tokens_saved: int
    selection_pass_before: float | None
    selection_pass_after: float | None
    accuracy_delta: float | None
    verdict: str  # accept | reject | inconclusive
    detail: dict


def _surface_tokens(inv: Inventory, counter) -> int:
    api = [t.to_api_dict() for t in inv.tools]
    return counter.count(api) - counter.count([])


def _mean_pass(findings) -> float | None:
    rates = [
        f.measurement.get("pass_rate")
        for f in findings
        if f.kind == "selection_error" and f.measurement.get("mode") == "labeled"
    ]
    rates = [r for r in rates if r is not None]
    return sum(rates) / len(rates) if rates else None


def validate(
    before: Inventory,
    after: Inventory,
    *,
    queries: list[dict] | None = None,
    token_backend: str = "offline",
    model: str | None = None,
    chat_client=None,
    min_accuracy_drop: float = 0.05,
) -> ValidationDelta:
    counter = make_counter(token_backend, model=model)

    tb = _surface_tokens(before, counter)
    ta = _surface_tokens(after, counter)

    pass_before = pass_after = acc_delta = None
    if queries and chat_client is not None:
        probe = NoiseProbe()

        def ctx_for(inv):
            return RunContext(
                inventory=inv,
                counter=counter,
                available={"inventory", "api_key", "llm", "queries"},
                model=model,
                client=chat_client,
                queries=queries,
            )

        fb = probe.run(ctx_for(before))
        fa = probe.run(ctx_for(after))
        pass_before = _mean_pass(fb)
        pass_after = _mean_pass(fa)
        if pass_before is not None and pass_after is not None:
            acc_delta = round(pass_after - pass_before, 3)

    # Verdict.
    saved = tb - ta
    if acc_delta is None:
        verdict = "inconclusive" if saved <= 0 else "accept_on_tokens"
    elif acc_delta < -min_accuracy_drop:
        verdict = "reject"  # tanks accuracy
    elif saved > 0:
        verdict = "accept"
    else:
        verdict = "inconclusive"

    return ValidationDelta(
        surface_tokens_before=tb,
        surface_tokens_after=ta,
        tokens_saved=saved,
        selection_pass_before=pass_before,
        selection_pass_after=pass_after,
        accuracy_delta=acc_delta,
        verdict=verdict,
        detail={
            "backend": counter.backend,
            "authoritative": counter.authoritative,
            "queries_used": len(queries) if queries else 0,
        },
    )
