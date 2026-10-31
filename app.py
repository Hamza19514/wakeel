"""WAKEEL web app: gate keeper screen (/gate) and finance screen (/finance).

    python app.py      # http://localhost:8000
"""
import itertools
import json
import os

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel

from wakeel import agent, db, mailer, report, tools

HERE = os.path.dirname(os.path.abspath(__file__))
app = FastAPI(title="WAKEEL")

# Fake RFID gate events: each tag is one unit. Real readers would post the same shape.
RFID_EVENTS = itertools.cycle([
    {"reader": "GATE-1", "supplier_id": "SUP-B", "tags": [{"epc": "E200-PRJ-0001", "item_code": "PRJ"}]
     + [{"epc": f"E200-HDM-{i:04d}", "item_code": "HDM"} for i in range(1, 11)]},
    {"reader": "GATE-1", "supplier_id": "SUP-C", "tags": [{"epc": f"E200-UPS-{i:04d}", "item_code": "UPS"} for i in range(1, 4)]
     + [{"epc": f"E200-RTR-{i:04d}", "item_code": "RTR"} for i in range(1, 3)]},
    {"reader": "GATE-1", "supplier_id": "SUP-A", "tags": [{"epc": f"E200-LAP-{i:04d}", "item_code": "LAP"} for i in range(1, 5)]},
])


def conn():
    if not os.path.exists(db.DB_PATH):
        db.reset()
    return db.connect()


class NewDelivery(BaseModel):
    source: str = "text"
    supplier_id: str | None = None


class Message(BaseModel):
    text: str | None = None
    supplier_id: str | None = None
    prefer_bills: list[str] | None = None
    image: dict | None = None  # {"media_type": "image/jpeg", "data": "<base64>"}
    speech_alternatives: list[str] | None = None  # other speech-to-text guesses for the same words
    replace_text: bool = False  # the gate keeper picked which heard sentence was right


@app.get("/")
def index():
    return RedirectResponse("/gate")


@app.get("/gate")
def gate():
    return FileResponse(os.path.join(HERE, "web", "gate.html"))


@app.get("/finance")
def finance():
    return FileResponse(os.path.join(HERE, "web", "finance.html"))


@app.get("/api/info")
def info():
    c = conn()
    return {"engine": agent.engine_name(), "model": agent.MODEL if agent.engine_name() == "claude" else None,
            "email": "Gmail" if mailer.configured() else "drafts only",
            "suppliers": tools.list_suppliers(c)}


@app.post("/api/deliveries")
def new_delivery(body: NewDelivery):
    return {"delivery_id": tools.start_delivery(conn(), body.source, body.supplier_id)}


@app.post("/api/deliveries/{delivery_id}/message")
def message(delivery_id: str, body: Message):
    c = conn()
    if body.image:
        c.execute("UPDATE deliveries SET source='photo' WHERE id=?", (delivery_id,))
    if body.speech_alternatives:
        c.execute("UPDATE deliveries SET source='voice' WHERE id=?", (delivery_id,))
    return agent.handle(c, delivery_id, body.text, body.supplier_id, body.prefer_bills, body.image,
                        speech_alternatives=body.speech_alternatives, replace_text=body.replace_text)


@app.post("/api/deliveries/{delivery_id}/rfid")
def rfid(delivery_id: str):
    event = next(RFID_EVENTS)
    counts = {}
    for t in event["tags"]:
        counts[t["item_code"]] = counts.get(t["item_code"], 0) + 1
    items = [{"item_code": k, "qty_delivered": v, "qty_damaged": 0, "delivered_model": None, "not_delivered": False}
             for k, v in counts.items()]
    c = conn()
    c.execute("UPDATE deliveries SET source='rfid' WHERE id=?", (delivery_id,))
    return agent.handle(c, delivery_id, supplier_id=event["supplier_id"], items=items) | {"rfid_event": event}


