"""Snakemake helper functions and utilities."""

CURL_ARGS = "--fail --silent --show-error --location --retry 5 --retry-delay 5 --retry-all-errors --continue-at -"


USE_PREBUILT = config["building_rasters"]["source"] == "prebuilt"
POPULATION_FILE = (
    f"<resources>/automatic/ghsl/pop_{config['population']['epoch']}_100.tif"
)
# The stable-data release range includes both endpoints.
ECUK_RELEASES = range(
    internal["resources"]["stable"]["ECUK_releases"][0],
    internal["resources"]["stable"]["ECUK_releases"][-1] + 1,
)


DEMAND_YEARS = list(
    range(config["demand_years"]["start"], config["demand_years"]["end"])
)
JRC_IDEES_VERSION = internal["resources"]["jrc"]["use_version"]
JRC_IDEES_VERSIONS = [str(i) for i in internal["resources"]["jrc"]["versions"]]
JRC_IDEES_SPATIAL_SCOPE = [
    country
    for country in internal["resources"]["jrc"]["spatial_scope"]
    if not (JRC_IDEES_VERSION > 2015 and country == "UK")
]
WEATHER_YEARS = list(
    range(config["weather_years"]["start"], config["weather_years"]["end"])
)
WEATHER_DEMAND_YEARS = dict(zip(WEATHER_YEARS, DEMAND_YEARS))
LOCAL_UNSCALED_HEAT_PROFILES = expand(
    "<resources>/automatic/shapes/{{shapes}}/hourly_unscaled_heat_demand_local/{weather_year}.nc",
    weather_year=WEATHER_YEARS,
)
JRC_SPATIAL_SCOPE = internal["resources"]["jrc"]["spatial_scope"]
SECTOR_TO_JRC_DATASET = {"services": "Tertiary", "residential": "Residential"}


def additional_config_validation() -> None:
    """Run additional validation that JSON schemas do not support."""
    if config["demand_years"]["start"] >= config["demand_years"]["end"]:
        raise ValueError(
            "Configuration error: demand_years.start must be less than "
            "demand_years.end. "
            "The end year is exclusive."
        )
    if config["weather_years"]["start"] >= config["weather_years"]["end"]:
        raise ValueError(
            "Configuration error: weather_years.start must be less than "
            "weather_years.end. "
            "The end year is exclusive."
        )
    if len(DEMAND_YEARS) != len(WEATHER_YEARS):
        raise ValueError(
            "Configuration error: weather year span must match demand year span. "
            f"Got demand years {DEMAND_YEARS} and weather years {WEATHER_YEARS}."
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


def _read_checkpoint_lines(path):
    """Read nonempty checkpoint lines with surrounding whitespace removed."""
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
        inputs.append(f"<resources>/automatic/baseline/che/{sector}_final.parquet")
    if "GBR" in countries:
        inputs.append(f"<resources>/automatic/baseline/ecuk/{sector}_final.parquet")
    return inputs


def _annual_energy_balance_proxy_population_inputs(wildcards):
    """Return population inputs when countries use annual energy balance proxies."""
    country_data = checkpoints.prepare_shape_country_scope.get(
        shapes=wildcards.shapes
    ).output.country_ids
    countries = set(_read_checkpoint_lines(country_data))
    proxy_countries = set(config["data_proxies"]["annual_energy_balance"])
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
        year for year in ECUK_RELEASES if year > config["demand_years"]["end"] - 1
    )
    return f"<resources>/automatic/GBR/ecuk-end-use-{release}.xlsx"


def read_building_plan(wildcards):
    """Load the building source plan from its JSON manifest."""
    import json

    path = checkpoints.prepare_building_sources.get(
        shapes=wildcards.shapes
    ).output.manifest
    with open(path) as stream:
        return json.load(stream)


def eubucco_download_inputs(wildcards):
    """Return EUBUCCO download paths required by the building source plan."""
    plan = read_building_plan(wildcards)
    regions = sorted(
        {
            nuts2
            for region in plan["regions"].values()
            for nuts2 in region["eubucco_nuts2_ids"]
            if "eubucco" in {region["residential_source"], region["commercial_source"]}
        }
    )
    return [
        str(rules.download_eubucco.output.table).format(region=region)
        for region in regions
    ]


