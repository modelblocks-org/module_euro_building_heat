"""Select published building grids or rebuild them from configured sources."""

with open(workflow.source_path("../internal/precomputed_defaults.yaml")) as stream:
    precomputed_defaults = yaml.safe_load(stream)


# Execute in a separate namespace; source_path supports imported/remote modules.
precomputed_helpers = {}
with open(workflow.source_path("../scripts/_precomputed.py")) as stream:
    exec(stream.read(), precomputed_helpers)
USE_PRECOMPUTED = config["building_rasters"][
    "source"
] == "auto" and precomputed_helpers["matches_precomputed"](config, precomputed_defaults)

if USE_PRECOMPUTED:

    include: "precomputed.smk"

else:

    include: "building_count.smk"
    include: "floor_area.smk"
    include: "space_heat_weight.smk"
