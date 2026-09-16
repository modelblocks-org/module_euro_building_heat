"""Schema validation generics."""

from collections.abc import Iterable
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
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


class ResidentialSpaceHeatWeightSchema(pa.DataFrameModel):
    """Schema for space-heating support aggregated to Modelblocks shapes."""

    class Config:
        coerce = True
        strict = True

    shape_id: Series[str] = pa.Field(unique=True)
    country_id: Series[str] = pa.Field(str_length=3)
    weight: Series[float] = pa.Field(ge=0)

    @pa.check("country_id", name="uppercase")
    def check_country_id(cls, country_id: Series[str]) -> Series[bool]:
        return country_id.str.isupper()


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
