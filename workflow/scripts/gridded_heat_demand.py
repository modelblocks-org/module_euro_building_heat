"""Export both annual heat sinks directly from aligned structural support.

The first pass measures each shape's sector support after the original
weather-intersection allocation. The second pass normalises to the annual
shape totals. This preserves boundary corrections without intermediate
household/commercial demand rasters or reprojection.
"""

import logging
from collections.abc import Iterator
from contextlib import ExitStack
from pathlib import Path
from time import perf_counter

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from _utils import window_polygons
from rasterio.features import geometry_mask, rasterize
from shapely.geometry import box

logger = logging.getLogger(__name__)


def shape_blocks(reference, shapes: gpd.GeoDataFrame) -> Iterator:
    """Assign output cells to shapes by the existing pixel-centre convention."""
    # Index polygon parts so distant islands do not enlarge candidate bounds.
    # Keep original row IDs and ordering: later shapes win overlapping pixels.
    geometries = shapes.geometry.reset_index(drop=True).explode(index_parts=False)
    spatial_index = geometries.sindex
    for _, window in reference.block_windows(1):
        left, bottom, right, top = reference.window_bounds(window)
        margin = max(abs(reference.transform.a), abs(reference.transform.e))
        bounds = box(left - margin, bottom - margin, right + margin, top + margin)
        local = geometries.iloc[spatial_index.query(bounds)].sort_index(kind="stable")
        local = local.intersection(bounds)
        local = local[~local.is_empty]
        labels = np.zeros((int(window.height), int(window.width)), dtype="int32")
        if len(local):
            rasterize(
                [(geometry, int(i + 1)) for i, geometry in local.items()],
                out=labels,
                transform=reference.window_transform(window),
                all_touched=False,
            )
        yield window, labels


def support_blocks(sources, shapes, intersections, factors, assigned=None):
    """Evaluate both sector weights together, retaining intersection boundary cells."""
    reference = sources["household"]
    years = len(factors["household"])
    for window, labels in shape_blocks(reference, shapes):
        structural = {
            sector: source.read(1, window=window, masked=True)
            .filled(0)
            .astype("float64")
            for sector, source in sources.items()
        }
        # These tiles contribute neither support nor energy in either pass.
        # Unwritten output tiles read as the output profile's nodata value (0).
        if not any(np.any(values) for values in structural.values()):
            continue
        heated = {sector: np.zeros((years, *labels.shape)) for sector in sources}
        local = window_polygons(intersections, window, reference.transform)
        for index, geometry in local.geometry.items():
            mask = geometry_mask(
                [geometry],
                labels.shape,
                reference.window_transform(window),
                invert=True,
                all_touched=False,
            )
            for sector, support in structural.items():
                energy = factors[sector][:, index, None] * support[mask]
                heated[sector][:, mask] += energy
                if assigned is not None:
                    assigned[sector][:, index] += energy.sum(axis=1)
        yield window, labels, structural, heated


