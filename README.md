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
building floor area, population, and heating degree days (HDD). It includes
building acquisition and hectare-raster preparation in the same workflow.

Data processing steps:

<p align="center">
  <img src="./figures/rulegraph.png" width="100%" alt="Snakemake rule graph of the current workflow">
</p>

1. Validate the land shapes and prepare shared geography. Preserve the input shape IDs.
2. Reuse or download pinned EUBUCCO/Microsoft buildings and one GHSL 2025 population
   raster at 100 m. Prepare complete NUTS-region floor-area totals and count proxies.
3. Process balanced building batches once, retaining floor area, population,
   compactness statistics, and counts. Export residential/commercial/public
   buildings per hectare independently of the weather and heat-demand branches.
4. Build residential structural support from the floor-area/population blend,
   compactness, and age. Commercial support uses commercial/public floor area.
5. Combine national Eurostat, JRC-IDEES, ECUK, and Swiss statistics into useful heat.
   Reuse ERA5 temperatures to calculate annual HDD and weather-driven hourly profiles.
6. Allocate national space heat with structural support times HDD; hot water uses
   structural support without HDD and cooking uses population. Write both final
   annual demand rasters directly, preserving country and shape totals.
7. Aggregate When2Heat profiles, align local behaviour to UTC, and scale to annual
   demand. Calculate heat-pump COP and electricity demand with the existing methods.
8. Produce report figures alongside their data outputs, making them available
   automatically when reporting on those data targets.

### Reusing building downloads

The raw caches are persistent and shared across shape cases. `eubucco_cache` and
`microsoft_cache` path variables default to `resources/automatic/eubucco` and
`resources/automatic/microsoft`. Override them in the importing module to point
at existing caches, including those from `module_heat_rasters`. See the
[integration example](tests/integration/Snakefile).

The EUBUCCO v0.2 source layout and Microsoft 2026-08-13 release layout are
unchanged. Completed files are validated without another download, even if a
rule is forced to rerun. Partial transfers resume; only missing partitions or
bytes are fetched. Invalid completed files fail validation without being replaced.
EUBUCCO metadata are also versioned and cached independently of source planning.

For this local integration, the existing large cache directories are linked in
place; they were not copied or moved. Retain the original directories while
those links are in use. The code itself has no dependency on the other repository.

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

