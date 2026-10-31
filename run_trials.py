"""Run the test deliveries through WAKEEL and score them.

Each case runs on a fresh copy of the demo database. Results go to
tests/results.md and tests/results.json.

    python run_trials.py                          # engine picked automatically (claude if the key is in .env)
    python run_trials.py offline                  # force the offline engine
    python run_trials.py claude tests/team.json   # another test file, e.g. sentences your team wrote
    python run_trials.py claude tests/test_deliveries.json T11,T15   # only some cases
"""
import json
import os
import sys
import tempfile
import time

if len(sys.argv) > 1:
    os.environ["WAKEEL_ENGINE"] = sys.argv[1]

from wakeel import agent, db, tools

HERE = os.path.dirname(os.path.abspath(__file__))


def caught(kind, result, asked):
    lines = [l for r in result.get("proposal", {}).get("records", []) for l in r["lines"]]
    flags = {f["kind"] for f in result.get("proposal", {}).get("flags", [])}
    return {
        "damaged": any(l["qty_damaged"] for l in lines),
        "missing": any(l["qty_missing"] for l in lines),
        "partial": any(l["qty_delivered"] and l["qty_missing"] for l in lines),
        "wrong_model": any(l["qty_wrong_model"] for l in lines),
        "split": len(result.get("proposal", {}).get("records", [])) > 1,
        "ambiguous": asked,
        "duplicate": "duplicate" in flags,
        "unexpected_item": "unexpected_item" in flags,
    }[kind]


def run_case(case, path):
    conn = db.reset(path)
    for text in case.get("setup", []):
        d = tools.start_delivery(conn)
        agent.handle(conn, d, text)
        tools.confirm_delivery(conn, d)

    t0 = time.perf_counter()
    if "rfid" in case:
        d = tools.start_delivery(conn, source="rfid")
        res = agent.handle(conn, d, supplier_id=case["rfid"]["supplier_id"], items=case["rfid"]["items"])
    else:
        d = tools.start_delivery(conn)
        res = agent.handle(conn, d, case["text"])
    asked = res["type"] == "question"
    if asked and case.get("answer"):
        res = agent.handle(conn, d, supplier_id=case["answer"].get("supplier_id"), prefer_bills=case["answer"].get("prefer_bills"))
    elapsed = time.perf_counter() - t0

    alerts = []
    if res["type"] == "proposal":
        tools.confirm_delivery(conn, d)
        alerts = [r["kind"] for r in conn.execute("SELECT kind FROM manager_alerts")]

    exp, problems = case["expect"], []
    if exp.get("question", False) != asked:
        problems.append(f"expected question={exp.get('question', False)}, got {asked}")
    got_bills = {r["bill_id"]: r for r in res.get("proposal", {}).get("records", [])}
    if set(got_bills) != set(exp["bills"]):
        problems.append(f"bills {sorted(got_bills)} != expected {sorted(exp['bills'])}")
    for bid, e in exp["bills"].items():
        g = got_bills.get(bid)
        if not g:
            continue
        if (g["pay"], g["hold"]) != (e["pay"], e["hold"]):
            problems.append(f"{bid}: pay/hold {g['pay']:.0f}/{g['hold']:.0f} != {e['pay']}/{e['hold']}")
        statuses = {l["item_code"]: l["status"] for l in g["lines"]}
        for code, st in e["lines"].items():
            if statuses.get(code) != st:
                problems.append(f"{bid} {code}: {statuses.get(code)} != {st}")
    got_flags = sorted({f["kind"] for f in res.get("proposal", {}).get("flags", [])})
    if got_flags != sorted(exp.get("flags", [])):
        problems.append(f"flags {got_flags} != {sorted(exp.get('flags', []))}")
    for a in exp.get("alerts", []):
        if a not in alerts:
            problems.append(f"missing manager alert {a}")

    planted = {k: caught(k, res, asked) for k in case["planted"]}
    return {
        "id": case["id"], "title": case["title"], "passed": not problems, "problems": problems,
        "asked_question": asked, "no_touch": res["type"] == "proposal" and not asked,
        "planted": planted, "seconds": round(elapsed, 3), "engine": res.get("engine"),
        "final": res.get("message"),
    }


def main():
    test_file = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, "tests", "test_deliveries.json")
    cases = json.load(open(test_file, encoding="utf-8"))
    if len(sys.argv) > 3:
        only = sys.argv[3].split(",")
        cases = [c for c in cases if c["id"] in only]
    tmp = os.path.join(tempfile.mkdtemp(), "trial.db")
    results = []
    for c in cases:
        results.append(run_case(c, tmp))
        print(f"{c['id']}: {'PASS' if results[-1]['passed'] else 'FAIL'}", flush=True)

    n = len(results)
    passed = sum(r["passed"] for r in results)
    planted = sum(len(r["planted"]) for r in results)
    found = sum(v for r in results for v in r["planted"].values())
    no_touch = sum(r["no_touch"] for r in results)
    avg = sum(r["seconds"] for r in results) / n
    summary = {
        "engine": results[0]["engine"], "cases": n, "passed": passed,
        "planted_problems": planted, "problems_caught": found,
        "no_touch_share": round(no_touch / n, 3), "avg_agent_seconds": round(avg, 3),
    }

    md = ["# WAKEEL trial results", "", f"Test file: `{os.path.relpath(test_file, HERE)}`  ",
          f"Engine: **{summary['engine']}** · Cases: **{n}** · Passed: **{passed}/{n}**  ",
          f"Planted problems caught: **{found}/{planted}** · No-touch share: **{no_touch}/{n}** "
          f"(reached the confirm screen with no question) · Avg agent time: **{avg:.2f}s**", "",
          "| Case | Scenario | Result | Question asked | Planted problems caught | Notes |",
          "|---|---|---|---|---|---|"]
    for r in results:
        pc = ", ".join(f"{k} {'✓' if v else '✗'}" for k, v in r["planted"].items()) or "-"
        md.append(f"| {r['id']} | {r['title']} | {'PASS' if r['passed'] else 'FAIL'} | {'yes' if r['asked_question'] else 'no'} "
                  f"| {pc} | {'; '.join(r['problems'])} |")
    # Keep one result file per engine so the two runs can be compared side by side.
    name = f"results_{summary['engine']}_{os.path.splitext(os.path.basename(test_file))[0]}"
    for path in (name, "results"):
        with open(os.path.join(HERE, "tests", path + ".md"), "w", encoding="utf-8") as f:
            f.write("\n".join(md) + "\n")
        with open(os.path.join(HERE, "tests", path + ".json"), "w", encoding="utf-8") as f:
            json.dump({"summary": summary, "results": results}, f, ensure_ascii=False, indent=2)
    print()
    print("\n".join(md))
    print(f"\nSaved to tests/{name}.md")


if __name__ == "__main__":
    main()
