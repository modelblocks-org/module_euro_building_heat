"""Snakemake helper functions and utilities."""

CURL_ARGS = "--fail --silent --show-error --location --retry 5 --retry-delay 5 --retry-all-errors --continue-at -"


def _get_year_range(group: str) -> list[int]:
    """Get ordered year range (lower->higher)."""
    if group not in ["demand_years", "weather_years"]:
        raise ValueError(f"Invalid year range requested: '{group}''.")
    return list(range(config[group]["start"], config[group]["end"]))


DEMAND_YEARS = _get_year_range("demand_years")
JRC_IDEES_VERSION = internal["resources"]["jrc"]["use_version"]
JRC_IDEES_VERSIONS = [str(i) for i in internal["resources"]["jrc"]["versions"]]
JRC_IDEES_SPATIAL_SCOPE = [
    country
    for country in internal["resources"]["jrc"]["spatial_scope"]
    if not (JRC_IDEES_VERSION > 2015 and country == "UK")
]
WEATHER_YEARS = _get_year_range("weather_years")
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


def get_configured_population_file() -> str:
    """Helper to obtain the GHSL population file from the configuration."""
    epoch = config["population"]["epoch"]
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
        inputs.append(f"<resources>/automatic/baseline/che/{sector}_final.parquet")
    if "GBR" in countries:
        inputs.append(f"<resources>/automatic/baseline/ecuk/{sector}_final.parquet")
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
        if year > config["demand_years"]["end"] - 1
    )
    return f"<resources>/automatic/GBR/ecuk-end-use-{release}.xlsx"


def building_source_outputs(wildcards):
    return checkpoints.prepare_building_sources.get(shapes=wildcards.shapes).output


def building_plan_input(wildcards):
    return building_source_outputs(wildcards).manifest


def eubucco_stats_input(wildcards):
    return rules.download_eubucco_stats.output.table


def read_building_plan(wildcards):
    import json

    path = building_source_outputs(wildcards).manifest
    with open(path) as stream:
        return json.load(stream)


def eubucco_download_inputs(wildcards):
    plan = read_building_plan(wildcards)
    regions = (
        ["eubucco_lat_lon"]
        if plan["eubucco_source"] == "lightweight"
        else sorted(
            {
                nuts2
                for region in plan["regions"].values()
                for nuts2 in region["eubucco_nuts2_ids"]
                if "eubucco"
                in {region["residential_source"], region["commercial_source"]}
            }
        )
    )
    return [
        str(rules.download_eubucco.output.table).format(region=region)
        for region in regions
    ]


def eubucco_download_url(wildcards):
    return internal["resources"]["automatic"][
        f"eubucco_{config['buildings_eubucco']['source']}"
    ].format(
        version=config["buildings_eubucco"]["version"],
        nuts2=wildcards.region,
    )


def microsoft_download_rows(wildcards):
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
    outputs = building_source_outputs(wildcards)
    plan = read_building_plan(wildcards)
    if any(
        region["eubucco_nuts2_ids"]
        and "eubucco" in {region["residential_source"], region["commercial_source"]}
        for region in plan["regions"].values()
    ):
        return str(rules.process_eubucco.output.table).format(shapes=wildcards.shapes)
    return outputs.empty_eubucco


def selected_microsoft_input(wildcards):
    outputs = building_source_outputs(wildcards)
    plan = read_building_plan(wildcards)
    if any(region["microsoft_quadkeys"] for region in plan["regions"].values()):
        return str(rules.process_microsoft.output.table).format(shapes=wildcards.shapes)
    return outputs.empty_microsoft


def selected_microsoft_statistics_input(wildcards):
    outputs = building_source_outputs(wildcards)
    plan = read_building_plan(wildcards)
    if any(region["microsoft_quadkeys"] for region in plan["regions"].values()):
        return str(rules.process_microsoft.output.statistics).format(
            shapes=wildcards.shapes
        )
    return outputs.empty_microsoft_statistics


def read_floor_area_batch_plan(wildcards):
    import json

    path = checkpoints.prepare_floor_area_batches.get(
        shapes=wildcards.shapes
    ).output.manifest
    with open(path) as stream:
        return json.load(stream)


def floor_area_batch_plan_input(wildcards):
    return checkpoints.prepare_floor_area_batches.get(
        shapes=wildcards.shapes
    ).output.manifest


def floor_area_batch_inputs(wildcards):
    plan = read_floor_area_batch_plan(wildcards)
    return [
        str(rules.create_floor_area_batch.output.partials).format(
            shapes=wildcards.shapes, batch=batch
        )
        for batch in plan["batches"]
    ]


def space_heat_weight_batch_inputs(wildcards):
    plan = read_floor_area_batch_plan(wildcards)
    return [
        str(rules.create_space_heat_weight_batch.output.partials).format(
            shapes=wildcards.shapes, batch=batch
        )
        for batch in plan["batches"]
    ]


def selected_microsoft_totals_input(wildcards):
    outputs = building_source_outputs(wildcards)
    plan = read_building_plan(wildcards)
    if any(region["microsoft_quadkeys"] for region in plan["regions"].values()):
        return str(rules.process_microsoft.output.totals).format(
            shapes=wildcards.shapes
        )
    return outputs.empty_microsoft_totals


def raster_settings():
    return config["raster"]


def intermediate_raster_settings():
    return {**raster_settings(), "dtype": config["processing"]["intermediate_dtype"]}


def report_figure_inputs(wildcards):
    """Include heat, building and applicable baseline figures in one report DAG."""
    root = "<resources>/automatic/shapes/{shapes}/plots"
    figures = [root + "/annual_heat_demand.png"]
    figures += [
        root + f"/{dataset}_timeseries.pdf"
        for dataset in ("heat_demand", "heat_pump_cop", "heat_pump_electricity_demand")
    ]
    figures += [
        root + f"/{dataset}.png"
        for dataset in (
            "building_count",
            "residential_space_heat_weight",
            "commercial_space_heat_weight",
        )
    ]
    for sector in ["residential", "services"]:
        figures.append(f"<resources>/automatic/baseline/jrc_idees/{sector}.pdf")
        figures += [
            path.removesuffix("_final.parquet") + ".pdf"
            for path in _official_final_demand_inputs(wildcards, sector)
        ]
    return figures
