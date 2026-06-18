"""Rules to download automatic resource files for heat demand."""

CURL_ARGS = "--fail --silent --show-error --location --retry 5 --retry-delay 5 --retry-all-errors --continue-at -"


rule download_when2heat_params:
    output:
        directory("<resources>/automatic/when2heat"),
    log:
        "<logs>/automatic/download_when2heat_params.log",
    conda:
        "../envs/shell.yaml"
    params:
        curl_args=CURL_ARGS,
        url=lambda wildcards: internal["resources"]["automatic"][
            "when2heat_params"
        ].format(
            dataset="{"
            + ",".join(
                [
                    "daily_demand.csv",
                            "hourly_factors_COM.csv",
                            "hourly_factors_MFH.csv",
                            "hourly_factors_SFH.csv",
                        ]
                    )
            + "}"
        ),
    message:
        "Download When2Heat demand profile parameters."
    shell:
        "mkdir -p {output} && curl {params.curl_args} --output '{output}/#1' '{params.url}' 2> {log}"


rule download_gridded_weather_data:
    input:
        locations="<shapes>",
    output:
        raw_weather=directory(
            "<resources>/automatic/shapes/{shapes}/gridded-weather/raw"
        ),
    log:
        "<logs>/{shapes}/automatic/download_gridded_weather_data.log",
    conda:
        "../envs/shell.yaml"
    params:
        weather_years=WEATHER_YEARS,
        download_workers=config["weather"].get("download_workers", 2),
    message:
        "Download raw ERA5 gridded weather data for '{wildcards.shapes}'."
    script:
        "../scripts/download_gridded_weather_data.py"


rule download_heat_pump_characteristics:
    output:
        "<resources>/automatic/heat-pump-characteristics.nc",
    log:
        "<logs>/automatic/download_heat_pump_characteristics.log",
    conda:
        "../envs/shell.yaml"
    params:
        curl_args=CURL_ARGS,
        url=internal["resources"]["automatic"]["heat_pump_characteristics"],
    message:
        "Download manufacturer heat-pump characteristic data."
    shell:
        "curl {params.curl_args} --output {output}.tmp '{params.url}' 2> {log} && mv {output}.tmp {output}"


rule download_raw_population:
    output:
        temp(f"<resources>/automatic/{ghsl_population['stem']}_V1_0.zip"),
    log:
        "<logs>/automatic/download_raw_population.log",
    conda:
        "../envs/shell.yaml"
    params:
        curl_args=CURL_ARGS,
        url=(
            internal["resources"]["automatic"]["population"]
            + f"/{ghsl_population['stem']}/V1-0/{ghsl_population['stem']}_V1_0.zip"
        ),
    message:
        f"Download GHSL gridded population data for {ghsl_population['epoch']} at {ghsl_population['resolution']} m."
    shell:
        "curl {params.curl_args} --output {output}.tmp '{params.url}' 2> {log} && mv {output}.tmp {output}"


rule unzip_raw_population:
    input:
        rules.download_raw_population.output,
    output:
        f"<resources>/automatic/{ghsl_population['stem']}_V1_0.tif",
    log:
        "<logs>/automatic/unzip_raw_population.log",
    conda:
        "../envs/shell.yaml"
    params:
        member=f"{ghsl_population['stem']}_V1_0.tif",
    message:
        "Extract gridded population data."
    script:
        "../scripts/extract_zip_member.py"


JRC_IDEES_SPATIAL_SCOPE = internal["resources"]["automatic"]["jrc_idees_spatial_scope"]
JRC_IDEES_VERSION = internal["resources"]["automatic"]["jrc_idees_version"]


rule download_jrc_idees:
    output:
        temp("<resources>/automatic/jrc-idees/{country_code}.zip"),
    log:
        "<logs>/automatic/download_jrc_idees_{country_code}.log",
    wildcard_constraints:
        country_code="|".join(JRC_IDEES_SPATIAL_SCOPE),
    conda:
        "../envs/shell.yaml"
    params:
        curl_args=CURL_ARGS,
        dataset_url=internal["resources"]["automatic"]["jrc_idees"],
        version=JRC_IDEES_VERSION,
    message:
        "Download JRC-IDEES data for {wildcards.country_code}."
    shell:
        "curl {params.curl_args} --output {output}.tmp '{params.dataset_url}/JRC-IDEES-{params.version}_{wildcards.country_code}.zip' 2> {log} && mv {output}.tmp {output}"


