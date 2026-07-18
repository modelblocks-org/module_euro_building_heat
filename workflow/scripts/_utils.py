"""General utility functions."""

from typing import Literal

import pycountry

EUROSTAT_TO_ALPHA3 = {"EL": "GRC", "UK": "GBR"}
ALPHA3_TO_EUROSTAT = {v: k for k, v in EUROSTAT_TO_ALPHA3.items()}


def convert_country_code(code: str, to: Literal["alpha3", "eurostat"]):
    """Standardisation of country codes."""
    match to:
        case "alpha3":
            if code in EUROSTAT_TO_ALPHA3:
                country = EUROSTAT_TO_ALPHA3[code]
            else:
                country = pycountry.countries.get(alpha_2=code).alpha_3
        case "eurostat":
            if code in ALPHA3_TO_EUROSTAT:
                country = EUROSTAT_TO_ALPHA3[code]
            else:
                country = pycountry.countries.get(alpha_3=code).alpha_2
        case _:
            raise ValueError(f"Unsupported request {to!r}.")

    return country
