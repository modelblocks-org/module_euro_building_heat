"""Allocate physical floor area and retain residential heat support."""


checkpoint prepare_floor_area_batches:
    input:
        plan=building_plan_input,
        stats=eubucco_stats_input,
    output:
        manifest="<resources>/automatic/{shapes}/heat_rasters/batches.json",
    log:
        "<logs>/{shapes}/prepare_floor_area_batches.log",
    conda:
        "../envs/module.yaml"
    params:
        step="floor_area_batches",
        batch_count=config["processing"]["nuts3_batches"],
    script:
        "../scripts/prepare_buildings.py"


rule prepare_building_population:
    input:
        regions=rules.prepare_nuts3.output.regions,
        nuts3_source=rules.download_nuts3.output.geojson,
        stats=eubucco_stats_input,
        population=get_configured_population_file(),
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
        population=config["population"],
        proxies=config["data_proxies"]["floor_area"],
        country_codes=internal["country_codes"],
    script:
        "../scripts/prepare_buildings.py"


rule prepare_floor_area_totals:
    input:
        nuts3=rules.prepare_nuts3.output.regions,
        nuts3_source=rules.download_nuts3.output.geojson,
        census=rules.download_eurostat_floor_area.output.table,
        population_summaries=rules.prepare_building_population.output.table,
        eubucco_stats=eubucco_stats_input,
        plan=building_plan_input,
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
        eurostat=config["eurostat"],
        eubucco=config["buildings_eubucco"],
        proxies=config["data_proxies"]["floor_area"],
        country_codes=internal["country_codes"],
    script:
        "../scripts/prepare_buildings.py"


rule create_floor_area_batch:
    input:
        scope=rules.prepare_shapes.output.scope,
        nuts3=rules.prepare_nuts3.output.regions,
        totals=rules.prepare_floor_area_totals.output.table,
        count_proxies=rules.prepare_building_count_proxies.output.table,
        plan=building_plan_input,
        batches=floor_area_batch_plan_input,
        eubucco=selected_eubucco_input,
        microsoft=selected_microsoft_input,
        microsoft_statistics=selected_microsoft_statistics_input,
        population=get_configured_population_file(),
    output:
        partials=directory(
            "<resources>/automatic/{shapes}/heat_rasters/batches/{batch}"
        ),
    log:
        "<logs>/{shapes}/create_floor_area_batch_{batch}.log",
    conda:
        "../envs/module.yaml"
    threads: 1
    resources:
        mem_mb=4096,
    params:
        eubucco=config["buildings_eubucco"],
        microsoft=config["buildings_microsoft"],
        surface_volume=config["heat"]["spatial_weights"]["surface_volume"],
        population_resampling=config["heat"]["spatial_weights"]["population"][
            "resampling"
        ],
        raster=intermediate_raster_settings(),
    script:
        "../scripts/calculate_floor_area.py"


rule merge_structural_support:
    input:
        shapes=rules.prepare_shapes.output.shapes,
        plan=building_plan_input,
        batches=floor_area_batch_plan_input,
        support=floor_area_batch_inputs,
        weights=space_heat_weight_batch_inputs,
        building_count="<building_count>",
    output:
        residential_space_heat_weight="<resources>/automatic/shapes/{shapes}/support/residential_space_heat_weight.tif",
        commercial_space_heat_weight="<resources>/automatic/shapes/{shapes}/support/commercial_space_heat_weight.tif",
        diagnostics="<resources>/automatic/{shapes}/space_heat_weight/diagnostics.parquet",
    log:
        "<logs>/{shapes}/merge_structural_support.log",
    conda:
        "../envs/module.yaml"
    resources:
        mem_mb=4096,
    params:
        mode="support",
        population=config["population"],
        microsoft=config["buildings_microsoft"],
        space_heat_weight=config["heat"]["spatial_weights"],
        raster=raster_settings(),
    script:
        "../scripts/merge_heat_rasters.py"
