"""Focused tests for annual heat-demand stage boundaries."""

import importlib
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

SCRIPTS = Path(__file__).parents[2] / "workflow" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.modules.setdefault("xarray", MagicMock())

_jrc = importlib.import_module("_jrc")
annual_energy_balance = importlib.import_module("annual_energy_balance")
final_heat_demand = importlib.import_module("final_heat_demand")
useful_heat = importlib.import_module("useful_heat")


def _baseline(**updates) -> pd.DataFrame:
    values = {
        "carrier_name": ["gas", "gas", "gas", "gas"],
        "sector": ["residential"] * 4,
        "end_use": ["space_heat", "hot_water"] * 2,
        "country_code": ["AUT"] * 4,
        "unit": ["twh"] * 4,
        "energy": ["final_energy"] * 4,
        "year": [2020, 2020, 2022, 2022],
        "value": [3.0, 1.0, 1.0, 3.0],
    }
    values.update(updates)
    return pd.DataFrame(values)


def _balance(countries=("AUT",), values=(8.0,)) -> pd.DataFrame:
    index = pd.MultiIndex.from_tuples(
        [("gas", country) for country in countries],
        names=["carrier_name", "country_code"],
    )
    return pd.DataFrame(values, index=index, columns=pd.Index([2021], name="year"))


def _wide_demand(countries=("AUT",), space=(6.0,), water=(2.0,)) -> pd.DataFrame:
    tuples = []
    values = []
    for country, space_value, water_value in zip(countries, space, water):
        tuples.extend(
            [
                ("space_heat", "gas", country, "household"),
                ("hot_water", "gas", country, "household"),
            ]
        )
        values.extend([[space_value], [water_value]])
    index = pd.MultiIndex.from_tuples(
        tuples, names=["end_use", "carrier_name", "country_code", "cat_name"]
    )
    return pd.DataFrame(values, index=index, columns=pd.Index([2021], name="year"))


def test_jrc_output_is_canonical_twh(monkeypatch):
    """JRC adapters should expose TWh and canonical residential carriers."""
    index = pd.MultiIndex.from_tuples(
        [("renewable_heat", "space_heat", "AT", "ktoe", "final_energy")],
        names=["carrier_name", "end_use", "country_code", "unit", "energy"],
    )
    parsed = pd.DataFrame([[2.0]], index=index, columns=pd.Index([2020], name="year"))
    monkeypatch.setattr(_jrc, "_process_country_dataset", lambda *_: parsed)

    result = _jrc.get_sector_data(["unused.xlsx"], "RES", "final_energy")

    assert result.loc[0, "unit"] == "twh"
    assert result.loc[0, "carrier_name"] == "solar_thermal"
    assert result.loc[0, "value"] == pytest.approx(2 * _jrc.KTOE_TO_TWH)


def test_baseline_schema_rejects_noncanonical_units():
    """The shared baseline schema should enforce TWh inputs."""
    try:
        import _schemas
        from pandera.errors import SchemaError
    except ImportError:
        pytest.skip("The default test environment lacks Pandera GeoSeries typing.")
    baseline = _baseline().iloc[[0]].copy()
    _schemas.BaselineSchema.validate(baseline)
    with pytest.raises(SchemaError, match="unit"):
        _schemas.BaselineSchema.validate(baseline.assign(unit="ktoe"))


def test_energy_balance_slicing_requires_no_conversion():
    """Final-demand preparation should preserve normalized balance values."""
    index = pd.MultiIndex.from_tuples(
        [("FC_OTH_HH_E", "E7000", "twh", "AUT", 2020)],
        names=["cat_code", "carrier_code", "unit", "country", "year"],
    )
    balance = pd.Series([3.5], index=index)
    carriers = pd.DataFrame(
        {"residential_carrier_name": ["electricity"]}, index=pd.Index(["E7000"])
    )

    result = final_heat_demand._slice_energy_balance_by_sector(
        balance, carriers, ["FC_OTH_HH_E"], "residential"
    )

    assert result.loc[("electricity", "AUT"), 2020] == pytest.approx(3.5)