def write_heat_demand_rasters(
    annual_demand: pd.DataFrame,
    shapes: gpd.GeoDataFrame,
    residential_support_path: str,
    commercial_support_path: str,
    grid_shapes: gpd.GeoDataFrame,
    grid_factors: dict[str, np.ndarray],
    weather_demand_years: dict[int, int],
    output_paths: dict[str, str],
    *,
    hdd_elasticity: float = 0.5,
    hdd_base_temperature: float = 15.5,
) -> None:
    """Preserve annual sector/shape totals and write combined MWh per 100 m cell."""
    pairs = list(weather_demand_years.items())
    sectors = ("household", "commercial")
    with ExitStack() as stack:
        sources = {
            sector: stack.enter_context(rasterio.open(path))
            for sector, path in zip(
                sectors,
                [residential_support_path, commercial_support_path],
                strict=True,
            )
        }
        reference = sources["household"]
        shapes = shapes.to_crs(reference.crs).reset_index(drop=True)
        intersections = grid_shapes.to_crs(reference.crs).reset_index(drop=True)
        shape_ids = pd.Index(shapes.shape_id)
        count = len(shapes) + 1
        totals = {
            (sink, sector): np.zeros((len(pairs) if sink == "space_heat" else 1, count))
            for sink in output_paths
            for sector in sectors
        }
        assigned = {sector: np.zeros_like(grid_factors[sector]) for sector in sectors}
        started = perf_counter()
        logger.info("Measuring raster allocation support (pass 1 of 2).")
        for _, labels, structural, heated in support_blocks(
            sources, shapes, intersections, grid_factors, assigned
        ):
            for (sink, sector), total in totals.items():
                values = (
                    heated[sector] if sink == "space_heat" else structural[sector][None]
                )
                for band, value in enumerate(values):
                    total[band] += np.bincount(
                        labels.ravel(), weights=value.ravel(), minlength=count
                    )
        logger.info("Measured raster support in %.1fs.", perf_counter() - started)

        # Check the original weather-intersection country allocation before the
        # final shape correction, which can redistribute boundary-cell energy.
        mapping = shapes.set_index("shape_id").country_id
        countries = grid_shapes.id.map(mapping).reset_index(drop=True)
        if countries.isna().any():
            raise ValueError("Weather intersections contain unknown shape IDs.")
        for sector in sectors:
            for band, (_, year) in enumerate(pairs):
                annual = annual_demand.loc[
                    (annual_demand.year == year)
                    & (annual_demand.category == sector)
                    & (annual_demand.end_use == "space_heat")
                ]
                expected = (
                    annual.groupby(annual.shape_id.map(mapping)).heat_demand_twh.sum()
                    * 1e6
                )
                actual = (
                    pd.Series(assigned[sector][band])
                    .groupby(countries)
                    .sum()
                    .reindex(expected.index, fill_value=0)
                )
                if not np.allclose(actual, expected, rtol=1e-6, atol=1e-3):
                    raise ValueError(
                        f"Weather-intersection allocation changed {sector} country totals for {year}."
                    )

        profile = {
            **reference.profile,
            "count": len(pairs),
            "dtype": "float64",
            "nodata": 0,
            "tiled": True,
            "blockxsize": 512,
            "blockysize": 512,
            "compress": "deflate",
            # Prefer faster lossless writes over the default compression level.
            "zlevel": 1,
            "predictor": 3,
            "BIGTIFF": "IF_SAFER",
        }
        outputs, scales, expected_totals = {}, {}, {}
        for sink, path in output_paths.items():
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            output = stack.enter_context(rasterio.open(path, "w", **profile))
            outputs[sink] = output
            output.update_tags(
                end_use=sink,
                categories="household,commercial",
                units="MWh/cell",
                allocation=(
                    "sector structural support * HDD^elasticity normalised per shape"
                    if sink == "space_heat"
                    else "sector structural support without HDD normalised per shape"
                ),
            )
            if sink == "space_heat":
                output.update_tags(
                    hdd_elasticity=hdd_elasticity,
                    hdd_base_temperature_celsius=hdd_base_temperature,
                )
            for band, (weather_year, demand_year) in enumerate(pairs):
                annual = annual_demand.loc[
                    (annual_demand.year == demand_year)
                    & (annual_demand.end_use == sink)
                ]
                expected_totals[sink, band] = np.zeros(count)
                for sector in sectors:
                    energy = (
                        annual.loc[annual.category == sector]
                        .set_index("shape_id")
                        .heat_demand_twh.reindex(shape_ids)
                        .to_numpy()
                        * 1e6
                    )
                    support = totals[sink, sector][
                        band if sink == "space_heat" else 0, 1:
                    ]
                    scale = np.zeros(count)
                    np.divide(energy, support, out=scale[1:], where=support > 0)
                    scales[sink, sector, band] = scale
                    expected_totals[sink, band][1:] += energy
                output.set_band_description(
                    band + 1, f"{sink}_{demand_year}_weather_{weather_year}"
                )
                output.set_band_unit(band + 1, "MWh/cell")
                output.update_tags(
                    band + 1, demand_year=demand_year, weather_year=weather_year
                )

        actual_totals = {key: np.zeros(count) for key in expected_totals}
        started = perf_counter()
        logger.info("Writing normalized demand rasters (pass 2 of 2).")
        for window, labels, structural, heated in support_blocks(
            sources, shapes, intersections, grid_factors
        ):
            for sink, output in outputs.items():
                for band in range(len(pairs)):
                    energy = sum(
                        (
                            heated[sector][band]
                            if sink == "space_heat"
                            else structural[sector]
                        )
                        * scales[sink, sector, band][labels]
                        for sector in sectors
                    )
                    actual_totals[sink, band] += np.bincount(
                        labels.ravel(), weights=energy.ravel(), minlength=count
                    )
                    output.write(energy, band + 1, window=window)
        logger.info("Wrote demand rasters in %.1fs.", perf_counter() - started)
        for key, expected in expected_totals.items():
            if not np.allclose(actual_totals[key], expected, rtol=1e-6, atol=1e-3):
                raise ValueError(
                    f"Demand raster changed annual shape totals for {key}."
                )
