"""Step 3 check: the agent reads real Arabic sentences.

    python step3_check.py              # Claude if ANTHROPIC_API_KEY is in .env, else offline
    python step3_check.py offline      # force the offline rule-based parser
"""
import os
import sys

if len(sys.argv) > 1:
    os.environ["WAKEEL_ENGINE"] = sys.argv[1]

from wakeel import agent, db, tools

SENTENCES = [
    "وصلت من شركة أ: الطابعة والمكتب وثلاث شاشات، وحدة منها مكسورة. الماوس ما وصل.",
    "شركة أ جابوا الطابعة والمكتب و٣ شاشات، وحده منهم خربانه، والماوس ماجا",
    "من شركة أ وصلت ثلاث شاشات سامسونج بدل ديل، والطابعة والمكتب والماوس",
    "شركة ب جابت ست كراسي بس",
    "شركة أ: خمس كيبوردات واربع لابتوبات مع اربع ماوسات",
    "شركة أ جابت شاشة وحدة",
]

print("Engine:", agent.engine_name(), "\n")
for text in SENTENCES:
    conn = db.reset()                      # fresh demo data for every sentence
    delivery = tools.start_delivery(conn)
    result = agent.handle(conn, delivery, text)
    print("=" * 70)
    print("GATE KEEPER:", text)
    if result["type"] == "proposal":
        for rec in result["proposal"]["records"]:
            lines = ", ".join(f"{l['item_en']} {l['status']}" for l in rec["lines"])
            print(f"  -> Bill {rec['bill_id']}: {lines}")
            print(f"     PAY {rec['pay']:,.0f} SAR   HOLD {rec['hold']:,.0f} SAR")
        for f in result["proposal"]["flags"]:
            print("  !!", f["message"])
    else:
        print("  -> WAKEEL asks:", result.get("question_en") or result["message"])
        for o in result.get("options", []):
            print("     option:", o.get("bill_id") or o.get("supplier_id"))
print("=" * 70)
