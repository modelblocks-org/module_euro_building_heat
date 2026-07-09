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


rule download_stable_dataset:
    output:
        "<resources>/automatic/stable/{dataset}",
    log:
        "<logs>/automatic/download_stable_dataset_{dataset}.log",
    wildcard_constraints:
        dataset="|".join(internal["resources"]["stable"]["datasets"]),
    conda:
        "../envs/shell.yaml"
    params:
        curl_args=CURL_ARGS,
        url=lambda wc: internal["resources"]["stable"]["url"].format(dataset=wc.dataset),
    message:
        "Download stable dataset {wildcards.dataset}."
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


rule download_raw_population:
    output:
        temp("<resources>/automatic/ghsl/pop_{ghsl_epoch}_{ghsl_resolution}.zip"),
    log:
        "<logs>/automatic/download_raw_population_{ghsl_epoch}_{ghsl_resolution}.log",
    conda:
        "../envs/shell.yaml"
    params:
        curl_args=CURL_ARGS,
        url=lambda wc: internal["resources"]["ghsl"]["url"].format(
            stem=internal["resources"]["ghsl"]["stem"].format(
                epoch=wc.ghsl_epoch, resolution=wc.ghsl_resolution
            )
        ),
    message:
        "Download GHSL gridded population data for {wildcards.ghsl_epoch} at {wildcards.ghsl_resolution} m."
    shell:
        "curl {params.curl_args} --output {output:q} {params.url:q} 2> {log:q}"


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
