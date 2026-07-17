"""Plotting utilities."""

import math

import inflection
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from cmap import Colormap
from matplotlib.axes import Axes
from matplotlib.figure import Figure


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
        y_label += f" ({inflection.humanize(unit)})"

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
            edgecolor="black"
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
