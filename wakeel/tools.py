"""WAKEEL agent tools.

These are deterministic and shared by both engines (the Claude agent and the
offline rule-based engine). Money and database writes never depend on free
text from the model: the model only supplies the structured items it heard.

Flow:  find_open_bills -> match_bill -> propose_receiving (draft, shown to the
gate keeper) -> confirm_delivery, which runs create_receiving_record,
decide_payment, notify_finance, draft_supplier_email and schedule_followup.
"""
import json
import uuid
from datetime import date, timedelta

from . import db

FOLLOWUP_DAYS = 7
MANAGER_HOLD_THRESHOLD = 5000  # SAR: holds above this go to the manager


# ---------------------------------------------------------------- lookups

def list_suppliers(conn):
    return [
        {"id": r["id"], "name_en": r["name_en"], "name_ar": r["name_ar"], "aliases": json.loads(r["aliases"])}
        for r in conn.execute("SELECT * FROM suppliers ORDER BY id")
    ]


def list_items(conn):
    return [
        {"code": r["code"], "name_en": r["name_en"], "name_ar": r["name_ar"], "aliases": json.loads(r["aliases"])}
        for r in conn.execute("SELECT * FROM items ORDER BY code")
    ]


def _supplier(conn, supplier_id):
    return conn.execute("SELECT * FROM suppliers WHERE id=?", (supplier_id,)).fetchone()


def _item_names(conn):
    return {r["code"]: (r["name_en"], r["name_ar"]) for r in conn.execute("SELECT code, name_en, name_ar FROM items")}


def find_open_bills(conn, supplier_id):
    """All open bills for a supplier, with what is still outstanding on each line."""
    bills = []
    for b in conn.execute("SELECT * FROM bills WHERE supplier_id=? AND status='open' ORDER BY bill_date", (supplier_id,)):
        lines = []
        for l in conn.execute("SELECT * FROM bill_lines WHERE bill_id=? ORDER BY id", (b["id"],)):
            lines.append({
                "bill_line_id": l["id"],
                "item_code": l["item_code"],
                "model": l["model"],
                "qty_ordered": l["qty_ordered"],
                "qty_received": l["qty_received"],
                "qty_remaining": l["qty_ordered"] - l["qty_received"],
                "unit_price": l["unit_price"],
                "line_total": l["line_total"],
                "expected_delivery_date": l["expected_delivery_date"],
            })
        bills.append({"bill_id": b["id"], "po_id": b["po_id"], "bill_date": b["bill_date"], "lines": lines})
    return bills


# ---------------------------------------------------------------- matching

def _model_differs(delivered_model, ordered_model):
    if not delivered_model:
        return False
    d, o = delivered_model.lower().strip(), ordered_model.lower()
    if d in o:
        return False
    brand = o.split()[0]
    return brand not in d


