"""Calculate unscreened per-cell DH networks using building-count trench lengths."""

import json
import sys
from contextlib import ExitStack
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from _schemas import DH_COST_BANDS, DH_HEAT_BANDS
from rasterio.features import geometry_mask
from rasterio.windows import Window, bounds, transform


def country_parameters(
    country_table: pd.DataFrame,
    country: str,
    p: dict[str, Any],
    data_proxies: dict[str, list[str]],
) -> dict[str, Any]:
    """Return country parameters for district heating network sizing and costing.

    Use the assessment year to interpolate between the start and end years of the
    country parameter table. If the country is missing, use country proxies.
    """
    table = country_table.set_index("country_id")
    weight = (p["assessment_year"] - table.start_year) / (
        table.end_year - table.start_year
    )
    weight = weight.clip(0, 1)
    table["connection_fraction"] = table.share_start + weight * (
        table.share_end - table.share_start
    )
    donors = data_proxies.get(country, [])
    result: dict[str, Any] = {"provenance": {}}
    for key in ("connection_fraction", "c1_eur_per_m", "c2_eur_per_m2"):
        if country in table.index and pd.notna(table.at[country, key]):
            result[key] = float(table.at[country, key])
            result["provenance"][key] = {country: table.at[country, "source"]}
        else:
            values = table.loc[donors, key]
            result[key] = float(values.mean())
            result["provenance"][key] = table.loc[donors, "source"].to_dict()
    return result


def distribution_trench_length(
    building_count: np.ndarray, share: float, p: dict[str, Any]
) -> np.ndarray:
    """Return distribution trench metres for MS * building_count.

    Below the configured minimum connected buildings, return zero: no viable
    distribution network.
    """
    curve = p["distribution_length_curve"]
    connected = building_count * share
    viable = connected >= curve["minimum_connected_buildings"]
    length = np.zeros_like(connected, dtype=float)
    length[viable] = (
        curve["log_slope_m"] * np.log(connected[viable]) + curve["intercept_m"]
    )
    return length


def service_trench_length(
    building_count: np.ndarray, share: float, cell_area_ha: float, p: dict[str, Any]
) -> np.ndarray:
    """Return service trench metres from connected buildings per hectare.

    L_u = c / (1 + a * exp(-b * MS * N / A_ha)); L = L_u * A_ha.
    Explicitly return zero for no connected buildings, overriding the nonzero
    logistic intercept.
    """
    curve = p["service_length_curve"]
    connected = building_count * share
    length = np.zeros_like(connected, dtype=float)
    active = connected > 0
    with np.errstate(over="raise", divide="raise", invalid="raise"):
        density = connected[active] / cell_area_ha
        length[active] = (
            cell_area_ha
            * curve["asymptote_m_per_ha"]
            / (
                1
                + curve["logistic_a"]
                * np.exp(-curve["logistic_b_ha_per_building"] * density)
            )
        )
    return length


def service_pipe_diameter(
    density_gj_per_m: np.ndarray, p: dict[str, Any]
) -> np.ndarray:
    """Return service diameter in metres from annual linear heat density in GJ/m.

    DN_mm = a * exp(b * Q/L). Convert mm to m for C2 in EUR/m².
    """
    curve = p["service_diameter_curve"]
    # Check in log space before exponentiation: extreme densities must yield
    # an invalid estimate, not overflow or a clipped (apparently valid) cost.
    log_diameter = (
        np.log(curve["exponential_a_mm"] / 1000)
        + curve["exponential_b_m_per_gj"] * density_gj_per_m
    )
    valid = (
        np.isfinite(log_diameter)
        & (density_gj_per_m >= 0)
        & (log_diameter >= np.log(p["pipe_diameter_range_m"][0]))
        & (log_diameter <= np.log(p["pipe_diameter_range_m"][1]))
    )
    diameter = np.full_like(density_gj_per_m, np.nan, dtype=float)
    diameter[valid] = np.exp(log_diameter[valid])
    return diameter


