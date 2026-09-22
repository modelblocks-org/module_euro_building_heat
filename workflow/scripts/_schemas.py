"""Schema validation generics."""

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

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


# Source tables retain their reader dtypes; Series[Any] validates values without
# imposing a new dtype or coercing columns.


class EubuccoRegionsSchema(pa.DataFrameModel):
    """Schema for EUBUCCO administrative-region metadata."""

    class Config:
        coerce = False
        strict = False

    region_id: Series[Any] = pa.Field(unique=True, nullable=True)
    geometry: Series[Any] = pa.Field()

    @pa.dataframe_check
    def check_crs(cls, table) -> bool:
        return table.crs is not None and table.crs.to_epsg() == 3035

    @pa.check("region_id")
    def check_region_id(cls, values) -> bool:
        return values.str.fullmatch("[A-Z0-9]+").all()

    @pa.check("geometry")
    def valid_geometry(cls, values) -> bool:
        return values.is_valid.all()


class EubuccoStatsSchema(pa.DataFrameModel):
    """Schema for EUBUCCO NUTS-3 floor-area statistics."""

    class Config:
        coerce = False
        strict = False

    region_id: Series[Any] = pa.Field(unique=True, nullable=True)
    country: Series[Any] = pa.Field(nullable=True)
    n: Series[Any] = pa.Field(ge=0)
    n_type_residential: Series[Any] = pa.Field(ge=0)
    n_subtype_commercial: Series[Any] = pa.Field(ge=0)
    n_subtype_public: Series[Any] = pa.Field(ge=0)
    floor_area_type_residential: Series[Any] = pa.Field(ge=0)
    n_floors_0_2: Series[Any] = pa.Field(ge=0)
    n_floors_2_4: Series[Any] = pa.Field(ge=0)
    n_floors_4_7: Series[Any] = pa.Field(ge=0)
    n_floors_7_inf: Series[Any] = pa.Field(ge=0)
    floor_area_subtype_commercial: Series[Any] = pa.Field(ge=0)
    floor_area_subtype_public: Series[Any] = pa.Field(ge=0)
    geometry: Series[Any] = pa.Field(nullable=True)

    @pa.dataframe_check
    def check_crs(cls, table) -> bool:
        return table.crs is not None and table.crs.to_epsg() == 3035

    @pa.check("country")
    def check_country(cls, values) -> bool:
        return values.str.fullmatch("[A-Z]{2}").all()

    @pa.dataframe_check
    def finite_values(cls, table) -> bool:
        return np.isfinite(
            table[
                [
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
                ]
            ].to_numpy()
        ).all()


class MicrosoftIndexSchema(pa.DataFrameModel):
    """Schema for the columns consumed from Microsoft's tile index."""

    class Config:
        coerce = False
        strict = False

    Location: Series[Any] = pa.Field(nullable=True)
    QuadKey: Series[Any] = pa.Field(nullable=True)
    Url: Series[Any] = pa.Field(nullable=True)

    @pa.check("QuadKey")
    def check_quadkey(cls, values) -> bool:
        return values.str.fullmatch("[0-3]{9}").all()

    @pa.check("Url")
    def https_urls(cls, values) -> bool:
        return values.str.startswith("https://").all()


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
