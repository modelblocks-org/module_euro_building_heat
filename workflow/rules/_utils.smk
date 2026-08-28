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
LOCAL_UNSCALED_HEAT_PROFILES = expand(
    "<resources>/automatic/shapes/{{shapes}}/hourly_unscaled_heat_demand_local/{weather_year}.nc",
    weather_year=WEATHER_YEARS,
)
JRC_SPATIAL_SCOPE = internal["resources"]["jrc"]["spatial_scope"]
SECTOR_TO_JRC_DATASET = {"services": "Tertiary", "residential": "Residential"}


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


def _read_checkpoint_lines(path):
    with open(path) as f:
        return [line.strip() for line in f if line.strip()]


def _official_final_demand_inputs(wildcards, sector):
    """Return country statistics that provide absolute final demand."""
    country_data = checkpoints.prepare_shape_country_scope.get(
        shapes=wildcards.shapes
    ).output.source_country_ids
    countries = set(_read_checkpoint_lines(country_data))
    inputs = []
    if "CHE" in countries:
        inputs.append(f"<resources>/automatic/baseline/che/{sector}_final.csv")
    if "GBR" in countries:
        inputs.append(f"<resources>/automatic/baseline/ecuk/{sector}_final.csv")
    return inputs


def _annual_energy_balance_proxy_population_inputs(wildcards):
    country_data = checkpoints.prepare_shape_country_scope.get(
        shapes=wildcards.shapes
    ).output.country_ids
    countries = set(_read_checkpoint_lines(country_data))
    proxy_countries = set(
        config.get("data_proxies", {}).get("annual_energy_balance", {})
    )
    if countries & proxy_countries:
        return expand(
            "<resources>/automatic/shapes/{shapes}/population.nc",
            shapes=wildcards.shapes,
        )
    return []


def _get_jrc_baseline_files(sector: str) -> list[str]:
    """Get all the files needed to construct a JRC-IDEES baseline."""
    jrc_version = internal["resources"]["jrc"]["use_version"]
    countries = JRC_SPATIAL_SCOPE
    dataset = SECTOR_TO_JRC_DATASET[sector]
    uk_missing = jrc_version > 2015

    file = "<resources>/automatic/jrc-idees/{version}/{dataset}_{country}.xlsx"
    requested_files = [
        file.format(version=jrc_version, country=country, dataset=dataset)
        for country in countries
        if not (country == "UK" and uk_missing)
    ]

    if uk_missing:
        requested_files.append(file.format(version=2015, country="UK", dataset=dataset))

    return requested_files


def _get_ecuk_baseline_file() -> str:
    """Select the first ECUK release covering the configured model period."""
    release = min(
        year
        for year in get_supported_ecuk_releases()
        if year > config["years"]["end"] - 1
    )
    return f"<resources>/automatic/GBR/ecuk-end-use-{release}.xlsx"