def network_costs(
    heat: np.ndarray,
    cell_area_ha: float,
    country: dict[str, Any],
    p: dict[str, Any],
    building_count: np.ndarray,
) -> np.ndarray:
    """Calculate network investment for fixed connected horizon demand.

    Calculate investment as I = L * (C1 + C2 * d).
    Zero investments are undefined; NaNs propagate into total investment.
    """
    curve = p["distribution_diameter_curve"]
    share = country["connection_fraction"]
    distribution = distribution_trench_length(building_count, share, p)
    service = service_trench_length(building_count, share, cell_area_ha, p)
    result = np.full((3, heat.size), np.nan)
    if share == 0:
        return result
    active = (
        np.isfinite(heat)
        & (heat > 0)
        & np.isfinite(building_count)
        & (building_count > 0)
    )
    lengths = np.stack([distribution[active], service[active]])
    # Annual MWh/cell -> GJ/cell, once for both pipe categories.
    delivered_gj = heat[active] * share * 3.6
    density = np.divide(
        delivered_gj, lengths, out=np.zeros_like(lengths), where=lengths > 0
    )
    diameter = np.full_like(density, curve["small_pipe_diameter_m"])
    # The small-pipe regime depends on linear heat density; branching on total
    # heat is inconsistent with the regression domain and can give negative diameters.
    large_distribution = (lengths[0] > 0) & (
        density[0] >= curve["small_pipe_density_gj_per_m"]
    )
    diameter[0, large_distribution] = (
        curve["log_slope_m"] * np.log(density[0, large_distribution])
        + curve["intercept_m"]
    )
    diameter[1] = service_pipe_diameter(density[1], p)
    valid = (
        np.isfinite(diameter)
        & (diameter >= p["pipe_diameter_range_m"][0])
        & (diameter <= p["pipe_diameter_range_m"][1])
        & (lengths > 0)
    )
    diameter[~valid] = np.nan
    investment = lengths * (
        country["c1_eur_per_m"] + country["c2_eur_per_m2"] * diameter
    )
    # An absent network is not free: totals require both network components.
    investment[investment == 0] = np.nan
    result[:2, active] = investment
    result[2, active] = investment.sum(axis=0)
    return result


