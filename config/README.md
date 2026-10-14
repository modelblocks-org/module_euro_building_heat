# Configuration

Use [config.yaml](config.yaml) to configure the module. The
[integration configuration](../tests/integration/test_config.yaml) and
[Snakefile](../tests/integration/Snakefile) show how to use it from another workflow.
The [schema](../workflow/internal/config.schema.yaml) defines required settings,
accepted values, and validation constraints. Input and output paths are documented
in [INTERFACE.yaml](../INTERFACE.yaml).

## Building rasters

`building_rasters.source` selects how building grids are obtained:

- `prebuilt` downloads published grids and crops them to the requested shapes.
  It requires `population.epoch: 2025`. Rebuild settings may remain in the
  configuration, but building-processing settings, floor-area proxies, and
  structural spatial-weight settings do not affect the published grids.
  Only `hdd` under `spatial_weights` applies in this mode.
  The shapes must fit the published grid's coverage and coordinate system.
- `rebuild` generates grids from building footprints and census data. It requires
  the `processing`, `buildings_eubucco`, `buildings_microsoft`, and `eurostat`
  sections under `building_rasters`, plus `data_proxies.floor_area` and the
  structural settings under `spatial_weights`.

Rebuild settings control the following:

- `processing.nuts3_batches`: number of balanced regional processing batches.
- `buildings_eubucco.floor_bin_representatives`: representative storey counts
  used to estimate reference-country mean storeys and missing floor area.
- `buildings_microsoft.release`: footprint release in `YYYY-MM-DD` format.
- `buildings_microsoft.minimum_building_count`: tiles below this source-building
  count use population-based estimates. Populated cells without a retained
  building centroid also use population-based estimates.
- `eurostat.useful_to_gross_ratio`: conversion from useful dwelling area to gross
  building area, including walls and shared space.
- `eurostat.floor_space_m2`: representative useful area for each dwelling-area class.
- `eurostat.rooms.GE9`: representative room count for the open-ended census class.
- `eurostat.floor_area_per_room_m2`: useful area per room when floor-space totals
  are missing or zero, before conversion to gross area.

## Years and geometry

`demand_years` selects annual heat-demand data. `weather_years` selects weather for
hourly profiles and heating degree days (HDD). Both ranges include `start` and
exclude `end`. They must have equal lengths and are paired in order. Output hourly
timestamps use the weather years.

`population.epoch` selects the GHSL population year. `crs.projected` sets the
projected CRS for geometry calculations, including shape centroids.
`threads.aggregation` controls the worker count for aggregation tasks.

## Country proxies

`data_proxies` supplies reference countries where baseline data are missing.
Keys and reference lists use ISO alpha-3 country codes. Multiple reference-country
values are averaged. Use an empty mapping when a proxy section is not needed.

- `floor_area.countries`: reference countries for missing floor area and building
  counts, using separate reference ratios. Required only for `rebuild`.
- `sfh_mfh_shares`: single- and multi-family dwelling shares.
- `annual_energy_balance`: per-capita energy intensities scaled to the target
  population. Target and reference countries need land shapes with positive
  assigned population.
- `household_end_use`: residential carrier-level end-use shares.
- `jrc_idees`: commercial carrier-level end-use shares.

## Spatial weights

For `rebuild`, residential structural weights combine floor area, population,
compactness, and building age. Commercial weights use commercial/public floor area.

- `population.share`: population fraction of the residential floor-area/population
  blend. Only population in cells with residential floor area contributes.
- `surface_volume.elasticity`: exponent on the surface-to-volume ratio calculated
  from measured footprint perimeter. Zero gives neutral compactness weighting.
- `age.multipliers`: relative heat-intensity factors by census construction period.
  Equal factors give neutral age weighting.

For both source modes, `hdd` adjusts residential and commercial space-heat allocation:

- `base_temperature`: daily mean ERA5 temperature threshold in degrees Celsius.
- `elasticity`: exponent on HDD. Zero disables the correction.

HDD-adjusted weights are normalized within each country. Hourly profiles derive
from When2Heat and do not receive a second HDD adjustment.

## Energy conversion and heat pumps

`useful_heat_demand: actual` uses published useful heat where available.
`calculate_all` converts final energy using `tech_efficiencies` throughout.
UK ECUK final demand is converted using these efficiencies in both modes.

`tech_efficiencies` defines final-to-useful conversion fractions by carrier for
`space_heat`, `hot_water`, and `cooking`.

`heat_pump` controls the combined air-source and ground-source COP profile:

- `sink_temperature`: delivery temperature in degrees Celsius for each sink.
- `space_heat_sink_shares`: fractions of space heat supplied by each sink, summing
  to one. Omit exactly one configured sink to designate it for hot water.
- `heat_pump_shares`: air-source (`ashp`) and ground-source (`gshp`) fractions,
  summing to one.
- `correction_factor`: multiplier converting manufacturer COP to operating performance.
