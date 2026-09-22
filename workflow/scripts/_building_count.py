"""Sector building counts kept independent of floor-area and heat support."""

import numpy as np
import pandas as pd
from _utils import clipped_grid, point_grid, points_within_scope


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
    profile, full_profile, sectors, sources, ratios, proxy_population, scope, clipped
):
    """Count sector centroids and reuse the shared Microsoft population fallback.

    Microsoft frames already exclude sparse tiles. Counts remain fractional;
    clipping never redistributes population or footprint contributions.
    """
    proxy_population = clipped_grid(proxy_population, full_profile, profile, clipped)
    counts = np.zeros((profile["height"], profile["width"]))
    for sector, buildings in sectors.items():
        inside = buildings.loc[points_within_scope(buildings, scope)]
        if sources[f"{sector}_source"] == "eubucco":
            counts += point_grid(profile, inside, np.ones(len(inside)))
        else:
            counts += point_grid(
                profile, inside, np.full(len(inside), ratios[f"{sector}_share"])
            )
            counts += proxy_population * ratios[f"{sector}_per_person"]
    return counts