def calculate_district_heating(
    shapes_path: str,
    demand_path: str,
    hot_water_path: str,
    country_parameters_path: str,
    heat_output: str,
    costs_output: str,
    p: dict[str, Any],
    data_proxies: dict[str, Any],
    grid_crs: str,
    building_count_path: str,
) -> None:
    """Write raster blocks using country parameters assigned by cell centre."""
    shapes = gpd.read_parquet(shapes_path).to_crs(grid_crs)
    countries = sorted(shapes.country_id.unique())
    country_table = pd.read_csv(country_parameters_path)
    invalid_cells = 0
    invalid_heat = 0.0
    proxies = data_proxies["district_heating"]
    parameters = {c: country_parameters(country_table, c, p, proxies) for c in countries}
    # These are file-format/provenance conventions, not configurable physical assumptions.
    tags = {
        "technology": "district_heating",
        "grid_crs": grid_crs,
        "assessment_year": p["assessment_year"],
        "assumptions": json.dumps(p | {"data_proxies": proxies}, sort_keys=True),
        "country_parameters": json.dumps(parameters, sort_keys=True),
        "method": "Fallahnejad2024 adaptation; fixed horizon demand and connection fraction; unscreened",
        "domain": "cell centres in supplied land shapes; outside is nodata",
        "pipe_diameter_validity_m": json.dumps(p["pipe_diameter_range_m"]),
        "invalid_estimates": "NaN cost; heat retained; outside empirical diameter envelope or insufficient connected buildings; not economic rejection",
        "service_model": "SanchezGarcia2023; logistic trench length; exponential diameter",
        "distribution_length": "Fallahnejad2024; trench metres from fixed connection fraction * building_count",
        "cost_scope": "distribution and service network investment only; no generation, losses, pumping or O&M",
    }
    with ExitStack() as stack:
        # Heat uses share the upstream grid and configured demand/weather pairing.
        heat = stack.enter_context(rasterio.open(demand_path))
        hot_water = stack.enter_context(rasterio.open(hot_water_path))
        # Band 1 is weather_years.start and its paired demand year.
        tags.update(heat.tags(1))
        buildings = stack.enter_context(rasterio.open(building_count_path))
        cell_area_ha = abs(heat.transform.determinant) / 10000
        tags["cell_area_ha"] = cell_area_ha
        profile = dict(
            driver="GTiff",
            width=heat.width,
            height=heat.height,
            transform=heat.transform,
            crs=heat.crs,
            dtype="float64",
            nodata=np.nan,
            tiled=True,
            compress="deflate",
            BIGTIFF="IF_SAFER",
        )
        supply = stack.enter_context(
            rasterio.open(heat_output, "w", count=2, **profile)
        )
        costs = stack.enter_context(
            rasterio.open(costs_output, "w", count=len(DH_COST_BANDS), **profile)
        )
        for output, descriptions, units in (
            (supply, DH_HEAT_BANDS, ("MWh/cell",) * supply.count),
            (costs, DH_COST_BANDS, ("EUR/cell",) * costs.count),
        ):
            output.update_tags(**tags)
            for band, (description, unit) in enumerate(
                zip(descriptions, units, strict=True), 1
            ):
                output.set_band_description(band, description)
                output.set_band_unit(band, unit)
        block_size = 512
        for row in range(0, heat.height, block_size):
            for col in range(0, heat.width, block_size):
                window = Window(
                    col,
                    row,
                    min(block_size, heat.width - col),
                    min(block_size, heat.height - row),
                )
                # Generated heat rasters use nodata=0 for empty support. A zero
                # end use must not erase positive demand from the other end use.
                # Other nodata sentinels denote unknown demand and propagate NaN.
                demand = sum(
                    source.read(1, window=window, masked=True)
                    .astype(float)
                    .filled(0 if source.nodata == 0 else np.nan)
                    for source in (heat, hot_water)
                )
                # Upstream building_count uses 0 as both empty and nodata; both
                # imply an undefined network, never a free distribution network.
                building_count = (
                    buildings.read(1, window=window, masked=True)
                    .astype(float)
                    .filled(np.nan)
                )
                candidates = shapes.iloc[
                    sorted(shapes.sindex.intersection(bounds(window, heat.transform)))
                ]
                supply_block = np.full((supply.count, *demand.shape), np.nan)
                cost_block = np.full((costs.count, *demand.shape), np.nan)
                for country, group in candidates.groupby("country_id", sort=True):
                    selected = geometry_mask(
                        group.geometry,
                        out_shape=demand.shape,
                        transform=transform(window, heat.transform),
                        invert=True,
                    )
                    share = parameters[country]["connection_fraction"]
                    supply_block[0, selected] = demand[selected]
                    supply_block[1, selected] = share * demand[selected]
                    cost_block[:, selected] = network_costs(
                        demand[selected],
                        cell_area_ha,
                        parameters[country],
                        p,
                        building_count[selected],
                    )
                invalid = (supply_block[1] > 0) & ~np.isfinite(cost_block[2])
                invalid_cells += int(invalid.sum())
                invalid_heat += float(supply_block[1, invalid].sum())
                supply.write(supply_block, window=window)
                costs.write(cost_block, window=window)
        for output in (supply, costs):
            output.update_tags(
                invalid_network_cells=invalid_cells,
                invalid_network_connected_heat_mwh_per_year=invalid_heat,
            )
        print(
            f"Undefined DH estimates: {invalid_cells} cells, {invalid_heat:g} connected MWh/year",
            file=sys.stderr,
        )


if __name__ == "__main__":
    sys.stderr = open(snakemake.log[0], "w", buffering=1)
    calculate_district_heating(
        snakemake.input.shapes,
        snakemake.input.space_heat,
        snakemake.input.hot_water,
        snakemake.input.country_parameters,
        snakemake.output.heat,
        snakemake.output.costs,
        snakemake.params.assumptions,
        snakemake.params.data_proxies,
        snakemake.params.grid_crs,
        snakemake.input.building_count,
    )
