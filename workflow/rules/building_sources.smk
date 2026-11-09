"""Select, acquire, and process building records for the requested regions."""


checkpoint prepare_building_sources:
    input:
        regions=rules.prepare_nuts3.output.regions,
        eubucco_nuts=rules.download_eubucco_nuts.output.table,
        eubucco_stats=rules.download_eubucco_stats.output.table,
        microsoft_index=f"<resources>/automatic/microsoft/{config['building_rasters']['buildings_microsoft']['release']}/dataset-links.csv",
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
        microsoft_release=config["building_rasters"]["buildings_microsoft"]["release"],
        proxies=config["data_proxies"]["floor_area"],
    message:
        "Plan building-data sources for '{wildcards.shapes}' shapes."
    script:
        "../scripts/prepare_buildings.py"


rule download_eubucco:
    output:
        table=update("<resources>/automatic/eubucco/v0.2/downloads/{region}.parquet"),
    log:
        "<logs>/eubucco/v0.2/download_{region}.log",
    conda:
        "../envs/module.yaml"
    params:
        url=lambda wc: internal["resources"]["automatic"]["eubucco_buildings"].format(
            nuts2=wc.region
        ),
    message:
        "Download EUBUCCO building data for {wildcards.region}."
    script:
        "../scripts/download.py"


rule process_eubucco:
    input:
        plan=lambda wc: checkpoints.prepare_building_sources.get(
            shapes=wc.shapes
        ).output.manifest,
        regions=rules.prepare_nuts3.output.regions,
        downloads=eubucco_download_inputs,
    output:
        table="<resources>/automatic/{shapes}/eubucco/v0.2/buildings.parquet",
    log:
        "<logs>/{shapes}/eubucco/v0.2/process.log",
    conda:
        "../envs/module.yaml"
    resources:
        mem_mb=4096,
    params:
        quality_control=config["building_rasters"]["buildings_eubucco"][
            "quality_control"
        ],
        footprint_allocation_threshold_cells=config["building_rasters"][
            "buildings_eubucco"
        ]["footprint_allocation_threshold_cells"],
    message:
        "Process EUBUCCO buildings for '{wildcards.shapes}' shapes."
    script:
        "../scripts/process_eubucco.py"


rule download_microsoft_index:
    output:
        table=update(
            f"<resources>/automatic/microsoft/{config['building_rasters']['buildings_microsoft']['release']}/dataset-links.csv"
        ),
    log:
        f"<logs>/microsoft/{config['building_rasters']['buildings_microsoft']['release']}/download_index.log",
    conda:
        "../envs/module.yaml"
    params:
        url=internal["resources"]["automatic"]["microsoft_index"].format(
            release=config["building_rasters"]["buildings_microsoft"]["release"]
        ),
    message:
        "Download the Microsoft building dataset index."
    script:
        "../scripts/download.py"


rule download_microsoft:
    input:
        index=rules.download_microsoft_index.output.table,
    output:
        table=update(
            f"<resources>/automatic/microsoft/{config['building_rasters']['buildings_microsoft']['release']}/downloads/{{quadkey}}-{{part}}.csv.gz"
        ),
    log:
        f"<logs>/microsoft/{config['building_rasters']['buildings_microsoft']['release']}/download_{{quadkey}}_{{part}}.log",
    wildcard_constraints:
        quadkey="[0-3]{9}",
        part="[0-9]{5}",
    conda:
        "../envs/module.yaml"
    message:
        "Download Microsoft buildings for tile {wildcards.quadkey}, part {wildcards.part}."
    script:
        "../scripts/download_microsoft.py"


rule process_microsoft:
    input:
        plan=lambda wc: checkpoints.prepare_building_sources.get(
            shapes=wc.shapes
        ).output.manifest,
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
    params:
        footprint_allocation_threshold_cells=config["building_rasters"][
            "buildings_microsoft"
        ]["footprint_allocation_threshold_cells"],
    message:
        "Process Microsoft buildings for '{wildcards.shapes}' shapes."
    script:
        "../scripts/process_microsoft.py"
