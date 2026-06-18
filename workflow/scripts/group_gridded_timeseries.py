"""Aggregate gridded heat demand profiles to user-provided shapes."""

import warnings
from functools import partial
from multiprocessing import Pool

import xarray as xr


def group_gridcells(
    gridded_data: xr.Dataset, grid_weight: xr.DataArray, threads: int
) -> xr.DataArray:
    """Group gridded heat data into resolution-specific units.

    Args:
        gridded_data (xr.Dataset): Gridded timeseries space heat and hot water data.
        grid_weight (xr.DataArray): Weighted mapping from grid (a.k.a. "site") to units.
        threads (int): Number of threads over which to undertake multiprocessing.

    Returns:
        xr.DataArray: data in resolution-specific units.
    """
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

    apply_weights = partial(
        _site_weighted_ave, gridded_data=gridded_data, grid_weight=grid_weight
    )
    # This is a slow operation, so we parallelise it.
    with Pool(threads) as pool:
        per_id_averages = pool.map(apply_weights, weighted_ids)
    weighted_average_ds = xr.concat(
        per_id_averages, dim=xr.IndexVariable("id", weighted_ids)
    )

    return weighted_average_ds


def _site_weighted_ave(
    id: str, gridded_data: xr.Dataset, grid_weight: xr.DataArray
) -> xr.Dataset:
    """Get the weighted average of all gridcells for a given spatial unit (id).

    This function exists to enable multi-processing across IDs.
    """
    id_grid_weight = grid_weight.sel(id=id).dropna("site")
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
    gridded_data = xr.open_dataset(
        snakemake.input.gridded_timeseries_data, decode_timedelta=True
    )
    grid_weights = xr.open_dataarray(
        snakemake.input.grid_weights, decode_timedelta=True
    )

    resolution_specific_data = group_gridcells(
        gridded_data, grid_weights, snakemake.threads
    )
    resolution_specific_data.to_netcdf(snakemake.output[0])
