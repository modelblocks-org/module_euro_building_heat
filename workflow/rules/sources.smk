"""Acquire validated sources and prepare geography and building support."""


rule download_nuts3:
    output:
        geojson="<resources>/automatic/gisco/nuts3.geojson",
    log:
        "<logs>/download_nuts3.log",
    conda:
        "../envs/module.yaml"
    params:
        kind="nuts3",
        url=internal["resources"]["stable"]["url"].format(dataset="nuts3.geojson"),
    script:
        "../scripts/download.py"


rule download_eurostat_floor_area:
    output:
        table="<resources>/automatic/eurostat/cens_21dwbnr_r3.tsv.gz",
    log:
        "<logs>/download_eurostat_floor_area.log",
    conda:
        "../envs/module.yaml"
    params:
        kind="floor_area",
        url=internal["resources"]["stable"]["url"].format(
            dataset="cens_21dwbnr_r3.tsv.gz"
        ),
    script:
        "../scripts/download.py"


rule download_eubucco_nuts:
    output:
        table=update(
            "<resources>/automatic/eubucco-metadata/v0.2/NUTS-regions-2016.parquet"
        ),
    log:
        "<logs>/download_eubucco_nuts.log",
    conda:
        "../envs/module.yaml"
    params:
        kind="eubucco_nuts",
        url=internal["resources"]["automatic"]["eubucco_nuts"],
    script:
        "../scripts/download.py"


rule download_eubucco_stats:
    output:
        table=update("<resources>/automatic/eubucco-metadata/v0.2/region-stats.parquet"),
    log:
        "<logs>/download_eubucco_stats.log",
    conda:
        "../envs/module.yaml"
    params:
        kind="eubucco_stats",
        url=internal["resources"]["automatic"]["eubucco_stats"],
    script:
        "../scripts/download.py"


rule prepare_nuts3:
    input:
        shapes=rules.prepare_shapes.output.shapes,
        nuts3=rules.download_nuts3.output.geojson,
    output:
        regions="<resources>/automatic/{shapes}/nuts3.parquet",
    log:
        "<logs>/{shapes}/prepare_nuts3.log",
    conda:
        "../envs/module.yaml"
    params:
        step="nuts3",
        country_codes=internal["country_codes"],
    script:
        "../scripts/prepare_buildings.py"


checkpoint prepare_building_sources:
    input:
        regions=rules.prepare_nuts3.output.regions,
        eubucco_nuts=rules.download_eubucco_nuts.output.table,
        eubucco_stats=rules.download_eubucco_stats.output.table,
        microsoft_index=f"<microsoft_cache>/{config['buildings_microsoft']['release']}/dataset-links.csv",
    output:
        manifest="<resources>/automatic/{shapes}/buildings/plan.json",
        empty_eubucco="<resources>/automatic/{shapes}/buildings/empty_eubucco.parquet",
        empty_microsoft="<resources>/automatic/{shapes}/buildings/empty_microsoft.parquet",
        empty_microsoft_totals="<resources>/automatic/{shapes}/buildings/empty_microsoft_totals.parquet",
        empty_microsoft_statistics="<resources>/automatic/{shapes}/buildings/empty_microsoft_tile_statistics.parquet",
    log:
        "<logs>/{shapes}/prepare_building_sources.log",
    conda:
        "../envs/module.yaml"
    params:
        step="building_sources",
        eubucco=config["buildings_eubucco"],
        microsoft=config["buildings_microsoft"],
        proxies=config["data_proxies"]["floor_area"],
    script:
        "../scripts/prepare_buildings.py"


rule download_eubucco:
    output:
        table=update(
            f"<eubucco_cache>/v0.2/{config['buildings_eubucco']['source']}/downloads/{{region}}.parquet"
        ),
    log:
        f"<logs>/eubucco/v0.2/{config['buildings_eubucco']['source']}/download_{{region}}.log",
    conda:
        "../envs/module.yaml"
    params:
        kind="eubucco",
        source=config["buildings_eubucco"]["source"],
        url=eubucco_download_url,
    script:
        "../scripts/download.py"


rule process_eubucco:
    input:
        plan=building_plan_input,
        regions=rules.prepare_nuts3.output.regions,
        downloads=eubucco_download_inputs,
    output:
        table=f"<resources>/automatic/{{shapes}}/eubucco/v0.2/{config['buildings_eubucco']['source']}/buildings.parquet",
    log:
        f"<logs>/{{shapes}}/eubucco/v0.2/{config['buildings_eubucco']['source']}/process.log",
    conda:
        "../envs/module.yaml"
    resources:
        mem_mb=4096,
    script:
        "../scripts/process_eubucco.py"


rule download_microsoft_index:
    output:
        table=update(
            f"<microsoft_cache>/{config['buildings_microsoft']['release']}/dataset-links.csv"
        ),
    log:
        f"<logs>/microsoft/{config['buildings_microsoft']['release']}/download_index.log",
    conda:
        "../envs/module.yaml"
    params:
        kind="microsoft_index",
        url=internal["resources"]["automatic"]["microsoft_index"].format(
            release=config["buildings_microsoft"]["release"]
        ),
    script:
        "../scripts/download.py"


rule download_microsoft:
    input:
        index=rules.download_microsoft_index.output.table,
    output:
        table=update(
            f"<microsoft_cache>/{config['buildings_microsoft']['release']}/downloads/{{quadkey}}-{{part}}.csv.gz"
        ),
    log:
        f"<logs>/microsoft/{config['buildings_microsoft']['release']}/download_{{quadkey}}_{{part}}.log",
    wildcard_constraints:
        quadkey="[0-3]{9}",
        part="[0-9]{5}",
    conda:
        "../envs/module.yaml"
    params:
        kind="microsoft",
    script:
        "../scripts/download.py"


rule process_microsoft:
    input:
        plan=building_plan_input,
        regions=rules.prepare_nuts3.output.regions,
        downloads=microsoft_download_inputs,
    output:
        table="<resources>/automatic/{shapes}/microsoft/buildings.parquet",
        totals="<resources>/automatic/{shapes}/microsoft/footprint_totals.parquet",
        statistics="<resources>/automatic/{shapes}/microsoft/tile_statistics.parquet",
    log:
        "<logs>/{shapes}/microsoft/process.log",
    conda:
        "../envs/module.yaml"
    resources:
        mem_mb=4096,
    script:
        "../scripts/process_microsoft.py"
