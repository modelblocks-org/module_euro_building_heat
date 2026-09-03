"""General utility functions."""

import pycountry

EUROSTAT_TO_ALPHA3 = {"EL": "GRC", "UK": "GBR"}


def eurostat_to_alpha3(code: str) -> str:
    """Convert a Eurostat alpha-2-like country code to ISO alpha-3."""
    if code in EUROSTAT_TO_ALPHA3:
        return EUROSTAT_TO_ALPHA3[code]
    return pycountry.countries.get(alpha_2=code).alpha_3
