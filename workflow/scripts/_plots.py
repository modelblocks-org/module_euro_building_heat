"""Plotting utilities."""

import math
from pathlib import Path

import geopandas as gpd
import inflection
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from cmap import Colormap
from matplotlib.axes import Axes
from matplotlib.colors import Normalize
from matplotlib.figure import Figure


def draw_empty(ax: Axes, title: str, message: str = "No data available") -> None:
    """Render a placeholder on an axis when a plot has no data."""
    ax.text(0.5, 0.5, message, ha="center", va="center", fontsize=12, alpha=0.7)
    ax.set_title(title)
    ax.set_axis_off()


def plot_heat_demand_timeseries(
    demand: pd.DataFrame, output_path: str | Path, rolling_days: int = 1
) -> None:
    """Plot per-unit rolling mean demand profiles for every shape."""
    daily_demand = demand.sort_index().resample("D").sum(min_count=1)
    mean_daily_demand = daily_demand.mean().replace(0, np.nan)
    per_unit_demand = daily_demand.div(mean_daily_demand)
    smoothed_demand = per_unit_demand.rolling(
        window=rolling_days, center=True, min_periods=1
    ).mean()

    n_shapes = max(1, len(smoothed_demand.columns))
    row_height = 1.6
    fig_height = max(3.0, 1.0 + n_shapes * row_height)
    fig, axes = plt.subplots(
        nrows=n_shapes, ncols=1, figsize=(10, fig_height), squeeze=False
    )
    axes = axes.ravel()
    fig.subplots_adjust(
        left=0.1,
        right=0.99,
        bottom=min(0.05, 0.4 / fig_height),
        top=1 - min(0.05, 0.8 / fig_height),
        hspace=0.55,
    )

    fig.suptitle(
        f"Per-unit heat demand factors ({rolling_days}-day rolling mean)",
        fontsize="x-large",
    )
    if len(smoothed_demand.columns) == 0:
        draw_empty(axes[0], "No heat demand shapes")
    else:
        for ax, shape_id in zip(axes, smoothed_demand.columns):
            series = smoothed_demand[shape_id].dropna()
            if series.empty:
                draw_empty(ax, str(shape_id))
            else:
                ax.plot(series.index, series)
                ax.set_title(
                    str(shape_id), loc="left", fontsize="medium", fontweight="bold"
                )
                ax.margins(x=0)
                ax.set_ylabel("Per unit")
            ax.set_xlabel("")

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output)
    plt.close(fig)


def plot_annual_heat_demand_choropleth(
    shapes: gpd.GeoDataFrame, annual_demand: pd.DataFrame, output_path: str | Path
) -> None:
    """Plot total annual useful heat demand in every user-provided shape."""
    demand_by_year = annual_demand.groupby(level="year").sum().sort_index()

    shapes = shapes.copy()
    shapes["shape_id"] = (
        shapes["shape_id"].astype(str).str.replace(".", "-", regex=False)
    )
    common_ids = demand_by_year.columns.intersection(shapes["shape_id"])

    demand_by_year = demand_by_year.loc[:, common_ids]
    shapes = shapes[shapes["shape_id"].isin(common_ids)].to_crs("EPSG:4326")
    years = demand_by_year.index.tolist()
    n_columns = min(3, len(years))
    n_rows = math.ceil(len(years) / n_columns)
    fig, axes = plt.subplots(
        nrows=n_rows,
        ncols=n_columns,
        figsize=(6 * n_columns, 5 * n_rows),
        squeeze=False,
        layout="constrained",
    )
    axes_flat = axes.ravel()

    values = demand_by_year.to_numpy(dtype=float)
    finite_values = values[np.isfinite(values)]
    maximum = float(finite_values.max()) if finite_values.size else 0.0
    norm = Normalize(vmin=0, vmax=maximum if maximum > 0 else 1)
    cmap = "viridis"

    visible_axes = []
    for ax, year in zip(axes_flat, years):
        demand_for_year = demand_by_year.loc[year].rename("heat_demand_twh")
        plot_data = shapes.merge(
            demand_for_year, left_on="shape_id", right_index=True, how="left"
        )
        plot_data.plot(
            column="heat_demand_twh",
            ax=ax,
            cmap=cmap,
            norm=norm,
            edgecolor="white",
            linewidth=0.25,
            missing_kwds={"color": "lightgrey"},
        )
        ax.set_title(str(year), fontsize="large", fontweight="bold")
        ax.set_axis_off()
        visible_axes.append(ax)

    for ax in axes_flat[len(years) :]:
        ax.set_visible(False)

    colorbar = fig.colorbar(
        plt.cm.ScalarMappable(norm=norm, cmap=cmap),
        ax=visible_axes,
        location="bottom",
        shrink=0.7,
        aspect=35,
        pad=0.02,
    )
    colorbar.set_label("Annual useful heat demand (TWh)")
    fig.suptitle("Annual useful heat demand by shape", fontsize="x-large")

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, bbox_inches="tight", pad_inches="layout")
    plt.close(fig)


