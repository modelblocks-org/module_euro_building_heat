JRC_SPATIAL_SCOPE = internal["resources"]["jrc"]["spatial_scope"]

SECTOR_TO_JRC_DATASET = {"services": "Tertiary", "residential": "Residential"}


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


rule baseline_jrc_idees_sector:
    input:
        jrc_files=lambda wc: _get_jrc_baseline_files(wc.sector),
    output:
        final="<resources>/automatic/baseline/jrc_idees/{sector}_final.csv",
        useful="<resources>/automatic/baseline/jrc_idees/{sector}_useful.csv",
        plot="<resources>/automatic/baseline/jrc_idees/{sector}.pdf",
    log:
        "<logs>/baseline/baseline_jrc_idees_{sector}.log",
    conda:
        "../envs/module.yaml"
    params:
        countries=JRC_SPATIAL_SCOPE,
    message:
        "Create JRC-IDEES baseline for {wildcards.sector}."
    script:
        "../scripts/baseline_jrc_idees_sector.py"


rule baseline_che_final_demand:
    input:
        raw_stats="<resources>/automatic/stable/CHE_energy_consumption_households.xlsx",
        parser=workflow.source_path("../scripts/_che.py"),
    output:
        residential="<resources>/automatic/baseline/che/residential_final.csv",
        services="<resources>/automatic/baseline/che/services_final.csv",
        residential_plot="<resources>/automatic/baseline/che/residential.pdf",
        services_plot="<resources>/automatic/baseline/che/services.pdf",
    log:
        "<logs>/baseline/baseline_che.log",
    conda:
        "../envs/module.yaml"
    message:
        "Create CHE baseline."
    script:
        "../scripts/baseline_che_final_demand.py"


rule baseline_ecuk_final_demand:
    input:
        raw_stats=_get_ecuk_baseline_file(),
        parser=workflow.source_path("../scripts/_ecuk.py"),
    output:
        residential="<resources>/automatic/baseline/ecuk/residential_final.csv",
        services="<resources>/automatic/baseline/ecuk/services_final.csv",
        residential_plot="<resources>/automatic/baseline/ecuk/residential.pdf",
        services_plot="<resources>/automatic/baseline/ecuk/services.pdf",
    log:
        "<logs>/baseline/baseline_ecuk.log",
    conda:
        "../envs/module.yaml"
    message:
        "Create ECUK baseline."
    script:
        "../scripts/baseline_ecuk_final_demand.py"
