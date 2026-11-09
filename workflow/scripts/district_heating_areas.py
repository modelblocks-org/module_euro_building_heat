"""Identify coherent DH areas using heat density and connectivity."""

import json
import sys
from io import BytesIO
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import rasterio
import shapely
from _schemas import DH_AREA_COLUMNS
from rasterio.features import geometry_mask, geometry_window, shapes
from scipy import ndimage


def identify_areas(
    density: np.ndarray,
    connected_heat: np.ndarray,
    selection: dict[str, Any],
) -> np.ndarray:
    """Selects connected areas of sufficient heat density and total connected heat.
    """
    structure = np.ones((3, 3), dtype=bool)
    mask = (density > selection["minimum_heat_per_hectare"])
    mask = (
        ndimage.binary_closing(np.pad(mask, 1), structure=structure)[1:-1, 1:-1]
    )
    labels, count = ndimage.label(mask, structure)
    totals = np.bincount(
        labels[mask], weights=connected_heat[mask], minlength=count + 1
    )
    selected = totals >= selection["minimum_heat_per_district_heating_area"]
    selected[0] = False
    return ndimage.label(selected[labels], structure)[0]


def calculate_areas(
    shapes_path: str, costs_path: str, heat_path: str, ids_path: str, areas_path: str
) -> None:
    """Aggregate horizon rasters into polygons and potentials.

    Load one country window at a time so processing blocks never split areas.
    Demand and connected heat are read directly from the upstream heat product.
    """
    area_frames = []
    offset = 0
    with rasterio.open(costs_path) as source, rasterio.open(heat_path) as heat:
        regions = gpd.read_parquet(shapes_path).to_crs(source.crs)
        tags = source.tags()
        p = json.loads(tags["assumptions"])
        selection = p["area_selection"]
        countries = json.loads(tags["country_parameters"])
        cell_area_ha = float(tags["cell_area_ha"])
        profile = source.profile.copy()
        profile.update(count=1, dtype="int32", nodata=0)
        with rasterio.open(ids_path, "w+", **profile) as output:
            output.set_band_description(1, "area_id")
            output.set_band_unit(1, "1")
            output.update_tags(
                **(tags | {"area_selection": json.dumps(selection, sort_keys=True)}),
                area_method="density, closing, connectivity and minimum connected annual heat",
                excluded_cells="zero delivery or undefined total network cost; ID 0",
            )
            for country_id, group in regions.groupby("country_id", sort=True):
                country = countries[country_id]
                window = geometry_window(source, group.geometry)
                transform = source.window_transform(window)
                shape = (int(window.height), int(window.width))
                domain = geometry_mask(
                    group.geometry, out_shape=shape, transform=transform, invert=True
                )
                investment = source.read([1, 2, 3], window=window, masked=True).filled(
                    np.nan
                )
                demand, connected = heat.read(
                    [1, 2], window=window, masked=True
                ).filled(np.nan)
                eligible = domain & (demand > 0) & np.isfinite(investment[2])
                labels = identify_areas(
                    demand / cell_area_ha, connected, eligible, selection
                )
                selected = labels > 0
                count = int(labels.max())
                if not count:
                    continue
                local_ids = labels[selected]

                def aggregate(values: np.ndarray) -> np.ndarray:
                    """Sum selected cells for each local area, omitting background."""
                    return np.bincount(
                        local_ids, weights=values[selected], minlength=count + 1
                    )[1:]

                ids = np.arange(offset + 1, offset + count + 1)
                connected_totals = aggregate(connected)
                investments = np.stack([aggregate(a) for a in investment])
                cells = np.bincount(local_ids, minlength=count + 1)[1:]
                # Polygonize edge-connected pieces, then unite diagonal pieces by ID.
                pieces = (
                    gpd.GeoDataFrame(
                        [
                            {
                                "local_id": int(value),
                                "geometry": shapely.geometry.shape(geom),
                            }
                            for geom, value in shapes(
                                labels, mask=selected, transform=transform
                            )
                        ],
                        crs=source.crs,
                    )
                    .dissolve(by="local_id")
                    .sort_index()
                )
                areas = gpd.GeoDataFrame(
                    {
                        "area_id": ids,
                        "country_id": country_id,
                        "cell_count": cells,
                        "area_ha": cells * cell_area_ha,
                        "assessment_year": int(tags["assessment_year"]),
                        "connection_fraction": country["connection_fraction"],
                        "heat_demand_mwh_per_year": aggregate(demand),
                        "heat_potential_mwh_per_year": connected_totals,
                        "distribution_investment_eur": investments[0],
                        "service_investment_eur": investments[1],
                        "total_investment_eur": investments[2],
                    },
                    geometry=pieces.geometry.to_numpy(),
                    crs=source.crs,
                )
                area_frames.append(areas)
                # Country windows can overlap; update only this country's selected cells.
                block = output.read(1, window=window)
                block[selected] = labels[selected] + offset
                output.write(block, 1, window=window)
                offset += count
        areas = gpd.GeoDataFrame(
            pd.concat(area_frames, ignore_index=True)
            if area_frames
            else {
                name: pd.Series(dtype=dtype) for name, dtype in DH_AREA_COLUMNS.items()
            },
            geometry="geometry",
            crs=source.crs,
        )
        # Preserve GeoParquet metadata while adding the resolved country provenance.
        with BytesIO() as buffer:
            areas.to_parquet(buffer, index=False)
            buffer.seek(0)
            table = pq.read_table(buffer)
            metadata = dict(table.schema.metadata)
            metadata[b"district_heating"] = json.dumps(tags, sort_keys=True).encode()
            pq.write_table(table.replace_schema_metadata(metadata), areas_path)


if __name__ == "__main__":
    sys.stderr = open(snakemake.log[0], "w", buffering=1)
    calculate_areas(
        snakemake.input.shapes,
        snakemake.input.costs,
        snakemake.input.heat,
        snakemake.output.ids,
        snakemake.output.areas,
    )
