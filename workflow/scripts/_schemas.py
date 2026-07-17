"""Schema validation generics."""

from collections.abc import Iterable

import _jrc
import pandas as pd
from pandera import pandas as pa
from pandera.typing.geopandas import GeoSeries
from pandera.typing.pandas import Series
from shapely.geometry import MultiPolygon, Polygon

ENERGY_TYPES: set[str] = {"useful_energy", "final_energy"}
SECTORS: set[str] = {"residential", "services"}


class ShapesSchema(pa.DataFrameModel):
    """Schema for geographic shapes."""

    class Config:
        coerce = True
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


class JRCIDEESSchema(pa.DataFrameModel):
    """Schema for tertiary JRC data."""

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

    carrier_name: Series[str] = pa.Field(isin=set(_jrc.CARRIERS.values()))
    "Name of the carrier."
    sector: Series[str] = pa.Field(isin=SECTORS)
    "Energy sector."
    end_use: Series[str] = pa.Field(isin=set(_jrc.END_USES.values()))
    "End use for the carrier."
    country_code: Series[str]
    "Country code."
    unit: Series[str]
    "Unit of the value."
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
