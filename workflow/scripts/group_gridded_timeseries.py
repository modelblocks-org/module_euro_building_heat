"""Aggregate gridded heat demand profiles to user-provided shapes."""

import warnings

import xarray as xr
from dask import config as dask_config


def group_gridcells(gridded_data: xr.Dataset, grid_weight: xr.DataArray) -> xr.Dataset:
    """Group gridded heat data into resolution-specific units.

    Args:
        gridded_data (xr.Dataset): Gridded timeseries space heat and hot water data.
        grid_weight (xr.DataArray): Weighted mapping from grid (a.k.a. "site") to units.

    Returns:
        xr.Dataset: Data grouped into resolution-specific units.
    """
    required_dimensions = {"site", "id"}
    if set(grid_weight.dims) != required_dimensions:
        raise ValueError(
            "Grid weights must have exactly the dimensions 'site' and 'id'."
        )
    if grid_weight.site.to_index().has_duplicates:
        raise ValueError("Grid weights contain duplicate site coordinates.")
    if grid_weight.id.to_index().has_duplicates:
        raise ValueError("Grid weights contain duplicate shape IDs.")
    if (grid_weight.fillna(0) < 0).any().item():
        raise ValueError("Grid weights cannot contain negative values.")

    population_by_id = grid_weight.sum("site")
    weighted_ids = population_by_id.id.where(population_by_id > 0, drop=True).values
    unweighted_ids = sorted(
        str(id)
        for id in population_by_id.id.where(population_by_id <= 0, drop=True).values
    )
    if unweighted_ids:
        warnings.warn(
            "No population weights found for shape IDs, so these shapes will be "
            f"skipped in gridded heat demand aggregation: {unweighted_ids}",
            stacklevel=2,
        )
    if len(weighted_ids) == 0:
        raise ValueError("No shapes have positive population weights.")

    # Construct every shape reduction in one Dask graph. Source chunks shared by
    # multiple shapes are then loaded and calculated once instead of once per process.
    per_id_averages = [
        _site_weighted_ave(id, gridded_data, grid_weight) for id in weighted_ids
    ]
    weighted_average_ds = xr.concat(
        per_id_averages, dim=xr.IndexVariable("id", weighted_ids)
    )

    return weighted_average_ds


def _site_weighted_ave(
    id: str, gridded_data: xr.Dataset, grid_weight: xr.DataArray
) -> xr.Dataset:
    """Get the weighted average of all gridcells for a given spatial unit (id)."""
    id_grid_weight = grid_weight.sel(id=id).where(lambda weight: weight > 0, drop=True)
    if id_grid_weight.sum("site").item() <= 0:
        raise ValueError(f"No population weights found for shape ID {id!r}.")
    normalised_weight = id_grid_weight / id_grid_weight.sum("site")
    normalised_weight = normalised_weight.reset_coords(drop=True)
    weighted_sites = gridded_data.sel(site=normalised_weight.site)
    return xr.Dataset(
        {
            name: (data * normalised_weight).sum("site")
            for name, data in weighted_sites.data_vars.items()
        }
    )


if __name__ == "__main__":
    with (
        xr.open_dataset(
            snakemake.input.gridded_timeseries_data, decode_timedelta=True, chunks={}
        ) as gridded_data,
        xr.open_dataarray(
            snakemake.input.grid_weights, decode_timedelta=True
        ) as grid_weights_file,
    ):
        grid_weights = grid_weights_file.load()
        resolution_specific_data = group_gridcells(gridded_data, grid_weights)
        if "time" in resolution_specific_data.coords:
            resolution_specific_data.attrs["timezone"] = "UTC"
            resolution_specific_data.time.attrs["timezone"] = "UTC"
        with dask_config.set(scheduler="threads", num_workers=snakemake.threads):
            resolution_specific_data.to_netcdf(snakemake.output[0])
