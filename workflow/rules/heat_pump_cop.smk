"""Rules for heat-pump COP and electricity demand for heating."""


rule heat_pump_cop:
    input:
        temperature_air=rules.process_gridded_weather_data.output.temperature,
        temperature_ground=rules.process_gridded_weather_data.output.tsoil5,
        heat_pump_characteristics="<resources>/automatic/stable/heat_pump_characteristics.nc",
    output:
        "<resources>/automatic/shapes/{shapes}/gridded-heat-pump-cop.nc",
    log:
        "<logs>/{shapes}/heat-pump/heat_pump_cop.log",
    conda:
        "../envs/heat_demand.yaml"
    params:
        sink_temperature=config["heat"]["heat_pump"]["sink_temperature"],
        space_heat_sink_shares=config["heat"]["heat_pump"]["space_heat_sink_shares"],
        correction_factor=config["heat"]["heat_pump"]["correction_factor"],
        heat_pump_shares=config["heat"]["heat_pump"]["heat_pump_shares"],
        weather_years=WEATHER_YEARS,
    message:
        "Generate gridded heat-pump coefficient of performance (COP)."
    script:
        "../scripts/heat_pump_cop.py"


rule group_gridded_timeseries_heat_pump_cop:
    input:
        gridded_timeseries_data=rules.heat_pump_cop.output[0],
        grid_weights=rules.population_per_weather_gridbox.output[0],
    output:
        temp("<resources>/automatic/shapes/{shapes}/heat-pump-cop.nc"),
    log:
        "<logs>/{shapes}/heat-pump/group_gridded_timeseries_heat_pump_cop.log",
    conda:
        "../envs/heat_demand.yaml"
    threads: config["threads"]["aggregation"]
    message:
        "Aggregate gridded heat-pump COP profiles to '{wildcards.shapes}' shapes."
    script:
        "../scripts/group_gridded_timeseries.py"


rule heat_pump_electricity_demand_timeseries:
    input:
        timeseries_data=rules.group_gridded_timeseries_heat_pump_cop.output[0],
        annual_demand="<annual_heat_demand>",
        heat_demand="<heat_demand>",
    output:
        cop="<heat_pump_cop>",
        electricity_demand="<heat_pump_electricity_demand>",
    log:
        "<logs>/{shapes}/heat-pump/heat_pump_electricity_demand_timeseries.log",
    conda:
        "../envs/heat_demand.yaml"
    params:
        weather_model_years=WEATHER_MODEL_YEARS,
    message:
        "Calculate heat-pump COP and electricity demand time series for '{wildcards.shapes}' shapes."
    script:
        "../scripts/heat_pump_final_timeseries.py"
