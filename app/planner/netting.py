"""Materials netting (PLANNER_SPEC: "Materials netting").

need = pairs x qty_per_pair x (1 + waste), rounded up.
buy  = ceil(max(need - on_hand, 0) / MOQ) x MOQ.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Netting:
    need: int
    buy: int


def net_material(
    pairs: int,
    qty_per_pair: float,
    waste_pct: float,
    on_hand: float,
    moq: float,
) -> Netting:
    # round(..., 6) before ceil guards against float noise such as 4950.000000001.
    gross = round(pairs * qty_per_pair * (1 + waste_pct / 100), 6)
    need = math.ceil(gross)
    shortfall = max(need - on_hand, 0)
    if moq > 0:
        buy = math.ceil(round(shortfall / moq, 6)) * moq
    else:
        buy = shortfall
    return Netting(need=need, buy=int(buy))
