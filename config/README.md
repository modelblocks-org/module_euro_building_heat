# Configuration

The example in `config/config.yaml` supplies all defaults. Imported workflows
can override individual settings; `workflow/internal/config.schema.yaml`
validates the merged configuration. Public files are listed in `INTERFACE.yaml`.

## Published building grids or full rebuild

`building_rasters.source: auto` selects one of two branches automatically:

- With the published assumptions, download `building_count.tif`,
  `residential_space_heat_weight.tif`, and `commercial_space_heat_weight.tif`
  from a pinned Zenodo record. These grids replace raw EUBUCCO/Microsoft
  downloads, building processing, and structural weighting. They are cached
  once across shape sets, cropped to each requested scope without resampling,
  and used by the usual demand and hourly calculations. Report plots are
  generated locally.
- Changing any of the following selects the full building workflow:
  `population.epoch`, `population.resolution`, `buildings_eubucco`,
  `buildings_microsoft`, `eurostat`, `building_count`, `data_proxies.floor_area`,
  `heat.spatial_weights`, `processing.intermediate_dtype`, or `raster`.
  This can require large source downloads and substantially more processing.
  For example, changing `heat.spatial_weights.population.share` from `0.5`
  to `0.6` rebuilds the grids automatically.

The comparison uses `workflow/internal/precomputed_defaults.yaml`, a frozen
snapshot of the published assumptions. Editing `config/config.yaml` or passing
module overrides therefore has the same effect. Restoring those assumptions
selects downloads again. Do not update the frozen snapshot without publishing
matching grids.

Demand/weather years, `heat.hdd`, energy efficiencies, heat-pump parameters,
other proxy groups, `crs.projected`, plotting, threads, `population.chunk_size`,
and `processing.nuts3_batches` do not select a rebuild. Their downstream
calculations still respond to configuration changes.

Set `building_rasters.source: rebuild` to force the original workflow even
with default assumptions. This is also needed for scopes outside the published
grids' coverage or CRS. Cropping uses raster cell centres; a rebuild instead
retains the original building-centroid clipping at scope boundaries, so boundary
cells can differ. The download branch does not produce the rebuild branch's
internal per-region diagnostics table.

**Publication is pending:** the Zenodo URL is intentionally empty in
`workflow/internal/settings.yaml`, at
`resources.precomputed_building_rasters.url`. Default raster jobs fail with an
explanatory error until it is filled in; use `source: rebuild` in the meantime.
The publisher should set it to
`https://zenodo.org/records/<version-specific-record-id>/files/{dataset}.tif`
and upload the three GeoTIFFs named above. They must share the aligned 100 m
EPSG:3035 grid, cover the advertised scope, and use the frozen settings, original
band descriptions/units and nodata convention. Publish complete-region support
before clipping to consumer shapes. A larger raster bounding box must not be
used to imply coverage in areas that were never processed. Downloads are
written to `.part` files before being renamed; cropped values are checked for
finite, nonnegative values and grid compatibility.

## Shared sources and building processing

- `population`: one GHSL GHS-POP source for building preparation, annual heat,
  and hourly aggregation. `epoch` defaults to **2025**, `resolution` is **100 m**,
  and `chunk_size` controls bounded aggregation windows. The previous automatic
  nearest-demand-year selection and 1 km heat population source are removed.
  Population summaries are cached across building control and reference regions.
  Only exact matching projected geometries reuse a sum; floor-area references
  retain their GISCO/Eurostat coverage and count references retain EUBUCCO coverage.
- `buildings_eubucco`: version `0.2`, source `full` by default, centroid assignment,
  and floor-bin representatives. The `lightweight` source requires
  `heat.spatial_weights.surface_volume.method: equivalent_square`; observed
  `footprint_perimeter` requires the full source.
- `buildings_microsoft`: pinned release `2026-08-13`. Sparse tiles below the
  configured `minimum_building_count`, and populated cells without a retained
  centroid, share the existing population fallback for both floor area and counts.
- `eurostat`: Census 2021 floor-area assumptions, including useful-to-gross ratio,
  representative dwelling areas and room counts. All values remain configurable.
- `processing`: balanced NUTS processing batches and persistent float64
  intermediates. Building centroids and population cells retain their original
  clipping conventions; complete NUTS-region totals are normalized before clipping.
- `raster`: aligned 100 m equal-area grid, output dtype, compression, tile size,
  and nodata 0. Automatic CRS selection retains EPSG:3035 for European scopes
  and Mollweide otherwise. All public rasters share this grid.
- `building_count`: independent reference-count shares and counts-per-person
  proxies. Residential, commercial, and public buildings are included; other
  uses are excluded. Proxy counts can be fractional.
