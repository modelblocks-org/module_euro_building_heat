"""Plotting utilities."""

import math
from pathlib import Path
from typing import Any

import geopandas as gpd
import inflection
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rioxarray
from cmap import Colormap
from matplotlib.axes import Axes
from matplotlib.colors import PowerNorm
from matplotlib.figure import Figure

PLOT_STYLE = {
    "font.family": "serif",
    "font.serif": ["Times New Roman", "Times", "DejaVu Serif"],
    "axes.labelcolor": "#29333d",
    "text.color": "#29333d",
    "xtick.color": "#4b5563",
    "ytick.color": "#4b5563",
}


MAP_CMAP = "YlOrRd"


MAP_BACKGROUND_COLOR = "#eef3f5"


MAP_BASE_COLOR = "#e6e2d9"


MAP_BASE_EDGE_COLOR = "#7a858d"


def save_figure(figure: Figure, output_path: str | Path, **kwargs) -> None:
    """Save and close any diagnostic figure, creating its destination directory."""
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, **kwargs)
    plt.close(figure)


def plot_outlines(shapes, axis: Axes, style: dict, *, by_country=False) -> None:
    """Draw shape or country outlines with the same map conventions."""
    if by_country:
        shapes = shapes.dissolve(by="country_id" if "country_id" in shapes else None)
    shapes.boundary.plot(ax=axis, **style, zorder=3)


def draw_empty(ax: Axes, title: str, message: str = "No data available") -> None:
    """Render a placeholder on an axis when a plot has no data."""
    ax.text(0.5, 0.5, message, ha="center", va="center", fontsize=12, alpha=0.7)
    ax.set_title(title)
    ax.set_axis_off()


def plot_timeseries(
    timeseries: pd.DataFrame,
    output_path: str | Path,
    ylabel: str,
    *,
    normalise: bool = False,
    average_hours: int = 24,
) -> None:
    """Plot complete hourly profiles and their rolling average for every shape."""
    plotted_timeseries = timeseries.sort_index()
    if normalise:
        plotted_timeseries = plotted_timeseries.div(
            plotted_timeseries.mean().replace(0, np.nan)
        )
    averaged_timeseries = plotted_timeseries.rolling(
        window=average_hours, center=True, min_periods=1
    ).mean()

    n_shapes = max(1, len(plotted_timeseries.columns))
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

    for ax, shape_id in zip(axes, plotted_timeseries.columns):
        series = plotted_timeseries[shape_id].dropna()
        if series.empty:
            draw_empty(ax, str(shape_id))
        else:
            average = averaged_timeseries[shape_id].dropna()
            ax.plot(series.index, series, linewidth=0.6, alpha=0.25)
            ax.plot(average.index, average, color="black", linewidth=1.0)
            ax.set_title(
                str(shape_id), loc="left", fontsize="medium", fontweight="bold"
            )
            ax.margins(x=0)
            ax.set_ylabel(ylabel)
        ax.set_xlabel("")

    save_figure(fig, output_path)


def plot_heat_demand_timeseries(
    demand: pd.DataFrame, output_path: str | Path, rolling_days: int = 1
) -> None:
    """Plot per-unit hourly demand and its rolling average for every shape."""
    plot_timeseries(
        demand, output_path, "Per unit", normalise=True, average_hours=24 * rolling_days
    )


