"""Rules for gridded and shape-aggregated heat-demand time series."""


rule unscaled_heat_profiles:
    input:
        wind_speed="<resources>/automatic/gridded-weather/wind10m.nc",
        temperature="<resources>/automatic/gridded-weather/temperature.nc",
        when2heat=rules.download_when2heat_params.output[0],
    output:
        "<resources>/automatic/hourly_unscaled_heat_demand.nc",
    log:
        "<logs>/timeseries/unscaled_heat_profiles.log",
    conda:
        "../envs/heat.yaml"
    params:
        weather_years=WEATHER_YEARS,
    message:
        "Generate gridded heat demand profile shapes from weather data."
    script:
        "../scripts/unscaled_heat_profiles.py"


rule population_per_weather_gridbox:
    input:
        weather_grid="<resources>/automatic/gridded-weather/grid.nc",
        population=rules.unzip_raw_population.output[0],
        locations=rules.filter_shapes.output[0],
    output:
        "<resources>/automatic/shapes/{shapes}/population.nc",
    log:
        "<logs>/{shapes}/timeseries/population_per_weather_gridbox.log",
    conda:
        "../envs/geo.yaml"
    params:
        lat_name="lat",
        lon_name="lon",
    message:
        "Calculate population weights per weather gridbox for '{wildcards.shapes}'."
    script:
        "../scripts/population_per_gridbox.py"


rule group_gridded_timeseries_heat_demand:
    input:
        gridded_timeseries_data=rules.unscaled_heat_profiles.output[0],
        grid_weights=rules.population_per_weather_gridbox.output[0],
    output:
        temp("<resources>/automatic/shapes/{shapes}/hourly_unscaled_heat_demand.nc"),
    log:
        "<logs>/{shapes}/timeseries/group_gridded_timeseries_heat_demand.log",
    conda:
        "../envs/heat.yaml"
    threads: config["threads"]["aggregation"]
    message:
        "Aggregate gridded heat demand profiles to '{wildcards.shapes}' shapes."
    script:
        "../scripts/group_gridded_timeseries.py"


rule heat_demand_final_timeseries:
    input:
        timeseries_data=rules.group_gridded_timeseries_heat_demand.output[0],
        annual_demand="<annual_heat_demand>",
    output:
        "<heat_demand>",
    log:
        "<logs>/{shapes}/timeseries/heat_demand_final_timeseries.log",
    conda:
        "../envs/heat.yaml"
    params:
        sfh_mfh_shares=config["heat"]["sfh_mfh_shares"],
        scaling_factor=config["scaling"]["power"],
        weather_model_years=WEATHER_MODEL_YEARS,
    message:
        "Scale heat demand time series for '{wildcards.shapes}' shapes."
    script:
        "../scripts/heat_demand_final_timeseries.py"


rule heat_demand_visualization:
    input:
        heat_demand="<heat_demand>",
        shapes=rules.filter_shapes.output[0],
    output:
        "<heat_demand_visualization>",
    log:
        "<logs>/{shapes}/visualization/heat_demand_visualization.log",
    conda:
        "../envs/geo.yaml"
    params:
        max_steps=1000,
    message:
        "Create interactive heat demand visualization for '{wildcards.shapes}' shapes."
    script:
        "../scripts/heat_demand_visualization.py"
