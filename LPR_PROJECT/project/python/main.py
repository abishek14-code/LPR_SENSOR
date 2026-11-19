import argparse

from .config import AppConfig
from .evidence import generate_evidence
from .experiments import run_demo, run_seeded_experiments
from .io_utils import save_decision_record, format_decision
from .recorded_pipeline import process_recorded_csv
from .simulator_adapter import TraceAdapterConfig


def print_record(record):
    print(format_decision(record.to_dict()))


def main(argv=None):
    parser = argparse.ArgumentParser(description="P3 LPR controller demonstrator")
    parser.add_argument("--demo", action="store_true", help="run the deterministic stateful demonstrator")
    parser.add_argument("--experiments", action="store_true", help="run seeded simulation experiments and generate evidence")
    parser.add_argument("--recorded-csv", help="process a recorded LTspice/Multisim CSV")
    parser.add_argument("--evidence-json", help="recorded opening/closing checks and OCP evidence bound to CSV identities")
    parser.add_argument("--variants", nargs="+", default=["enhanced"], choices=("baseline", "conservative", "enhanced", "no_reverse"))
    parser.add_argument("--source-type", default="ltspice", choices=("ltspice", "multisim"), help="recorded CSV source type")
    parser.add_argument("--trials", type=int, default=100, help="independent trials per experiment scenario")
    parser.add_argument("--output-dir", default="results", help="evidence or decision-record output directory")
    args = parser.parse_args(argv)
    config = AppConfig()
    if args.demo:
        for record in run_demo(config):
            print_record(record)
            save_decision_record(record.to_dict(), f"{args.output_dir}/generated", record.scenario_id)
    elif args.experiments:
        records, summary = run_seeded_experiments(config, trials=args.trials, output_dir=args.output_dir, variants=args.variants)
        figures = generate_evidence(config, summary=summary, output_dir=args.output_dir)
        print(f"Completed seeded trials: {len(records)}")
        print(f"Summary table: {args.output_dir}/tables/seeded_trial_metrics.csv")
        print(f"Figures generated: {len(figures)}")
    elif args.recorded_csv:
        try:
            record = process_recorded_csv(args.recorded_csv, config, TraceAdapterConfig(source_type=args.source_type), evidence_path=args.evidence_json)
        except (ValueError, OSError) as exc:
            from .decision_engine import reject_input
            result = reject_input(exc, config, source_type=args.source_type)
            data = {"scenario_id": "invalid_recorded_input", "source_type": args.source_type,
                    "decision": "REJECT", "diagnostic_codes": list(result.reason_codes),
                    "decision_result": result.to_dict(), "state_history": [], "final_state": "REJECT"}
            print(format_decision(data))
            save_decision_record(data, f"{args.output_dir}/generated", "invalid_recorded_input")
            return 2
        print_record(record)
        save_decision_record(record.to_dict(), f"{args.output_dir}/generated", record.scenario_id)
    else:
        parser.error("use --demo, --experiments, or --recorded-csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
