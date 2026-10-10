"""Usage: py -m comparison; py -m comparison --plot-only."""

import argparse
import csv
from dataclasses import asdict
import json
from math import isfinite
from pathlib import Path

from elevator_controller import BaselinePolicy, SchedulingPolicy

from .plots import read_csv
from .report import report
from .runner import Settings, run
from .scenarios import scenarios


def write_csv(path: Path, rows: list[dict], fields=None) -> None:
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fields or list(rows[0]))
        writer.writeheader()
        for row in rows:
            writer.writerow({key: round(value, 6) if isinstance(value, float) else value for key, value in row.items()})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("comparison/results"))
    parser.add_argument("--seeds", type=int, nargs="+", default=[11, 22, 33, 44, 55])
    parser.add_argument("--time-step", type=float, default=0.1)
    parser.add_argument("--plot-only", action="store_true", help="Regenerate plots from existing CSV files.")
    parser.add_argument("--no-plots", action="store_true", help="Run without matplotlib; write data and report only.")
    args = parser.parse_args()
    if args.plot_only and args.no_plots:
        parser.error("--plot-only and --no-plots cannot be combined")
    if not isfinite(args.time_step) or args.time_step <= 0:
        parser.error("--time-step must be finite and positive")
    output = args.output
    if not args.plot_only:
        data = output / "data"
        data.mkdir(parents=True, exist_ok=True)
        settings = Settings(time_step=args.time_step)
        experiments = scenarios(tuple(dict.fromkeys(args.seeds)))
        manifest = {"settings": asdict(settings), "floors": 10, "max_hall_wait_s": 60,
                    "scenarios": [asdict(scenario) for scenario in experiments]}
        (data / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        summaries = []
        for scenario in experiments:
            for name, policy in (("baseline", BaselinePolicy()), ("service_sweep", SchedulingPolicy())):
                result = run(scenario, policy, name, settings)
                prefix = f"{scenario.name}_{scenario.seed}_{name}"
                write_csv(data / f"{prefix}_trace.csv", result.trace)
                write_csv(data / f"{prefix}_requests.csv", [asdict(record) for record in result.metrics.records],
                          ["request_id", "kind", "floor", "direction", "created_at", "served_at"])
                write_csv(data / f"{prefix}_services.csv", result.services, ["time_s", "floor", "direction"])
                write_csv(data / f"{prefix}_passengers.csv", result.passengers)
                summaries.append(result.summary)
                print(f"{prefix}: hall served={result.summary['hall_completed']}, pending={result.summary['hall_pending']}")
        write_csv(data / "summary.csv", summaries)
    else:
        summaries = read_csv(output / "data" / "summary.csv")
    report(output, summaries)
    if not args.no_plots:
        from .plots import generate
        for path in generate(output):
            print(path)
    print(f"Report: {output / 'REPORT.md'}")


if __name__ == "__main__":
    main()