- `plotting`: raster diagnostic maximum size and outline style. These settings
  affect only plot jobs. The import example requests all annotated report figures
  through `plots/report_manifest.txt`, alongside the nine data outputs. Individual
  figures remain independently requestable; count-only targets do not require
  the heat report.

## Persistent building caches

Override `eubucco_cache` and `microsoft_cache` in the importing module's
`pathvars` to reuse existing caches, without copying or moving the large files:

```python
module building_heat:
    snakefile: "path/to/module_euro_building_heat/workflow/Snakefile"
    pathvars:
        eubucco_cache="/existing/resources/automatic/eubucco",
        microsoft_cache="/existing/resources/automatic/microsoft"
use rule * from building_heat as building_heat_*
```

The defaults are `<resources>/automatic/eubucco` and
`<resources>/automatic/microsoft`. Keep these layouts intact:

- EUBUCCO: `v{version}/{source}/downloads/{region}.parquet`, including
  `eubucco_lat_lon.parquet` for the lightweight source.
- Microsoft: `{release}/dataset-links.csv` and
  `{release}/downloads/{quadkey}-{part}.csv.gz`, where parts are five digits
  numbered using the existing sorted URL order.

These are persistent `update(...)` outputs. A rerun validates completed files
without invoking curl. Partial transfers retain their names and resume with
`--continue-at -`; validation succeeds before a partial file is renamed.
Invalid completed files cause a validation error and are not replaced.
Changes to heat assumptions, environments, or Snakemake history must never
cause an existing building partition to be downloaded again.

EUBUCCO region metadata have independent persistent rules under
`<resources>/automatic/eubucco-metadata/v{version}/`. Source planning reads these
files instead of fetching them again. During local integration, metadata were
seeded from the original module after checking its saved source version.

Changing the source/version or requesting a wider geographic scope can require
previously uncached files. Existing releases and partitions remain available.

## Demand, weather and proxies

- `demand_years` selects annual statistics from 2010 through 2023. `start` is
  inclusive and `end` exclusive.
- `weather_years` uses the same range convention and must have the same length;
  years are paired in order. Output timestamps follow the weather years.
- `threads.aggregation` controls hourly aggregation parallelism.
- `crs.projected` controls geometry calculations such as timezone centroids.
- `data_proxies.floor_area` retains the raster module's method and reference
  countries. Count proxies share this country list but use independent ratios.
- Other `data_proxies` entries retain their existing meanings:
  `sfh_mfh_shares`, `annual_energy_balance`, `household_end_use`, and `jrc_idees`.
  Energy-balance population proxies still require the relevant target and
  reference countries in the supplied shapes.
- `heat.useful_heat_demand`, `heat.tech_efficiencies`, and `heat.heat_pump`
  retain the existing useful-energy conversion and heat-pump assumptions.
  Heat-pump source shares and sink shares each sum to one; exactly one sink
  omitted from space-heat shares represents hot water.

## Spatial and hourly weighting

`heat.spatial_weights` contains the imported population blend, compactness
elasticity/method, and age assumptions. Residential support blends floor-area
and eligible population shares within complete NUTS regions, then applies
country-centred compactness and observed-age factors. Eligible population is
restricted to cells with residential floor area; diagnostics report eligible
and excluded population. Missing height/observed age and Microsoft compactness
retain neutral corrections. The combined 1981–2000 census age bin keeps its
explicit configured multiplier. Commercial support is physical commercial/public
floor area without those additional corrections.

`heat.hdd` defines the daily-mean ERA5 base temperature (15.5 °C) and annual
severity exponent (0.5). For each paired year, spatial space-heat weights are
`sum_weather_cells(structural_support * HDD**elasticity)`. These weights are
normalized over supplied shapes within each country. Their demand sums to the
national total even when the supplied shapes cover only part of that country.
An elasticity of zero disables HDD, including in zero-HDD cells.

Hot water uses the structural sector support without HDD, and cooking uses
population. The two final demand GeoTIFFs are written directly in MWh/cell.
Each sector is normalized separately to its annual shape totals before summing;
this retains the existing corrections for weather-intersection boundary cells.
There are no intermediate household/commercial demand GeoTIFFs or commercial
resampling step. Building counts and support remain cached independently of HDD.

Hourly When2Heat profiles retain structural sector weights for space heat and
population weights for hot water. Annual HDD is not applied again to hourly
profiles. Heat-pump COP aggregation remains population weighted. UTC alignment,
geometry-derived IANA timezones, DST, and leap-year handling remain unchanged.
