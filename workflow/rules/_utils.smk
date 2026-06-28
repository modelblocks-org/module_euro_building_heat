"""Snakemake helper functions and utilities."""


# Helpers
def _get_year_range(group: str) -> list[int]:
    """Get ordered year range (lower->higher)."""
    if group not in ["years", "weather"]:
        raise ValueError(f"Invalid year range requested: '{group}''.")
    return list(range(config[group]["start"], config[group]["end"]))


def _get_population_epoch() -> dict[str, str]:
    """Get a valid epoch configuration"""
    population_epoch = min(
        internal["resources"]["automatic"]["population_epochs"],
        key=lambda year: abs(year - config["years"]["start"]),
    )
    population_resolution = config["population"]["resolution"]
    return {
        "epoch": population_epoch,
        "resolution": population_resolution,
        "stem": f"GHS_POP_E{population_epoch}_GLOBE_R2023A_54009_{population_resolution}",
    }


# Globals
GHSL_POPULATION = _get_population_epoch()
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
