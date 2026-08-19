"""Rules for annual heat demand by shape."""


def _read_checkpoint_lines(path):
    with open(path) as f:
        return [line.strip() for line in f if line.strip()]


def _official_final_demand_inputs(wildcards, sector):
    """Return country statistics that provide absolute final demand."""
    country_data = checkpoints.prepare_shape_country_scope.get(
        shapes=wildcards.shapes
    ).output.source_country_ids
    countries = set(_read_checkpoint_lines(country_data))
    inputs = []
    if "CHE" in countries:
        inputs.append(f"<resources>/automatic/baseline/che/{sector}_final.csv")
    if "GBR" in countries:
        inputs.append(f"<resources>/automatic/baseline/ecuk/{sector}_final.csv")
    return inputs


def _annual_energy_balance_proxy_population_inputs(wildcards):
    country_data = checkpoints.prepare_shape_country_scope.get(
        shapes=wildcards.shapes
    ).output.country_ids
    countries = set(_read_checkpoint_lines(country_data))
    proxy_countries = set(
        config.get("data_proxies", {}).get("annual_energy_balance", {})
    )
    if countries & proxy_countries:
        return expand(
            "<resources>/automatic/shapes/{shapes}/population.nc",
            shapes=wildcards.shapes,
        )
    return []


checkpoint prepare_shape_country_scope:
    input:
        shapes=rules.prepare_shapes.output[0],
    output:
        country_ids="<resources>/automatic/shapes/{shapes}/country_ids.txt",
        source_country_ids="<resources>/automatic/shapes/{shapes}/source_country_ids.txt",
    log:
        "<logs>/{shapes}/annual/prepare_shape_country_scope.log",
    conda:
        "../envs/module.yaml"
    params:
        commercial_end_use_scope=internal["scope"]["datasets"]["commercial_end_use"][
            "countries"
        ],
        jrc_idees_proxies=config.get("data_proxies", {}).get("jrc_idees", {}),
    message:
        "Determine heat-demand country scope for '{wildcards.shapes}' shapes."
    script:
        "../scripts/prepare_shape_country_scope.py"


rule process_annual_energy_balances:
    input:
        energy_balance="<resources>/automatic/stable/estat_nrg_bal_c.tsv.gz",
        ch_energy_balance="<resources>/automatic/stable/CHE_energy_balance.xlsx",
        ch_industry_energy_balance="<resources>/automatic/stable/CHE_energy_consumption_industry.xlsx",
        gbr_residential=rules.baseline_ecuk_final_demand.output.residential,
        gbr_services=rules.baseline_ecuk_final_demand.output.services,
        cat_names=workflow.source_path("../internal/energy-balance-category-names.csv"),
        carrier_names=workflow.source_path(
            "../internal/energy-balance-carrier-names.csv"
        ),
    output:
        temp("<resources>/automatic/annual-energy-balances.csv"),
    log:
        "<logs>/annual/process_annual_energy_balances.log",
    conda:
        "../envs/module.yaml"
    params:
        first_year=min(config["years"]["start"], 2000),
    message:
        "Process annual energy balances from Eurostat, Swiss, and ECUK statistics."
    script:
        "../scripts/annual_energy_balance.py"


rule process_final_heat_demand:
    input:
        energy_balance=rules.process_annual_energy_balances.output[0],
        residential_baselines="<resources>/automatic/baseline/jrc_idees/residential_final.csv",
        services_baselines="<resources>/automatic/baseline/jrc_idees/services_final.csv",
        official_residential_demand=lambda wc: _official_final_demand_inputs(
            wc, "residential"
        ),
        official_services_demand=lambda wc: _official_final_demand_inputs(
            wc, "services"
        ),
        shapes=rules.prepare_shapes.output[0],
        population=_annual_energy_balance_proxy_population_inputs,
        country_ids="<resources>/automatic/shapes/{shapes}/country_ids.txt",
        carrier_names=workflow.source_path(
            "../internal/energy-balance-carrier-names.csv"
        ),
    output:
        final_demand=temp(
            "<resources>/automatic/shapes/{shapes}/annual-final-heat-demand-twh.csv"
        ),
    log:
        "<logs>/{shapes}/annual/process_final_heat_demand.log",
    conda:
        "../envs/module.yaml"
    params:
        model_years=MODEL_YEARS,
        countries=lambda wildcards, input: _read_checkpoint_lines(input.country_ids),
        data_proxies=config.get("data_proxies", {}),
    message:
        "Prepare national annual final heat demand for household and commercial sectors."
    script:
        "../scripts/final_heat_demand.py"


rule process_useful_heat:
    input:
        final_demand=rules.process_final_heat_demand.output.final_demand,
        residential_useful_baseline="<resources>/automatic/baseline/jrc_idees/residential_useful.csv",
        services_useful_baseline="<resources>/automatic/baseline/jrc_idees/services_useful.csv",
    output:
        total_demand="<resources>/automatic/shapes/{shapes}/annual-heat-demand-twh.csv",
    log:
        "<logs>/{shapes}/annual/process_useful_heat.log",
    conda:
        "../envs/module.yaml"
    params:
        model_years=MODEL_YEARS,
        heat_tech_params=config["heat"]["tech_efficiencies"],
        useful_heat_demand=config["heat"].get("useful_heat_demand", "actual"),
    message:
        "Calculate national annual useful heat demand."
    script:
        "../scripts/useful_heat.py"


rule rescale_annual_heat_demand_to_shapes:
    input:
        annual_demand=rules.process_useful_heat.output.total_demand,
        shapes=rules.prepare_shapes.output[0],
        population="<resources>/automatic/shapes/{shapes}/population.nc",
    output:
        "<annual_heat_demand>",
    log:
        "<logs>/{shapes}/annual/rescale_annual_heat_demand_to_shapes.log",
    conda:
        "../envs/module.yaml"
    message:
        "Scale national annual heat demand to '{wildcards.shapes}' shapes."
    script:
        "../scripts/rescale_annual_heat_demand.py"
