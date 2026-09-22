"""Create hectare-level support for national residential space-heating demand."""


rule download_eurostat_building_age:
    output:
        table="<resources>/automatic/eurostat/cens_21dwop_r3.tsv.gz",
    log:
        "<logs>/download_eurostat_building_age.log",
    conda:
        "../envs/module.yaml"
    params:
        kind="building_age",
        year=config["eurostat"]["reference_year"],
        url=internal["resources"]["stable"]["url"].format(
            dataset="cens_21dwop_r3.tsv.gz"
        ),
    script:
        "../scripts/download.py"


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
        year=config["eurostat"]["reference_year"],
        settings=config["heat"]["spatial_weights"]["age"],
    script:
        "../scripts/prepare_buildings.py"


rule prepare_space_heat_sv_statistics:
    input:
        support=floor_area_batch_inputs,
    output:
        table="<resources>/automatic/{shapes}/heat_rasters/sv_statistics.parquet",
    log:
        "<logs>/{shapes}/prepare_space_heat_sv_statistics.log",
    conda:
        "../envs/module.yaml"
    params:
        step="space_heat_sv_statistics",
    script:
        "../scripts/prepare_buildings.py"


rule create_space_heat_weight_batch:
    input:
        support=rules.create_floor_area_batch.output.partials,
        age=rules.prepare_nuts3_building_age.output.table,
        sv_statistics=rules.prepare_space_heat_sv_statistics.output.table,
    output:
        partials=directory(
            "<resources>/automatic/{shapes}/heat_rasters/weights/{batch}"
        ),
    log:
        "<logs>/{shapes}/create_space_heat_weight_batch_{batch}.log",
    conda:
        "../envs/module.yaml"
    resources:
        mem_mb=4096,
    params:
        population_share=config["heat"]["spatial_weights"]["population"]["share"],
        eubucco_support=config["heat"]["spatial_weights"]["population"][
            "eubucco_support"
        ],
        microsoft_population_fallback=config["buildings_microsoft"][
            "population_fallback"
        ],
        raster=raster_settings(),
    script:
        "../scripts/weighted_floor_area.py"
