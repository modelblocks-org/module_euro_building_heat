"""Optional aggregate target for figures produced alongside workflow data."""


rule report_figures:
    input:
        report_figure_inputs,
    output:
        "<resources>/automatic/shapes/{shapes}/plots/report_manifest.txt",
    log:
        "<logs>/{shapes}/plots/report_manifest.log",
    conda:
        "../envs/module.yaml"
    script:
        "../scripts/write_report_manifest.py"