def microsoft_download_rows(wildcards):
    """Return Microsoft index rows for planned quadkeys, sorted by quadkey and URL."""
    import csv

    plan = read_building_plan(wildcards)
    quadkeys = {
        key
        for region in plan["regions"].values()
        for key in region["microsoft_quadkeys"]
    }
    with open(rules.download_microsoft_index.output.table, newline="") as stream:
        rows = [row for row in csv.DictReader(stream) if row["QuadKey"] in quadkeys]
    return sorted(rows, key=lambda row: (row["QuadKey"], row["Url"]))


def microsoft_download_inputs(wildcards):
    """Return Microsoft download paths with numbered parts for each quadkey."""
    counts = {}
    downloads = []
    for row in microsoft_download_rows(wildcards):
        part = counts.get(row["QuadKey"], 0)
        counts[row["QuadKey"]] = part + 1
        downloads.append(
            str(rules.download_microsoft.output.table).format(
                quadkey=row["QuadKey"], part=f"{part:05d}"
            )
        )
    return downloads


def selected_eubucco_input(wildcards):
    """Return processed EUBUCCO data or an empty table when no region needs it."""
    outputs = checkpoints.prepare_building_sources.get(shapes=wildcards.shapes).output
    plan = read_building_plan(wildcards)
    if any(
        region["eubucco_nuts2_ids"]
        and "eubucco" in {region["residential_source"], region["commercial_source"]}
        for region in plan["regions"].values()
    ):
        return str(rules.process_eubucco.output.table).format(shapes=wildcards.shapes)
    return outputs.empty_eubucco


def selected_microsoft_input(wildcards):
    """Return processed Microsoft data or an empty table when no quadkeys are planned."""
    outputs = checkpoints.prepare_building_sources.get(shapes=wildcards.shapes).output
    plan = read_building_plan(wildcards)
    if any(region["microsoft_quadkeys"] for region in plan["regions"].values()):
        return str(rules.process_microsoft.output.table).format(shapes=wildcards.shapes)
    return outputs.empty_microsoft


def selected_microsoft_statistics_input(wildcards):
    """Return Microsoft statistics or an empty table when no quadkeys are planned."""
    outputs = checkpoints.prepare_building_sources.get(shapes=wildcards.shapes).output
    plan = read_building_plan(wildcards)
    if any(region["microsoft_quadkeys"] for region in plan["regions"].values()):
        return str(rules.process_microsoft.output.statistics).format(
            shapes=wildcards.shapes
        )
    return outputs.empty_microsoft_statistics


def read_floor_area_batch_plan(wildcards):
    """Load the floor area batch plan from its checkpoint JSON manifest."""
    import json

    path = floor_area_batch_plan_input(wildcards)
    with open(path) as stream:
        return json.load(stream)


def floor_area_batch_plan_input(wildcards):
    """Resolve source planning before the dependent floor-area batch checkpoint."""
    # Discover nested checkpoints in order, including when manifests already exist.
    checkpoints.prepare_building_sources.get(shapes=wildcards.shapes).output
    return checkpoints.prepare_floor_area_batches.get(
        shapes=wildcards.shapes
    ).output.manifest


def floor_area_batch_inputs(wildcards):
    """Return floor area partial output paths for all planned batches."""
    plan = read_floor_area_batch_plan(wildcards)
    return [
        str(rules.create_floor_area_batch.output.partials).format(
            shapes=wildcards.shapes, batch=batch
        )
        for batch in plan["batches"]
    ]


def space_heat_weight_batch_inputs(wildcards):
    """Return space heat weight partial output paths for all planned batches."""
    plan = read_floor_area_batch_plan(wildcards)
    return [
        str(rules.create_space_heat_weight_batch.output.partials).format(
            shapes=wildcards.shapes, batch=batch
        )
        for batch in plan["batches"]
    ]


def selected_microsoft_totals_input(wildcards):
    """Return Microsoft totals or an empty table when no quadkeys are planned."""
    outputs = checkpoints.prepare_building_sources.get(shapes=wildcards.shapes).output
    plan = read_building_plan(wildcards)
    if any(region["microsoft_quadkeys"] for region in plan["regions"].values()):
        return str(rules.process_microsoft.output.totals).format(
            shapes=wildcards.shapes
        )
    return outputs.empty_microsoft_totals
