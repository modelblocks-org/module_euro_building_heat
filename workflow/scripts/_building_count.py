"""Sector building counts kept independent of floor-area and heat support."""

import numpy as np
import pandas as pd
from _utils import clipped_grid, point_grid


def reference_count_ratios(stats, population):
    """Return sector shares of all buildings and counts per covered inhabitant."""
    counts = pd.Series(
        {
            "residential": stats.n_type_residential.sum(),
            "commercial": stats.n_subtype_commercial.sum()
            + stats.n_subtype_public.sum(),
        }
    )
    return pd.concat(
        [
            (counts / stats.n.sum()).add_suffix("_share"),
            (counts / population).add_suffix("_per_person"),
        ]
    )


def building_count_grid(
    profile, full_profile, sectors, sources, ratios, proxy_population, clipped
):
    """Sum sector building equivalents and the Microsoft population fallback.

    Microsoft inputs must exclude sparse tiles. Proxy counts can be fractional.
    Clipping preserves the regional allocation weights.
    """
    proxy_population = clipped_grid(proxy_population, full_profile, profile, clipped)
    counts = np.zeros((profile["height"], profile["width"]))
    for sector, buildings in sectors.items():
        inside = buildings.loc[buildings.in_scope]
        if sources[f"{sector}_source"] == "eubucco":
            counts += point_grid(profile, inside, inside.count_weight)
        else:
            counts += point_grid(
                profile, inside, inside.count_weight * ratios[f"{sector}_share"]
            )
            counts += proxy_population * ratios[f"{sector}_per_person"]
    return counts