rule unzip_jrc_idees:
    input:
        country_data="<resources>/automatic/jrc-idees/{country_code}.zip",
    output:
        "<resources>/automatic/jrc-idees/tertiary_{country_code}.xlsx",
    log:
        "<logs>/automatic/unzip_jrc_idees_{country_code}.log",
    wildcard_constraints:
        country_code="|".join(JRC_IDEES_SPATIAL_SCOPE),
    conda:
        "../envs/shell.yaml"
    params:
        member=lambda wildcards: (
            f"JRC-IDEES-{JRC_IDEES_VERSION}_Tertiary_{wildcards.country_code}.xlsx"
        ),
    message:
        "Extract JRC-IDEES tertiary sector data for {wildcards.country_code}."
    script:
        "../scripts/extract_zip_member.py"


rule download_uk_jrc_idees_2015:
    output:
        temp("<resources>/automatic/GBR/jrc-idees-2015_UK.zip"),
    log:
        "<logs>/automatic/download_uk_jrc_idees_2015.log",
    conda:
        "../envs/shell.yaml"
    params:
        curl_args=CURL_ARGS,
        dataset_url=internal["resources"]["automatic"]["GBR"]["jrc_idees_2015"],
    message:
        "Download legacy JRC-IDEES 2015 data for UK."
    shell:
        "curl {params.curl_args} --output {output}.tmp '{params.dataset_url}/JRC-IDEES-2015_All_xlsx_UK.zip' 2> {log} && mv {output}.tmp {output}"


rule unzip_uk_jrc_idees_2015:
    input:
        country_data=rules.download_uk_jrc_idees_2015.output,
    output:
        "<resources>/automatic/GBR/jrc-idees-2015_Tertiary_UK.xlsx",
    log:
        "<logs>/automatic/unzip_uk_jrc_idees_2015.log",
    conda:
        "../envs/shell.yaml"
    params:
        member="JRC-IDEES-2015_Tertiary_UK.xlsx",
    message:
        "Extract legacy JRC-IDEES 2015 tertiary sector data for UK."
    script:
        "../scripts/extract_zip_member.py"


rule download_eurostat_energy_data:
    output:
        "<resources>/automatic/eurostat/{dataset}.tsv.gz",
    log:
        "<logs>/automatic/download_eurostat_energy_data_{dataset}.log",
    wildcard_constraints:
        dataset="energy-balance|hh-end-use",
    conda:
        "../envs/shell.yaml"
    params:
        curl_args=CURL_ARGS,
        url=lambda wc: internal["resources"]["automatic"]["eurostat"][wc.dataset],
    message:
        "Download {wildcards.dataset} Eurostat data."
    shell:
        "curl {params.curl_args} --output {output}.tmp '{params.url}' 2> {log} && mv {output}.tmp {output}"


rule download_swiss_energy_data:
    output:
        "<resources>/automatic/CHE/{dataset}.xlsx",
    log:
        "<logs>/automatic/download_swiss_energy_data_{dataset}.log",
    wildcard_constraints:
        dataset="energy-balance|industry-energy-balance|end-use",
    conda:
        "../envs/shell.yaml"
    params:
        curl_args=CURL_ARGS,
        url=lambda wc: internal["resources"]["automatic"]["CHE"][wc.dataset],
    message:
        "Download {wildcards.dataset} Swiss energy statistics."
    shell:
        "curl {params.curl_args} --output {output}.tmp '{params.url}' 2> {log} && mv {output}.tmp {output}"


ECUK_END_USE_URLS = internal["resources"]["automatic"]["GBR"]["ecuk_end_use"]
ECUK_END_USE_YEAR = min(
    int(year) for year in ECUK_END_USE_URLS if int(year) > config["years"]["end"] - 1
)


rule download_ecuk_end_use:
    output:
        "<resources>/automatic/GBR/ecuk-end-use-{ecuk_year}.xlsx",
    log:
        "<logs>/automatic/download_ecuk_end_use_{ecuk_year}.log",
    wildcard_constraints:
        ecuk_year="|".join(str(year) for year in sorted(ECUK_END_USE_URLS)),
    conda:
        "../envs/shell.yaml"
    params:
        curl_args=CURL_ARGS,
        url=lambda wc: ECUK_END_USE_URLS[int(wc.ecuk_year)],
    message:
        "Download ECUK {wildcards.ecuk_year} end-use data tables."
    shell:
        "curl {params.curl_args} --output {output}.tmp '{params.url}' 2> {log} && mv {output}.tmp {output}"