Earth Data Hub access requires an account and an API key. Look at
[their webpage](https://earthdatahub.destine.eu) for how to obtain one.
Save the key by itself in `resources/user/edh_api.txt`; the file is ignored by
Git.

## Input / output structure
<!-- Please describe input / output file placement below -->

Please consult the [interface file](./INTERFACE.yaml) for more information.

The module also exports heat demand separately for space heating and hot water:

- `annual/space_heat_demand_mwh.tif` and `annual/hot_water_demand_mwh.tif`:
  annual useful heat demand in MWh per 100 m cell, combining households and
  commercial buildings. Each band identifies its demand and weather years.
- `hourly/space_heat_profile.parquet` and `hourly/hot_water_profile.parquet`:
  normalized hourly shares with a UTC `timesteps` index and one column per shape.
  Each shape and weather year sums to 1 (or 0 for zero demand). Multiply these
  shares by the matching annual sink demand in MWh to obtain hourly MWh.

These paths are relative to `<results>/{shapes}/`. Existing public paths,
annual year-pair metadata, and UTC profile schemas are retained. The additional
`rasters/building_count.tif` output contains one `building_count` band in
`buildings/ha`, excluding other building uses; proxy counts can be fractional.
All three public rasters use the same aligned 100 m grid, with nodata 0.

Both sectors retain their own structural weights, with HDD applied only to
annual space heat. The final raster allocation corrects weather-intersection
boundary differences to preserve each shape's annual demand. The structural
support rasters are generated internally; they are no longer user inputs.

The shared population source defaults to 2025 at 100 m. This intentionally
replaces the previous heat branch's nearest-demand-year, 1 km population source
and can change population-based demand and profile aggregation.

Diagnostic figures are produced by the data-processing scripts and declared as
Snakemake `report(...)` outputs of the same rules. Reporting on a completed data
target therefore includes its figures and those from upstream processing rules:

```shell
snakemake results/working_EU/hourly/heat_pump_cop_pu.parquet --report report_working_EU.html
```

To build missing outputs before creating the report, add `--cores 4 --report-after-run`.
Existing runs created before plots were restored to the processing rules need
one normal workflow run to generate missing figures.

The report includes annual heat maps, hourly demand, COP and electricity
profiles, applicable JRC/Swiss/UK baselines, and building-count and structural
support maps. Request
`<resources>/automatic/shapes/{shapes}/plots/report_manifest.txt` to explicitly
build all report figures, or request individual figure paths. The manifest also
includes `space_heat_demand_density.png` under **Heat demand**, showing annual
space-heating demand in MWh/cell (1 ha cells) for the first raster band, labelled
with its demand and weather years. It uses the same maximum-coarsened preview
as the other raster figures, without converting the raster values.
Figures are co-outputs of data-processing jobs, so rebuilding them can rerun
those jobs.

The [import example](tests/integration/Snakefile) requests all nine data outputs
and the optional report manifest. From its directory, build the outputs and an
HTML report with:

```shell
snakemake --use-conda --cores 2 --report report.html --report-after-run
```


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
- `default`: includes the module dependencies plus development and test tools.
Because it contains `Snakemake`, `conda` and `pytest` as dependencies it **should not be used** in `Snakemake` rules.
- `module`: contains minimal dependencies used in `Snakemake` rules.
If modified, be sure to export it to `Snakemake` so it can be recreated by module users:

```shell
# create module.yaml and conda-spec pin files in workflow/envs/
pixi run export-snakemake-env module
```


## Testing
<!-- Please do not modify this templated section -->

Run focused checks without downloads or an ERA5 key:

```shell
pixi run pytest tests/test_*.py
```

The full integration run below downloads any missing sources and calculates the
configured case; it is intended for production validation:

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

- EUBUCCO v0.2 building footprints: <https://docs.eubucco.com/v0.2/>
- Microsoft Global ML Building Footprints: <https://github.com/microsoft/GlobalMLBuildingFootprints>
- Floor-area/compactness methodology: Müller et al. (2019), <https://doi.org/10.3390/en12244789>
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
<table>
  <tbody>
    <tr>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/brynpickering"><img src="https://avatars.githubusercontent.com/u/17178478?v=4?s=100" width="100px;" alt="Bryn Pickering"/><br /><sub><b>Bryn Pickering</b></sub></a><br /><a href="#ideas-brynpickering" title="Ideas, Planning, & Feedback">🤔</a> <a href="https://github.com/modelblocks-org/module_euro_building_heat/commits?author=brynpickering" title="Code">💻</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://orcid.org/0000-0003-2288-6423"><img src="https://avatars.githubusercontent.com/u/72193617?v=4?s=100" width="100px;" alt="Ivan Ruiz Manuel"/><br /><sub><b>Ivan Ruiz Manuel</b></sub></a><br /><a href="https://github.com/modelblocks-org/module_euro_building_heat/commits?author=irm-codebase" title="Code">💻</a> <a href="#ideas-irm-codebase" title="Ideas, Planning, & Feedback">🤔</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/jnnr"><img src="https://avatars.githubusercontent.com/u/32454596?v=4?s=100" width="100px;" alt="Jann Launer"/><br /><sub><b>Jann Launer</b></sub></a><br /><a href="https://github.com/modelblocks-org/module_euro_building_heat/commits?author=jnnr" title="Code">💻</a></td>
      <td align="center" valign="top" width="14.28%"><a href="http://www.pfenninger.org"><img src="https://avatars.githubusercontent.com/u/141709?v=4?s=100" width="100px;" alt="Stefan Pfenninger-Lee"/><br /><sub><b>Stefan Pfenninger-Lee</b></sub></a><br /><a href="https://github.com/modelblocks-org/module_euro_building_heat/commits?author=sjpfenninger" title="Code">💻</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/Yegberink"><img src="https://avatars.githubusercontent.com/u/152057926?v=4?s=100" width="100px;" alt="Yegberink"/><br /><sub><b>Yegberink</b></sub></a><br /><a href="#ideas-Yegberink" title="Ideas, Planning, & Feedback">🤔</a> <a href="https://github.com/modelblocks-org/module_euro_building_heat/commits?author=Yegberink" title="Code">💻</a> <a href="#maintenance-Yegberink" title="Maintenance">🚧</a> <a href="https://github.com/modelblocks-org/module_euro_building_heat/commits?author=Yegberink" title="Documentation">📖</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/mbrmbrmbrmbr"><img src="https://avatars.githubusercontent.com/u/175977791?v=4?s=100" width="100px;" alt="Machteld van den Broek"/><br /><sub><b>Machteld van den Broek</b></sub></a><br /><a href="#projectManagement-mbrmbrmbrmbr" title="Project Management">📆</a> <a href="#mentoring-mbrmbrmbrmbr" title="Mentoring">🧑‍🏫</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/tud-mchen6"><img src="https://avatars.githubusercontent.com/u/133768452?v=4?s=100" width="100px;" alt="mchen6"/><br /><sub><b>mchen6</b></sub></a><br /><a href="https://github.com/modelblocks-org/module_euro_building_heat/commits?author=tud-mchen6" title="Code">💻</a></td>
    </tr>
  </tbody>
</table>

<!-- markdownlint-restore -->
<!-- prettier-ignore-end -->

<!-- ALL-CONTRIBUTORS-LIST:END -->

This project follows the [all-contributors](https://github.com/all-contributors/all-contributors) specification. Contributions of any kind welcome!
