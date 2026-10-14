"""Rules to download automatic resource files for heat demand."""


rule download_when2heat_params:
    output:
        "<resources>/automatic/when2heat/{dataset}.csv",
    log:
        "<logs>/automatic/download_when2heat_params_{dataset}.log",
    wildcard_constraints:
        dataset="|".join(internal["resources"]["when2heat"]["datasets"]),
    localrule: True
    conda:
        "../envs/module.yaml"
    params:
        curl_args=CURL_ARGS,
        url=lambda wc: internal["resources"]["when2heat"]["url"].format(
            dataset=wc.dataset
        ),
    message:
        "Download When2Heat demand profile parameters for {wildcards.dataset}."
    shell:
        "curl {params.curl_args} --output {output:q} {params.url:q} 2> {log:q}"


rule download_timezone_boundaries:
    output:
        archive=ensure(
            "<resources>/automatic/timezone-boundary-builder/timezones.zip",
            sha256=internal["resources"]["timezone_boundaries"]["sha256"],
        ),
    log:
        "<logs>/automatic/download_timezone_boundaries.log",
    localrule: True
    conda:
        "../envs/module.yaml"
    params:
        curl_args=CURL_ARGS,
        url=internal["resources"]["timezone_boundaries"]["url"].format(
            version=internal["resources"]["timezone_boundaries"]["use_version"]
        ),
    message:
        "Download pinned IANA timezone boundaries."
    shell:
        "curl {params.curl_args} --output {output.archive:q} {params.url:q} 2> {log:q}"


rule extract_timezone_boundaries:
    input:
        archive=rules.download_timezone_boundaries.output.archive,
    output:
        geojson=temp(
            "<resources>/automatic/timezone-boundary-builder/timezones.geojson"
        ),
    log:
        "<logs>/automatic/extract_timezone_boundaries.log",
    threads: 1
    params:
        internal_paths=internal["resources"]["timezone_boundaries"]["file"],
    message:
        "Extract the pinned IANA timezone boundary GeoJSON."
    wrapper:
        "v9.8.0/utils/libarchive/extract"


rule download_stable_dataset:
    output:
        "<resources>/automatic/stable/{dataset}",
    log:
        "<logs>/automatic/download_stable_dataset_{dataset}.log",
    wildcard_constraints:
        dataset="|".join(internal["resources"]["stable"]["datasets"]),
    localrule: True
    conda:
        "../envs/module.yaml"
    params:
        curl_args=CURL_ARGS,
        url=lambda wc: internal["resources"]["stable"]["url"].format(dataset=wc.dataset),
    message:
        "Download stable dataset {wildcards.dataset}."
    shell:
        "curl {params.curl_args} --output {output:q} {params.url:q} 2> {log:q}"


rule download_era5_data:
    input:
        shapes="<resources>/automatic/shapes/{shapes}/land_shapes.parquet",
        edh_api="<edh_api>",
    output:
        era5="<resources>/automatic/shapes/{shapes}/era5/heat.nc",
    log:
        "<logs>/{shapes}/era5/download_era5_data.log",
    localrule: True
    conda:
        "../envs/module.yaml"
    params:
        weather_years=WEATHER_YEARS,
    message:
        "Download ERA5 weather data from Earth Data Hub for '{wildcards.shapes}'."
    script:
        "../scripts/download_era5_data.py"


rule download_raw_population:
    output:
        archive=update("<resources>/automatic/ghsl/pop_{ghsl_epoch}_100.zip"),
    log:
        "<logs>/automatic/download_raw_population_{ghsl_epoch}_100.log",
    conda:
        "../envs/module.yaml"
    params:
        url=lambda wc: internal["resources"]["ghsl"]["url"].format(
            stem=internal["resources"]["ghsl"]["stem"].format(epoch=wc.ghsl_epoch)
        ),
    message:
        "Download GHSL population data for epoch {wildcards.ghsl_epoch}."
    script:
        "../scripts/download.py"