def match_bill(conn, supplier_id, items, prefer_bills=None):
    """Pick the right open bill line for every delivered item.

    items: [{item_code, qty_delivered (int or null = everything outstanding),
             qty_damaged, delivered_model, not_delivered}]
    Returns {"status": "matched", "allocations": [...], "flags": [...]}
         or {"status": "needs_clarification", "question": ..., "options": [...]}.
    """
    prefer_bills = [p.upper() for p in (prefer_bills or [])]
    bills = find_open_bills(conn, supplier_id)
    names = _item_names(conn)

    # Evidence per bill: how many of the mentioned items it carries.
    mentioned = {i["item_code"] for i in items}
    score = {b["bill_id"]: sum(1 for l in b["lines"] if l["item_code"] in mentioned and l["qty_remaining"] > 0) for b in bills}
    bill_by_id = {b["bill_id"]: b for b in bills}

    allocations, flags = [], []
    remaining = {l["bill_line_id"]: l["qty_remaining"] for b in bills for l in b["lines"]}

    for it in items:
        code = it["item_code"]
        name_en, name_ar = names.get(code, (code, code))
        cands = [(b, l) for b in bills for l in b["lines"] if l["item_code"] == code and remaining[l["bill_line_id"]] > 0]

        if it.get("not_delivered"):
            continue  # mentioned as missing: lines on the matched bill are marked missing anyway

        qty = it.get("qty_delivered")
        if not cands:
            prev = conn.execute(
                "SELECT bl.bill_id, bl.qty_received FROM bill_lines bl JOIN bills b ON b.id=bl.bill_id "
                "WHERE b.supplier_id=? AND bl.item_code=? AND bl.qty_received>0 ORDER BY b.bill_date DESC",
                (supplier_id, code),
            ).fetchall()
            if prev:
                flags.append({
                    "kind": "duplicate", "item_code": code, "qty": qty,
                    "message": f"{name_en} x{qty or '?'}: already fully received on bill {prev[0]['bill_id']}. "
                               f"Possible duplicate delivery or duplicate bill - do not pay twice.",
                })
            else:
                flags.append({
                    "kind": "unexpected_item", "item_code": code, "qty": qty,
                    "message": f"{name_en} x{qty or '?'}: not on any open bill from this supplier.",
                })
            continue

        # Order candidates: preferred bills, then bill evidence, then exact quantity, then oldest.
        def key(c):
            b, l = c
            return (
                0 if b["bill_id"] in prefer_bills else 1,
                -score[b["bill_id"]],
                0 if qty is not None and remaining[l["bill_line_id"]] == qty else 1,
                b["bill_date"],
            )
        cands.sort(key=key)

        cand_bills = list(dict.fromkeys(b["bill_id"] for b, _ in cands))
        if len(cand_bills) > 1 and not any(b in prefer_bills for b in cand_bills):
            k0, k1 = key(cands[0]), key(next(c for c in cands if c[0]["bill_id"] != cands[0][0]["bill_id"]))
            # Same evidence and no exact-quantity winner: ask one short question.
            # (Splitting across bills is only automatic when the quantity overflows one bill.)
            fits_one = qty is not None and qty <= remaining[cands[0][1]["bill_line_id"]]
            if k0[:3] == k1[:3] and (fits_one or qty is None):
                opts = []
                for bid in cand_bills:
                    b = bill_by_id[bid]
                    l = next(l for l in b["lines"] if l["item_code"] == code)
                    others = [names[x["item_code"]][1] for x in b["lines"] if x["item_code"] != code and x["qty_remaining"] > 0]
                    opts.append({
                        "bill_id": bid, "bill_date": b["bill_date"],
                        "label": f"{bid} ({b['bill_date']}) - {name_ar} ×{remaining[l['bill_line_id']]}"
                                 + (f" مع {'، '.join(others)}" if others else ""),
                    })
                return {
                    "status": "needs_clarification",
                    "question": f"الـ{name_ar} موجودة في أكثر من فاتورة مفتوحة. هل وصل معها شيء آخر، أو أي فاتورة تقصد؟",
                    "question_en": f"{name_en} is on {len(cand_bills)} open bills. Which bill is this delivery for?",
                    "options": opts,
                }

        left = qty if qty is not None else remaining[cands[0][1]["bill_line_id"]]
        damaged_left = it.get("qty_damaged") or 0
        for b, l in cands:
            if left <= 0:
                break
            take = min(left, remaining[l["bill_line_id"]])
            dmg = min(damaged_left, take)
            allocations.append({
                "bill_id": b["bill_id"], "bill_line_id": l["bill_line_id"], "item_code": code,
                "qty_delivered": take, "qty_damaged": dmg,
                "delivered_model": it.get("delivered_model"),
            })
            remaining[l["bill_line_id"]] -= take
            left -= take
            damaged_left -= dmg
        if left > 0:
            flags.append({
                "kind": "duplicate", "item_code": code, "qty": left,
                "message": f"{name_en}: {left} more than is outstanding on all open bills. "
                           f"Possible duplicate or over-delivery - extra units not accepted.",
            })

    return {"status": "matched", "allocations": allocations, "flags": flags}


# ---------------------------------------------------------------- draft

def decide_payment(lines):
    """Pay / hold per line. Pay only for units received in good condition and right model."""
    pay = sum(l["qty_ok"] * l["unit_price"] for l in lines)
    hold = sum((l["qty_damaged"] + l["qty_wrong_model"] + l["qty_missing"]) * l["unit_price"] for l in lines)
    return round(pay, 2), round(hold, 2)


