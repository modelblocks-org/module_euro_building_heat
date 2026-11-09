"""Rules for heat-pump COP and electricity demand for heating."""


rule heat_pump_cop:
    input:
        temperature_air=rules.process_gridded_weather_data.output.temperature,
        temperature_ground=rules.process_gridded_weather_data.output.tsoil5,
        heat_pump_characteristics="<resources>/automatic/stable/heat_pump_characteristics.nc",
    output:
        cop=temp("<resources>/automatic/shapes/{shapes}/gridded-heat-pump-cop.nc"),
    log:
        "<logs>/{shapes}/hourly/heat_pump_cop.log",
    conda:
        "../envs/module.yaml"
    params:
        sink_temperature=config["heat_pump"]["sink_temperature"],
        space_heat_sink_shares=config["heat_pump"]["space_heat_sink_shares"],
        correction_factor=config["heat_pump"]["correction_factor"],
        heat_pump_shares=config["heat_pump"]["heat_pump_shares"],
        weather_years=WEATHER_YEARS,
    message:
        "Generate gridded heat-pump coefficient of performance (COP)."
    script:
        "../scripts/heat_pump_cop.py"


rule group_gridded_timeseries_heat_pump_cop:
    input:
        gridded_timeseries_data=rules.heat_pump_cop.output.cop,
        grid_weights=rules.prepare_spatial_weights.output.population,
    output:
        cop=temp("<resources>/automatic/shapes/{shapes}/heat-pump-cop.nc"),
    log:
        "<logs>/{shapes}/hourly/group_gridded_timeseries_heat_pump_cop.log",
    conda:
        "../envs/module.yaml"
    threads: config["threads"]["aggregation"]
    message:
        "Aggregate gridded heat-pump COP profiles to '{wildcards.shapes}' shapes."
    script:
        "../scripts/group_gridded_timeseries.py"


rule heat_pump_electricity_demand_timeseries:
    input:
        timeseries_data=rules.group_gridded_timeseries_heat_pump_cop.output.cop,
        annual_demand=rules.rescale_annual_heat_demand_to_shapes.output.annual_demand,
        heat_demand=rules.heat_demand_final_timeseries.output.timeseries,
        shape_timezones=rules.prepare_shape_timezones.output.timezones,
    output:
        cop="<heat_pump_cop>",
        electricity_demand="<heat_pump_electricity_demand>",
        cop_plot=report(
            "<resources>/automatic/shapes/{shapes}/{scenario}/plots/heat_pump_cop_timeseries_{year}.pdf",
            category="European Building Heat",
            subcategory="Heat pumps",
        ),
        electricity_demand_plot=report(
            "<resources>/automatic/shapes/{shapes}/{scenario}/plots/heat_pump_electricity_demand_timeseries_{year}.pdf",
            category="European Building Heat",
            subcategory="Heat pumps",
        ),
    log:
        "<logs>/{shapes}/{scenario}/hourly/heat_pump_electricity_demand_timeseries_{year}.log",
    conda:
        "../envs/module.yaml"
    params:
        weather_demand_years=WEATHER_DEMAND_YEARS,
    message:
        "Calculate heat-pump COP and electricity demand time series for '{wildcards.shapes}' shapes."
    script:
        "../scripts/heat_pump_final_timeseries.py"
