"""UTC index and Parquet metadata helpers for hourly module outputs."""

import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

OUTPUT_TIMEZONE = "UTC"
SHAPE_TIMEZONES_METADATA_KEY = "shape_timezones"
BOUNDARY_METADATA_KEYS = (
    "timezone_boundary_source",
    "timezone_boundary_release",
    "timezone_boundary_sha256",
    "timezone_boundary_attribution",
)


def boundary_metadata(
    source: str, release: str, sha256: str, attribution: str
) -> dict[str, str]:
    """Build the stable timezone-boundary provenance metadata."""
    return {
        "timezone_boundary_source": source,
        "timezone_boundary_release": release,
        "timezone_boundary_sha256": sha256,
        "timezone_boundary_attribution": attribution,
    }


def parquet_metadata(path: str | Path) -> dict[str, str]:
    """Read UTF-8 schema metadata from a Parquet file."""
    raw_metadata = pq.read_schema(path).metadata or {}
    return {
        key.decode("utf-8"): value.decode("utf-8")
        for key, value in raw_metadata.items()
    }


def write_parquet_with_metadata(
    data: pd.DataFrame, path: str | Path, metadata: dict[str, str]
) -> None:
    """Write a Pandas table and merge custom values into Arrow schema metadata."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")

    table = pa.Table.from_pandas(data, preserve_index=True)
    schema_metadata = dict(table.schema.metadata or {})
    schema_metadata.update(
        {
            str(key).encode("utf-8"): str(value).encode("utf-8")
            for key, value in metadata.items()
        }
    )
    table = table.replace_schema_metadata(schema_metadata)
    pq.write_table(table, temporary_path)
    temporary_path.replace(path)


def read_shape_timezones(path: str | Path) -> pd.Series:
    """Read and validate the internal shape-to-IANA-timezone mapping."""
    mapping = pd.read_parquet(path)
    mapping = mapping.loc[:, ["shape_id", "timezone"]].copy()
    mapping["shape_id"] = mapping["shape_id"].astype(str)
    mapping["timezone"] = mapping["timezone"].astype(str)

    for timezone in sorted(mapping["timezone"].unique()):
        ZoneInfo(timezone)

    return mapping.set_index("shape_id")["timezone"].rename("timezone")


def timezone_boundary_metadata(path: str | Path) -> dict[str, str]:
    """Read required boundary provenance from the shape-timezone mapping."""
    metadata = parquet_metadata(path)
    return {key: metadata[key] for key in BOUNDARY_METADATA_KEYS}


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
    data: pd.DataFrame, path: str | Path, shape_timezones_path: str | Path
) -> pd.DataFrame:
    """Write a UTC-aware hourly table with timezone and source metadata."""
    result = utc_aware_hourly_frame(data)
    shape_timezones = read_shape_timezones(shape_timezones_path)

    output_ids = pd.Index(result.columns.astype(str))
    selected_timezones = shape_timezones.reindex(output_ids)

    metadata = timezone_boundary_metadata(shape_timezones_path)
    metadata.update(
        {
            "output_timezone": OUTPUT_TIMEZONE,
            SHAPE_TIMEZONES_METADATA_KEY: json.dumps(
                selected_timezones.to_dict(), sort_keys=True, separators=(",", ":")
            ),
        }
    )
    write_parquet_with_metadata(result, path, metadata)
    return result
