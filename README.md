# European Building Heat

This module prepares time series of heat demand and heat supply technologies for buildings in European countries.

<!-- Example module output -->
<p align="center">
  <img src="./figures/yearly_heat_demand_profile.png" width="80%">
  <br>
  <em>Example 2023 daily heat-demand profile (seven-day rolling mean).</em>
</p>


## About
<!-- Please do not modify this templated section -->

This is a modular `snakemake` workflow created as part of the [Modelblocks project](https://www.modelblocks.org/). It can be imported directly into any `snakemake` workflow.

For more information, please consult the Modelblocks [documentation](https://modelblocks.readthedocs.io/en/latest/),
the [integration example](./tests/integration/Snakefile),
and the `snakemake` [documentation](https://snakemake.readthedocs.io/en/stable/snakefiles/modularization.html).

## Overview
<!-- Please describe the processing stages of this module here -->

This module combines national annual heat-demand statistics with weather-driven
hourly profiles and scales them to user-provided European regions using
population weights.

Data processing steps:

<p align="center">
  <img src="./figures/rulegraph.png" width="100%">
</p>

1. Read and validate the user-provided regions. Only land shapes are processed,
   and configured proxy countries fill gaps in the available statistics.
2. Retrieve the required weather, population, timezone, and building-energy
   datasets from ERA5, GHSL, Eurostat, JRC-IDEES, ECUK, and Swiss sources.
3. Combine these statistics into national annual heat demand for households and
   services. Published useful-heat data take precedence where available;
   otherwise, configured technology efficiencies convert final to useful demand.
4. Allocate national demand to the requested shapes using population weights,
   producing a disaggregated annual TWh dataset.

<p align="center">
  <img src="./figures/annual_heat_demand.png" width="50%">
</p>

5. Derive local building-type shares and timezones, then combine ERA5 weather
   with When2Heat profiles to represent space heat and hot water demand.
6. Aggregate profiles to the requested shapes, align local behavior to a
   continuous UTC timeline, and scale each profile to its annual demand total.
7. Calculate air-source and ground-source heat-pump COP from weather and the
   configured sink temperatures and technology shares.
8. Write hourly heat demand, heat-pump electricity / COP timeseries.

### Timezone handling

Timezone assignment is geometry-based and independent of `country_id`, which
is used only to match shapes to national heat statistics. The workflow computes
each shape centroid in the configured projected CRS, transforms the centroid to
EPSG:4326, and intersects it with the pinned, land-only timezone-boundary dataset.

Each centroid must intersect exactly one valid IANA timezone. Assignment fails
with the affected shape IDs and centroid coordinates if no timezone or multiple
timezones match. There is no country-code, representative-point, ocean-zone,
or nearest-zone fallback.

When2Heat hourly factors are interpreted in local civil time and selected onto
a canonical UTC hourly index. The spring daylight-saving gap is skipped and
the repeated autumn hour is selected twice. ERA5 analysis timestamps remain UTC.

## Configuration
<!-- Please describe how to configure this module below -->

Please consult the configuration [README](./config/README.md) and
[example](./config/config.yaml) for all configuration options.

Earth Data Hub access requires an account and an API key from the
[account settings](https://earthdatahub.destine.eu/account-settings#my-personal-access-tokens).
Save the key by itself in `resources/user/edh_api.txt`; the file is ignored by
Git.

## Input / output structure
<!-- Please describe input / output file placement below -->

Please consult the [interface file](./INTERFACE.yaml) for more information.


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
  <https://github.com/evansiroky/timezone-boundary-builder/>
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
