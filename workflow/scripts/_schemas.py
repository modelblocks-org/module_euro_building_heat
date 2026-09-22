"""Schema validation generics."""

import json
from collections.abc import Iterable
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import rasterio
import shapely
from pandera import pandas as pa
from pandera.typing.geopandas import GeoSeries
from pandera.typing.pandas import Series
from shapely.geometry import MultiPolygon, Polygon

ENERGY_TYPES: tuple[str, ...] = ("useful_energy", "final_energy")


SECTORS: tuple[str, ...] = ("residential", "services")


CARRIERS: tuple[str, ...] = (
    "ambient_heat",
    "biomass_and_waste",
    "direct_electric",
    "electricity",
    "gas",
    "heat",
    "heat_pump",
    "oil",
    "renewable_heat",
    "solar_thermal",
    "solid_fossil",
)


END_USES: tuple[str, ...] = (
    "cooking",
    "end_use_electricity",
    "hot_water",
    "space_heat",
)


ANNUAL_HEAT_END_USES: tuple[str, ...] = ("cooking", "hot_water", "space_heat")


BUILDING_CATEGORIES: tuple[str, ...] = ("commercial", "household")


def validate_residential_space_heat_weight_raster(
    path: str | Path, *, check_values: bool = True, sector: str = "residential"
) -> None:
    """Validate the heat-raster module's one-band support contract."""
    with rasterio.open(path) as raster:
        assert raster.count == 1
        assert raster.crs
        assert np.allclose(np.abs(raster.res), 100)
        assert raster.nodatavals == (0.0,)
        assert sector in {"residential", "commercial"}
        assert raster.descriptions == (f"{sector}_space_heat_weight",)
        expected_unit = "m2/ha" if sector == "commercial" else "weighted_m2/ha"
        if raster.units != (expected_unit,):
            raise ValueError(
                f"{sector.capitalize()} support raster {path} must use units "
                f"{expected_unit!r}; got {raster.units!r}."
            )
        if not check_values:
            return
        for _, window in raster.block_windows(1):
            values = raster.read(1, window=window)
            assert np.isfinite(values).all()
            assert (values >= 0).all()


class ShapesSchema(pa.DataFrameModel):
    """Schema for geographic shapes."""

    class Config:
        coerce = False
        strict = "filter"

    shape_id: Series[str] = pa.Field(unique=True)
    "A unique identifier for this shape."
    country_id: Series[str] = pa.Field(str_length=3)
    "Country ISO alpha-3 code."
    shape_class: Series[str] = pa.Field(eq="land")
    "Identifier of the shape's context."
    geometry: GeoSeries
    "Shape (multi)polygon."

    @pa.check("geometry", element_wise=True)
    def check_geometries(cls, geom) -> bool:
        return (
            isinstance(geom, (Polygon, MultiPolygon))
            and not geom.is_empty
            and geom.is_valid
        )

    @pa.check("country_id", name="uppercase")
    def custom_check(cls, country_id: Series[str]) -> Series[bool]:
        return country_id.str.isupper()


class BaselineSchema(pa.DataFrameModel):
    """Schema for baseline files."""

    class Config:
        coerce = False
        strict = True
        unique = [
            "carrier_name",
            "sector",
            "end_use",
            "country_code",
            "unit",
            "energy",
            "year",
        ]

    carrier_name: Series[str] = pa.Field(isin=CARRIERS)
    "Name of the carrier."
    sector: Series[str] = pa.Field(isin=SECTORS)
    "Energy sector."
    end_use: Series[str] = pa.Field(isin=END_USES)
    "End use for the carrier."
    country_code: Series[str]
    "Country code."
    unit: Series[str] = pa.Field(eq="twh")
    "Unit of the value."
    # FIXME: needs better name
    energy: Series[str] = pa.Field(isin=ENERGY_TYPES)
    "Type of energy measurement."
    year: Series[int] = pa.Field(ge=2000)
    "Measurement year."
    value: Series[float] = pa.Field(ge=0)
    "Value."

    @classmethod
    def validate_countries(
        cls, df: pd.DataFrame, countries: Iterable[str]
    ) -> pd.DataFrame:
        """Run validation ensuring country scope matches expectations."""
        validated = cls.validate(df)

        mismatch = set(validated["country_code"].unique()) ^ set(countries)
        if mismatch:
            raise ValueError(f"Found countries outside scope: {mismatch}")

        return validated

    @classmethod
    def get_column_names(cls) -> list[str]:
        """Get the schema column names."""
        return list(cls.to_schema().columns.keys())


