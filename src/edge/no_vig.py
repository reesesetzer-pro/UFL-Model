"""
De-vig odds: extract a "no-vig" probability from American odds pairs.

We support three methods. Default to power because it's the most accurate
on football two-way markets but multiplicative and additive are useful as
checks.

    additive (proportional):  p_i = q_i / sum(q)
    multiplicative (logit):   solve for k such that prod(q_i^k) = 1
    power:                    p_i = q_i^k where sum(q_i^k) = 1, solved
                              numerically; closer to true probabilities
                              for two-way markets

Reference: Stern, "Comparing Methods for Estimating Win Probabilities..."
plus the more recent Buchdahl/Pinnacle papers.
"""
from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Iterable, Optional

# --------------------------------------------------------------------------
# American odds <-> implied probability

def american_to_decimal(american: float) -> float:
    if american >= 100:
        return 1 + american / 100.0
    return 1 + 100.0 / abs(american)


def decimal_to_american(decimal: float) -> int:
    if decimal <= 1.0:
        raise ValueError("decimal must be > 1")
    if decimal >= 2.0:
        return int(round((decimal - 1) * 100))
    return int(round(-100 / (decimal - 1)))


def american_to_implied(american: float) -> float:
    return 1.0 / american_to_decimal(american)


def implied_to_american(prob: float) -> int:
    if prob <= 0 or prob >= 1:
        raise ValueError(f"prob out of (0,1): {prob}")
    return decimal_to_american(1.0 / prob)


# --------------------------------------------------------------------------
# Devig methods

def devig_additive(q: list[float]) -> list[float]:
    s = sum(q)
    return [x / s for x in q]


def devig_multiplicative(q: list[float], iters: int = 60) -> list[float]:
    # Solve for k such that prod(q_i^k) = 1 (i.e. log-sum = 0)
    # Newton-style binary search on k in (0, 5)
    lo, hi = 0.001, 5.0
    for _ in range(iters):
        mid = (lo + hi) / 2
        s = sum(qi ** mid for qi in q)
        if s > 1:
            lo = mid
        else:
            hi = mid
    k = (lo + hi) / 2
    out = [qi ** k for qi in q]
    # Renormalize tiny errors
    s = sum(out)
    return [x / s for x in out]


def devig_power(q: list[float], iters: int = 60) -> list[float]:
    """Power method: p_i = q_i^k, solve k so sum(p) = 1."""
    lo, hi = 0.001, 5.0
    for _ in range(iters):
        mid = (lo + hi) / 2
        s = sum(qi ** mid for qi in q)
        if s > 1:
            lo = mid
        else:
            hi = mid
    k = (lo + hi) / 2
    return [qi ** k for qi in q]


def devig(q: list[float], method: str = "power") -> list[float]:
    if method == "additive":
        return devig_additive(q)
    if method == "multiplicative":
        return devig_multiplicative(q)
    if method == "power":
        return devig_power(q)
    raise ValueError(f"unknown method {method}")


# --------------------------------------------------------------------------
# Multi-book consensus

@dataclass
class MarketLine:
    book: str
    side_a: float       # American odds for side A (e.g. home moneyline)
    side_b: float       # American odds for side B
    label_a: str = "A"
    label_b: str = "B"


def consensus_no_vig(lines: list[MarketLine],
                     method: str = "power") -> Optional[dict]:
    """
    Average no-vig probabilities across multiple books.
    Returns {label_a: avg_prob_a, label_b: avg_prob_b, n_books, books_used}.
    """
    if not lines:
        return None
    probs_a, probs_b = [], []
    used = []
    for ln in lines:
        try:
            qa = american_to_implied(ln.side_a)
            qb = american_to_implied(ln.side_b)
            pa, pb = devig([qa, qb], method=method)
            probs_a.append(pa)
            probs_b.append(pb)
            used.append(ln.book)
        except Exception:
            continue
    if not used:
        return None
    label_a = lines[0].label_a
    label_b = lines[0].label_b
    return {
        label_a: sum(probs_a) / len(probs_a),
        label_b: sum(probs_b) / len(probs_b),
        "n_books": len(used),
        "books_used": used,
    }


def best_price(lines: list[MarketLine], side: str) -> Optional[tuple[str, float]]:
    """Best available American odds across books for a given side ('a' or 'b').
    Returns (book, american_odds)."""
    if side not in ("a", "b"):
        raise ValueError("side must be 'a' or 'b'")
    best = None
    for ln in lines:
        odds = ln.side_a if side == "a" else ln.side_b
        try:
            decimal = american_to_decimal(odds)
        except Exception:
            continue
        if best is None or decimal > best[1]:
            best = (ln.book, decimal)
    if best is None:
        return None
    return (best[0], decimal_to_american(best[1]))


if __name__ == "__main__":
    # NFL-style example: -110 / -110 spread
    p1, p2 = devig([american_to_implied(-110), american_to_implied(-110)])
    print(f"-110/-110 → {p1*100:.2f}% / {p2*100:.2f}% (expect 50/50)")
    # Asymmetric: -150 / +130
    p1, p2 = devig([american_to_implied(-150), american_to_implied(+130)])
    print(f"-150/+130 → {p1*100:.2f}% / {p2*100:.2f}%")

    # Multi-book consensus
    lines = [
        MarketLine("dk",       -150, +130, "home_ML", "away_ML"),
        MarketLine("fd",       -145, +125, "home_ML", "away_ML"),
        MarketLine("mgm",      -155, +135, "home_ML", "away_ML"),
        MarketLine("caesars",  -148, +128, "home_ML", "away_ML"),
        MarketLine("bet365",   -150, +130, "home_ML", "away_ML"),
        MarketLine("thescore", -147, +127, "home_ML", "away_ML"),
        MarketLine("hardrock", -150, +132, "home_ML", "away_ML"),
    ]
    cons = consensus_no_vig(lines)
    print(f"\nConsensus across {cons['n_books']} books: home={cons['home_ML']*100:.2f}%")
    print(f"Best home price: {best_price(lines, 'a')}")
    print(f"Best away price: {best_price(lines, 'b')}")
