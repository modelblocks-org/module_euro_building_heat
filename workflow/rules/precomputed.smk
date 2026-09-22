"""Use published building rasters when scientific settings match the release."""


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
        url=published_grid_url,
    message:
        "Download precomputed {wildcards.dataset} raster."
    shell:
        "curl --fail --silent --show-error --location --retry 5 "
        "--output {output.raster:q}.part {params.url:q} 2> {log:q} && "
        "mv {output.raster:q}.part {output.raster:q}"


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