class AnnualHeatDemandSchema(pa.DataFrameModel):
    """Schema for annual useful heat demand allocated to shapes."""

    class Config:
        coerce = False
        strict = True
        ordered = True
        unique = ["end_use", "category", "year", "shape_id"]

    end_use: Series[str] = pa.Field(isin=ANNUAL_HEAT_END_USES)
    "Annual heat-demand end use."
    category: Series[str] = pa.Field(isin=BUILDING_CATEGORIES)
    "Building category."
    year: Series[int] = pa.Field(ge=2000)
    "Demand year."
    shape_id: Series[str]
    "Shape identifier."
    heat_demand_twh: Series[float] = pa.Field(ge=0)
    "Annual useful heat demand in TWh."


def validate_shape_source(path: str | Path) -> gpd.GeoDataFrame:
    """Validate user-provided land and maritime polygons."""
    shapes = gpd.read_parquet(path)
    if shapes.crs is None:
        raise ValueError("The shapes GeoParquet file must define a CRS.")
    land = ShapesSchema.validate(shapes.loc[shapes.shape_class.eq("land")]).copy()
    if land.empty:
        raise ValueError("No land shapes remain after filtering non-land regions.")
    projected = land.to_crs("ESRI:54009")
    if not np.isclose(projected.area.sum(), projected.geometry.union_all().area):
        raise ValueError("Land shapes must not overlap.")
    return land


def validate_nuts3_source(path: str | Path) -> gpd.GeoDataFrame:
    """Validate the GISCO NUTS-3 source."""
    nuts3 = gpd.read_file(path)
    assert {"NUTS_ID", "CNTR_CODE", "LEVL_CODE", "geometry"} <= set(nuts3)
    assert nuts3.crs
    assert nuts3.NUTS_ID.is_unique
    assert nuts3.LEVL_CODE.eq(3).all()
    assert nuts3.geometry.notna().all()
    return nuts3


def validate_census(path: str | Path) -> pd.DataFrame:
    """Validate the Eurostat dwelling floor-space table structure."""
    data = pd.read_csv(path, sep="\t", dtype=str)
    assert data.columns[0].endswith("\\TIME_PERIOD")
    dimensions = data.columns[0].removesuffix("\\TIME_PERIOD").split(",")
    assert dimensions == ["freq", "area", "n_room", "building", "unit", "geo"]
    assert sum(column.strip() == "2021" for column in data.columns) == 1
    return data


def validate_building_age_census(path: str | Path) -> pd.DataFrame:
    """Validate the Eurostat NUTS-3 dwelling construction-period table."""
    data = pd.read_csv(path, sep="\t", dtype=str)
    assert data.columns[0].endswith("\\TIME_PERIOD")
    dimensions = data.columns[0].removesuffix("\\TIME_PERIOD").split(",")
    assert dimensions == ["freq", "housing", "y_const", "unit", "geo"]
    assert sum(column.strip() == "2021" for column in data.columns) == 1
    return data


def validate_eubucco_nuts(path: str | Path) -> gpd.GeoDataFrame:
    """Validate EUBUCCO administrative-region metadata."""
    regions = gpd.read_parquet(path, columns=["region_id", "geometry"])
    assert regions.crs.to_epsg() == 3035
    assert regions.region_id.is_unique
    assert regions.region_id.str.fullmatch(r"[A-Z0-9]+").all()
    assert regions.geometry.is_valid.all()
    return regions


def validate_eubucco_stats(path: str | Path) -> gpd.GeoDataFrame:
    """Validate EUBUCCO NUTS-3 floor-area statistics."""
    columns = [
        "region_id",
        "country",
        "n",
        "n_type_residential",
        "n_subtype_commercial",
        "n_subtype_public",
        "floor_area_type_residential",
        "n_floors_0_2",
        "n_floors_2_4",
        "n_floors_4_7",
        "n_floors_7_inf",
        "floor_area_subtype_commercial",
        "floor_area_subtype_public",
        "geometry",
    ]
    stats = gpd.read_parquet(path, columns=columns)
    assert stats.crs.to_epsg() == 3035
    assert stats.region_id.is_unique
    assert stats.country.str.fullmatch(r"[A-Z]{2}").all()
    assert np.isfinite(stats[columns[2:-1]].to_numpy()).all()
    assert stats[columns[2:-1]].ge(0).all().all()
    return stats


def validate_microsoft_index(path: str | Path) -> pd.DataFrame:
    """Validate the columns used from Microsoft's pinned tile index."""
    links = pd.read_csv(path, dtype={"QuadKey": str})
    assert {"Location", "QuadKey", "Url"} <= set(links)
    assert links.QuadKey.str.fullmatch(r"[0-3]{9}").all()
    assert links.Url.str.startswith("https://").all()
    return links


