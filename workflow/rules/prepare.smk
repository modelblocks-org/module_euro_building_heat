"""Rules for cleaning and preparation of user/downloaded files."""


rule prepare_sfh_mfh_shares:
    input:
        census="<resources>/automatic/eurostat/cens_21dwbnr_r3.tsv.gz",
        shapes="<resources>/automatic/shapes/{shapes}/land_shapes.parquet",
    output:
        temp("<resources>/automatic/shapes/{shapes}/sfh_mfh_shares.parquet"),
    log:
        "<logs>/{shapes}/prepare_sfh_mfh_shares.log",
    conda:
        "../envs/module.yaml"
    params:
        proxies=config["data_proxies"]["sfh_mfh_shares"],
    message:
        "Calculate national single- and multi-family dwelling shares for '{wildcards.shapes}'."
    script:
        "../scripts/prepare_sfh_mfh_shares.py"


rule unzip_jrc_idees:
    input:
        rules.download_jrc_idees.output[0],
    output:
        temp("<resources>/automatic/jrc-idees/{version}/{dataset}_{country_code}.xlsx"),
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
        temp("<resources>/automatic/GBR/ecuk-end-use-{release}.xlsx"),
    log:
        "<logs>/unzip_GBR_end_use_{release}.log",
    wildcard_constraints:
        release="|".join([str(i) for i in ECUK_RELEASES]),
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
        "<resources>/automatic/ghsl/pop_{ghsl_epoch}_100.tif",
    log:
        "<logs>/automatic/unzip_raw_population_{ghsl_epoch}_100.log",
    params:
        internal_paths=lambda wc: internal["resources"]["ghsl"]["stem"].format(
            epoch=wc.ghsl_epoch
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
        shapes="<resources>/automatic/shapes/{shapes}/land_shapes.parquet",
        scope="<resources>/automatic/shapes/{shapes}/scope_equal_area.parquet",
    log:
        "<logs>/{shapes}/prepare_shapes.log",
    conda:
        "../envs/module.yaml"
    message:
        "Filter non-land regions from '{wildcards.shapes}' shapes."
    script:
        "../scripts/prepare_shapes.py"


rule prepare_shape_timezones:
    input:
        shapes=rules.prepare_shapes.output[0],
        timezone_boundaries=rules.extract_timezone_boundaries.output.geojson,
    output:
        temp("<resources>/automatic/shapes/{shapes}/shape_timezones.parquet"),
    log:
        "<logs>/{shapes}/prepare_shape_timezones.log",
    conda:
        "../envs/module.yaml"
    params:
        projected_crs=config["crs"]["projected"],
    message:
        "Assign geometry-derived IANA timezones to '{wildcards.shapes}' shapes."
    script:
        "../scripts/prepare_shape_timezones.py"


if not USE_PREBUILT:

    rule prepare_nuts3:
        input:
            shapes=rules.prepare_shapes.output.shapes,
            nuts3=rules.download_nuts3.output.geojson,
        output:
            regions="<resources>/automatic/{shapes}/nuts3.parquet",
        log:
            "<logs>/{shapes}/prepare_nuts3.log",
        conda:
            "../envs/module.yaml"
        params:
            step="nuts3",
            country_codes=internal["country_codes"],
        message:
            "Prepare NUTS3 regions for '{wildcards.shapes}' shapes."
        script:
            "../scripts/prepare_buildings.py"

    rule prepare_building_population:
        input:
            regions=rules.prepare_nuts3.output.regions,
            nuts3_source=rules.download_nuts3.output.geojson,
            stats=rules.download_eubucco_stats.output.table,
            population=POPULATION_FILE,
        output:
            table="<resources>/automatic/{shapes}/building_population.parquet",
        log:
            "<logs>/{shapes}/prepare_building_population.log",
        conda:
            "../envs/module.yaml"
        resources:
            mem_mb=4096,
        params:
            step="building_population",
            proxies=config["data_proxies"]["floor_area"],
            country_codes=internal["country_codes"],
        message:
            "Summarize population for building estimates in '{wildcards.shapes}' shapes."
        script:
            "../scripts/prepare_buildings.py"

    rule prepare_floor_area_totals:
        input:
            nuts3=rules.prepare_nuts3.output.regions,
            nuts3_source=rules.download_nuts3.output.geojson,
            census=rules.download_eurostat_floor_area.output.table,
            population_summaries=rules.prepare_building_population.output.table,
            eubucco_stats=rules.download_eubucco_stats.output.table,
            plan=lambda wc: checkpoints.prepare_building_sources.get(
                shapes=wc.shapes
            ).output.manifest,
            microsoft_totals=selected_microsoft_totals_input,
        output:
            table="<resources>/automatic/{shapes}/floor_area_totals.parquet",
        log:
            "<logs>/{shapes}/prepare_floor_area_totals.log",
        conda:
            "../envs/module.yaml"
        resources:
            mem_mb=4096,
        params:
            step="floor_area_totals",
            eurostat=config["building_rasters"]["eurostat"],
            eubucco=config["building_rasters"]["buildings_eubucco"],
            proxies=config["data_proxies"]["floor_area"],
            country_codes=internal["country_codes"],
        message:
            "Calculate regional floor-area totals for '{wildcards.shapes}' shapes."
        script:
            "../scripts/prepare_buildings.py"

    rule prepare_building_count_proxies:
        input:
            plan=lambda wc: checkpoints.prepare_building_sources.get(
                shapes=wc.shapes
            ).output.manifest,
            regions=rules.prepare_nuts3.output.regions,
            stats=rules.download_eubucco_stats.output.table,
            population_summaries="<resources>/automatic/{shapes}/building_population.parquet",
        output:
            table="<resources>/automatic/{shapes}/building_count_proxies.parquet",
        log:
            "<logs>/{shapes}/prepare_building_count_proxies.log",
        conda:
            "../envs/module.yaml"
        params:
            proxies=config["data_proxies"]["floor_area"],
            country_codes=internal["country_codes"],
        message:
            "Prepare building-count proxies for '{wildcards.shapes}' shapes."
        script:
            "../scripts/prepare_building_count_proxies.py"

    rule prepare_nuts3_building_age:
        input:
            nuts3=rules.prepare_nuts3.output.regions,
            floor_area=rules.prepare_floor_area_totals.output.table,
            census=rules.download_eurostat_building_age.output.table,
        output:
            table="<resources>/automatic/{shapes}/nuts3_building_age.parquet",
        log:
            "<logs>/{shapes}/prepare_nuts3_building_age.log",
        conda:
            "../envs/module.yaml"
        params:
            step="nuts3_building_age",
            settings=config["spatial_weights"]["age"],
        message:
            "Prepare NUTS3 building-age distributions for '{wildcards.shapes}' shapes."
        script:
            "../scripts/prepare_buildings.py"
