"""Rules to download automatic resource files for heat demand."""


rule download_when2heat_params:
    output:
        directory("<resources>/automatic/when2heat"),
    log:
        "<logs>/automatic/download_when2heat_params.log",
    conda:
        "../envs/shell.yaml"
    params:
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
        "mkdir -p {output} && curl -f -sSLo '{output}/#1' '{params.url}' 2> {log}"


rule download_gridded_weather_data:
    output:
        "<resources>/automatic/gridded-weather/{data_var}.nc",
    log:
        "<logs>/automatic/download_gridded_weather_data_{data_var}.log",
    wildcard_constraints:
        data_var="grid|temperature|wind10m",
    conda:
        "../envs/shell.yaml"
    params:
        dataset_url=internal["resources"]["automatic"]["gridded_weather_data"],
    message:
        "Download gridded {wildcards.data_var} data."
    shell:
        "curl -f -sSLo {output} '{params.dataset_url}/files/{wildcards.data_var}.nc' 2> {log}"


rule download_raw_population:
    output:
        temp("<resources>/automatic/raw-population-data.zip"),
    log:
        "<logs>/automatic/download_raw_population.log",
    conda:
        "../envs/shell.yaml"
    params:
        url=internal["resources"]["automatic"]["population"],
    message:
        "Download gridded population data."
    shell:
        "curl -f -sSLo {output} '{params.url}' 2> {log}"


rule unzip_raw_population:
    input:
        rules.download_raw_population.output,
    output:
        "<resources>/automatic/JRC_1K_POP_2018.tif",
    log:
        "<logs>/automatic/unzip_raw_population.log",
    conda:
        "../envs/shell.yaml"
    message:
        "Extract gridded population data."
    shell:
        "unzip -p '{input}' 'JRC_1K_POP_2018.tif' > '{output}' 2> {log}"


JRC_IDEES_SPATIAL_SCOPE = internal["resources"]["automatic"]["jrc_idees_spatial_scope"]


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
        dataset_url=internal["resources"]["automatic"]["jrc_idees"],
    message:
        "Download JRC-IDEES data for {wildcards.country_code}."
    shell:
        "curl -f -sSLo {output} '{params.dataset_url}/JRC-IDEES-2015_All_xlsx_{wildcards.country_code}.zip' 2> {log}"


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
    message:
        "Extract JRC-IDEES tertiary sector data for {wildcards.country_code}."
    shell:
        "unzip -p {input.country_data} JRC-IDEES-2015_Tertiary_{wildcards.country_code}.xlsx > {output} 2> {log}"


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
        url=lambda wc: internal["resources"]["automatic"]["eurostat"][wc.dataset],
    message:
        "Download {wildcards.dataset} Eurostat data."
    shell:
        "curl -f -sSLo {output} {params.url} 2> {log}"


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
        url=lambda wc: internal["resources"]["automatic"]["CHE"][wc.dataset],
    message:
        "Download {wildcards.dataset} Swiss energy statistics."
    shell:
        "curl -f -sSLo {output} {params.url} 2> {log}"