def validate_microsoft_feature(feature: dict):
    """Normalize and validate one GeoJSONL building footprint."""
    assert "geometry" in feature
    geometry = shapely.make_valid(
        shapely.geometry.shape(feature["geometry"]),
        method="structure",
        keep_collapsed=False,
    )
    assert geometry.geom_type in {"Polygon", "MultiPolygon"}
    assert geometry.is_valid
    assert not geometry.is_empty
    return geometry


def validate_eubucco_source(path: str | Path, source: str) -> None:
    """Validate fields consumed from an EUBUCCO distribution."""
    schema = pq.read_schema(path)
    common = {"id", "region_id", "type", "subtype", "floors", "height"}
    required = (
        common | {"footprint_area", "lon", "lat"}
        if source == "lightweight"
        else common | {"geometry"}
    )
    assert required <= set(schema.names)
    if source == "full":
        geo = json.loads(schema.metadata[b"geo"])
        assert geo["primary_column"] == "geometry"
        assert geo["columns"]["geometry"]["crs"]["id"] == {
            "authority": "EPSG",
            "code": 3035,
        }


def validate_population_raster(path: str | Path) -> None:
    """Validate a GHSL GHS-POP Mollweide raster."""
    with rasterio.open(path) as raster:
        assert raster.count == 1
        assert raster.crs.to_string() == "ESRI:54009"
        assert np.allclose(np.abs(raster.res), 100)
        assert np.issubdtype(np.dtype(raster.dtypes[0]), np.floating)


def validate_building_count_reference(stats, population) -> None:
    """Require usable, nonoverlapping sector counts and covered population."""
    assert stats.n.sum() > 0
    assert np.isfinite(population)
    assert population > 0
    counts = (
        stats.n_type_residential + stats.n_subtype_commercial + stats.n_subtype_public
    )
    assert counts.le(stats.n).all()


def validate_building_count_proxies(table) -> None:
    """Validate the generated country count-proxy table at its boundaries."""
    columns = [
        "residential_share",
        "commercial_share",
        "residential_per_person",
        "commercial_per_person",
    ]
    assert set(table) == {"country_id", *columns}
    assert table.country_id.is_unique
    assert table.country_id.str.fullmatch(r"[A-Z]{3}").all()
    assert np.isfinite(table[columns].to_numpy(dtype=float)).all()
    assert table[columns].ge(0).all().all()
    assert (table.residential_share + table.commercial_share).le(1 + 1e-12).all()


def validate_building_count_raster(raster, *, check_values=True) -> None:
    """Validate one-band hectare counts without loading the whole raster."""
    validate_raster_contract(
        raster, ("building_count",), ("buildings/ha",), check_values=check_values
    )


def validate_nonnegative(*arrays) -> None:
    """Require finite, nonnegative physical support and heat weights."""
    for values in arrays:
        assert np.isfinite(values).all()
        assert (np.asarray(values) >= 0).all()


def validate_population_summaries(table) -> None:
    """Keep coverage-specific population rows unique and finite."""
    assert set(table) == {"population_kind", "region_id", "country_id", "population"}
    assert not table.duplicated(["population_kind", "region_id"]).any()
    assert table.population_kind.isin(
        ["regions", "floor_reference", "count_reference"]
    ).all()
    assert table.region_id.notna().all()
    assert table.country_id.str.fullmatch(r"[A-Z]{3}").all()
    validate_nonnegative(table.population)


def validate_conserved_total(actual, expected) -> None:
    """Allow floating-point summation error, not lost regional allocation."""
    np.testing.assert_allclose(actual, expected, rtol=1e-10, atol=1e-8)


def validate_population_allocation(population, total, population_total) -> None:
    """Require a usable denominator whenever positive population is allocated."""
    validate_nonnegative(population, total, population_total)
    assert population.sum() <= population_total or np.isclose(
        population.sum(), population_total, rtol=1e-10, atol=1e-8
    )
    if total > 0 and population.sum() > 0:
        assert population_total > 0


def validate_floor_area_allocation(support, remaining, proxy, total) -> None:
    """Require footprint support for the part not allocated to population."""
    validate_nonnegative(support, remaining, proxy, total)
    validate_conserved_total(remaining + proxy, total)
    assert support > 0 or np.isclose(remaining, 0, rtol=0, atol=1e-8)


def validate_sector_count_ratios(buildings, proxy_floor_area, ratios, sector) -> None:
    """Require positive count estimates wherever a sector has support."""
    share, density = ratios[f"{sector}_share"], ratios[f"{sector}_per_person"]
    validate_nonnegative(share, density)
    if buildings.floor_area_m2.sum() > 0:
        assert share > 0
    if proxy_floor_area.sum() > 0:
        assert density > 0


def validate_heat_normalization(total, population_total, share, reference, age) -> None:
    """Reject missing eligible population rather than silently changing the blend."""
    validate_nonnegative(total, population_total, share, reference, age)
    assert share <= 1
    assert reference > 0
    assert age > 0
    if total > 0 and share > 0:
        assert population_total > 0, "Positive heat blend requires eligible population"


