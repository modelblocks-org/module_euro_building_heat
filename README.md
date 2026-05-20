# Euro building heat

This module prepares time series of heat demand and heat supply technologies for buildings in European countries.

<!-- Place an attractive image of module outputs here -->


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
   statistics, Swiss energy statistics, and JRC-IDEES tertiary-sector data.
3. Process national annual heat demand. Household demand is derived from
   Eurostat and Swiss end-use statistics; commercial demand is estimated using
   energy balances and JRC-IDEES tertiary-sector end-use data.
4. Convert final energy demand to useful heat demand with the configured
   technology efficiencies for space heat, hot water, and cooking.
5. Allocate national annual useful heat demand to the requested shapes using
   population shares calculated on the weather grid.
6. Generate unscaled hourly heat-demand profiles from gridded temperature and
   wind-speed data using the When2Heat method.
7. Aggregate the gridded profiles to the requested shapes with the same
   population weights.
8. Scale the hourly profiles to the annual useful heat demand and write the
   final shape-level heat-demand time series.
9. Optionally create an interactive HTML choropleth map for visual inspection
   of the heat-demand output.

## Configuration
<!-- Please describe how to configure this module below -->

Please consult the configuration [README](./config/README.md) and the [configuration example](./config/config.yaml) for a general overview on the configuration options of this module.

The main configuration groups are:

- `years`: first model year and exclusive end year. For example, `start: 2018`
  and `end: 2019` processes the 2018 calendar year.
- `threads.aggregation`: number of threads used when aggregating gridded heat
  profiles to shapes.
- `scaling.power`: factor used to convert MWh to the power or energy unit
  expected by the downstream model.
- `heat.tech_efficiencies`: carrier-specific efficiencies used to convert final
  energy demand into useful heat demand by end use.
- `heat.sfh_mfh_shares`: shares of single-family and multi-family households
  used when combining household heat profiles.

The module is designed to be called from another Snakemake workflow. A minimal
import looks like this:

```python
module module_euro_building_heat:
    pathvars:
        shapes="resources/user/my_shapes/shapes.parquet",
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
- `geometry`: polygon geometry in a coordinate reference system readable by
  GeoPandas.

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
| `heat_demand_visualization` | `<results>/{shapes}/visualization/heat_demand.html` | Interactive map for checking the output. |

The final `heat_demand` file is a Parquet table with timesteps on the index and
shape IDs as columns. Values are scaled using `scaling.power`.

## Development
<!-- Please do not modify this templated section -->

We use [`pixi`](https://pixi.sh/) as our package manager for development.
Once installed, run the following to clone this repository and install all dependencies.

```shell
git clone git@github.com:modelblocks-org/module_euro_building_heat.git
cd module_euro_building_heat
pixi install --all
```

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
- Gridded weather data used for temperature, wind speed, and grid definitions:
  <https://zenodo.org/records/11516744>
- When2Heat demand profile parameter archive:
  <https://zenodo.org/records/10965295>
- JRC-IDEES 2015 energy demand data:
  <https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/JRC-IDEES/JRC-IDEES-2015_v1>
- Eurostat gridded population data, JRC 1 km population grid 2018:
  <https://ec.europa.eu/eurostat/cache/GISCO/geodatafiles/JRC_GRID_2018.zip>
- Eurostat household end-use and energy-balance datasets, distributed here via
  the Euro-Calliope dataset mirror:
  <https://github.com/calliope-project/euro-calliope-datasets>
- Swiss Federal Office of Energy statistics for Swiss energy balances and
  end-use demand:
  <https://www.bfe.admin.ch/bfe/en/home/versorgung/statistik-und-geodaten/energiestatistiken.html>
