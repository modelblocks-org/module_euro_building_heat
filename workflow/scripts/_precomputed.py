"""Select the published-grid branch using its frozen scientific configuration."""


def matches_precomputed(config, baseline):
    """Compare all raster-affecting settings, including complete proxy mappings."""
    actual = {key: config[key] for key in baseline}
    actual["population"] = {
        key: value for key, value in config["population"].items() if key != "chunk_size"
    }
    actual["spatial_weights"] = {
        key: value for key, value in config["spatial_weights"].items() if key != "hdd"
    }
    actual["data_proxies"] = {"floor_area": config["data_proxies"]["floor_area"]}
    return actual == baseline