rule download_jrc_idees:
    output:
        temp("<resources>/automatic/jrc-idees/{country_code}_v{version}.zip"),
    log:
        "<logs>/automatic/download_jrc_idees_{country_code}_v{version}.log",
    wildcard_constraints:
        country_code="|".join(internal["resources"]["jrc"]["spatial_scope"]),
        version="|".join(JRC_IDEES_VERSIONS),
    localrule: True
    conda:
        "../envs/module.yaml"
    params:
        curl_args=CURL_ARGS,
        dataset_url=lambda wc: get_jrc_url(wc.country_code, wc.version),
    message:
        "Download JRC-IDEES data for {wildcards.country_code}-{wildcards.version}."
    shell:
        "curl {params.curl_args} --output {output:q} {params.dataset_url:q} 2> {log:q}"


rule download_nuts3:
    output:
        geojson="<resources>/automatic/gisco/nuts3.geojson",
    log:
        "<logs>/download_nuts3.log",
    conda:
        "../envs/module.yaml"
    params:
        url=internal["resources"]["stable"]["url"].format(dataset="nuts3.geojson"),
    message:
        "Download NUTS3 region boundaries."
    script:
        "../scripts/download.py"


rule download_eurostat_floor_area:
    output:
        table="<resources>/automatic/eurostat/cens_21dwbnr_r3.tsv.gz",
    log:
        "<logs>/download_eurostat_floor_area.log",
    conda:
        "../envs/module.yaml"
    params:
        url=internal["resources"]["stable"]["url"].format(
            dataset="cens_21dwbnr_r3.tsv.gz"
        ),
    message:
        "Download Eurostat dwelling census data for floor-area estimates."
    script:
        "../scripts/download.py"


rule download_eubucco_nuts:
    output:
        table=update(
            "<resources>/automatic/eubucco-metadata/v0.2/NUTS-regions-2016.parquet"
        ),
    log:
        "<logs>/download_eubucco_nuts.log",
    conda:
        "../envs/module.yaml"
    params:
        url=internal["resources"]["automatic"]["eubucco_nuts"],
    message:
        "Download EUBUCCO NUTS region metadata."
    script:
        "../scripts/download.py"


rule download_eubucco_stats:
    output:
        table=update("<resources>/automatic/eubucco-metadata/v0.2/region-stats.parquet"),
    log:
        "<logs>/download_eubucco_stats.log",
    conda:
        "../envs/module.yaml"
    params:
        url=internal["resources"]["automatic"]["eubucco_stats"],
    message:
        "Download EUBUCCO regional building statistics."
    script:
        "../scripts/download.py"


rule download_eurostat_building_age:
    output:
        table="<resources>/automatic/eurostat/cens_21dwop_r3.tsv.gz",
    log:
        "<logs>/download_eurostat_building_age.log",
    conda:
        "../envs/module.yaml"
    params:
        url=internal["resources"]["stable"]["url"].format(
            dataset="cens_21dwop_r3.tsv.gz"
        ),
    message:
        "Download Eurostat building-age census data."
    script:
        "../scripts/download.py"


if USE_PREBUILT:

    rule download_precomputed_building_raster:
        output:
            raster="<resources>/automatic/precomputed_buildings/{dataset}.tif",
        log:
            "<logs>/download_precomputed_{dataset}.log",
        wildcard_constraints:
            dataset="building_count|residential_space_heat_weight|commercial_space_heat_weight",
        conda:
            "../envs/module.yaml"
        params:
            url=lambda wc: internal["resources"]["stable"]["url"].format(
                dataset=f"{wc.dataset}.tif"
            ),
        message:
            "Download precomputed {wildcards.dataset} raster."
        shell:
            "curl --fail --silent --show-error --location --retry 5 "
            "--output {output.raster:q}.part {params.url:q} 2> {log:q} && "
            "mv {output.raster:q}.part {output.raster:q}"
