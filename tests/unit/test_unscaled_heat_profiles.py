"""Focused tests for lazy generation of unscaled hourly heat profiles."""

import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

xr = pytest.importorskip("xarray")
dask = pytest.importorskip("dask")

SCRIPTS = Path(__file__).parents[2] / "workflow" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import unscaled_heat_profiles as profiles  # noqa: E402


def _pandas_reference_temperature(temperature):
    """Reproduce the former eager implementation on a small in-memory array."""
    daily_average = (
        temperature.to_series()
        .unstack("time")
        .T.resample("1D")
        .mean()
        .stack()
        .to_xarray()
    )
    return sum(
        (0.5**i) * daily_average.shift(time=i).bfill("time") for i in range(4)
    ) / sum(0.5**i for i in range(4))


def _weather_data():
    time = pd.date_range("2017-12-25", "2019-01-05 23:00", freq="h")
    phase = np.arange(time.size, dtype=np.float32) / 240
    temperature = np.stack([5 + np.sin(phase), 10 + np.cos(phase)]).astype(
        np.float32
    )
    wind = np.stack(
        [np.full(time.size, 3, dtype=np.float32), np.full(time.size, 6)]
    ).astype(np.float32)
    coordinates = {"site": [0, 1], "time": time}
    return (
        xr.Dataset(
            {"temperature": (("site", "time"), temperature)},
            coords=coordinates,
            attrs={"unit": "degrees C"},
        ),
        xr.Dataset(
            {"wind10m": (("site", "time"), wind)},
            coords=coordinates,
            attrs={"unit": "m/s"},
        ),
    )


def _daily_parameters():
    buildings = ["SFH", "MFH", "COM"]
    columns = pd.MultiIndex.from_product(
        [buildings, ["normal", "windy"]], names=["building", "windiness"]
    )
    values = pd.Series(
        {
            "A": 1.0,
            "B": -37.0,
            "C": 5.67,
            "D": 0.1,
            "m_s": 0.01,
            "b_s": 0.5,
            "m_w": 0.005,
            "b_w": 0.2,
        }
    )
    return pd.DataFrame(
        np.tile(values.to_numpy()[:, None], (1, len(columns))),
        index=values.index,
        columns=columns,
    )


def _hourly_parameters():
    return xr.DataArray(
        np.full((3, 24, 10, 7), 1 / 24, dtype=np.float32),
        dims=("building", "hour", "temperature", "weekday"),
        coords={
            "building": ["SFH", "MFH", "COM"],
            "hour": np.arange(24),
            "temperature": np.arange(-15, 35, 5),
            "weekday": np.arange(7),
        },
    )


def test_reference_temperature_matches_pandas_implementation():
    hourly = pd.date_range("2018-01-01", periods=8 * 24, freq="h")
    temperature = xr.DataArray(
        np.arange(2 * hourly.size, dtype=np.float32).reshape(2, hourly.size),
        dims=("site", "time"),
        coords={"site": [0, 1], "time": hourly},
    )

    expected = _pandas_reference_temperature(temperature)
    actual = profiles.get_reference_temperature(temperature)

    xr.testing.assert_allclose(actual, expected)
    assert actual.dtype == expected.dtype


def test_profile_calculation_remains_lazy():
    temperature, wind = _weather_data()
    temperature = temperature.chunk({"site": 1, "time": 168})
    wind = wind.chunk({"site": 1, "time": 168})

    reference = profiles.get_reference_temperature(temperature["temperature"])
    result = profiles._get_unscaled_heat_profile_for_weather_year(
        temperature,
        wind,
        _daily_parameters(),
        _hourly_parameters(),
        2018,
    )

    assert dask.is_dask_collection(reference.data)
    assert all(
        dask.is_dask_collection(array.data) for array in result.data_vars.values()
    )
    assert set(result.data_vars) == {"space_heat", "hot_water"}
    assert result.sizes == {"building": 3, "site": 2, "time": 8760}


