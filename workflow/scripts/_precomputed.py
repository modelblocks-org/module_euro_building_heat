"""Select the published-grid branch using its frozen scientific configuration."""


def matches_precomputed(config, baseline):
    """Compare all raster-affecting settings, including complete proxy mappings."""
    actual = {key: config[key] for key in baseline}
    actual["population"] = {
        key: value for key, value in config["population"].items() if key != "chunk_size"
    }
    actual["processing"] = {
        key: value for key, value in config["processing"].items() if key != "nuts3_batches"
    }
    actual["heat"] = {"spatial_weights": config["heat"]["spatial_weights"]}
    actual["data_proxies"] = {"floor_area": config["data_proxies"]["floor_area"]}
    return actual == baseline