def test_ecuk_replaces_gbr_sector_year_in_energy_balance(tmp_path):
    """ECUK totals should replace, rather than add to, overlapping UK balances."""
    index_names = ["cat_code", "carrier_code", "unit", "country", "year"]
    existing = pd.Series(
        [3600.0, 7200.0],
        index=pd.MultiIndex.from_tuples(
            [
                ("FC_OTH_HH_E", "G3000", "TJ", "GBR", 2020),
                ("FC_OTH_HH_E", "G3000", "TJ", "AUT", 2020),
            ],
            names=index_names,
        ),
    )
    residential = (
        _baseline().iloc[:2].assign(country_code="GBR", year=2020, value=[3.0, 1.0])
    )
    services = residential.assign(sector="services", value=[2.0, 2.0])
    paths = []
    for sector, baseline in (("residential", residential), ("services", services)):
        path = tmp_path / f"{sector}.csv"
        baseline.to_csv(path, index=False)
        paths.append(path)
    carrier_names = pd.DataFrame(
        {"residential_carrier_name": ["gas"], "services_carrier_name": ["gas"]},
        index=pd.Index(["G3000"], name="carrier_code"),
    )

    result = annual_energy_balance._overlay_gbr_energy_balance(
        existing, paths, carrier_names, index_names
    )

    assert result.loc[("FC_OTH_HH_E", "G3000", "TJ", "GBR", 2020)] == pytest.approx(
        4 * annual_energy_balance.TWH_TO_TJ
    )
    assert result.loc[("FC_OTH_CP_E", "G3000", "TJ", "GBR", 2020)] == pytest.approx(
        4 * annual_energy_balance.TWH_TO_TJ
    )
    assert result.loc[("FC_OTH_HH_E", "G3000", "TJ", "AUT", 2020)] == pytest.approx(
        7200
    )


def test_calculate_shares_and_exact_year_match():
    """Observed shares should sum to one and retain an exact source year."""
    shares = final_heat_demand.calculate_end_use_shares(_baseline())
    matched = final_heat_demand.match_model_years(
        shares, [2020], ["carrier_name", "country_code"]
    )

    assert (
        shares.groupby(level=["carrier_name", "country_code", "year"]).sum().eq(1).all()
    )
    assert matched.loc[("gas", "space_heat", "AUT", 2020)] == pytest.approx(0.75)


def test_nearest_year_tie_uses_earlier_year():
    """Equidistant source years should resolve deterministically to the earlier."""
    shares = final_heat_demand.calculate_end_use_shares(_baseline())
    matched = final_heat_demand.match_model_years(
        shares, [2021], ["carrier_name", "country_code"]
    )

    assert matched.loc[("gas", "space_heat", "AUT", 2021)] == pytest.approx(0.75)


def test_nearest_year_uses_closest_observation():
    """A non-exact model year should use its closest observed source year."""
    shares = final_heat_demand.calculate_end_use_shares(_baseline())
    matched = final_heat_demand.match_model_years(
        shares, [2023], ["carrier_name", "country_code"]
    )

    assert matched.loc[("gas", "space_heat", "AUT", 2023)] == pytest.approx(0.25)


def test_scaled_demand_sums_to_balance():
    """Scaled end uses should conserve each carrier balance total."""
    shares = final_heat_demand.calculate_end_use_shares(_baseline())
    shares = final_heat_demand.match_model_years(
        shares, [2021], ["carrier_name", "country_code"]
    )
    result = final_heat_demand.scale_to_energy_balance(shares, _balance())

    assert result.groupby(level=["carrier_name", "country_code", "year"]).sum().iloc[
        0
    ] == pytest.approx(8)


def test_official_demand_uses_nearest_year_and_replaces_country_year(tmp_path):
    """ECUK-like totals should replace a complete calculated UK year."""
    official_source = _baseline(
        country_code=["GBR"] * 4,
        year=[2015, 2015, 2020, 2020],
        value=[1.0, 3.0, 2.0, 6.0],
    )
    official_path = tmp_path / "ecuk.csv"
    official_source.to_csv(official_path, index=False)

    official = final_heat_demand.read_official_final_demand([official_path], [2021])
    calculated = pd.Series(
        [9.0, 7.0, 5.0],
        index=pd.MultiIndex.from_tuples(
            [
                ("gas", "space_heat", "GBR", 2021),
                ("gas", "cooking", "GBR", 2021),
                ("gas", "space_heat", "AUT", 2021),
            ],
            names=["carrier_name", "end_use", "country_code", "year"],
        ),
        name="value",
    )

    result = final_heat_demand.overlay_official_final_demand(calculated, official)

    assert result.loc[("gas", "space_heat", "GBR", 2021)] == pytest.approx(2)
    assert result.loc[("gas", "hot_water", "GBR", 2021)] == pytest.approx(6)
    assert ("gas", "cooking", "GBR", 2021) not in result.index
    assert result.loc[("gas", "space_heat", "AUT", 2021)] == pytest.approx(5)