def hold_breakdown(lines):
    """Every held riyal traced to a bill line: qty x that line's unit price."""
    out = []
    for l in lines:
        for reason, q in (("damaged", l["qty_damaged"]), ("wrong_model", l["qty_wrong_model"]), ("missing", l["qty_missing"])):
            if q:
                out.append({"item_en": l["item_en"], "item_ar": l["item_ar"], "reason": reason, "qty": q,
                            "unit_price": l["unit_price"], "amount": round(q * l["unit_price"], 2)})
    return out


def propose_receiving(conn, delivery_id, supplier_id, allocations, flags=None):
    """Build the receiving record draft, one per bill, and store it on the delivery."""
    names = _item_names(conn)
    flags = flags or []
    bill_ids = list(dict.fromkeys(a["bill_id"] for a in allocations if a["qty_delivered"] > 0))
    records = []
    for bid in bill_ids:
        bill = conn.execute("SELECT * FROM bills WHERE id=?", (bid,)).fetchone()
        lines = []
        for l in conn.execute("SELECT * FROM bill_lines WHERE bill_id=? ORDER BY id", (bid,)):
            outstanding = l["qty_ordered"] - l["qty_received"]
            if outstanding <= 0:
                continue
            allocs = [a for a in allocations if a["bill_line_id"] == l["id"]]
            delivered = sum(a["qty_delivered"] for a in allocs)
            damaged = sum(a.get("qty_damaged") or 0 for a in allocs)
            dmodel = next((a.get("delivered_model") for a in allocs if a.get("delivered_model")), None)
            wrong = delivered if _model_differs(dmodel, l["model"]) else 0
            if wrong:
                damaged = 0
            ok = max(0, delivered - damaged - wrong)
            missing = max(0, outstanding - delivered)
            if wrong:
                status = "wrong_model"
            elif damaged:
                status = "damaged"
            elif delivered and missing:
                status = "partial"
            elif missing:
                status = "missing"
            else:
                status = "received"
            lines.append({
                "bill_line_id": l["id"], "item_code": l["item_code"],
                "item_en": names[l["item_code"]][0], "item_ar": names[l["item_code"]][1],
                "model": l["model"], "delivered_model": dmodel,
                "qty_outstanding": outstanding, "qty_delivered": delivered,
                "qty_ok": ok, "qty_damaged": damaged, "qty_wrong_model": wrong, "qty_missing": missing,
                "unit_price": l["unit_price"], "status": status,
            })
        pay, hold = decide_payment(lines)
        records.append({"bill_id": bid, "bill_date": bill["bill_date"], "lines": lines, "pay": pay, "hold": hold,
                        "hold_breakdown": hold_breakdown(lines),
                        "next_actions": _next_actions(lines, pay, hold)})

    proposal = {"delivery_id": delivery_id, "supplier_id": supplier_id, "records": records, "flags": flags,
                "total_pay": round(sum(r["pay"] for r in records), 2),
                "total_hold": round(sum(r["hold"] for r in records), 2)}
    conn.execute("UPDATE deliveries SET supplier_id=?, proposal=?, status='awaiting_confirmation' WHERE id=?",
                 (supplier_id, json.dumps(proposal, ensure_ascii=False), delivery_id))
    conn.commit()
    return proposal


def _next_actions(lines, pay, hold):
    acts = []
    if hold == 0:
        acts.append("Tell finance to pay the full bill and close it.")
    else:
        acts.append(f"Tell finance to pay {pay:,.0f} SAR and hold {hold:,.0f} SAR.")
    if any(l["qty_damaged"] for l in lines):
        acts.append("Email the supplier a damage report and ask for replacements.")
    if any(l["qty_missing"] for l in lines):
        acts.append(f"Email the supplier about missing items; follow up in {FOLLOWUP_DAYS} days; keep the bill open.")
    if any(l["qty_wrong_model"] for l in lines):
        acts.append("Hold the wrong-model line and ask procurement whether to accept the substitution.")
    return acts


# ---------------------------------------------------------------- actions on confirm

