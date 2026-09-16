"""Plot the accident-capacity distribution used by the experiment backend."""

from __future__ import annotations

import csv
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib import font_manager
from scipy.stats import beta as beta_distribution


plt.rcParams["font.family"] = "sans-serif"
plt.rcParams["font.sans-serif"] = ["STHeiti", "Arial", "DejaVu Sans", "Liberation Sans"]
plt.rcParams["svg.fonttype"] = "none"
plt.rcParams["pdf.fonttype"] = 42

CHINESE_FONT_PATH = Path("/System/Library/Fonts/STHeiti Medium.ttc")
if CHINESE_FONT_PATH.exists():
    font_manager.fontManager.addfont(CHINESE_FONT_PATH)

ALPHA = 6.83057
BETA = 4.05907
INCIDENT_PROBABILITY = 0.20
NORMAL_CAPACITY = 4.0

BLUE = "#245B8A"
BLUE_FILL = "#BFD5E6"
RED = "#B44A43"
RED_FILL = "#EBC7C2"
GRAY = "#555555"
LIGHT_GRAY = "#D6D6D6"


def configure_axes(ax: plt.Axes) -> None:
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_linewidth(0.7)
    ax.spines["bottom"].set_linewidth(0.7)
    ax.tick_params(width=0.7, length=3, labelsize=7)
    ax.grid(axis="y", color=LIGHT_GRAY, linewidth=0.45, alpha=0.65)
    ax.set_axisbelow(True)