@pytest.mark.parametrize("country", ["GBR", "CHE"])
def test_official_demand_supplies_country_without_model_year_balance(country):
    """National statistics should not depend on a model-year balance row."""
    calculated = pd.Series(
        dtype=float,
        index=pd.MultiIndex.from_arrays(
            [[], [], [], []], names=["carrier_name", "end_use", "country_code", "year"]
        ),
        name="value",
    )
    official = pd.Series(
        [8.0],
        index=pd.MultiIndex.from_tuples(
            [("gas", "space_heat", country, 2021)], names=calculated.index.names
        ),
        name="value",
    )

    result = final_heat_demand.overlay_official_final_demand(calculated, official)

    assert result.loc[("gas", "space_heat", country, 2021)] == pytest.approx(8)


def test_proxy_can_use_overlaid_official_reference():
    """A proxied country should be able to use official GBR end-use shares."""
    official = pd.Series(
        [6.0, 2.0],
        index=pd.MultiIndex.from_tuples(
            [("gas", "space_heat", "GBR", 2021), ("gas", "hot_water", "GBR", 2021)],
            names=["carrier_name", "end_use", "country_code", "year"],
        ),
        name="value",
    )
    demand = final_heat_demand._to_sector_wide(
        final_heat_demand.overlay_official_final_demand(
            pd.Series(dtype=float, index=official.index[:0], name="value"), official
        ),
        "residential",
        [2021],
    )

    result = final_heat_demand.proxy_end_use_demand(
        demand,
        _balance(("GBR", "MCO"), (0.0, 4.0)),
        {"MCO": ["GBR"]},
        ["GBR", "MCO"],
        "residential",
    )

    assert result.loc[("space_heat", "gas", "MCO", "household"), 2021] == pytest.approx(
        3
    )


@pytest.mark.parametrize(
    ("references", "expected"), [(["AUT"], 4.0), (["AUT", "BEL"], 3.0)]
)
def test_energy_balance_proxy(references, expected):
    """Balance proxying should average reference per-capita intensities."""
    balance = _balance(("AUT", "BEL"), (8.0, 8.0))
    population = pd.Series({"AUT": 4.0, "BEL": 8.0, "LIE": 2.0})

    result = final_heat_demand.proxy_energy_balance(
        balance, population, {"LIE": references}, ["AUT", "BEL", "LIE"]
    )

    assert result.loc[("gas", "LIE"), 2021] == pytest.approx(expected)


@pytest.mark.parametrize(
    ("references", "expected_space"), [(["AUT"], 6.0), (["AUT", "BEL"], 5.0)]
)
def test_end_use_proxy(references, expected_space):
    """End-use proxying should average reference shares before scaling."""
    demand = _wide_demand(("AUT", "BEL"), (6.0, 4.0), (2.0, 4.0))
    balance = _balance(("AUT", "BEL", "LIE"), (8.0, 8.0, 8.0))

    result = final_heat_demand.proxy_end_use_demand(
        demand, balance, {"LIE": references}, ["AUT", "BEL", "LIE"], "residential"
    )

    assert result.loc[("space_heat", "gas", "LIE", "household"), 2021] == pytest.approx(
        expected_space
    )


def test_end_use_proxy_treats_ambient_only_country_as_missing():
    """Synthetic ambient heat should not prevent end-use proxying."""
    demand = _wide_demand()
    ambient_index = pd.MultiIndex.from_tuples(
        [
            ("space_heat", "ambient_heat", "AUT", "household"),
            ("space_heat", "ambient_heat", "LIE", "household"),
        ],
        names=demand.index.names,
    )
    ambient = pd.DataFrame([[1.0], [2.0]], index=ambient_index, columns=demand.columns)
    demand = pd.concat([demand, ambient])
    balance_index = pd.MultiIndex.from_tuples(
        [
            ("gas", "AUT"),
            ("gas", "LIE"),
            ("ambient_heat", "AUT"),
            ("ambient_heat", "LIE"),
        ],
        names=["carrier_name", "country_code"],
    )
    balance = pd.DataFrame(
        [8.0, 8.0, 1.0, 2.0], index=balance_index, columns=pd.Index([2021], name="year")
    )

    result = final_heat_demand.proxy_end_use_demand(
        demand, balance, {"LIE": ["AUT"]}, ["AUT", "LIE"], "residential"
    )

    assert result.index.is_unique
    assert result.loc[("space_heat", "gas", "LIE", "household"), 2021] == pytest.approx(
        6
    )
    assert result.loc[("hot_water", "gas", "LIE", "household"), 2021] == pytest.approx(
        2
    )
    assert result.loc[
        ("space_heat", "ambient_heat", "LIE", "household"), 2021
    ] == pytest.approx(2)


