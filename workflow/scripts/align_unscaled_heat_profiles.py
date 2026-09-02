"""Align compact local-clock heat profiles to a canonical UTC timeline."""

import sys
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd
import xarray as xr
from _timeseries import LOCAL_TIME_BASIS, OUTPUT_TIMEZONE, read_shape_timezones
from dask import config as dask_config

if TYPE_CHECKING:
    snakemake: Any


@dataclass
class LocalScheduleGroup:
    """Shape IDs sharing one exact UTC-to-local clock schedule."""

    local_labels: pd.DatetimeIndex
    ids: list[str]
    timezones: list[str]


def canonical_utc_index(weather_year: int) -> pd.DatetimeIndex:
    """Return the complete UTC hourly index for a weather year."""
    return pd.date_range(
        f"{weather_year}-01-01",
        f"{weather_year + 1}-01-01",
        freq="h",
        inclusive="left",
        tz=OUTPUT_TIMEZONE,
        name="time",
    )


def utc_index_to_local_clock(
    utc_index: pd.DatetimeIndex, timezone: str
) -> pd.DatetimeIndex:
    """Convert UTC instants to timezone-naive local civil-clock labels."""
    return utc_index.tz_convert(timezone).tz_localize(None).rename("time")


def group_ids_by_local_schedule(
    canonical_index: pd.DatetimeIndex, selected_timezones: pd.Series
) -> list[LocalScheduleGroup]:
    """Group shape IDs whose UTC-to-local labels match for the complete year."""
    groups: dict[bytes, LocalScheduleGroup] = {}
    for timezone, timezone_ids in selected_timezones.groupby(selected_timezones):
        local_labels = utc_index_to_local_clock(canonical_index, timezone)
        schedule_key = local_labels.asi8.tobytes()
        if schedule_key not in groups:
            groups[schedule_key] = LocalScheduleGroup(local_labels, [], [])
        groups[schedule_key].ids.extend(timezone_ids.index.to_list())
        groups[schedule_key].timezones.append(timezone)
    return list(groups.values())


def align_local_profiles_to_utc(
    local_profiles: xr.Dataset, shape_timezones: pd.Series, weather_year: int
) -> xr.Dataset:
    """Select local-clock values for every canonical UTC timestamp by schedule."""
    profile_ids = pd.Index(local_profiles.id.values, name="shape_id")
    missing_timezones = sorted(profile_ids.difference(shape_timezones.index))
    if missing_timezones:
        raise ValueError(
            f"No inferred timezone found for aggregated shape IDs: {missing_timezones}"
        )

    canonical_index = canonical_utc_index(weather_year)
    canonical_naive = canonical_index.tz_localize(None)
    available_local_labels = pd.DatetimeIndex(local_profiles.time.values)
    selected_timezones = shape_timezones.reindex(profile_ids)
    aligned_groups = []

    for schedule_group in group_ids_by_local_schedule(
        canonical_index, selected_timezones
    ):
        missing_labels = schedule_group.local_labels.difference(available_local_labels)
        if not missing_labels.empty:
            raise ValueError(
                "Local profile buffer does not cover timezones "
                f"{schedule_group.timezones}; missing labels from "
                f"{missing_labels.min()} to {missing_labels.max()}."
            )

        local_indexer = xr.DataArray(
            schedule_group.local_labels.values, dims="utc_time"
        )
        aligned = local_profiles.sel(id=schedule_group.ids, time=local_indexer)
        aligned = (
            aligned.drop_vars("time")
            .rename({"utc_time": "time"})
            .assign_coords(time=canonical_naive.values)
        )
        aligned_groups.append(aligned)

    result = xr.concat(aligned_groups, dim="id").sel(id=profile_ids)
    result.attrs.pop("time_basis", None)
    result.attrs.pop("weather_year", None)
    result.attrs["timezone"] = OUTPUT_TIMEZONE
    result.time.attrs.pop("time_basis", None)
    result.time.attrs["timezone"] = OUTPUT_TIMEZONE
    return result


def align_local_profile_files_to_utc(
    local_profile_paths: list[str],
    shape_timezones_path: str,
    weather_years: list[int],
    workers: int,
    out_path: str,
) -> None:
    """Read cached annual profiles, align them to UTC, and write one dataset."""
    weather_years = [int(year) for year in weather_years]
    if len(weather_years) != len(local_profile_paths):
        raise ValueError("Each weather year must have one local-profile input path.")

    shape_timezones = read_shape_timezones(shape_timezones_path)
    with ExitStack() as stack:
        aligned_years = []
        for weather_year, local_profile_path in zip(
            weather_years, local_profile_paths, strict=True
        ):
            local_profiles = stack.enter_context(
                xr.open_dataset(local_profile_path, decode_timedelta=True, chunks={})
            )
            if local_profiles.attrs.get("time_basis") != LOCAL_TIME_BASIS:
                raise ValueError(
                    f"Local profile {local_profile_path!r} is not marked as "
                    f"{LOCAL_TIME_BASIS!r}."
                )
            if int(local_profiles.attrs.get("weather_year", -1)) != weather_year:
                raise ValueError(
                    f"Local profile {local_profile_path!r} does not contain "
                    f"weather year {weather_year}."
                )
            aligned_years.append(
                align_local_profiles_to_utc(
                    local_profiles, shape_timezones, weather_year
                )
            )

        result = xr.concat(aligned_years, dim="time").sortby("time")
        result.attrs["timezone"] = OUTPUT_TIMEZONE
        result.time.attrs["timezone"] = OUTPUT_TIMEZONE
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        with dask_config.set(scheduler="threads", num_workers=workers):
            result.to_netcdf(out_path)


if __name__ == "__main__":
    sys.stderr = open(snakemake.log[0], "w", buffering=1)
    align_local_profile_files_to_utc(
        local_profile_paths=list(snakemake.input.local_profiles),
        shape_timezones_path=snakemake.input.shape_timezones,
        weather_years=snakemake.params.weather_years,
        workers=snakemake.threads,
        out_path=snakemake.output[0],
    )