def create_receiving_record(conn, delivery, record, confirmed_by):
    cur = conn.execute(
        "INSERT INTO receiving_records (bill_id, delivery_id, source, raw_input, created_at, confirmed_by) VALUES (?,?,?,?,?,?)",
        (record["bill_id"], delivery["id"], delivery["source"], delivery["raw_input"], db.now(), confirmed_by),
    )
    rid = cur.lastrowid
    for l in record["lines"]:
        conn.execute(
            "INSERT INTO receiving_lines (record_id, bill_line_id, qty_ok, qty_damaged, qty_wrong_model, qty_missing, delivered_model, status) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (rid, l["bill_line_id"], l["qty_ok"], l["qty_damaged"], l["qty_wrong_model"], l["qty_missing"], l["delivered_model"], l["status"]),
        )
        # Only good, correct-model units count as received; the rest stays open on the bill.
        conn.execute("UPDATE bill_lines SET qty_received = qty_received + ? WHERE id=?", (l["qty_ok"], l["bill_line_id"]))
    left = conn.execute("SELECT SUM(qty_ordered - qty_received) FROM bill_lines WHERE bill_id=?", (record["bill_id"],)).fetchone()[0]
    if not left:
        conn.execute("UPDATE bills SET status='closed' WHERE id=?", (record["bill_id"],))
    return rid


def _issue_phrases(lines, lang, prices=False):
    out = []
    for l in lines:
        n = l["item_en"].lower() if lang == "en" else l["item_ar"]

        def cost(q):
            return f" x {l['unit_price']:,.0f} = {q * l['unit_price']:,.0f} SAR" if prices else ""
        if l["qty_damaged"]:
            out.append(f"{l['qty_damaged']} {n} damaged{cost(l['qty_damaged'])} (replacement requested)" if lang == "en"
                       else f"{n} عدد {l['qty_damaged']} وصل تالفاً، نطلب الاستبدال")
        if l["qty_wrong_model"]:
            out.append(f"{l['qty_wrong_model']} {n} wrong model{cost(l['qty_wrong_model'])} ({l['delivered_model']} instead of {l['model']}) - awaiting procurement"
                       if lang == "en" else f"{n} عدد {l['qty_wrong_model']} بموديل مختلف ({l['delivered_model']} بدلاً من {l['model']})")
        if l["qty_missing"]:
            out.append(f"{l['qty_missing']} {n} not delivered{cost(l['qty_missing'])} (follow-up set)" if lang == "en"
                       else f"{n} عدد {l['qty_missing']} لم يصل")
    return out


def notify_finance(conn, supplier, record):
    issues = _issue_phrases(record["lines"], "en", prices=True)
    if record["hold"] == 0:
        msg = f"Bill {record['bill_id']}, {supplier['name_en']}: all items received in good condition. Approve full payment of {record['pay']:,.0f} SAR. Receiving record attached."
    else:
        msg = (f"Bill {record['bill_id']}, {supplier['name_en']}: pay {record['pay']:,.0f} SAR now. "
               f"Hold {record['hold']:,.0f} SAR: {'; '.join(issues)}. Receiving record attached.")
    conn.execute("INSERT INTO finance_notifications (bill_id, pay_amount, hold_amount, message, created_at) VALUES (?,?,?,?,?)",
                 (record["bill_id"], record["pay"], record["hold"], msg, db.now()))
    return msg


