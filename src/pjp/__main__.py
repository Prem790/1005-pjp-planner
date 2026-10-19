"""Command-line entry point. Only completed stages are exposed."""

import argparse
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(prog="pjp", description="PJP planning POC")
    commands = parser.add_subparsers(dest="command", required=True)
    audit = commands.add_parser("audit", help="Read-only workbook audit")
    audit.add_argument("--workbook", required=True, type=Path)
    audit.add_argument("--out", default=Path("artifacts/audit"), type=Path)
    rank = commands.add_parser("rank", help="Explainable initial dealer ranking")
    rank.add_argument("--workbook", required=True, type=Path)
    rank.add_argument("--config", default=Path("config/ranking.json"), type=Path)
    rank.add_argument("--out", default=Path("artifacts/ranking"), type=Path)
    select = commands.add_parser("select", help="Select provisional focus dealers")
    select.add_argument("--workbook", required=True, type=Path)
    select.add_argument("--ranking", default=Path("artifacts/ranking/dealer_rankings.csv"), type=Path)
    select.add_argument("--config", default=Path("config/selection.json"), type=Path)
    select.add_argument("--out", default=Path("artifacts/focus"), type=Path)
    plan = commands.add_parser("plan", help="Build November calendar and daily routes")
    plan.add_argument("--workbook", required=True, type=Path)
    plan.add_argument("--focus", default=Path("artifacts/focus/focus_dealers.csv"), type=Path)
    plan.add_argument("--selection-config", default=Path("config/selection.json"), type=Path)
    plan.add_argument("--config", default=Path("config/scheduling.json"), type=Path)
    plan.add_argument("--out", default=Path("artifacts/plan"), type=Path)
    nudge = commands.add_parser("nudge", help="Generate grounded WhatsApp drafts and evidence")
    nudge.add_argument("--plan", default=Path("artifacts/plan/plan_november.csv"), type=Path)
    nudge.add_argument("--focus", default=Path("artifacts/plan/scheduled_focus_dealers.csv"), type=Path)
    nudge.add_argument("--ranking-config", default=Path("config/ranking.json"), type=Path)
    nudge.add_argument("--config", default=Path("config/nudges.json"), type=Path)
    nudge.add_argument("--out", default=Path("artifacts/nudges"), type=Path)
    evaluate = commands.add_parser("evaluate-static", help="Run static baseline on practice seeds")
    evaluate.add_argument("--workbook", default=Path("data/private/PJP_Assignment_Sample_Data.xlsx"), type=Path)
    evaluate.add_argument("--plan", default=Path("artifacts/plan/plan_november.csv"), type=Path)
    evaluate.add_argument("--focus", default=Path("artifacts/plan/scheduled_focus_dealers.csv"), type=Path)
    evaluate.add_argument("--simulator", default=Path("data/private/PJP_Simulator/simulate.py"), type=Path)
    evaluate.add_argument("--seeds", nargs="+", type=int, choices=[1,2,3], default=[1,2,3])
    evaluate.add_argument("--out", default=Path("artifacts/simulation/static"), type=Path)
    replan = commands.add_parser("replan", help="Evening simulator-compatible adaptive command")
    replan.add_argument("--state", required=True, type=Path)
    replan.add_argument("--plan", required=True, type=Path)
    replan.add_argument("--out", required=True, type=Path)
    replan.add_argument("--context", default=Path("artifacts/replanning/context.json"), type=Path)
    replan.add_argument("--memory", type=Path)
    adaptive = commands.add_parser("evaluate-adaptive", help="Run adaptive practice simulations")
    adaptive.add_argument("--workbook", default=Path("data/private/PJP_Assignment_Sample_Data.xlsx"), type=Path)
    adaptive.add_argument("--plan", default=Path("artifacts/plan/plan_november.csv"), type=Path)
    adaptive.add_argument("--context", default=Path("artifacts/replanning/context.json"), type=Path)
    adaptive.add_argument("--out", default=Path("artifacts/simulation/adaptive"), type=Path)
    adaptive.add_argument("--seeds", nargs="+", type=int, choices=[1,2,3], default=[1,2,3])
    run = commands.add_parser("run", help="Run the complete assignment and package private deliverables")
    run.add_argument("--workbook", default=Path("data/private/PJP_Assignment_Sample_Data.xlsx"), type=Path)
    run.add_argument("--simulator", default=Path("data/private/PJP_Simulator/simulate.py"), type=Path)
    args = parser.parse_args(argv)
    if args.command == "audit":
        from .audit import main as audit_main
        audit_main(["--workbook", str(args.workbook), "--out", str(args.out)])
    elif args.command == "rank":
        from .ranking import run_ranking
        run_ranking(args.workbook, args.config, args.out)
    elif args.command == "select":
        from .selection import run_selection
        run_selection(args.workbook, args.ranking, args.config, args.out)
    elif args.command == "plan":
        from .planning import run_planning
        run_planning(args.workbook, args.focus, args.selection_config, args.config, args.out)
    elif args.command == "nudge":
        from .nudges import run_nudges
        run_nudges(args.plan, args.focus, args.ranking_config, args.config, args.out)
    elif args.command == "evaluate-static":
        from .evaluation import run_static_evaluation
        run_static_evaluation(args.workbook, args.plan, args.focus, args.simulator, args.out, args.seeds)
    elif args.command == "replan":
        from .replanning import run_replan
        run_replan(args.state, args.plan, args.out, args.context, args.memory)
    elif args.command == "evaluate-adaptive":
        from .adaptive_evaluation import run_adaptive_evaluation
        run_adaptive_evaluation(args.workbook, args.plan, args.context, args.out, args.seeds)
    elif args.command == "run":
        from .pipeline import run_pipeline
        run_pipeline(args.workbook, args.simulator)


if __name__ == "__main__":
    main()
