"""Phase 2 pilot: run arms over the pilot questions, then grade and report.

    python -m agents.pilot run --arm raw --runs 3 [--limit N] [--dry-run]
    python -m agents.pilot report

Runs are resumable: a (question, arm, run) with a record on disk is skipped unless it ended in
an API/harness error.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import yaml

from agents.harness import (
    ROOT,
    USAGE_KEYS,
    _path,
    cost_usd,
    load_config,
    load_pricing,
    make_arm,
    run_dir,
    run_question,
)

PILOT_PATH = ROOT / "eval" / "pilot.yaml"
REPORT_PATH = ROOT / "reports" / "pilot.json"


def load_questions(questions_dir: Path | None = None) -> dict[str, dict[str, Any]]:
    qdir = questions_dir or _path(load_config(), "questions")
    out: dict[str, dict[str, Any]] = {}
    for p in sorted(qdir.glob("*.yaml")):
        if p.name.startswith("leaks"):
            continue
        for q in (yaml.safe_load(p.read_text()) or {}).get("questions", []):
            out[q["id"]] = q
    return out


def load_pilot(path: Path = PILOT_PATH) -> tuple[str, list[str]]:
    data = yaml.safe_load(path.read_text())
    if isinstance(data, list):
        return path.stem, [str(q) for q in data]
    return str(data.get("pilot_id", path.stem)), [str(q) for q in data["questions"]]


def record_path(cfg: dict[str, Any], pilot_id: str, arm: str, run: int, qid: str) -> Path:
    return run_dir(cfg, pilot_id, arm, run) / f"{qid}.record.json"


def _done(p: Path) -> bool:
    if not p.exists():
        return False
    try:
        return json.loads(p.read_text()).get("stop_reason") != "error"
    except json.JSONDecodeError:
        return False


def estimate_cost_per_run(
    cfg: dict[str, Any], pricing: dict[str, Any], records: list[dict]
) -> tuple[float, str]:
    real = [r["cost_usd"] for r in records if r.get("stop_reason") != "error" and r.get("turns")]
    if real:
        return sum(real) / len(real), f"mean of {len(real)} finished runs"
    e = cfg["estimate"]
    turns, step = e["turns"], e["input_tokens_per_turn"] + e["output_tokens_per_turn"]
    # Each turn reads the cached prefix, writes what the last turn added, and generates output.
    usage = {
        "input_tokens": turns * 50,
        "cache_read_input_tokens": sum(
            e["base_prompt_tokens"] + (t - 1) * step for t in range(1, turns + 1)
        ),
        "cache_creation_input_tokens": e["base_prompt_tokens"] + (turns - 1) * step,
        "output_tokens": turns * e["output_tokens_per_turn"],
    }
    return cost_usd(usage, cfg["model"], pricing), "planning estimate from config/agent.yml"


def all_records(cfg: dict[str, Any], pilot_id: str) -> list[dict[str, Any]]:
    base = _path(cfg, "runs") / pilot_id
    return [json.loads(p.read_text()) for p in sorted(base.glob("*/run*/*.record.json"))]


def cmd_run(args: argparse.Namespace) -> int:
    cfg, pricing = load_config(), load_pricing()
    pilot_id, qids = load_pilot(Path(args.pilot))
    pilot_id = args.pilot_id or pilot_id
    questions = load_questions()
    missing = [q for q in qids if q not in questions]
    if missing:
        print(f"unknown question ids: {missing}", file=sys.stderr)
        return 2
    if args.limit:
        qids = qids[: args.limit]
    todo = [
        (qid, run)
        for run in range(1, args.runs + 1)
        for qid in qids
        if not _done(record_path(cfg, pilot_id, args.arm, run, qid))
    ]
    per_run, basis = estimate_cost_per_run(cfg, pricing, all_records(cfg, pilot_id))
    print(
        f"pilot {pilot_id} · arm {args.arm} · {len(qids)} questions × {args.runs} runs · "
        f"{len(todo)} to run · model {cfg['model']} effort {cfg['effort']} "
        f"max_turns {cfg['max_turns']}"
    )
    print(f"estimated cost: ${per_run:.2f} per question-run ({basis}) → ${per_run * len(todo):.2f}")
    if args.dry_run or not todo:
        for qid, run in todo:
            print(f"  would run {qid} run{run}")
        return 0
    make_arm(args.arm, cfg).close()  # fail fast if the arm can't be built
    import anthropic

    client = anthropic.Anthropic()
    total = 0.0

    def one(qid: str, run: int) -> dict[str, Any]:
        return run_question(
            questions[qid],
            make_arm(args.arm, cfg),
            run,
            client=client,
            cfg=cfg,
            pricing=pricing,
            pilot_id=pilot_id,
        )

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futs = {pool.submit(one, qid, run): (qid, run) for qid, run in todo}
        for fut in as_completed(futs):
            qid, run = futs[fut]
            try:
                r = fut.result()
            except Exception as e:  # arm construction failed; the record was never written
                print(f"  {qid} run{run}: FAILED {type(e).__name__}: {e}")
                continue
            total += r["cost_usd"]
            print(
                f"  {qid} run{run}: {r['stop_reason']:<9} turns {r['turns']:>2} "
                f"${r['cost_usd']:.2f} {r['wall_s']:.0f}s flags {len(r['flags'])}"
                + (f" ERROR {r['error']}" if r["error"] else "")
            )
    print(f"spent ${total:.2f}")
    return 0


def _right(results: list[bool]) -> bool:
    """Right in at least 2 of 3 runs (generally, at least two thirds)."""
    return bool(results) and sum(results) >= math.ceil(2 * len(results) / 3)


def build_report(
    cfg: dict[str, Any], pilot_id: str, qids: list[str], questions: dict
) -> dict[str, Any]:
    from eval.grader import grade, load_gold

    gold: dict[str, Any] = {}
    gold_errors: dict[str, str] = {}
    for qid in qids:
        try:
            gold[qid] = load_gold(qid)["answer"]
        except (FileNotFoundError, ValueError) as e:
            gold_errors[qid] = str(e)
    runs = []
    for r in all_records(cfg, pilot_id):
        qid = r["question_id"]
        if qid not in qids:
            continue
        verdict = (
            grade(r["answer"], gold[qid], questions[qid]["answer"])
            if qid in gold
            else {"correct": None, "fields": {}, "error": gold_errors[qid]}
        )
        runs.append(
            {
                **{
                    k: r[k]
                    for k in (
                        "question_id",
                        "arm",
                        "run",
                        "stop_reason",
                        "turns",
                        "cost_usd",
                        "wall_s",
                    )
                },
                "flags": len(r["flags"]),
                "correct": verdict["correct"],
                "verdict": verdict,
                "answer": r["answer"],
                "error": r["error"],
            }
        )
    arms = sorted({r["arm"] for r in runs})
    by_question: dict[str, Any] = {}
    for qid in qids:
        q = questions[qid]
        by_question[qid] = {"trap": q["traps"][0], "traps": q["traps"], "arms": {}}
        for arm in arms:
            rs = sorted(
                (r for r in runs if r["question_id"] == qid and r["arm"] == arm),
                key=lambda r: r["run"],
            )
            res = [bool(r["correct"]) for r in rs if r["correct"] is not None]
            by_question[qid]["arms"][arm] = {"runs": res, "right": _right(res)}
    all_recs = {(r["question_id"], r["arm"], r["run"]): r for r in all_records(cfg, pilot_id)}
    by_arm: dict[str, Any] = {}
    for arm in arms:
        rs = [r for r in runs if r["arm"] == arm]
        graded = [q for q in qids if q in gold and by_question[q]["arms"][arm]["runs"]]
        right = [q for q in graded if by_question[q]["arms"][arm]["right"]]
        wrong = [q for q in graded if q not in right]
        wrong_by_trap: dict[str, int] = {}
        for q in wrong:
            wrong_by_trap[by_question[q]["trap"]] = wrong_by_trap.get(by_question[q]["trap"], 0) + 1
        usage = dict.fromkeys(USAGE_KEYS, 0)
        for r in rs:
            for k in USAGE_KEYS:
                usage[k] += all_recs[(r["question_id"], arm, r["run"])]["usage"].get(k, 0)
        cost = sum(r["cost_usd"] for r in rs)
        n_correct = sum(1 for r in rs if r["correct"])
        by_arm[arm] = {
            "graded_questions": len(graded),
            "right_2_of_3": len(right),
            "right_questions": right,
            "wrong_questions": wrong,
            "wrong_by_trap": dict(sorted(wrong_by_trap.items())),
            "runs": len(rs),
            "runs_correct": n_correct,
            "stop_reasons": {
                s: sum(1 for r in rs if r["stop_reason"] == s)
                for s in sorted({r["stop_reason"] for r in rs})
            },
            "flags": sum(r["flags"] for r in rs),
            "cost_usd": round(cost, 4),
            "cost_per_run_usd": round(cost / len(rs), 4) if rs else None,
            "cost_per_correct_run_usd": round(cost / n_correct, 4) if n_correct else None,
            "usage": usage,
        }
    return {
        "pilot_id": pilot_id,
        "generated_at": dt.datetime.now(dt.UTC).isoformat(),
        "model": cfg["model"],
        "effort": cfg["effort"],
        "questions": qids,
        "gold_errors": gold_errors,
        "by_arm": by_arm,
        "by_question": by_question,
        "runs": sorted(runs, key=lambda r: (r["arm"], r["question_id"], r["run"])),
    }


def print_summary(rep: dict[str, Any]) -> None:
    arms = list(rep["by_arm"])
    print(
        f"pilot {rep['pilot_id']} · {len(rep['questions'])} questions · "
        f"{rep['model']} ({rep['effort']})"
    )
    head = f"{'question':<9}{'trap':<8}" + "".join(f"{a:>14}" for a in arms)
    print(head)
    print("-" * len(head))
    for qid, q in rep["by_question"].items():
        cells = []
        for a in arms:
            res = q["arms"][a]["runs"]
            mark = "".join("✓" if x else "✗" for x in res) or "-"
            cells.append(f"{mark + (' R' if q['arms'][a]['right'] else '  '):>14}")
        print(f"{qid:<9}{q['trap']:<8}" + "".join(cells))
    print("-" * len(head))
    for a, s in rep["by_arm"].items():
        print(
            f"{a}: right (≥2/3) {s['right_2_of_3']}/{s['graded_questions']} · "
            f"wrong by trap {s['wrong_by_trap']} · "
            f"runs {s['runs_correct']}/{s['runs']} correct · ${s['cost_usd']:.2f} "
            f"(${s['cost_per_run_usd'] or 0:.2f}/run) · flags {s['flags']} · "
            f"stops {s['stop_reasons']}"
        )
    if rep["gold_errors"]:
        print(f"no gold for: {sorted(rep['gold_errors'])}")


def cmd_report(args: argparse.Namespace) -> int:
    cfg = load_config()
    pilot_id, qids = load_pilot(Path(args.pilot))
    pilot_id = args.pilot_id or pilot_id
    rep = build_report(cfg, pilot_id, qids, load_questions())
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, indent=1, default=str))
    print_summary(rep)
    print(f"wrote {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--arm", required=True, help="raw, or a layer name such as starter")
    r.add_argument("--runs", type=int, default=3)
    r.add_argument("--limit", type=int, default=0, help="only the first N pilot questions")
    r.add_argument("--concurrency", type=int, default=4)
    r.add_argument(
        "--dry-run", action="store_true", help="print the plan and estimate; call nothing"
    )
    for p in (r, sub.add_parser("report")):
        p.add_argument("--pilot", default=str(PILOT_PATH))
        p.add_argument("--pilot-id", default="", help="override pilot_id from the pilot file")
    sub.choices["report"].add_argument("--out", default=str(REPORT_PATH))
    args = ap.parse_args(argv)
    return cmd_run(args) if args.cmd == "run" else cmd_report(args)


if __name__ == "__main__":
    sys.exit(main())
