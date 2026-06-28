"""Schema validation generics."""

from pandera import pandas as pa
from pandera.typing.geopandas import GeoSeries
from pandera.typing.pandas import Series
from shapely.geometry import MultiPolygon, Polygon


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