def _histogram_legend(fig: Figure, axes_flat) -> None:
    fig.legends.clear()
    handles, labels = axes_flat[0].get_legend_handles_labels()
    fig.legend(handles[::-1], labels[::-1], loc="center left", bbox_to_anchor=(1, 0.5))


def _histogram_axis_formatting(
    ax: Axes, container: str, x_label: str, y_label: str, x_nbins: int
):
    ax.set_title(container)
    ax.set_xlabel(x_label)
    ax.set_ylabel(y_label)
    ax.locator_params(axis="x", nbins=x_nbins)
    ax.minorticks_off()


def _histogram_subplot(n_rows: int, n_columns: int) -> tuple[Figure, np.ndarray]:
    return plt.subplots(
        n_rows,
        n_columns,
        figsize=(n_columns * 6, n_rows * 4),
        constrained_layout=True,
        squeeze=False,
    )


# FIXME: color ordering should be deterministic
def plot_bar_histogram(
    df: pd.DataFrame,
    stacked_col: str,
    *,
    container_col: str,
    year_col: str = "year",
    value_col: str = "value",
    cmap: str = "petroff:petroff6",
    unit: str | None = None,
    x_nbins: int = 10,
    format_container: bool = True,
    legend: bool = True,
    fig=None,
    axes=None,
) -> tuple[Figure, Axes]:
    """Plot stacked values, optionally supplementing an existing histogram."""
    containers = sorted(df[container_col].unique())
    years = range(df[year_col].min(), df[year_col].max() + 1)

    labels_raw = df[stacked_col].unique()
    labels_human = {i: inflection.humanize(i) for i in labels_raw}

    if axes is None:
        n_columns = 2 if len(containers) > 1 else 1
        n_rows = math.ceil(len(containers) / n_columns)
        fig, axes = _histogram_subplot(n_rows, n_columns)
    axes_flat = np.atleast_1d(axes).ravel()
    if fig is None:
        fig = axes_flat[0].figure

    x_label = inflection.humanize(year_col)
    y_label = inflection.humanize(stacked_col)
    if unit:
        y_label += f" ({unit})"

    for ax, container in zip(axes_flat, containers):
        container_df = df.loc[df[container_col].eq(container)]
        final_energy = (
            container_df.groupby([year_col, stacked_col])[value_col]
            .sum()
            .unstack(stacked_col, fill_value=0)
            .reindex(index=years, columns=labels_raw, fill_value=0)
        )
        final_energy.rename(columns=labels_human).plot.bar(
            stacked=True,
            ax=ax,
            cmap=Colormap(cmap).to_mpl(),
            legend=False,
            rot=45,
            lw=0.5,
            edgecolor="black",
        )

        if format_container:
            _histogram_axis_formatting(ax, container, x_label, y_label, x_nbins)

    for ax in axes_flat[len(containers) :]:
        ax.set_visible(False)

    if legend:
        _histogram_legend(fig, axes_flat)
    return fig, axes


def plot_value_histogram(
    df: pd.DataFrame,
    *,
    container_col: str,
    year_col: str = "year",
    value_col: str = "value",
    label: str | None = None,
    unit: str | None = None,
    x_nbins: int = 10,
    format_container: bool = True,
    legend: bool = True,
    fig=None,
    axes=None,
) -> tuple[Figure, Axes]:
    """Plot a single value, optionally supplementing an existing histogram."""
    containers = sorted(df[container_col].unique())
    years = range(df[year_col].min(), df[year_col].max() + 1)

    if axes is None:
        n_columns = 2 if len(containers) > 1 else 1
        n_rows = math.ceil(len(containers) / n_columns)
        fig, axes = _histogram_subplot(n_rows, n_columns)
    axes_flat = np.atleast_1d(axes).ravel()
    if fig is None:
        fig = axes_flat[0].figure

    x_label = inflection.humanize(year_col)
    value_label = inflection.humanize(label or value_col)
    y_label = value_label
    if unit:
        y_label += f" ({inflection.humanize(unit)})"

    for ax, container in zip(axes_flat, containers):
        container_df = df.loc[df[container_col].eq(container)]
        values = container_df.groupby(year_col)[value_col].sum().reindex(years)
        positions = np.arange(len(values))
        ax.scatter(
            positions,
            values,
            marker="x",
            color="black",
            linewidth=1.5,
            label=value_label,
            zorder=3,
        )
        ax.set_xticks(positions, values.index, rotation=45)

        if format_container:
            _histogram_axis_formatting(ax, container, x_label, y_label, x_nbins)

    for ax in axes_flat[len(containers) :]:
        ax.set_visible(False)

    if legend:
        _histogram_legend(fig, axes_flat)
    return fig, axes
