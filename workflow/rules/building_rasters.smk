"""Prepare published building rasters or calculate them from building records."""

if USE_PRECOMPUTED:

    rule merge_building_count:
        input:
            shapes=rules.prepare_shapes.output.shapes,
            building_count="<resources>/automatic/precomputed_buildings/building_count.tif",
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
        params:
            raster=raster_settings(),
            plotting=config["plotting"],
            population=config["population"],
            datasets=["building_count"],
            source="zenodo",
        message:
            "Prepare precomputed building counts for '{wildcards.shapes}' shapes."
        script:
            "../scripts/prepare_precomputed_rasters.py"

    rule merge_structural_support:
        input:
            shapes=rules.prepare_shapes.output.shapes,
            residential_space_heat_weight="<resources>/automatic/precomputed_buildings/residential_space_heat_weight.tif",
            commercial_space_heat_weight="<resources>/automatic/precomputed_buildings/commercial_space_heat_weight.tif",
        output:
            residential_space_heat_weight="<resources>/automatic/shapes/{shapes}/support/residential_space_heat_weight.tif",
            commercial_space_heat_weight="<resources>/automatic/shapes/{shapes}/support/commercial_space_heat_weight.tif",
            residential_space_heat_weight_plot=report(
                "<resources>/automatic/shapes/{shapes}/plots/residential_space_heat_weight.png",
                category="European Building Heat",
                subcategory="Buildings",
            ),
            commercial_space_heat_weight_plot=report(
                "<resources>/automatic/shapes/{shapes}/plots/commercial_space_heat_weight.png",
                category="European Building Heat",
                subcategory="Buildings",
            ),
        log:
            "<logs>/{shapes}/merge_structural_support.log",
        conda:
            "../envs/module.yaml"
        params:
            raster=raster_settings(),
            plotting=config["plotting"],
            population=config["population"],
            datasets=["residential_space_heat_weight", "commercial_space_heat_weight"],
            source="zenodo",
        message:
            "Prepare precomputed space-heating weights for '{wildcards.shapes}' shapes."
        script:
            "../scripts/prepare_precomputed_rasters.py"

else:

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
        message:
            "Plan floor-area processing batches for '{wildcards.shapes}' shapes."
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
            microsoft=config["buildings_microsoft"],
            surface_volume=config["spatial_weights"]["surface_volume"],
            raster=raster_settings(),
        message:
            "Calculate floor area for batch {wildcards.batch} of '{wildcards.shapes}' shapes."
        script:
            "../scripts/calculate_floor_area.py"

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
        message:
            "Calculate surface-to-volume statistics for '{wildcards.shapes}' shapes."
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
            population_share=config["spatial_weights"]["population"]["share"],
            raster=raster_settings(),
        message:
            "Calculate space-heating weights for batch {wildcards.batch} of '{wildcards.shapes}' shapes."
        script:
            "../scripts/weighted_floor_area.py"

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
        message:
            "Merge building counts for '{wildcards.shapes}' shapes."
        script:
            "../scripts/merge_heat_rasters.py"

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
            residential_space_heat_weight_plot=report(
                "<resources>/automatic/shapes/{shapes}/plots/residential_space_heat_weight.png",
                category="European Building Heat",
                subcategory="Buildings",
            ),
            commercial_space_heat_weight_plot=report(
                "<resources>/automatic/shapes/{shapes}/plots/commercial_space_heat_weight.png",
                category="European Building Heat",
                subcategory="Buildings",
            ),
        log:
            "<logs>/{shapes}/merge_structural_support.log",
        conda:
            "../envs/module.yaml"
        resources:
            mem_mb=4096,
        params:
            plotting=config["plotting"],
            mode="support",
            population=config["population"],
            microsoft=config["buildings_microsoft"],
            space_heat_weight={
                key: value
                for key, value in config["spatial_weights"].items()
                if key != "hdd"
            },
            raster=raster_settings(),
        message:
            "Merge space-heating weights for '{wildcards.shapes}' shapes."
        script:
            "../scripts/merge_heat_rasters.py"
