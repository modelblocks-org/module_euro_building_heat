# European Building Heat

This module prepares time series of heat demand and heat supply technologies for buildings in European countries.

<!-- Place an attractive image of module outputs here -->
<p align="center">
  <img src="./figures/module.png" width="75%">
</p>


## About
<!-- Please do not modify this templated section -->

This is a modular `snakemake` workflow created as part of the [Modelblocks project](https://www.modelblocks.org/). It can be imported directly into any `snakemake` workflow.

For more information, please consult the Modelblocks [documentation](https://modelblocks.readthedocs.io/en/latest/),
the [integration example](./tests/integration/Snakefile),
and the `snakemake` [documentation](https://snakemake.readthedocs.io/en/stable/snakefiles/modularization.html).

## Overview
<!-- Please describe the processing stages of this module here -->

This module builds spatially aggregated building heat-demand time series for a
user-provided set of European regions. It combines national annual heat-demand
statistics with weather-driven hourly demand profiles, then scales the profiles
to the requested shapes using population weights.

Data processing steps:

1. Prepare the spatial scope from the user-provided shapes file. The module
   reads the `shape_id`, `country_id`, and `geometry` columns and determines
   which countries are needed for the requested `{shapes}` case.
2. Download automatic input datasets, including gridded weather data,
   gridded population data, When2Heat profile parameters, Eurostat energy
   statistics, Swiss energy statistics, JRC-IDEES tertiary-sector data, and a
   pinned IANA timezone-boundary map.
3. Process national annual heat demand. Household demand is derived from
   Eurostat and Swiss end-use statistics; commercial demand is estimated using
   energy balances and JRC-IDEES tertiary-sector end-use data.
4. Use published JRC-IDEES useful heat demand where available, or convert final
   energy demand with the configured technology efficiencies for space heat,
   hot water, and cooking.
5. Allocate national annual useful heat demand to the requested shapes using
   population shares calculated on the weather grid.
6. Infer one IANA timezone per shape from its geometric centroid, without using
   `country_id`.
7. Generate unscaled hourly heat-demand profiles from gridded temperature and
   wind-speed data using the When2Heat method and local civil-clock factors.
8. Aggregate the gridded profiles to the requested shapes with the same
   population weights and cache one compact local-clock file per weather year
   under the shape's automatic resources.
9. Convert each shape's local behavioral profile onto one canonical UTC hourly
   timeline, scale it to annual useful heat demand, and write the final
   shape-level time series.
10. Create a static annual-demand choropleth and a stacked hourly time-series
   plot alongside their respective datasets, both split by the user-provided
   shapes.

## Configuration
<!-- Please describe how to configure this module below -->

Please consult the configuration [README](./config/README.md) and the [configuration example](./config/config.yaml) for a general overview on the configuration options of this module.

The main configuration groups are:

- `years`: first model year and exclusive end year. For example, `start: 2018`
  and `end: 2019` processes the 2018 calendar year.
- `weather`: first weather year and exclusive end weather year used to shape
  hourly heat-demand profiles. The weather-year span must match the model-year
  span. Output time series keep weather-year timestamps while annual scaling
  uses the paired model years. ERA5 data is downloaded from Earth Data Hub in
  one reusable NetCDF file for the full time range, then processed locally on
  its native 0.25° grid.
- `threads.aggregation`: number of threads used when aggregating gridded heat
  profiles to shapes.
- `population.resolution`: GHSL GHS-POP resolution in metres. The default is
  `100`, the most precise available Mollweide grid; `1000` uses the smaller
  1 km product. The workflow selects the GHSL five-year population epoch
  closest to `years.start`.
- `scaling.power`: factor used to convert MWh to the power or energy unit
  expected by the downstream model.
- `heat.tech_efficiencies`: carrier-specific efficiencies used to convert final
  energy demand into useful heat demand by end use.
- `heat.useful_heat_demand`: `actual` (the default) prioritizes published
  JRC-IDEES useful heat demand, except for the UK where useful heat is always
  calculated from ECUK final demand; `calculate_all` applies the configured
  efficiencies consistently to final energy demand for every country.
- `heat.sfh_mfh_shares`: shares of single-family and multi-family households
  used when combining household heat profiles.

Earth Data Hub access requires an account and an API key from the
[account settings](https://earthdatahub.destine.eu/account-settings#my-personal-access-tokens).
Save the key by itself in `resources/user/edh_api.txt`. Surrounding spaces and
line breaks are ignored when the workflow reads the file. This credential file
is ignored by Git and is not stored in the module configuration.

The module is designed to be called from another Snakemake workflow. A minimal
import looks like this:

```python
module module_euro_building_heat:
    pathvars:
        shapes="resources/user/my_shapes/shapes.parquet",
        edh_api="resources/user/edh_api.txt",
        heat_demand="results/my_shapes/heat_demand.parquet",
        logs="resources/module/logs",
        resources="resources/module/resources",
        results="resources/module/results",
    snakefile:
        "path/to/module_euro_building_heat/workflow/Snakefile"
    config:
        config["module_euro_building_heat"]

use rule * from module_euro_building_heat as module_euro_building_heat_*
```

The user-provided shapes file must be a GeoParquet file with at least these
columns:

- `shape_id`: unique identifier for each output region.
- `country_id`: ISO alpha-3 country code used to match shapes to national heat
  statistics.
- `shape_class`: shape context. Only rows with the exact value `land` are
  processed; all other rows are dropped.
- `geometry`: polygon geometry in a coordinate reference system readable by
  GeoPandas.

### Timezone handling

Timezone assignment is geometry-based and independent of `country_id`, which
remains necessary only for annual-demand statistics. The workflow transforms
each shape to EPSG:4326 and queries the land-only comprehensive
`timezones.geojson.zip` from timezone-boundary-builder release `2026c` using
the shape's Shapely centroid. The archive is pinned by SHA-256 checksum
`7d3f0c5a33b6acd891335c0ad5ba767736b6914cb1a1d68c71921c17ce358948`.

Exactly one unique IANA `tzid` must intersect every centroid. Assignment fails
with the affected shape IDs and centroid coordinates if no polygon or multiple
timezone polygons match. There is deliberately no country-code,
representative-point, ocean-zone, or nearest-zone fallback. Consequently,
concave or multipart land shapes can fail when their geometric centroid falls
outside the land timezone polygons.

When2Heat hourly factors are interpreted as local civil-clock profiles and are
converted to UTC with the inferred IANA timezone. The spring DST gap is skipped
and the repeated autumn hour is selected twice. ERA5 analysis timestamps remain
unchanged UTC validity times, and the existing UTC-day reference-temperature
method is preserved.

## Input / output structure
<!-- Please describe input / output file placement below -->

Please consult the [interface file](./INTERFACE.yaml) for more information.

By default, the module expects user inputs and writes outputs through Snakemake
path variables:

| Path variable | Default path | Description |
| --- | --- | --- |
| `shapes` | `<resources>/user/{shapes}/shapes.parquet` | User-provided polygons to process. |
| `annual_heat_demand` | `<results>/{shapes}/annual/heat_demand_twh.parquet` | Annual useful heat demand in TWh by shape. |
| `heat_demand` | `<results>/{shapes}/aggregated/heat_demand.parquet` | Final hourly heat-demand time series by shape. |
| `heat_pump_cop` | `<results>/{shapes}/aggregated/heat_pump_cop.parquet` | Final hourly heat-pump COP time series by shape. |
| `heat_pump_electricity_demand` | `<results>/{shapes}/aggregated/heat_pump_electricity_demand.parquet` | Final hourly electricity demand for heat pumps by shape. |
| `annual_heat_demand_choropleth` | `<results>/{shapes}/visualization/annual_heat_demand.pdf` | Static annual useful heat-demand map by shape. |
| `heat_demand_timeseries` | `<results>/{shapes}/visualization/heat_demand_timeseries.pdf` | Static hourly heat-demand plot with one panel per shape. |

The final `heat_demand`, `heat_pump_cop`, and
`heat_pump_electricity_demand` files are wide Parquet tables with shape IDs as
columns and a timezone-aware `datetime64[ns, UTC]` index named `timesteps`.
Every timestamp identifies the start of its UTC hourly period. Their Parquet
schema metadata records `output_timezone: UTC`, the JSON shape-to-IANA-timezone
mapping under `shape_timezones`, the timezone-boundary source and release, its checksum, and ODbL
attribution. Existing hourly outputs must be regenerated from shape-timezone
assignment and unscaled-profile generation onward after upgrading.

## Development
<!-- Please do not modify this templated section -->

We use [`pixi`](https://pixi.sh/) as our package manager for development.
Once installed, run the following to clone this repository and install all dependencies.

```shell
git clone git@github.com:modelblocks-org/module_euro_building_heat.git
cd module_euro_building_heat
pixi install --all
```

Please be aware that this is a multi-environment project (see [pixi.toml](./pixi.toml) for details).
- `default`: used for development and integration testing.
Because it contains `Snakemake`, `conda` and `pytest` as dependencies it **should not be used** in `Snakemake` rules.
- `module`: contains minimal dependencies used in `Snakemake` rules.
If modified, be sure to export it to `Snakemake` so it can be recreated by module users:

```shell
# create module.yaml and conda-spec pin files in workflow/envs/
pixi run export-snakemake-env module
```


## Testing
<!-- Please do not modify this templated section -->

For testing, simply run:

```shell
pixi run test-integration
```

To test a minimal example of a workflow using this module:

```shell
pixi shell    # activate this project's environment
cd tests/integration/  # navigate to the integration example
snakemake --use-conda --cores 2  # run the workflow!
```

## References
<!-- Please provide thorough referencing below -->

This module is based on the following research and datasets:

- When2Heat heat-demand profile methodology and parameters:
  <https://github.com/oruhnau/when2heat>
- Earth Data Hub ERA5 hourly single-level weather data used for temperature,
  wind speed, soil temperature, and grid definitions:
  <https://earthdatahub.destine.eu/collections/era5/datasets/era5-single-levels-atmosphere>
- When2Heat demand profile parameter archive:
  <https://zenodo.org/records/10965295>
- JRC-IDEES 2023 energy demand data:
  <https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/JRC-IDEES/JRC-IDEES-2023_v1>
- GHSL GHS-POP R2023A gridded population data:
  <https://human-settlement.emergency.copernicus.eu/ghs_pop2023.php>
- Timezone Boundary Builder release 2026c, whose comprehensive land-only
  boundary data is derived from OpenStreetMap and distributed under the Open
  Data Commons Open Database License (ODbL):
  <https://github.com/evansiroky/timezone-boundary-builder/releases/tag/2026c>
- Eurostat household end-use and energy-balance datasets, distributed here via
  the Euro-Calliope dataset mirror:
  <https://github.com/calliope-project/euro-calliope-datasets>
- Swiss Federal Office of Energy statistics for Swiss energy balances and
  end-use demand:
  <https://www.bfe.admin.ch/bfe/en/home/versorgung/statistik-und-geodaten/energiestatistiken.html>

## Contributors ✨

Thanks goes to these wonderful people, sorted alphabetically ([emoji key](https://allcontributors.org/en/reference/emoji-key/)):

<!-- ALL-CONTRIBUTORS-LIST:START - Do not remove or modify this section -->
<!-- prettier-ignore-start -->
<!-- markdownlint-disable -->
<!-- markdownlint-restore -->
<!-- prettier-ignore-end -->
<!-- ALL-CONTRIBUTORS-LIST:END -->

This project follows the [all-contributors](https://github.com/all-contributors/all-contributors) specification. Contributions of any kind welcome!