def draft_supplier_email(conn, supplier, record):
    ar_issues = _issue_phrases(record["lines"], "ar")
    if not ar_issues:
        return None
    en_issues = _issue_phrases(record["lines"], "en")
    subject = f"Delivery for bill {record['bill_id']} / بخصوص توريد الفاتورة {record['bill_id']}"
    body_ar = (f"السادة {supplier['name_ar']} المحترمين،\n\nالسلام عليكم ورحمة الله وبركاته،\n\n"
               f"استلمنا اليوم التوريد الخاص بالفاتورة رقم {record['bill_id']}، ونود إفادتكم بالملاحظات التالية:\n"
               + "\n".join(f"- {x}" for x in ar_issues)
               + "\n\nنرجو التكرم بإفادتنا بموعد توريد البنود المتبقية أو المستبدلة. "
               "ستبقى الفاتورة مفتوحة حتى استكمال التوريد.\n\nمع خالص الشكر،\nفريق الاستلام")
    body_en = (f"Dear {supplier['name_en']},\n\nWe received today's delivery for bill {record['bill_id']} with the following issues:\n"
               + "\n".join(f"- {x}" for x in en_issues)
               + "\n\nPlease confirm when the remaining or replacement items will be delivered. "
               "The bill stays open until then.\n\nBest regards,\nReceiving team")
    conn.execute("INSERT INTO supplier_emails (supplier_id, bill_id, subject, body_ar, body_en, created_at) VALUES (?,?,?,?,?,?)",
                 (supplier["id"], record["bill_id"], subject, body_ar, body_en, db.now()))
    return {"subject": subject, "body_ar": body_ar, "body_en": body_en}


def schedule_followup(conn, record):
    due = (date.today() + timedelta(days=FOLLOWUP_DAYS)).isoformat()
    made = []
    for l in record["lines"]:
        for reason, q in (("missing", l["qty_missing"]), ("replacement", l["qty_damaged"]), ("substitution_decision", l["qty_wrong_model"])):
            if q:
                conn.execute("INSERT INTO followups (bill_id, bill_line_id, reason, qty, due_date) VALUES (?,?,?,?,?)",
                             (record["bill_id"], l["bill_line_id"], reason, q, due))
                made.append({"bill_line_id": l["bill_line_id"], "reason": reason, "qty": q, "due_date": due})
    return made


def _alert(conn, bill_id, kind, message):
    conn.execute("INSERT INTO manager_alerts (bill_id, kind, message, created_at) VALUES (?,?,?,?)", (bill_id, kind, message, db.now()))


def confirm_delivery(conn, delivery_id, confirmed_by="gate keeper"):
    """The one human tap. Saves the record and runs every follow-on action."""
    d = conn.execute("SELECT * FROM deliveries WHERE id=?", (delivery_id,)).fetchone()
    if not d or not d["proposal"]:
        raise ValueError("Nothing to confirm for this delivery.")
    if d["status"] == "confirmed":
        raise ValueError("This delivery is already confirmed.")
    proposal = json.loads(d["proposal"])
    supplier = _supplier(conn, proposal["supplier_id"])
    out = {"records": [], "finance": [], "emails": [], "followups": [], "alerts": []}
    for rec in proposal["records"]:
        out["records"].append(create_receiving_record(conn, d, rec, confirmed_by))
        out["finance"].append(notify_finance(conn, supplier, rec))
        email = draft_supplier_email(conn, supplier, rec)
        if email:
            out["emails"].append(email)
        out["followups"] += schedule_followup(conn, rec)
        for l in rec["lines"]:
            if l["qty_wrong_model"]:
                msg = f"{rec['bill_id']}: {l['item_en']} delivered as {l['delivered_model']} instead of {l['model']}. Accept substitution?"
                _alert(conn, rec["bill_id"], "substitution", msg)
                out["alerts"].append(msg)
        if rec["hold"] > MANAGER_HOLD_THRESHOLD:
            msg = f"{rec['bill_id']}: {rec['hold']:,.0f} SAR on hold (above {MANAGER_HOLD_THRESHOLD:,} SAR threshold)."
            _alert(conn, rec["bill_id"], "high_hold", msg)
            out["alerts"].append(msg)
    for f in proposal["flags"]:
        _alert(conn, None, f["kind"], f"{supplier['name_en']}: {f['message']}")
        out["alerts"].append(f["message"])
    conn.execute("UPDATE deliveries SET status='confirmed', confirmed_at=? WHERE id=?", (db.now(), delivery_id))
    conn.commit()
    return out


# ---------------------------------------------------------------- deliveries

def start_delivery(conn, source="text", supplier_id=None):
    did = "D-" + uuid.uuid4().hex[:8].upper()
    conn.execute("INSERT INTO deliveries (id, supplier_id, source, started_at) VALUES (?,?,?,?)", (did, supplier_id, source, db.now()))
    conn.commit()
    return did
