"""Rules for annual heat demand by shape."""


def _read_checkpoint_lines(path):
    with open(path) as f:
        return [line.strip() for line in f if line.strip()]


def _jrc_idees_inputs(wildcards):
    country_data = checkpoints.prepare_shape_country_scope.get(
        shapes=wildcards.shapes
    ).output.jrc_idees_country_codes
    return expand(
        "<resources>/automatic/jrc-idees/{version}/tertiary_{country_code}.xlsx",
        country_code=_read_checkpoint_lines(country_data),
        version=JRC_IDEES_VERSION,
    )


def _uk_jrc_idees_2015_inputs(wildcards):
    country_data = checkpoints.prepare_shape_country_scope.get(
        shapes=wildcards.shapes
    ).output.source_country_ids
    if "GBR" not in _read_checkpoint_lines(country_data):
        return []
    if config["years"]["start"] > 2019:
        return []
    return ["<resources>/automatic/jrc-idees/2015/tertiary_UK.xlsx"]


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
        jrc_idees_country_codes="<resources>/automatic/shapes/{shapes}/jrc_idees_country_codes.txt",
    log:
        "<logs>/{shapes}/annual/prepare_shape_country_scope.log",
    conda:
        "../envs/heat_demand.yaml"
    params:
        dataset_scopes=internal["scope"]["datasets"],
        data_proxies=config.get("data_proxies", {}),
        jrc_idees_spatial_scope=JRC_IDEES_SPATIAL_SCOPE,
    message:
        "Determine heat-demand country scope for '{wildcards.shapes}' shapes."
    script:
        "../scripts/prepare_shape_country_scope.py"


rule process_jrc_idees_tertiary:
    input:
        data=_jrc_idees_inputs,
    output:
        "<resources>/automatic/shapes/{shapes}/jrc-idees/tertiary_processed.csv",
    log:
        "<logs>/{shapes}/annual/process_jrc_idees_tertiary.log",
    conda:
        "../envs/heat_demand.yaml"
    message:
        "Process tertiary heat data from JRC-IDEES."
    script:
        "../scripts/jrc_idees_heat.py"


rule process_annual_energy_balances:
    input:
        energy_balance="<resources>/automatic/stable/estat_nrg_bal_c.tsv.gz",
        ch_energy_balance="<resources>/automatic/stable/CHE_energy_balance.xlsx",
        ch_industry_energy_balance="<resources>/automatic/stable/CHE_energy_consumption_industry.xlsx",
        cat_names=workflow.source_path("../internal/energy-balance-category-names.csv"),
        carrier_names=workflow.source_path(
            "../internal/energy-balance-carrier-names.csv"
        ),
    output:
        temp("<resources>/automatic/annual-energy-balances.csv"),
    log:
        "<logs>/annual/process_annual_energy_balances.log",
    conda:
        "../envs/heat_demand.yaml"
    params:
        first_year=min(config["years"]["start"], 2000),
    message:
        "Process annual energy balances from Eurostat and Swiss statistics."
    script:
        "../scripts/annual_energy_balance.py"


rule process_annual_heat_demand:
    input:
        hh_end_use="<resources>/automatic/stable/estat_nrg_d_hhq.tsv.gz",
        ch_end_use="<resources>/automatic/stable/CHE_energy_consumption_households.xlsx",
        energy_balance=rules.process_annual_energy_balances.output[0],
        commercial_demand=rules.process_jrc_idees_tertiary.output[0],
        shapes=rules.prepare_shapes.output[0],
        population=_annual_energy_balance_proxy_population_inputs,
        ecuk_end_use=checkpoint_ecuk_end_use_input,
        uk_jrc_idees_2015=_uk_jrc_idees_2015_inputs,
        country_ids="<resources>/automatic/shapes/{shapes}/country_ids.txt",
        source_country_ids="<resources>/automatic/shapes/{shapes}/source_country_ids.txt",
        carrier_names=workflow.source_path(
            "../internal/energy-balance-carrier-names.csv"
        ),
    output:
        total_demand="<resources>/automatic/shapes/{shapes}/annual-heat-demand-twh.csv",
        electricity="<resources>/automatic/shapes/{shapes}/annual-heat-electricity-demand-twh.csv",
    log:
        "<logs>/{shapes}/annual/process_annual_heat_demand.log",
    conda:
        "../envs/heat_demand.yaml"
    params:
        heat_tech_params=config["heat"]["tech_efficiencies"],
        model_years=MODEL_YEARS,
        countries=lambda wildcards, input: _read_checkpoint_lines(input.country_ids),
        source_countries=lambda wildcards, input: _read_checkpoint_lines(
            input.source_country_ids
        ),
        data_proxies=config.get("data_proxies", {}),
    message:
        "Calculate national annual heat demand for household and commercial sectors."
    script:
        "../scripts/annual_heat_demand.py"


rule rescale_annual_heat_demand_to_shapes:
    input:
        annual_demand=rules.process_annual_heat_demand.output.total_demand,
        shapes=rules.prepare_shapes.output[0],
        population="<resources>/automatic/shapes/{shapes}/population.nc",
    output:
        "<annual_heat_demand>",
    log:
        "<logs>/{shapes}/annual/rescale_annual_heat_demand_to_shapes.log",
    conda:
        "../envs/heat_demand.yaml"
    message:
        "Scale national annual heat demand to '{wildcards.shapes}' shapes."
    script:
        "../scripts/rescale_annual_heat_demand.py"