@app.post("/api/deliveries/{delivery_id}/confirm")
def confirm(delivery_id: str):
    c = conn()
    try:
        out = tools.confirm_delivery(c, delivery_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    paths = [report.build_report(c, r) for r in out["records"]]
    out["reports"] = [{"record_id": r, "url": f"/api/reports/{r}", "name": os.path.basename(p)} for r, p in zip(out["records"], paths)]
    out["sent"] = mailer.send_delivery(c, out, paths)
    return out


@app.get("/api/reports/{record_id}")
def get_report(record_id: int):
    row = conn().execute("SELECT report_path FROM finance_notifications WHERE record_id=?", (record_id,)).fetchone()
    if not row or not row[0] or not os.path.exists(row[0]):
        raise HTTPException(404, "Report not found")
    return FileResponse(row[0], media_type="application/pdf", filename=os.path.basename(row[0]))


@app.post("/api/deliveries/{delivery_id}/cancel")
def cancel(delivery_id: str):
    c = conn()
    c.execute("UPDATE deliveries SET status='cancelled' WHERE id=? AND status!='confirmed'", (delivery_id,))
    c.commit()
    return {"ok": True}


def rows(c, sql, *args):
    return [dict(r) for r in c.execute(sql, args)]


@app.get("/api/finance")
def finance_data():
    c = conn()
    bills = []
    for b in rows(c, "SELECT b.*, s.name_en FROM bills b JOIN suppliers s ON s.id=b.supplier_id ORDER BY b.supplier_id, b.bill_date"):
        lines = rows(c, "SELECT bl.*, i.name_en AS item FROM bill_lines bl JOIN items i ON i.code=bl.item_code WHERE bill_id=?", b["id"])
        b["total"] = sum(l["qty_ordered"] * l["unit_price"] for l in lines)
        b["received_value"] = sum(l["qty_received"] * l["unit_price"] for l in lines)
        b["lines"] = lines
        bills.append(b)
    return {
        "notifications": rows(c, "SELECT * FROM finance_notifications ORDER BY id DESC"),
        "emails": rows(c, "SELECT * FROM supplier_emails ORDER BY id DESC"),
        "followups": rows(c, "SELECT f.*, i.name_en AS item FROM followups f JOIN bill_lines bl ON bl.id=f.bill_line_id "
                             "JOIN items i ON i.code=bl.item_code ORDER BY f.due_date, f.id"),
        "alerts": rows(c, "SELECT * FROM manager_alerts ORDER BY id DESC"),
        "bills": bills,
    }


@app.post("/api/finance/{notification_id}/approve")
def approve(notification_id: int):
    c = conn()
    c.execute("UPDATE finance_notifications SET status='approved_by_finance' WHERE id=?", (notification_id,))
    c.commit()
    return {"ok": True}


@app.get("/api/metrics")
def metrics():
    c = conn()
    done = rows(c, "SELECT * FROM deliveries WHERE status='confirmed'")
    from datetime import datetime
    mins = [(datetime.fromisoformat(d["confirmed_at"]) - datetime.fromisoformat(d["started_at"])).total_seconds() / 60 for d in done]
    lines = rows(c, "SELECT status, COUNT(*) n FROM receiving_lines GROUP BY status")
    alerts = rows(c, "SELECT kind, COUNT(*) n FROM manager_alerts GROUP BY kind")
    trial = None
    path = os.path.join(HERE, "tests", "results.json")
    if os.path.exists(path):
        trial = json.load(open(path, encoding="utf-8"))["summary"]
    return {
        "deliveries_confirmed": len(done),
        "avg_minutes_gate_to_finance": round(sum(mins) / len(mins), 2) if mins else None,
        "no_touch_share": round(sum(1 for d in done if not d["clarifications"]) / len(done), 2) if done else None,
        "line_statuses": {l["status"]: l["n"] for l in lines},
        "alerts": {a["kind"]: a["n"] for a in alerts},
        "trial": trial,
    }


@app.post("/api/reset")
def reset():
    db.reset()
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