def test_daily_profiles_do_not_evaluate_masked_wind_categories():
    temperature = xr.DataArray(
        [[5.0], [10.0], [15.0]],
        dims=("site", "time"),
        coords={"time": pd.date_range("2018-01-01", periods=1)},
    ).chunk({"site": 1})
    wind = xr.DataArray(
        [3.0, 6.0, np.nan], dims="site", coords={"site": temperature.site}
    ).chunk({"site": 1})

    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        result = profiles.when2heat_daily(
            temperature, wind, _daily_parameters(), profiles._heat_function
        ).compute()

    assert result.sel(site=[0, 1]).notnull().all()
    assert result.sel(site=2).isnull().all()


def test_space_heating_is_zero_at_and_above_bdew_temperature_bound():
    reference_temperature = xr.DataArray(
        [[40.0, 42.0]],
        dims=("site", "time"),
        coords={"site": [0], "time": pd.date_range("2018-01-01", periods=2)},
    ).chunk({"site": 1})
    wind = xr.DataArray([3.0], dims="site", coords={"site": [0]}).chunk(
        {"site": 1}
    )
    daily_parameters = _daily_parameters()
    hourly_parameters = _hourly_parameters()

    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        daily_total = profiles.when2heat_daily(
            reference_temperature,
            wind,
            daily_parameters,
            profiles._heat_function,
        )
        daily_hot_water = profiles.when2heat_daily(
            reference_temperature,
            wind,
            daily_parameters,
            profiles._water_function,
        )
        hourly_total = profiles.get_hourly_heat_profiles(
            reference_temperature, daily_total, hourly_parameters
        )
        hourly_hot_water = profiles.get_hourly_heat_profiles(
            reference_temperature.clip(min=profiles.HOT_WATER_REF_TEMP),
            daily_hot_water,
            hourly_parameters,
        )
        hourly_space = (hourly_total - hourly_hot_water).clip(min=0).compute()

    assert (hourly_space.sel(time=slice("2018-01-01", "2018-01-02")) == 0).all()
    missing_total = profiles._heat_function(
        xr.DataArray([np.nan], dims="site"),
        daily_parameters[("SFH", "normal")],
    )
    assert missing_total.isnull().all()


def test_chunked_netcdf_inputs_write_equivalent_output(tmp_path, monkeypatch):
    temperature, wind = _weather_data()
    temperature_path = tmp_path / "temperature.nc"
    wind_path = tmp_path / "wind.nc"
    output_path = tmp_path / "profiles.nc"
    input_encoding = {"chunksizes": (1, 168), "zlib": True}
    temperature.to_netcdf(
        temperature_path, encoding={"temperature": input_encoding}
    )
    wind.to_netcdf(wind_path, encoding={"wind10m": input_encoding})

    daily_parameters = _daily_parameters()
    hourly_parameters = _hourly_parameters()
    monkeypatch.setattr(profiles, "read_daily_parameters", lambda _: daily_parameters)
    monkeypatch.setattr(
        profiles, "read_hourly_parameters", lambda **_: hourly_parameters
    )

    with (
        xr.open_dataset(temperature_path, chunks={}) as chunked_temperature,
        xr.open_dataset(wind_path, chunks={}) as chunked_wind,
    ):
        expected = profiles._get_unscaled_heat_profile_for_weather_year(
            chunked_temperature,
            chunked_wind,
            daily_parameters,
            hourly_parameters,
            2018,
        ).compute()

    profiles.get_unscaled_heat_profiles(
        path_to_wind_speed=wind_path,
        path_to_temperature=temperature_path,
        path_to_when2heat_daily="unused",
        path_to_when2heat_hourly_com="unused",
        path_to_when2heat_hourly_mfh="unused",
        path_to_when2heat_hourly_sfh="unused",
        weather_years=[2018],
        out_path=output_path,
    )

    with xr.open_dataset(output_path) as actual:
        xr.testing.assert_allclose(actual.load(), expected)
        assert set(actual.data_vars) == {"space_heat", "hot_water"}
        assert actual.sizes == {"building": 3, "site": 2, "time": 8760}
        assert actual.time.values[0] == np.datetime64("2018-01-01T00:00:00")
        assert actual.time.values[-1] == np.datetime64("2018-12-31T23:00:00")
