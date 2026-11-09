"""Calculate per-cell district-heating costs and identify coherent DH areas."""


rule district_heating:
    input:
        shapes=rules.prepare_shapes.output.shapes,
        space_heat="<space_heat_demand>",
        hot_water="<hot_water_demand>",
        building_count="<building_count>",
        country_parameters=workflow.source_path(
            "../internal/district_heating_country_parameters.csv"
        ),
        shares=lambda wc: f"<scenario_input>/{config['scenarios'][wc.scenario]['district_heating_shares']}",
    output:
        heat="<resources>/automatic/{shapes}/{scenario}/district_heating/heat_{year}.tif",
        costs="<resources>/automatic/{shapes}/{scenario}/district_heating/costs_{year}.tif",
    log:
        "<logs>/{shapes}/{scenario}/district_heating/district_heating_{year}.log",
    conda:
        "../envs/module.yaml"
    params:
        assumptions=config["district_heating"],
        assessment_year=lambda wc: int(wc.year),
        data_proxies=config["data_proxies"],
        grid_crs=config["crs"]["projected"],
    message:
        "Calculate district-heating network costs for '{wildcards.shapes}' shapes."
    script:
        "../scripts/district_heating.py"


rule district_heating_areas:
    input:
        shapes=rules.prepare_shapes.output.shapes,
        costs=rules.district_heating.output.costs,
        heat=rules.district_heating.output.heat,
    output:
        ids="<resources>/automatic/{shapes}/{scenario}/district_heating/area_ids_{year}.tif",
        areas="<district_heating_areas>",
    log:
        "<logs>/{shapes}/{scenario}/district_heating/district_heating_areas_{year}.log",
    conda:
        "../envs/module.yaml"
    message:
        "Identify coherent district-heating areas for '{wildcards.shapes}' shapes."
    script:
        "../scripts/district_heating_areas.py"