def validate_raster_contract(raster, bands, units, *, check_values=True) -> None:
    """Validate persisted support metadata and physical values at I/O boundaries."""
    assert raster.count == len(bands)
    assert raster.descriptions == tuple(bands)
    assert raster.units == tuple(units)
    assert raster.crs.is_projected
    assert np.allclose(raster.res, (100, 100))
    if check_values:
        for _, window in raster.block_windows(1):
            validate_nonnegative(raster.read(window=window))


def validate_aligned_rasters(first, second) -> None:
    """Require identical cell locations for joint raster contracts."""
    assert first.crs == second.crs
    assert first.transform == second.transform
    assert first.shape == second.shape


def validate_heat_building_support(heat, counts, floor=None, commercial=None) -> None:
    """Require buildings and, regionally, residential floor area under heat."""
    validate_raster_contract(
        heat,
        ("residential_space_heat_weight",),
        ("weighted_m2/ha",),
        check_values=False,
    )
    validate_building_count_raster(counts, check_values=False)
    validate_aligned_rasters(heat, counts)
    if floor is not None:
        validate_raster_contract(
            floor, ("residential", "commercial"), ("m2/ha", "m2/ha"), check_values=False
        )
        validate_aligned_rasters(heat, floor)
    if commercial is not None:
        validate_raster_contract(
            commercial,
            ("commercial_space_heat_weight",),
            ("m2/ha",),
            check_values=False,
        )
        validate_aligned_rasters(heat, commercial)
    for _, window in heat.block_windows(1):
        values = heat.read(1, window=window)
        buildings = counts.read(1, window=window)
        validate_nonnegative(values, buildings)
        positive = values > 0
        assert (buildings[positive] > 0).all()
        if floor is not None:
            floor_values = floor.read(window=window)
            validate_nonnegative(floor_values)
            assert (floor_values[0][positive] > 0).all()
        if commercial is not None:
            commercial_values = commercial.read(1, window=window)
            validate_nonnegative(commercial_values)
            assert (buildings[commercial_values > 0] > 0).all()


def validate_commercial_space_heat_weight(heat, counts) -> None:
    """Require unweighted commercial floor area on the building-count grid."""
    validate_raster_contract(
        heat, ("commercial_space_heat_weight",), ("m2/ha",), check_values=False
    )
    validate_aligned_rasters(heat, counts)
    for _, window in heat.block_windows(1):
        values = heat.read(1, window=window)
        buildings = counts.read(1, window=window)
        validate_nonnegative(values, buildings)
        assert (buildings[values > 0] > 0).all()


def validate_support_profiles(profiles) -> None:
    """Require scoped bands to coincide and fit the complete region's grid."""
    floor, full, scoped = (
        profiles[key]
        for key in ("floor_area", "residential_full", "residential_scoped")
    )
    for key in ("crs", "transform", "height", "width"):
        assert floor[key] == scoped[key]
    assert full["crs"] == scoped["crs"]
    transform = ~full["transform"] * scoped["transform"]
    assert transform.a == transform.e == 1
    assert transform.b == transform.d == 0
    assert np.isclose(transform.c, round(transform.c))
    assert np.isclose(transform.f, round(transform.f))
    assert 0 <= round(transform.c) <= full["width"] - scoped["width"]
    assert 0 <= round(transform.f) <= full["height"] - scoped["height"]


def validate_heat_tables(summary, age, statistics) -> None:
    """Validate the indexed regional controls and country heat references."""
    for table in (summary, age, statistics):
        assert table.index.is_unique
    assert summary.index.isin(age.index).all()
    assert summary.country_id.isin(statistics.index).all()
    assert summary.residential_source.isin(["eubucco", "microsoft"]).all()
    validate_nonnegative(
        summary[
            ["residential_floor_area_m2", "valid_floor_area_m2", "weighted_sv_power"]
        ].to_numpy()
    )
    assert {
        "age_factor",
        "age_factor_raw",
        "age_data_available",
        "coverage_fraction",
    } <= set(age)
    validate_nonnegative(
        age.age_factor.to_numpy(), statistics.sv_power_reference.to_numpy()
    )
    assert age.age_factor.gt(0).all()
    assert statistics.sv_power_reference.gt(0).all()


def validate_heat_diagnostics(table) -> None:
    """Require unique regional diagnostics with explicit population eligibility."""
    assert table.region_id.is_unique
    assert table.country_id.str.fullmatch(r"[A-Z]{3}").all()
    assert table.residential_source.isin(["eubucco", "microsoft"]).all()
    validate_nonnegative(
        table[
            ["eligible_population", "excluded_population", "raw_heat_weight"]
        ].to_numpy()
    )
