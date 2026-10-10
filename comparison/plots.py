"""Generate presentation figures from saved CSV files, without rerunning simulations."""

import csv
from collections import defaultdict
from pathlib import Path
from statistics import mean


POLICIES = ("baseline", "service_sweep")
LABELS = {"baseline": "Strict internal priority", "service_sweep": "Service sweep + aging"}
COLORS = {"baseline": "#b7643f", "service_sweep": "#216b96"}
SCENARIOS = ("direction_change", "repeated_cabin", "mixed_traffic")
TITLES = {"direction_change": "Direction change", "repeated_cabin": "Repeated cabin requests", "mixed_traffic": "Mixed traffic"}


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as source:
        return list(csv.DictReader(source))


def grouped_summaries(rows: list[dict]) -> dict:
    groups = defaultdict(list)
    for row in rows:
        groups[row["scenario"], row["policy"]].append(row)
    return groups


def values(rows: list[dict], metric: str) -> list[float]:
    return [float(row[metric]) for row in rows if row[metric] not in (None, "")]


def generate(output: Path) -> list[Path]:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as error:
        raise RuntimeError("Install chart dependencies: py -m pip install -r comparison/requirements.txt") from error

    rows = read_csv(output / "data" / "summary.csv")
    groups = grouped_summaries(rows)
    figures = output / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "axes.spines.top": False,
                         "axes.spines.right": False, "svg.hashsalt": "elevator-comparison"})
    paths = []

    def save(figure, name):
        for suffix in ("png", "svg"):
            path = figures / f"{name}.{suffix}"
            metadata = {"Date": None} if suffix == "svg" else None
            figure.savefig(path, dpi=160, facecolor="white", metadata=metadata, bbox_inches="tight", pad_inches=0.15)
            paths.append(path)
        plt.close(figure)

    for scenario in SCENARIOS:
        seed = min(int(row["seed"]) for row in rows if row["scenario"] == scenario)
        fig, axes = plt.subplots(2, 1, figsize=(11, 6.5), sharex=True)
        max_age = 0.0
        for policy in POLICIES:
            trace = read_csv(output / "data" / f"{scenario}_{seed}_{policy}_trace.csv")
            times = [float(row["time_s"]) for row in trace]
            max_age = max(max_age, max(float(row["oldest_hall_wait_s"]) for row in trace))
            axes[0].plot(times, [float(row["position"]) for row in trace], color=COLORS[policy], label=LABELS[policy], lw=2)
            axes[1].plot(times, [float(row["oldest_hall_wait_s"]) for row in trace], color=COLORS[policy], lw=2)
        axes[0].set(ylabel="Elevator floor", ylim=(0.7, 10.3), yticks=range(1, 11))
        fig.legend(*axes[0].get_legend_handles_labels(), loc="upper center", bbox_to_anchor=(0.5, 0.925), ncol=2, fontsize=9)
        axes[1].set(xlabel="Simulation time (s)", ylabel="Oldest pending\nhall-call age (s)", ylim=(0, None))
        axes[1].set_ylim(0, max(1, max_age * 1.15))
        if max_age >= 60:
            axes[1].axhline(60, color="#8592a0", ls="--", lw=1)
            axes[1].text(0.99, 60, "60 s aging threshold (sweep only)", transform=axes[1].get_yaxis_transform(),
                         ha="right", va="bottom", fontsize=8, color="#596574")
        for ax in axes:
            ax.grid(alpha=0.18)
        fig.suptitle(f"{TITLES[scenario]} | same controller and simulated hardware", fontsize=15, fontweight="bold")
        note = f"Representative mixed-traffic seed: {seed}." if scenario == "mixed_traffic" else "Scripted passenger itinerary; cabin requests follow actual arrivals."
        fig.text(0.08, 0.02, note + "  Zero age = no pending hall calls.", fontsize=9, color="#596574")
        fig.tight_layout(rect=(0, 0.05, 1, 0.875))
        save(fig, scenario)

    metrics = (
        ("hall_mean_wait_s", "Mean hall wait (completed only)", "Seconds"),
        ("hall_max_wait_s", "Hall wait: per-run maximum (completed)", "Seconds"),
        ("hall_pending", "Unserved hall calls at end", "Calls"),
        ("hall_oldest_pending_s", "Oldest unserved hall call at end", "Seconds"),
        ("cabin_mean_wait_s", "Mean cabin request time (completed only)", "Seconds"),
        ("distance_floors", "Total travel distance", "Floors"),
    )
    fig, axes = plt.subplots(3, 2, figsize=(13, 11))
    for ax, (metric, title, unit) in zip(axes.flat, metrics):
        for offset, policy in ((-0.19, "baseline"), (0.19, "service_sweep")):
            for index, scenario in enumerate(SCENARIOS):
                samples = values(groups[scenario, policy], metric)
                x = index + offset
                if not samples:
                    ax.text(x, 0.04, "N/A", transform=ax.get_xaxis_transform(), ha="center", fontsize=9, color=COLORS[policy])
                    continue
                average = mean(samples)
                ax.bar(x, average, 0.36, color=COLORS[policy], label=LABELS[policy] if index == 0 else None)
                if len(samples) > 1:
                    ax.errorbar(x, average, yerr=[[average - min(samples)], [max(samples) - average]],
                                color="#334155", capsize=3, lw=1)
                ax.annotate(f"{average:.1f}", (x, max(samples)), xytext=(0, 5), textcoords="offset points", ha="center", fontsize=8)
        ax.set(title=title, ylabel=unit, xticks=range(3), xticklabels=["Direction\nchange", "Repeated\ncabin", "Mixed\ntraffic"])
        ax.set_ylim(bottom=0)
        ax.margins(y=0.22)
        ax.grid(axis="y", alpha=0.18)
        ax.set_axisbelow(True)
    from matplotlib.patches import Patch
    fig.legend(handles=[Patch(color=COLORS[p], label=LABELS[p]) for p in POLICIES], loc="upper center", bbox_to_anchor=(0.5, 0.954), ncol=2)
    fig.suptitle("Policy comparison | benefits and costs", fontsize=18, fontweight="bold", y=0.99)
    fig.text(0.07, 0.023, "Mixed traffic: equal-weight mean across seeds; whiskers show min-max, not confidence intervals.\n"
             "N/A = no completed requests. Unserved requests are excluded from completed-time means and reported separately.\n"
             "Repeated cabin: same itinerary, but the number of legs requested within the fixed horizon can differ.", fontsize=9, color="#596574")
    fig.tight_layout(rect=(0, 0.085, 1, 0.93), h_pad=2.4)
    save(fig, "summary")
    return paths
