"""Rules for cleaning and preparation of user/downloaded files."""


rule prepare_sfh_mfh_shares:
    input:
        census="<resources>/automatic/stable/cens_21dwbo_r2.tsv.gz",
        shapes="<resources>/automatic/shapes/{shapes}/land_shapes.parquet",
    output:
        "<resources>/automatic/shapes/{shapes}/sfh_mfh_shares.csv",
    log:
        "<logs>/{shapes}/prepare_sfh_mfh_shares.log",
    conda:
        "../envs/module.yaml"
    params:
        proxies=config.get("data_proxies", {}).get("sfh_mfh_shares", {}),
    message:
        "Calculate national single- and multi-family dwelling shares for '{wildcards.shapes}'."
    script:
        "../scripts/prepare_sfh_mfh_shares.py"


rule unzip_jrc_idees:
    input:
        rules.download_jrc_idees.output[0],
    output:
        "<resources>/automatic/jrc-idees/{version}/{dataset}_{country_code}.xlsx",
    log:
        "<logs>/automatic/unzip_jrc_idees_v{version}_{dataset}_{country_code}.log",
    wildcard_constraints:
        country_code="|".join(internal["resources"]["jrc"]["spatial_scope"]),
        version="|".join(JRC_IDEES_VERSIONS),
        dataset="|".join(["Tertiary", "Residential"]),
    threads: 1
    params:
        internal_paths=lambda wc: f"JRC-IDEES-{wc.version}_{wc.dataset}_{wc.country_code}.xlsx",
    message:
        "Unzip JRC IDEES Tertiary data for {wildcards.country_code}-{wildcards.version}."
    wrapper:
        "v9.8.0/utils/libarchive/extract"


rule unzip_ECUK_release:
    input:
        "<resources>/automatic/stable/GBR_End_Use_tables.zip",
    output:
        "<resources>/automatic/GBR/ecuk-end-use-{release}.xlsx",
    log:
        "<logs>/unzip_GBR_end_use_{release}.log",
    wildcard_constraints:
        release="|".join([str(i) for i in get_supported_ecuk_releases()]),
    threads: 1
    params:
        internal_paths=lambda wc: f"GBR_{wc.release}_End_Use_tables.xlsx",
    message:
        "Unzip ECUK end-use data table for {wildcards.release}."
    wrapper:
        "v9.8.0/utils/libarchive/extract"


rule unzip_raw_population:
    input:
        rules.download_raw_population.output[0],
    output:
        "<resources>/automatic/ghsl/pop_{ghsl_epoch}_{ghsl_resolution}.tif",
    log:
        "<logs>/automatic/unzip_raw_population_{ghsl_epoch}_{ghsl_resolution}.log",
    params:
        internal_paths=lambda wc: internal["resources"]["ghsl"]["stem"].format(
            epoch=wc.ghsl_epoch, resolution=wc.ghsl_resolution
        )
        + "_V1_0.tif",
    message:
        "Extract gridded population data."
    wrapper:
        "v9.8.0/utils/libarchive/extract"


rule prepare_shapes:
    input:
        shapes="<shapes>",
    output:
        "<resources>/automatic/shapes/{shapes}/land_shapes.parquet",
    log:
        "<logs>/{shapes}/prepare_shapes.log",
    conda:
        "../envs/module.yaml"
    params:
        dataset_scopes=internal["scope"]["datasets"],
        data_proxies=config.get("data_proxies", {}),
    message:
        "Filter non-land regions from '{wildcards.shapes}' shapes."
    script:
        "../scripts/prepare_shapes.py"


rule prepare_shape_timezones:
    input:
        shapes=rules.prepare_shapes.output[0],
        timezone_boundaries=rules.extract_timezone_boundaries.output.geojson,
    output:
        "<resources>/automatic/shapes/{shapes}/shape_timezones.parquet",
    log:
        "<logs>/{shapes}/prepare_shape_timezones.log",
    conda:
        "../envs/module.yaml"
    message:
        "Assign geometry-derived IANA timezones to '{wildcards.shapes}' shapes."
    script:
        "../scripts/prepare_shape_timezones.py"


rule clip_population:
    input:
        raster=get_configured_population_file(),
        like_vector=rules.prepare_shapes.output[0],
    output:
        path="<resources>/automatic/shapes/{shapes}/proxy.tif",
    log:
        "<logs>/{shapes}/clip_population.log",
    params:
        buffer=0,
    message:
        "Clipping proxy raster with '{wildcards.shapes}' shapes."
    wrapper:
        "v9.12.0/geo/rasterio/clip"
