"""Rules to download automatic resource files for heat demand."""

CURL_ARGS = "--fail --silent --show-error --location --retry 5 --retry-delay 5 --retry-all-errors --continue-at -"


rule download_when2heat_params:
    output:
        "<resources>/automatic/when2heat/{dataset}.csv",
    log:
        "<logs>/automatic/download_when2heat_params_{dataset}.log",
    wildcard_constraints:
        dataset="|".join(internal["resources"]["when2heat"]["datasets"]),
    conda:
        "../envs/shell.yaml"
    params:
        curl_args=CURL_ARGS,
        url=lambda wc: internal["resources"]["when2heat"]["url"].format(
            dataset=wc.dataset
        ),
    message:
        "Download When2Heat demand profile parameters for {wildcards.dataset}."
    shell:
        "curl {params.curl_args} --output {output:q} {params.url:q} 2> {log:q}"


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
        "curl {params.curl_args} --output {output:q} {params.url:q} 2> {log:q}"


rule download_raw_population:
    output:
        temp(f"<resources>/automatic/{GHSL_POPULATION['stem']}_V1_0.zip"),
    log:
        "<logs>/automatic/download_raw_population.log",
    conda:
        "../envs/shell.yaml"
    params:
        curl_args=CURL_ARGS,
        url=(
            internal["resources"]["automatic"]["population"]
            + f"/{GHSL_POPULATION['stem']}/V1-0/{GHSL_POPULATION['stem']}_V1_0.zip"
        ),
    message:
        f"Download GHSL gridded population data for {GHSL_POPULATION['epoch']} at {GHSL_POPULATION['resolution']} m."
    shell:
        "curl {params.curl_args} --output {output:q} {params.url:q} 2> {log:q}"


rule unzip_raw_population:
    input:
        rules.download_raw_population.output,
    output:
        f"<resources>/automatic/{GHSL_POPULATION['stem']}_V1_0.tif",
    log:
        "<logs>/automatic/unzip_raw_population.log",
    conda:
        "../envs/shell.yaml"
    params:
        member=f"{GHSL_POPULATION['stem']}_V1_0.tif",
    message:
        "Extract gridded population data."
    script:
        "../scripts/extract_zip_member.py"


rule download_jrc_idees:
    output:
        temp("<resources>/automatic/jrc-idees/{country_code}_v{version}.zip"),
    log:
        "<logs>/automatic/download_jrc_idees_{country_code}_v{version}.log",
    wildcard_constraints:
        country_code="|".join(internal["resources"]["jrc"]["spatial_scope"]),
        version="|".join(JRC_IDEES_VERSIONS),
    conda:
        "../envs/shell.yaml"
    params:
        curl_args=CURL_ARGS,
        dataset_url=lambda wc: get_jrc_url(wc.country_code, wc.version),
    message:
        "Download JRC-IDEES data for {wildcards.country_code}-{wildcards.version}."
    shell:
        "curl {params.curl_args} --output {output:q} {params.dataset_url:q} 2> {log:q}"


rule unzip_jrc_idees:
    input:
        rules.download_jrc_idees.output[0],
    output:
        "<resources>/automatic/jrc-idees/{version}/tertiary_{country_code}.xlsx",
    log:
        "<logs>/automatic/unzip_jrc_idees_{country_code}_v{version}.log",
    wildcard_constraints:
        country_code="|".join(internal["resources"]["jrc"]["spatial_scope"]),
        version="|".join(JRC_IDEES_VERSIONS),
    threads: 1
    params:
        internal_paths=lambda wc: f"JRC-IDEES-{wc.version}_Tertiary_{wc.country_code}.xlsx",
    message:
        "Unzip JRC IDEES Tertiary data for {wildcards.country_code}-{wildcards.version}."
    wrapper:
        "v9.8.0/utils/libarchive/extract"


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
        "curl {params.curl_args} --output {output:q} {params.url:q} 2> {log:q}"


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
        "curl {params.curl_args} --output {output:q} {params.url:q} 2> {log:q}"


rule download_GBR_end_use:
    output:
        "<resources>/automatic/GBR/end-use.zip",
    log:
        "<logs>/automatic/download_GBR_end_use.log",
    conda:
        "../envs/shell.yaml"
    params:
        curl_args=CURL_ARGS,
        url=internal["resources"]["automatic"]["GBR"]["end_use_zip"],
    message:
        "Download ECUK end-use data tables."
    shell:
        "curl {params.curl_args} --output {output:q} {params.url:q} 2> {log:q}"


rule unzip_GBR_end_use:
    input:
        rules.download_GBR_end_use.output[0],
    output:
        "<resources>/automatic/GBR/ecuk-end-use-{ecuk_year}.xlsx",
    log:
        "<logs>/unzip_GBR_end_use_{ecuk_year}.log",
    wildcard_constraints:
        ecuk_year="202[0-5]",
    threads: 1
    params:
        internal_paths=lambda wc: f"GBR_{wc.ecuk_year}_End_Use_tables.xlsx",
    message:
        "Unzip ECUK end-use data table for {wildcards.ecuk_year}."
    wrapper:
        "v9.8.0/utils/libarchive/extract"
