"""UTC index and Parquet attribute helpers for hourly module outputs."""

from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

OUTPUT_TIMEZONE = "UTC"
LOCAL_TIME_BASIS = "local_civil_clock"
SHAPE_TIMEZONES_METADATA_KEY = "shape_timezones"


def write_parquet_with_attributes(
    data: pd.DataFrame, path: str | Path, attributes: dict[str, object]
) -> None:
    """Write a Pandas table after merging attributes atomically."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")

    data.attrs.update(attributes)
    data.to_parquet(temporary_path, index=True)
    temporary_path.replace(path)


def read_shape_timezones(path: str | Path) -> pd.Series:
    """Read and validate the internal shape-to-IANA-timezone mapping."""
    mapping = pd.read_parquet(path)
    mapping = mapping.loc[:, ["shape_id", "timezone"]].copy()
    mapping["timezone"] = mapping["timezone"].astype(str)

    for timezone in sorted(mapping["timezone"].unique()):
        ZoneInfo(timezone)

    return mapping.set_index("shape_id")["timezone"].rename("timezone")


def utc_aware_hourly_frame(data: pd.DataFrame) -> pd.DataFrame:
    """Return a copy with a continuous, unique, UTC-aware hourly index."""
    result = data.copy()
    index = pd.DatetimeIndex(result.index)
    if index.tz is None:
        index = index.tz_localize(OUTPUT_TIMEZONE)
    else:
        index = index.tz_convert(OUTPUT_TIMEZONE)
    index = index.rename("timesteps")
    if len(index) > 1:
        expected = pd.date_range(index[0], index[-1], freq="h", tz=OUTPUT_TIMEZONE)
        if not index.equals(expected.rename("timesteps")):
            raise ValueError("Hourly output index is not a continuous UTC timeline.")

    result.index = index
    return result


def write_hourly_parquet(
    data: pd.DataFrame,
    path: str | Path,
    shape_timezones_path: str | Path,
    *,
    units: str,
) -> pd.DataFrame:
    """Write a UTC-aware hourly table with timezone and unit metadata."""
    result = utc_aware_hourly_frame(data)
    result.attrs["units"] = units
    shape_timezones = read_shape_timezones(shape_timezones_path)

    output_ids = pd.Index(result.columns)
    selected_timezones = shape_timezones.reindex(output_ids)

    attributes = {
        "output_timezone": OUTPUT_TIMEZONE,
        SHAPE_TIMEZONES_METADATA_KEY: selected_timezones.to_dict(),
    }
    write_parquet_with_attributes(result, path, attributes)
    return result