def build_figure() -> tuple[plt.Figure, dict[str, np.ndarray | float]]:
    loss = np.linspace(0.001, 0.999, 1_200)
    loss_density = beta_distribution.pdf(loss, ALPHA, BETA)

    service = np.linspace(0.001, NORMAL_CAPACITY - 0.001, 1_200)
    implied_loss = 1.0 - service / NORMAL_CAPACITY
    incident_service_density = (
        beta_distribution.pdf(implied_loss, ALPHA, BETA) / NORMAL_CAPACITY
    )
    mixed_continuous_density = INCIDENT_PROBABILITY * incident_service_density

    mean_loss = ALPHA / (ALPHA + BETA)
    sd_loss = np.sqrt(
        ALPHA
        * BETA
        / ((ALPHA + BETA) ** 2 * (ALPHA + BETA + 1))
    )
    mean_incident_capacity = NORMAL_CAPACITY * (1.0 - mean_loss)
    mean_capacity = (
        (1.0 - INCIDENT_PROBABILITY) * NORMAL_CAPACITY
        + INCIDENT_PROBABILITY * mean_incident_capacity
    )
    loss_interval = beta_distribution.ppf([0.025, 0.975], ALPHA, BETA)

    fig, (ax_loss, ax_capacity) = plt.subplots(
        1,
        2,
        figsize=(7.20, 3.25),
        gridspec_kw={"width_ratios": [1.0, 1.08], "wspace": 0.36},
    )

    configure_axes(ax_loss)
    ax_loss.fill_between(loss, loss_density, color=BLUE_FILL, alpha=0.9, linewidth=0)
    ax_loss.plot(loss, loss_density, color=BLUE, linewidth=1.6)
    ax_loss.axvline(mean_loss, color=RED, linewidth=1.1, linestyle=(0, (4, 2)))
    ax_loss.axvspan(
        loss_interval[0],
        loss_interval[1],
        color=BLUE,
        alpha=0.08,
        linewidth=0,
    )
    ax_loss.annotate(
        f"均值 = {mean_loss:.3f}\n标准差 = {sd_loss:.3f}",
        xy=(mean_loss, beta_distribution.pdf(mean_loss, ALPHA, BETA)),
        xytext=(0.09, 2.12),
        fontsize=7.2,
        color=GRAY,
        arrowprops={"arrowstyle": "-", "color": RED, "linewidth": 0.8},
    )
    ax_loss.text(
        0.03,
        0.96,
        "a",
        transform=ax_loss.transAxes,
        fontsize=9,
        fontweight="bold",
        va="top",
    )
    ax_loss.set_title("事故发生后的容量损失", fontsize=9, pad=8)
    ax_loss.set_xlabel("容量损失比例 $L$", fontsize=8)
    ax_loss.set_ylabel("条件概率密度", fontsize=8)
    ax_loss.set_xlim(0, 1)
    ax_loss.set_ylim(bottom=0)
    ax_loss.text(
        0.02,
        -0.24,
        r"$L\mid A=1\sim\mathrm{Beta}(6.83057,\,4.05907)$",
        transform=ax_loss.transAxes,
        fontsize=7.2,
        color=GRAY,
    )

    configure_axes(ax_capacity)
    ax_capacity.fill_between(
        service,
        mixed_continuous_density,
        color=RED_FILL,
        alpha=0.9,
        linewidth=0,
        label="事故状态的连续部分",
    )
    ax_capacity.plot(service, mixed_continuous_density, color=RED, linewidth=1.6)
    ax_capacity.axvline(
        mean_incident_capacity,
        color=RED,
        linewidth=1.0,
        linestyle=(0, (4, 2)),
    )
    ax_capacity.annotate(
        f"事故时平均服务率\n{mean_incident_capacity:.3f} 人/分钟",
        xy=(mean_incident_capacity, np.interp(mean_incident_capacity, service, mixed_continuous_density)),
        xytext=(2.18, mixed_continuous_density.max() * 0.79),
        fontsize=7.1,
        color=GRAY,
        ha="center",
        arrowprops={"arrowstyle": "-", "color": RED, "linewidth": 0.8},
    )
    ax_capacity.set_title("全部通勤日的瓶颈服务率", fontsize=9, pad=8)
    ax_capacity.set_xlabel("实际服务率 $S$（人/分钟）", fontsize=8)
    ax_capacity.set_ylabel("连续概率密度（已乘事故概率 0.20）", fontsize=8)
    ax_capacity.set_xlim(0, 4.18)
    ax_capacity.set_ylim(0, mixed_continuous_density.max() * 1.18)

    mass_axis = ax_capacity.twinx()
    mass_axis.set_ylim(0, 1)
    mass_axis.spines["top"].set_visible(False)
    mass_axis.spines["right"].set_linewidth(0.7)
    mass_axis.spines["left"].set_visible(False)
    mass_axis.tick_params(axis="y", width=0.7, length=3, labelsize=7)
    mass_axis.set_ylabel("离散概率质量", fontsize=8, color=BLUE)
    mass_axis.tick_params(axis="y", colors=BLUE)
    mass_axis.vlines(NORMAL_CAPACITY, 0, 1.0 - INCIDENT_PROBABILITY, color=BLUE, linewidth=2.2)
    mass_axis.scatter(
        [NORMAL_CAPACITY],
        [1.0 - INCIDENT_PROBABILITY],
        s=28,
        color=BLUE,
        edgecolor="white",
        linewidth=0.6,
        zorder=5,
    )
    mass_axis.annotate(
        "正常容量 4.0\n概率质量 = 0.80",
        xy=(NORMAL_CAPACITY, 1.0 - INCIDENT_PROBABILITY),
        xytext=(3.17, 0.92),
        fontsize=7.1,
        color=BLUE,
        ha="center",
        arrowprops={"arrowstyle": "-", "color": BLUE, "linewidth": 0.8},
    )
    ax_capacity.text(
        0.02,
        0.96,
        "b",
        transform=ax_capacity.transAxes,
        fontsize=9,
        fontweight="bold",
        va="top",
    )
    ax_capacity.text(
        0.02,
        -0.24,
        f"无条件平均服务率 = {mean_capacity:.3f} 人/分钟",
        transform=ax_capacity.transAxes,
        fontsize=7.2,
        color=GRAY,
    )

    fig.suptitle(
        "事故风险动态瓶颈的容量损失与服务率分布",
        fontsize=10.5,
        fontweight="bold",
        y=0.99,
    )
    fig.subplots_adjust(left=0.09, right=0.92, bottom=0.25, top=0.83)

    return fig, {
        "loss": loss,
        "loss_density": loss_density,
        "service": service,
        "incident_service_density": incident_service_density,
        "mixed_continuous_density": mixed_continuous_density,
        "mean_loss": mean_loss,
        "sd_loss": sd_loss,
        "mean_incident_capacity": mean_incident_capacity,
        "mean_capacity": mean_capacity,
    }


def write_source_data(data: dict[str, np.ndarray | float], output_path: Path) -> None:
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "loss_ratio",
                "conditional_loss_density",
                "service_rate",
                "conditional_incident_service_density",
                "unconditional_incident_density_contribution",
            ]
        )
        for values in zip(
            data["loss"],
            data["loss_density"],
            data["service"],
            data["incident_service_density"],
            data["mixed_continuous_density"],
            strict=True,
        ):
            writer.writerow(f"{float(value):.10f}" for value in values)


def main() -> None:
    figure_dir = Path(__file__).resolve().parents[1]
    source_data_dir = figure_dir / "source_data"
    source_data_dir.mkdir(parents=True, exist_ok=True)
    output_stem = figure_dir / "accident_capacity_distribution"

    figure, data = build_figure()
    figure.savefig(output_stem.with_suffix(".svg"), bbox_inches="tight")
    figure.savefig(output_stem.with_suffix(".pdf"), bbox_inches="tight")
    figure.savefig(output_stem.with_suffix(".png"), dpi=300, bbox_inches="tight")
    figure.savefig(output_stem.with_suffix(".tiff"), dpi=600, bbox_inches="tight")
    write_source_data(data, source_data_dir / "accident_capacity_distribution.csv")
    plt.close(figure)


if __name__ == "__main__":
    main()
