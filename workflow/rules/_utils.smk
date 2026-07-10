"""Snakemake helper functions and utilities."""


# Helpers
def _get_year_range(group: str) -> list[int]:
    """Get ordered year range (lower->higher)."""
    if group not in ["years", "weather"]:
        raise ValueError(f"Invalid year range requested: '{group}''.")
    return list(range(config[group]["start"], config[group]["end"]))


# Globals
MODEL_YEARS = _get_year_range("years")
JRC_IDEES_VERSION = internal["resources"]["jrc"]["use_version"]
JRC_IDEES_VERSIONS = [str(i) for i in internal["resources"]["jrc"]["versions"]]
JRC_IDEES_SPATIAL_SCOPE = [
    country
    for country in internal["resources"]["jrc"]["spatial_scope"]
    if not (JRC_IDEES_VERSION > 2015 and country == "UK")
]
WEATHER_YEARS = _get_year_range("weather")
WEATHER_MODEL_YEARS = dict(zip(WEATHER_YEARS, MODEL_YEARS))


# Helper functions
def additional_config_validation() -> None:
    """Run additional validation that JSON schemas do not support."""
    if config["years"]["start"] >= config["years"]["end"]:
        raise ValueError(
            "Configuration error: years.start must be less than years.end. "
            "The end year is exclusive."
        )
    if config["weather"]["start"] >= config["weather"]["end"]:
        raise ValueError(
            "Configuration error: weather.start must be less than weather.end. "
            "The end year is exclusive."
        )
    if len(MODEL_YEARS) != len(WEATHER_YEARS):
        raise ValueError(
            "Configuration error: weather year span must match model year span. "
            f"Got model years {MODEL_YEARS} and weather years {WEATHER_YEARS}."
        )


def get_jrc_url(country: str, version: int | str) -> str:
    """Helper for solving JRC-IDEES url inconsistencies."""
    if isinstance(version, str):
        version = int(version)
    if (
        country not in internal["resources"]["jrc"]["spatial_scope"]
        or version not in internal["resources"]["jrc"]["versions"]
        or (version > 2015 and country == "UK")
    ):
        raise ValueError(f"JRC-IDEES has no data for {country}-{version}.")

    folder = f"JRC-IDEES-{version}_v1"
    if version == 2015:
        file = f"JRC-IDEES-{version}_All_xlsx_{country}.zip"
    else:
        file = f"JRC-IDEES-{version}_{country}.zip"
    return internal["resources"]["jrc"]["url"].format(folder=folder, file=file)


def get_configured_population_file() -> str:
    """Helper to obtain the GHSL population file from the configuration."""
    epoch = min(
        internal["resources"]["ghsl"]["epochs"],
        key=lambda year: abs(year - config["years"]["start"]),
    )
    resolution = config["population"]["resolution"]
    return f"<resources>/automatic/ghsl/pop_{epoch}_{resolution}.tif"


def get_supported_ecuk_releases() -> list[int]:
    """Helper for supported ECUK releases in the stable repo."""
    release_range = internal["resources"]["stable"]["ECUK_releases"]
    # the internal range is inclusive, so extend by one
    return list(range(release_range[0], release_range[-1] + 1))


def get_era5_heat_files(shapes: str) -> list[str]:
    """Return monthly ERA5 files: previous December, weather years, following January."""
    raw_dir = f"<resources>/automatic/shapes/{shapes}/era5"
    months = (
        [(WEATHER_YEARS[0] - 1, 12)]
        + [(year, month) for year in WEATHER_YEARS for month in range(1, 13)]
        + [(WEATHER_YEARS[-1] + 1, 1)]
    )
    return [f"{raw_dir}/heat_{year:04d}_{month:02d}.nc" for year, month in months]


# Checkpoint helpers
def checkpoint_ecuk_end_use_input(wildcards) -> list[str]:
    country_data = checkpoints.prepare_shape_country_scope.get(
        shapes=wildcards.shapes
    ).output.source_country_ids
    ecuk_file = []
    if "GBR" in _read_checkpoint_lines(country_data):
        supported_releases = get_supported_ecuk_releases()
        ecuk_year = min(
            year for year in supported_releases if year > config["years"]["end"] - 1
        )
        ecuk_file = [f"<resources>/automatic/GBR/ecuk-end-use-{ecuk_year}.xlsx"]
    return ecuk_file