def plot_annual_heat_demand_choropleth(
    shapes: gpd.GeoDataFrame, annual_demand: pd.DataFrame, output_path: str | Path
) -> None:
    """Plot total annual useful heat demand in every user-provided shape."""
    demand_by_year = (
        annual_demand.groupby(["year", "shape_id"])["heat_demand_twh"]
        .sum()
        .unstack("shape_id")
        .sort_index()
    )

    common_ids = demand_by_year.columns.intersection(shapes["shape_id"])

    demand_by_year = demand_by_year.loc[:, common_ids]
    shapes = shapes[shapes["shape_id"].isin(common_ids)].to_crs("EPSG:3035")
    years = demand_by_year.index.tolist()
    n_columns = min(3, len(years))
    n_rows = math.ceil(len(years) / n_columns)

    values = demand_by_year.to_numpy(dtype=float)
    finite_values = values[np.isfinite(values)]
    maximum = float(finite_values.max()) if finite_values.size else 0.0
    norm = PowerNorm(gamma=0.6, vmin=0, vmax=maximum if maximum > 0 else 1)

    minimum_x, minimum_y, maximum_x, maximum_y = shapes.total_bounds
    width = maximum_x - minimum_x
    height = maximum_y - minimum_y
    padding = max(width, height) * 0.04

    with plt.rc_context(PLOT_STYLE):
        fig, axes = plt.subplots(
            nrows=n_rows,
            ncols=n_columns,
            figsize=(5.2 * n_columns, 5.2 * n_rows),
            squeeze=False,
            layout="constrained",
        )
        axes_flat = axes.ravel()
        visible_axes = []

        for ax, year in zip(axes_flat, years):
            demand_for_year = demand_by_year.loc[year].rename("heat_demand_twh")
            plot_data = shapes.merge(
                demand_for_year, left_on="shape_id", right_index=True, how="left"
            )
            ax.set_facecolor(MAP_BACKGROUND_COLOR)
            shapes.plot(
                ax=ax,
                color=MAP_BASE_COLOR,
                edgecolor=MAP_BASE_EDGE_COLOR,
                linewidth=0.5,
                zorder=1,
            )
            plot_data.dropna(subset=["heat_demand_twh"]).plot(
                column="heat_demand_twh",
                ax=ax,
                cmap=MAP_CMAP,
                norm=norm,
                edgecolor="white",
                linewidth=0.35,
                alpha=0.94,
                zorder=2,
            )
            plot_outlines(
                shapes,
                ax,
                {"color": MAP_BASE_EDGE_COLOR, "linewidth": 0.9},
                by_country=True,
            )
            ax.set_xlim(minimum_x - padding, maximum_x + padding)
            ax.set_ylim(minimum_y - padding, maximum_y + padding)
            ax.text(
                0.03,
                0.97,
                str(year),
                transform=ax.transAxes,
                ha="left",
                va="top",
                fontsize="large",
                fontweight="bold",
                bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.8},
                zorder=4,
            )
            ax.set_axis_off()
            visible_axes.append(ax)

        for ax in axes_flat[len(years) :]:
            ax.set_visible(False)

        colorbar = fig.colorbar(
            plt.cm.ScalarMappable(norm=norm, cmap=MAP_CMAP),
            ax=visible_axes,
            location="bottom",
            shrink=0.72,
            aspect=35,
            pad=0.02,
        )
        colorbar.outline.set_edgecolor("#a5adb3")
        colorbar.set_label("Annual useful heat demand (TWh)")

        save_figure(fig, output_path, bbox_inches="tight", pad_inches=0.05, dpi=200)


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


def plot_floor_area(
    raster_path: str,
    band: int,
    title: str,
    output_path: str,
    chunk_size: int,
    max_size: int,
    shapes: gpd.GeoDataFrame,
    outline: dict[str, Any],
    colorbar_label: str = "Floor area (m²/ha)",
) -> None:
    """Plot a Dask-coarsened density band without loading the full raster."""
    opened: Any = rioxarray.open_rasterio(
        raster_path, chunks={"x": chunk_size, "y": chunk_size}
    )
    with opened as raster:
        values = raster.sel(band=band)
        factor = max(1, int(np.ceil(max(values.shape) / max_size)))
        values = (
            values.coarsen(x=factor, y=factor, boundary="pad")
            .max()
            .compute()
            .to_numpy()
        )
        values = np.ma.masked_equal(values, raster.rio.nodata)
        shapes = shapes.to_crs(raster.rio.crs)
        bounds = raster.rio.bounds()
        extent = bounds[0], bounds[2], bounds[1], bounds[3]
    positive = values.compressed()
    vmax = (
        max(1, float(np.quantile(positive[positive > 0], 0.99)))
        if np.any(positive > 0)
        else 1
    )
    figure, axis = plt.subplots(figsize=(9, 7), constrained_layout=True)
    axis.set_facecolor(MAP_BACKGROUND_COLOR)
    image = axis.imshow(
        values, extent=extent, cmap=MAP_CMAP, norm=PowerNorm(0.35, vmin=0, vmax=vmax)
    )
    # Preserve each user-provided boundary, including internal borders and holes.
    plot_outlines(shapes, axis, outline)
    axis.set_xlim(extent[:2])
    axis.set_ylim(extent[2:])
    axis.set(title=title, xlabel="Easting (m)", ylabel="Northing (m)")
    figure.colorbar(image, ax=axis, label=colorbar_label)
    save_figure(figure, output_path, dpi=200)
