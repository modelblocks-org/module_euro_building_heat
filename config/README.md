We recommend consulting the following before using this module:
- `config/config.yaml`: a generic example configuration of this module.
- `workflow/internal/config.schema.yaml`: a schematic overview of all the configuration options of this module.
- `INTERFACE.yaml`: lists module input and output files, and their default locations.
- `tests/integration/Snakefile`: an example of how to call this module from another workflow.

## Configuration overview

For the complete module output set, configure `years`, `weather`, `threads`, `population`, `heat.tech_efficiencies`, and `heat.heat_pump` as shown in the example.
Only `data_proxies` and `heat.useful_heat_demand` are optional.

## Model and weather years

Both year ranges include `start` and exclude `end`:

```yaml
years:
  start: 2023
  end: 2024

weather:
  start: 2023
  end: 2024
```

`years` selects the annual heat-demand model years.
The supported model period is 2010–2023, so `years.start` must be at least 2010 and `years.end` cannot exceed 2024.

`weather` selects the ERA5 years used to construct the hourly profiles.
It may differ from `years`, but it must contain the same number of years.
Years arepaired in order: for example, `years: 2022–2024` and `weather: 2018–2020` use 2018 weather for 2022 demand and 2019 weather for 2023 demand.
Hourly output timestamps retain the configured weather years.

## Threads

```yaml
threads:
  aggregation: 8
```

`threads.aggregation` sets the number of worker threads used to calculate and aggregate gridded heat-demand profiles, align them to UTC, and aggregate heat-pump COP.
It must be an integer of at least one. Snakemake may provide fewer threads when the workflow-wide core limit is lower.

## Population resolution

```yaml
population:
  resolution: 1000
```

`population.resolution` selects the GHSL GHS-POP raster resolution in metres.
The accepted values are:

- `1000`: the smaller and faster 1 km dataset.
- `100`: the more detailed 100 m dataset, with a larger download and higher
  processing cost.

The workflow automatically chooses the available five-year GHSL population epoch closest to `years.start`.
Population is used to allocate national annual demand and weather-grid profiles to the user-provided shapes.

## Missing-country proxies

`data_proxies` is optional.
Each proxy group maps an unsupported target country to one or more reference countries using ISO 3166-1 alpha-3 codes.
When several references are listed, the workflow averages their relevant values.

```yaml
data_proxies:
  sfh_mfh_shares:
    MNE: [BGR, HRV, HUN, ROU, GRC]
  annual_energy_balance:
    AND: [ESP]
  household_end_use:
    MNE: [BGR, HRV, HUN, ROU, GRC]
  jrc_idees:
    MNE: [BGR, HRV, HUN, ROU, GRC]
```

The four groups fill different gaps:

- `sfh_mfh_shares` averages reference-country single-family and multi-family dwelling shares from the 2021 Eurostat census.
These shares combine the SFH and MFH demand profiles for the target country.
- `annual_energy_balance` averages the reference countries' per-capita energy balance intensities and scales the result to the target population.
Because population is calculated from the configured shapes, the target and every reference country in this group must be represented by land shapes in the input file and must have positive assigned population.
- `household_end_use` averages residential carrier-level end-use shares from the reference countries and applies them to the target country's energy balance.
- `jrc_idees` averages commercial carrier-level end-use shares derived from JRC-IDEES reference countries and applies them to the target country's services energy balance.

A proxy is needed only when a requested country is outside the corresponding dataset's built-in scope.
Reference countries must themselves be covered by that dataset.
Otherwise, the workflow stops with the affected country and proxy group.

## Useful heat demand

```yaml
heat:
  useful_heat_demand: actual
```

`heat.useful_heat_demand` controls how annual final energy is converted to useful heat:

- `actual` uses published JRC-IDEES useful heat wherever it is available and calculates the remaining values with `tech_efficiencies`.
Great Britain is always calculated from ECUK final demand because it is excluded from the published useful-heat overlay.
- `calculate_all` calculates useful heat from final energy and the configured efficiencies for every country.

The setting is optional and defaults to `actual`.

### Technology efficiencies

`heat.tech_efficiencies` provides final-to-useful energy conversion factors by end use and carrier.
Values are fractions: for example, `0.9` converts one unit of final energy into `0.9` units of useful heat.

```yaml
heat:
  tech_efficiencies:
    space_heat:
      gas: 0.97
      oil: 0.9
      solid_fossil: 0.8
      biowaste: 0.8
      solar_thermal: 1
      electricity: 1
    hot_water:
      gas: 0.97
      oil: 0.9
      solid_fossil: 0.8
      biowaste: 0.8
      solar_thermal: 1
      electricity: 1
    cooking:
      gas: 0.28
      oil: 0.28
      solid_fossil: 0.15
      biowaste: 0.1
      electricity: 0.5
```

Keep all parameters shown for each applicable end use.
Ambient heat, direct electricity, district heat, and heat-pump energy use fixed unit efficiency in the workflow and therefore have no configurable efficiency keys.

## Heat pumps

The `heat.heat_pump` settings determine the combined air-source and ground-source heat-pump COP profile:

```yaml
heat:
  heat_pump:
    sink_temperature:
      underfloor: 35
      radiator_large: 50
      radiator_conventional: 65
      hot_water: 60
    space_heat_sink_shares:
      underfloor: 0.1
      radiator_large: 0.15
      radiator_conventional: 0.75
    heat_pump_shares:
      ashp: 0.9
      gshp: 0.1
    correction_factor: 0.85
```

- `sink_temperature` gives the operating temperature in degrees Celsius for each heat-delivery method.
- `space_heat_sink_shares` weights the sink methods used for space heating.
Its keys must occur in `sink_temperature`, and its values must sum to one.
- Exactly one `sink_temperature` key must be absent from `space_heat_sink_shares`.
That method is treated as the hot-water sink.
- `heat_pump_shares.ashp` and `heat_pump_shares.gshp` set the air-source and ground-source shares.
Each must be between zero and one, and together they must sum to one.
- `correction_factor` is a positive multiplier that adjusts manufacturer COP curves to expected operational performance.

The resulting COP is weighted between space heating and hot water using the configured annual useful demand before electricity demand is calculated.

This data module is part of the [Modelblocks](https://www.modelblocks.org/) project.
Please consult the [Modelblocks documentation](https://modelblocks.readthedocs.io/) for more details.
