"""Report figures depend on completed data, keeping style changes independent."""


rule report_figures:
    input:
        report_figure_inputs,
    output:
        "<resources>/automatic/shapes/{shapes}/plots/report_manifest.txt",
    log:
        "<logs>/{shapes}/plots/report_manifest.log",
    conda:
        "../envs/module.yaml"
    params:
        kind="report_manifest",
    script:
        "../scripts/_plots.py"


rule plot_annual_heat:
    input:
        data="<annual_heat_demand>",
        shapes=rules.prepare_shapes.output.shapes,
    output:
        report(
            "<resources>/automatic/shapes/{shapes}/plots/annual_heat_demand.png",
            category="European Building Heat",
            subcategory="Heat demand",
        ),
    log:
        "<logs>/{shapes}/plots/annual_heat.log",
    conda:
        "../envs/module.yaml"
    params:
        kind="annual",
    script:
        "../scripts/_plots.py"


rule plot_hourly_heat:
    input:
        data=lambda wc: HOURLY_PLOTS[wc.dataset][0],
    output:
        report(
            "<resources>/automatic/shapes/{shapes}/plots/{dataset}_timeseries.pdf",
            category="European Building Heat",
            subcategory=lambda wc: (
                "Heat demand" if wc.dataset == "heat_demand" else "Heat pumps"
            ),
        ),
    log:
        "<logs>/{shapes}/plots/{dataset}.log",
    wildcard_constraints:
        dataset="|".join(HOURLY_PLOTS),
    conda:
        "../envs/module.yaml"
    params:
        kind="timeseries",
        unit=lambda wc: HOURLY_PLOTS[wc.dataset][1],
        normalise=lambda wc: HOURLY_PLOTS[wc.dataset][2],
    script:
        "../scripts/_plots.py"


rule plot_building_raster:
    input:
        data=lambda wc: RASTER_PLOTS[wc.dataset][0],
        shapes=rules.prepare_shapes.output.shapes,
    output:
        report(
            "<resources>/automatic/shapes/{shapes}/plots/{dataset}.png",
            category="European Building Heat",
            subcategory="Buildings",
        ),
    log:
        "<logs>/{shapes}/plots/{dataset}.log",
    wildcard_constraints:
        dataset="|".join(RASTER_PLOTS),
    conda:
        "../envs/module.yaml"
    params:
        kind="raster",
        title=lambda wc: RASTER_PLOTS[wc.dataset][1],
        unit=lambda wc: RASTER_PLOTS[wc.dataset][2],
        chunk_size=config["population"]["chunk_size"],
        plotting=config["plotting"],
    script:
        "../scripts/_plots.py"


rule plot_jrc_baseline:
    input:
        data="<resources>/automatic/baseline/jrc_idees/{sector}_final.parquet",
        useful=["<resources>/automatic/baseline/jrc_idees/{sector}_useful.parquet"],
    output:
        report(
            "<resources>/automatic/baseline/jrc_idees/{sector}.pdf",
            category="European Building Heat",
            subcategory="Baseline",
        ),
    log:
        "<logs>/plots/baseline_jrc_{sector}.log",
    conda:
        "../envs/module.yaml"
    params:
        kind="baseline",
    script:
        "../scripts/_plots.py"


rule plot_official_baseline:
    input:
        data="<resources>/automatic/baseline/{source}/{sector}_final.parquet",
        useful=[],
    output:
        report(
            "<resources>/automatic/baseline/{source}/{sector}.pdf",
            category="European Building Heat",
            subcategory="Baseline",
        ),
    log:
        "<logs>/plots/baseline_{source}_{sector}.log",
    wildcard_constraints:
        source="che|ecuk",
        sector="residential|services",
    conda:
        "../envs/module.yaml"
    params:
        kind="baseline",
    script:
        "../scripts/_plots.py"
