rule baseline_jrc_idees_sector:
    input:
        jrc_files=lambda wc: _get_jrc_baseline_files(wc.sector),
    output:
        final="<resources>/automatic/baseline/jrc_idees/{sector}_final.parquet",
        useful="<resources>/automatic/baseline/jrc_idees/{sector}_useful.parquet",
        plot=report(
            "<resources>/automatic/baseline/jrc_idees/{sector}.pdf",
            category="European Building Heat",
            subcategory="Baseline",
        ),
    log:
        "<logs>/baseline/baseline_jrc_idees_{sector}.log",
    conda:
        "../envs/module.yaml"
    params:
        countries=JRC_SPATIAL_SCOPE,
    message:
        "Create JRC-IDEES baseline for {wildcards.sector}."
    script:
        "../scripts/baseline_jrc_idees_sector.py"


rule baseline_che_final_demand:
    input:
        raw_stats="<resources>/automatic/stable/CHE_energy_consumption_households.xlsx",
        parser=workflow.source_path("../scripts/_che.py"),
    output:
        residential="<resources>/automatic/baseline/che/residential_final.parquet",
        services="<resources>/automatic/baseline/che/services_final.parquet",
        residential_plot=report(
            "<resources>/automatic/baseline/che/residential.pdf",
            category="European Building Heat",
            subcategory="Baseline",
        ),
        services_plot=report(
            "<resources>/automatic/baseline/che/services.pdf",
            category="European Building Heat",
            subcategory="Baseline",
        ),
    log:
        "<logs>/baseline/baseline_che.log",
    conda:
        "../envs/module.yaml"
    message:
        "Create CHE baseline."
    script:
        "../scripts/baseline_che_final_demand.py"


rule baseline_ecuk_final_demand:
    input:
        raw_stats=_get_ecuk_baseline_file(),
        parser=workflow.source_path("../scripts/_ecuk.py"),
    output:
        residential="<resources>/automatic/baseline/ecuk/residential_final.parquet",
        services="<resources>/automatic/baseline/ecuk/services_final.parquet",
        residential_plot=report(
            "<resources>/automatic/baseline/ecuk/residential.pdf",
            category="European Building Heat",
            subcategory="Baseline",
        ),
        services_plot=report(
            "<resources>/automatic/baseline/ecuk/services.pdf",
            category="European Building Heat",
            subcategory="Baseline",
        ),
    log:
        "<logs>/baseline/baseline_ecuk.log",
    conda:
        "../envs/module.yaml"
    message:
        "Create ECUK baseline."
    script:
        "../scripts/baseline_ecuk_final_demand.py"
