"""Prepare building-count assumptions independently of floor-area heat support."""


rule prepare_building_count_proxies:
    input:
        plan=building_plan_input,
        regions=rules.prepare_nuts3.output.regions,
        stats=eubucco_stats_input,
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
    script:
        "../scripts/prepare_building_count_proxies.py"


rule merge_building_count:
    input:
        shapes=rules.prepare_shapes.output.shapes,
        plan=building_plan_input,
        batches=floor_area_batch_plan_input,
        support=floor_area_batch_inputs,
    output:
        building_count="<building_count>",
        building_count_plot=report(
            "<resources>/automatic/shapes/{shapes}/plots/building_count.png",
            category="European Building Heat",
            subcategory="Buildings",
        ),
    log:
        "<logs>/{shapes}/merge_building_count.log",
    conda:
        "../envs/module.yaml"
    resources:
        mem_mb=4096,
    params:
        plotting=config["plotting"],
        mode="counts",
        population=config["population"],
        microsoft=config["buildings_microsoft"],
        raster=raster_settings(),
    script:
        "../scripts/merge_heat_rasters.py"
