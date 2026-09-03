"""Rules for gridded and shape-aggregated heat-demand time series."""


rule process_gridded_weather_data:
    input:
        era5=ancient(rules.download_era5_data.output.era5),
    output:
        grid=temp("<resources>/automatic/shapes/{shapes}/gridded_weather/grid.nc"),
        temperature=temp(
            "<resources>/automatic/shapes/{shapes}/gridded_weather/temperature.nc"
        ),
        wind10m=temp("<resources>/automatic/shapes/{shapes}/gridded_weather/wind10m.nc"),
        tsoil5=temp("<resources>/automatic/shapes/{shapes}/gridded_weather/tsoil5.nc"),
    log:
        "<logs>/{shapes}/timeseries/process_gridded_weather_data.log",
    conda:
        "../envs/module.yaml"
    params:
        weather_years=WEATHER_YEARS,
    message:
        "Process ERA5 weather data for '{wildcards.shapes}'."
    script:
        "../scripts/process_gridded_weather_data.py"


rule local_unscaled_heat_profiles:
    input:
        wind_speed=rules.process_gridded_weather_data.output.wind10m,
        temperature=rules.process_gridded_weather_data.output.temperature,
        grid_weights="<resources>/automatic/shapes/{shapes}/population.nc",
        when2heat_daily="<resources>/automatic/when2heat/daily_demand.csv",
        when2heat_hourly_com="<resources>/automatic/when2heat/hourly_factors_COM.csv",
        when2heat_hourly_mfh="<resources>/automatic/when2heat/hourly_factors_MFH.csv",
        when2heat_hourly_sfh="<resources>/automatic/when2heat/hourly_factors_SFH.csv",
    output:
        local_profiles=temp(LOCAL_UNSCALED_HEAT_PROFILES),
    log:
        "<logs>/{shapes}/timeseries/local_unscaled_heat_profiles.log",
    conda:
        "../envs/module.yaml"
    threads: config["threads"]["aggregation"]
    params:
        weather_years=WEATHER_YEARS,
    message:
        "Generate and aggregate local-clock heat demand profiles for '{wildcards.shapes}'."
    script:
        "../scripts/unscaled_heat_profiles.py"


rule unscaled_heat_profiles:
    input:
        local_profiles=rules.local_unscaled_heat_profiles.output.local_profiles,
        shape_timezones=rules.prepare_shape_timezones.output[0],
    output:
        temp("<resources>/automatic/shapes/{shapes}/hourly_unscaled_heat_demand.nc"),
    log:
        "<logs>/{shapes}/timeseries/unscaled_heat_profiles.log",
    conda:
        "../envs/module.yaml"
    threads: config["threads"]["aggregation"]
    params:
        weather_years=WEATHER_YEARS,
    message:
        "Align heat demand profiles for '{wildcards.shapes}' to UTC."
    script:
        "../scripts/align_unscaled_heat_profiles.py"


rule population_per_weather_gridbox:
    input:
        weather_grid=rules.process_gridded_weather_data.output.grid,
        population=rules.clip_population.output.path,
        locations=rules.prepare_shapes.output[0],
    output:
        temp("<resources>/automatic/shapes/{shapes}/population.nc"),
    log:
        "<logs>/{shapes}/timeseries/population_per_weather_gridbox.log",
    conda:
        "../envs/module.yaml"
    params:
        lat_name="lat",
        lon_name="lon",
    message:
        "Calculate population weights per weather gridbox for '{wildcards.shapes}'."
    script:
        "../scripts/population_per_gridbox.py"


rule heat_demand_final_timeseries:
    input:
        timeseries_data=rules.unscaled_heat_profiles.output[0],
        annual_demand=rules.rescale_annual_heat_demand_to_shapes.output.annual_demand,
        sfh_mfh_shares=rules.prepare_sfh_mfh_shares.output[0],
        shapes=rules.prepare_shapes.output[0],
        shape_timezones=rules.prepare_shape_timezones.output[0],
    output:
        timeseries="<heat_demand>",
        plot=report(
            "<resources>/automatic/shapes/{shapes}/plots/heat_demand_timeseries.pdf",
            category="European Building Heat",
            subcategory="Heat demand",
        ),
    log:
        "<logs>/{shapes}/timeseries/heat_demand_final_timeseries.log",
    conda:
        "../envs/module.yaml"
    params:
        weather_demand_years=WEATHER_DEMAND_YEARS,
    message:
        "Scale heat demand time series for '{wildcards.shapes}' shapes."
    script:
        "../scripts/heat_demand_final_timeseries.py"
