# Configuration

We recommend consulting the following before using this module:

- `config/config.yaml`: a generic example configuration of this module.
- `workflow/internal/config.schema.yaml`: a schematic overview of all the configuration options of this module.
- `INTERFACE.yaml`: lists module input and output files, and their default locations.
- `tests/integration/Snakefile`: an example of how to call this module from another workflow.

## Overview

This is only a brief overview of the configuration options.
Consult the configuration example and the schema for additional information.

- `building_rasters`: selects published building grids or processing from raw building data.
  - `source`: `auto` downloads the published Zenodo grids when the configuration matches
    `workflow/internal/precomputed_defaults.yaml`; otherwise it rebuilds them.
    `rebuild` always processes the raw sources and is selected in the example configuration.
    Changes to the population epoch, building sources, Eurostat assumptions,
    floor-area proxies, structural spatial weights, or raster settings trigger a rebuild
    in `auto` mode. Demand/weather years and HDD settings do not trigger a rebuild.
    Use `rebuild` for scopes outside the published grids' coverage or CRS.
- `demand_years`: annual heat-demand years, from 2010 through 2023.
  - `start`: first year to include.
  - `end`: first year not to include; it may be no later than 2024.
- `weather_years`: ERA5 years used to create the hourly profiles and annual heating-degree-day correction.
  This range must contain as many years as `demand_years`, with both ranges being paired in order.
  **Output timeseries use this year range in their timestamps**.
  - `start`: first weather year to include.
  - `end`: first weather year not to include.
- `threads`: parallelism available to aggregation tasks.
  - `aggregation`: maximum worker count, with a minimum of `1`.
- `population`: GHSL GHS-POP data used for building preparation, demand allocation, and hourly aggregation.
  Raster resolution is fixed at 100 metres.
  - `epoch`: population year, selected explicitly from the available five-year epochs
    between 1975 and 2030. The example uses `2025`.
  - `chunk_size`: aggregation window width and height in pixels.
    Must be a multiple of `256`, with a minimum of `256`; larger windows use more memory.
- `crs`: coordinate reference systems used for geometry calculations.
  - `projected`: projected CRS for operations such as centroid calculation, for example `EPSG:3035` or `3035`.
- `data_proxies`: mappings for requested countries missing from baseline datasets.
  Map each target ISO alpha-3 code to one or more covered reference-country codes.
  Multiple references are averaged.
  - `floor_area.countries`: reference countries for estimating missing floor area and building counts.
    Both estimates use the same country list, with separate reference ratios.
  - `sfh_mfh_shares`: proxies single and multi-family dwelling shares.
  - `annual_energy_balance`: proxies per-capita energy intensities and scales
    them to the target population.
    The target and references must have land shapes with positive assigned population.
  - `household_end_use`: proxies residential carrier-level end-use shares.
  - `jrc_idees`: proxies commercial carrier-level end-use shares.
- `buildings_eubucco`: EUBUCCO v0.2 building data used for building counts and floor-area estimates.
  - `source`: `full` includes observed footprint perimeters; `lightweight` requires
    `spatial_weights.surface_volume.method: equivalent_square`.
  - `floor_bin_representatives`: representative storey counts for the reported floor-count bins,
    used to estimate reference-country mean storeys and missing floor area.
- `buildings_microsoft`: Microsoft building footprints used alongside EUBUCCO.
  - `release`: dated source release in `YYYY-MM-DD` format; the example uses `2026-08-13`.
  - `minimum_building_count`: tiles with fewer retained buildings use population-based
    floor-area and count estimates. Populated cells without a retained building centroid
    also use this fallback.
- `eurostat`: Census 2021 dwelling data used to estimate residential floor area.
  The dataset and reference year are fixed.
  - `useful_to_gross_ratio`: multiplier converting useful dwelling area to gross building area.
  - `floor_space_m2`: representative useful area in square metres for each dwelling-area class.
  - `rooms.GE9`: assumed mean room count for dwellings with nine or more rooms.
    Classes 1–8 use their exact counts.
  - `floor_area_per_room_m2`: useful area per room for the fallback when floor-space totals
    are missing or zero; the useful-to-gross multiplier is applied afterwards.
- `spatial_weights`: controls spatial allocation of annual heat demand.
  Residential weights combine floor area, population, compactness, and building age;
  commercial weights use commercial/public floor area.
  - `population.share`: population fraction of the residential floor-area/population blend,
    between zero and one. Only population in cells with residential floor area is included.
  - `surface_volume`: residential compactness correction.
    - `elasticity`: non-negative exponent applied to the surface-to-volume ratio; `0` is neutral.
    - `method`: `footprint_perimeter` uses measured geometry and requires full EUBUCCO;
      `equivalent_square` estimates perimeter from footprint area.
  - `age`: residential age correction using Census 2021 dwelling-age data.
    - `multipliers`: relative heat-intensity factors for `before_1991`, `1991_2000`, and `after_2000`.
    - `cutoff_spanning_bin_multipliers`: separate factor for the combined `Y1981-2000` census bin.
      Set all age factors to `1` for neutral weighting.
  - `hdd`: annual heating-degree-day correction for residential and commercial space heat,
    normalized within each country. It is not applied again to hourly profiles.
    - `base_temperature`: daily-mean ERA5 temperature threshold in degrees Celsius; default `15.5`.
    - `elasticity`: non-negative exponent applied to HDD; default `0.5`. Use `0` to disable the correction.
- `useful_heat_demand`: `actual` (the default) uses published useful heat where available,
  while `calculate_all` applies the configured efficiencies everywhere.
  UK ECUK final demand is always converted using the configured efficiencies.
- `tech_efficiencies`: final-to-useful conversion factors by carrier under `space_heat`, `hot_water`, and `cooking`.
  Keep the carrier keys shown in the example configuration and adjust their numeric factors as needed.
- `heat_pump`: settings used to calculate the combined air-source and ground-source heat-pump COP profile.
  - `sink_temperature`: operating temperature in degrees Celsius for each heat-delivery method.
  - `space_heat_sink_shares`: space-heating share for each sink.
    Values must sum to one.
    Please omit exactly one configured sink to designate it for hot water.
  - `heat_pump_shares`: `ashp` and `gshp` shares, each between zero and one and together summing to one.
  - `correction_factor`: positive multiplier applied to the COP curves.
- `processing`: controls building-processing parallelism.
  - `nuts3_batches`: number of balanced regional processing batches, with a minimum of `1`.
- `raster`: storage settings for the output GeoTIFFs.
  All outputs use an aligned 100 m grid with nodata `0` and an automatically selected
  equal-area CRS: EPSG:3035 for European scopes and Mollweide otherwise.
  - `dtype`: `float32` or `float64` output precision.
  - `compression`: `deflate`, `lzw`, or `zstd` lossless compression.
  - `block_size`: tile width and height in pixels; must be a positive multiple of `16`.
- `plotting`: settings affecting diagnostic plots only.
  - `max_size`: maximum raster preview dimension in pixels.
  - `outline.color`: boundary colour accepted by Matplotlib.
  - `outline.linewidth`: positive boundary width in points.

The former `heat` wrapper is no longer used: `useful_heat_demand`, `tech_efficiencies`,
`heat_pump`, and `spatial_weights` are top-level settings.

This data module is part of the [Modelblocks](https://www.modelblocks.org/) project.
Please consult the [Modelblocks documentation](https://modelblocks.readthedocs.io/) for more details.
