"""Step 2 check: run the brief's main example through the tools, without any AI.

The items below are what the agent should understand from:
"وصلت من شركة أ: الطابعة والمكتب وثلاث شاشات، وحدة منها مكسورة. الماوس ما وصل."
"""
from wakeel import db, tools

conn = db.reset()
delivery = tools.start_delivery(conn)

items = [
    {"item_code": "PRN", "qty_delivered": 1, "qty_damaged": 0, "delivered_model": None, "not_delivered": False},
    {"item_code": "DSK", "qty_delivered": 1, "qty_damaged": 0, "delivered_model": None, "not_delivered": False},
    {"item_code": "MON", "qty_delivered": 3, "qty_damaged": 1, "delivered_model": None, "not_delivered": False},
    {"item_code": "MSE", "qty_delivered": 0, "qty_damaged": 0, "delivered_model": None, "not_delivered": True},
]

match = tools.match_bill(conn, "SUP-A", items)
print("Match:", match["status"])

proposal = tools.propose_receiving(conn, delivery, "SUP-A", match["allocations"], match["flags"])
for rec in proposal["records"]:
    print(f"\nBill {rec['bill_id']}")
    for line in rec["lines"]:
        print(f"  {line['item_en']:<10} {line['status']:<12} ok={line['qty_ok']} damaged={line['qty_damaged']} missing={line['qty_missing']}")
    print(f"  PAY {rec['pay']:,.0f} SAR   HOLD {rec['hold']:,.0f} SAR")
    print("  Hold breakdown (from the bill's unit prices):")
    for h in rec["hold_breakdown"]:
        print(f"    {h['item_en']:<10} {h['reason']:<12} {h['qty']} x {h['unit_price']:,.0f} = {h['amount']:,.0f} SAR")

print("\n--- After the gate keeper taps Confirm ---")
result = tools.confirm_delivery(conn, delivery)
print("Finance:", result["finance"][0])
print("Email subject:", result["emails"][0]["subject"])
print("Follow-ups:", [(f["reason"], f["qty"], f["due_date"]) for f in result["followups"]])