def test_missing_end_use_proxy_has_clear_error():
    """A missing required proxy configuration should produce a clear error."""
    with pytest.raises(ValueError, match="no proxy is configured"):
        final_heat_demand.proxy_end_use_demand(
            _wide_demand(),
            _balance(("AUT", "LIE"), (8.0, 8.0)),
            {},
            ["AUT", "LIE"],
            "residential",
        )


def test_ambient_heat_is_allocated_to_space_heat():
    """Unsplit ambient heat should be allocated entirely to space heating."""
    balance = pd.DataFrame(
        [3.0],
        index=pd.MultiIndex.from_tuples(
            [("ambient_heat", "AUT")], names=["carrier_name", "country_code"]
        ),
        columns=pd.Index([2021], name="year"),
    )
    empty = pd.Series(
        dtype=float,
        index=pd.MultiIndex.from_arrays(
            [[], [], [], []], names=["carrier_name", "end_use", "country_code", "year"]
        ),
    )

    result = final_heat_demand._allocate_ambient_heat(empty, balance)

    assert result.loc[("ambient_heat", "space_heat", "AUT", 2021)] == pytest.approx(3)


def test_useful_heat_multiplies_efficiencies_and_aggregates_carriers():
    """Useful heat should apply efficiencies before aggregating carriers."""
    index = pd.MultiIndex.from_tuples(
        [
            ("space_heat", "gas", "AUT", "household"),
            ("space_heat", "electricity", "AUT", "household"),
        ],
        names=["end_use", "carrier_name", "country_code", "cat_name"],
    )
    final = pd.DataFrame([[10.0], [4.0]], index=index, columns=[2020])

    result = useful_heat.calculate_useful_heat_from_final(
        final, {"space_heat": {"gas-eff": 0.5, "electricity-eff": 0.75}}
    )

    assert result.loc[("AUT", 2020), ("space_heat", "household")] == pytest.approx(8)


def test_published_precedence_only_replaces_supplied_values():
    """Published values should override only matching calculated entries."""
    index = pd.MultiIndex.from_tuples(
        [("AUT", 2020), ("BEL", 2020)], names=["country_code", "year"]
    )
    columns = pd.MultiIndex.from_tuples(
        [("space_heat", "household")], names=["end_use", "cat_name"]
    )
    calculated = pd.DataFrame([5.0, 6.0], index=index, columns=columns)
    published = pd.DataFrame([7.0], index=index[:1], columns=columns)

    result = useful_heat.apply_published_precedence(calculated, published)

    assert result.iloc[:, 0].tolist() == [7.0, 6.0]


@pytest.mark.parametrize(
    ("source", "expected"), [("actual", 7.0), ("calculate_all", 5.0)]
)
def test_useful_source_without_household_intermediate(tmp_path, source, expected):
    """Useful heat should need no household-balance intermediate file."""
    final_path = tmp_path / "final.csv"
    useful_path = tmp_path / "published.csv"
    output_path = tmp_path / "useful.csv"
    final_heat_demand._write_final_demand(
        _wide_demand(space=(10.0,), water=(2.0,)), final_path, ["AUT"]
    )
    published = pd.DataFrame(
        {
            "carrier_name": ["gas"],
            "sector": ["residential"],
            "end_use": ["space_heat"],
            "country_code": ["AUT"],
            "unit": ["twh"],
            "energy": ["useful_energy"],
            "year": [2020],
            "value": [7.0],
        }
    )
    published.to_csv(useful_path, index=False)

    useful_heat.calculate_useful_heat(
        final_path,
        [useful_path],
        [useful_path],
        {"space_heat": {"gas-eff": 0.5}, "hot_water": {"gas-eff": 0.5}},
        source,
        [2021],
        output_path,
    )

    result = pd.read_csv(output_path).set_index(
        ["country_code", "year", "end_use", "cat_name"]
    )["value"]
    assert result.loc[("AUT", 2021, "space_heat", "household")] == pytest.approx(
        expected
    )
    assert not result.isna().any()
