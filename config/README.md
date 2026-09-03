We recommend consulting the following before using this module:
- `config/config.yaml`: a generic example configuration of this module.
- `workflow/internal/config.schema.yaml`: a schematic overview of all the configuration options of this module.
- `INTERFACE.yaml`: lists module input and output files, and their default locations.
- `tests/integration/Snakefile`: an example of how to call this module from another workflow.

## Overview

This is only a brief overview of the configuration options.
Consult the configuration example and the schema for additional information.

- `demand_years`: annual heat-demand years, from 2010 through 2023.
  - `start`: first year to include.
  - `end`: first year not to include; it may be no later than 2024.
- `weather_years`: ERA5 years used to create the hourly profiles.
This range must contain as many years as `demand_years`, with both ranges being paired in order.
**Output timeseries use this year range in their timestamps**.
  - `start`: first weather year to include.
  - `end`: first weather year not to include.
- `threads`: parallelism available to aggregation tasks.
  - `aggregation`: maximum worker count, with a minimum of `1`.
- `population`: GHSL population data used to allocate demand to the input shapes.
The workflow selects the available population epoch closest to `demand_years.start`.
  - `resolution`: raster resolution in metres. Use `1000` for a smaller,
    faster calculation or `100` for more spatial detail.
- `crs`: coordinate reference systems used for geometry calculations.
  - `projected`: projected CRS for operations such as centroid calculation, for example `EPSG:3035` or `3035`.
- `data_proxies`: optional mappings for requested countries missing from baseline datasets.
    Map each target ISO alpha-3 code to one or more covered reference-country codes.
    Multiple references are averaged.
  - `sfh_mfh_shares`: proxies single and multi-family dwelling shares.
  - `annual_energy_balance`: proxies per-capita energy intensities and scales
    them to the target population.
    The target and references must have land shapes with positive assigned population.
  - `household_end_use`: proxies residential carrier-level end-use shares.
  - `jrc_idees`: proxies commercial carrier-level end-use shares.
- `heat`: controls conversion to useful heat and average heat-pump performance.
  - `useful_heat_demand`: This is optional.
    `actual` (the default) uses published useful heat where available, while `calculate_all` applies the configured efficiencies everywhere.
  - `tech_efficiencies`: final-to-useful conversion factors by carrier under `space_heat`, `hot_water`, and `cooking`.
    Keep the carrier keys shown in the example configuration and adjust their numeric factors as needed.
  - `heat_pump`: settings used to calculate the combined air-source and
    ground-source heat-pump COP profile.
    - `sink_temperature`: operating temperature in degrees Celsius for each heat-delivery method.
    - `space_heat_sink_shares`: space-heating share for each sink.
      Values must sum to one.
      Please omit exactly one configured sink to designate it for hot water.
    - `heat_pump_shares`: `ashp` and `gshp` shares, each between zero and one and together summing to one.
    - `correction_factor`: positive multiplier applied to the COP curves.

This data module is part of the [Modelblocks](https://www.modelblocks.org/) project.
Please consult the [Modelblocks documentation](https://modelblocks.readthedocs.io/) for more details.
