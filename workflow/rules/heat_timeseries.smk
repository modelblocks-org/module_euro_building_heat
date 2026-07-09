"""Rules for gridded and shape-aggregated heat-demand time series."""


rule process_gridded_weather_data:
    input:
        raw_weather=rules.download_gridded_weather_data.output.raw_weather,
    output:
        grid="<resources>/automatic/shapes/{shapes}/gridded-weather/grid.nc",
        temperature="<resources>/automatic/shapes/{shapes}/gridded-weather/temperature.nc",
        wind10m="<resources>/automatic/shapes/{shapes}/gridded-weather/wind10m.nc",
        tsoil5="<resources>/automatic/shapes/{shapes}/gridded-weather/tsoil5.nc",
    log:
        "<logs>/{shapes}/timeseries/process_gridded_weather_data.log",
    conda:
        "../envs/heat_demand.yaml"
    params:
        weather_years=WEATHER_YEARS,
    message:
        "Process raw ERA5 weather data for '{wildcards.shapes}'."
    script:
        "../scripts/process_gridded_weather_data.py"


rule unscaled_heat_profiles:
    input:
        wind_speed=rules.process_gridded_weather_data.output.wind10m,
        temperature=rules.process_gridded_weather_data.output.temperature,
        when2heat_daily="<resources>/automatic/when2heat/daily_demand.csv",
        when2heat_hourly_com="<resources>/automatic/when2heat/hourly_factors_COM.csv",
        when2heat_hourly_mfh="<resources>/automatic/when2heat/hourly_factors_MFH.csv",
        when2heat_hourly_sfh="<resources>/automatic/when2heat/hourly_factors_SFH.csv",
    output:
        "<resources>/automatic/shapes/{shapes}/gridded-hourly_unscaled_heat_demand.nc",
    log:
        "<logs>/{shapes}/timeseries/unscaled_heat_profiles.log",
    conda:
        "../envs/heat_demand.yaml"
    params:
        weather_years=WEATHER_YEARS,
    message:
        "Generate gridded heat demand profile shapes from weather data."
    script:
        "../scripts/unscaled_heat_profiles.py"


rule population_per_weather_gridbox:
    input:
        weather_grid=rules.process_gridded_weather_data.output.grid,
        population=rules.clip_population.output.path,
        locations=rules.prepare_shapes.output[0],
    output:
        "<resources>/automatic/shapes/{shapes}/population.nc",
    log:
        "<logs>/{shapes}/timeseries/population_per_weather_gridbox.log",
    conda:
        "../envs/heat_demand.yaml"
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
        "../envs/heat_demand.yaml"
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
        "../envs/heat_demand.yaml"
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
        shapes=rules.prepare_shapes.output[0],
    output:
        "<heat_demand_visualization>",
    log:
        "<logs>/{shapes}/visualization/heat_demand_visualization.log",
    conda:
        "../envs/heat_demand.yaml"
    params:
        max_steps=1000,
    message:
        "Create interactive heat demand visualization for '{wildcards.shapes}' shapes."
    script:
        "../scripts/heat_demand_visualization.py"
