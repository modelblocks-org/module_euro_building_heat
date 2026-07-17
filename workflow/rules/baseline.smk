
JRC_SPATIAL_SCOPE = internal["resources"]["jrc"]["spatial_scope"]

rule baseline_jrc_idees_tertiary:
    input:
        latest_jrc=expand(
            "<resources>/automatic/jrc-idees/{version}/tertiary_{country_code}.xlsx",
            country_code=[
                i for i in JRC_SPATIAL_SCOPE
                if not (i == "UK" and JRC_IDEES_VERSION > 2015)
            ],
            version=JRC_IDEES_VERSION,
        ),
        uk_2015="<resources>/automatic/jrc-idees/2015/tertiary_UK.xlsx"
    output:
        final="<resources>/automatic/baseline/jrc_idees/tertiary_final.csv",
        useful="<resources>/automatic/baseline/jrc_idees/tertiary_useful.csv",
        plot="<resources>/automatic/baseline/jrc_idees/tertiary.pdf",
    log:
        "<logs>/baseline/baseline_jrc_idees_tertiary.log",
    conda:
        "../envs/module.yaml"
    params:
        countries=JRC_SPATIAL_SCOPE
    message:
        "Baseline for commercial demand using JRC-IDEES tertiary data."
    script:
        "../scripts/baseline_jrc_idees_tertiary.py"
