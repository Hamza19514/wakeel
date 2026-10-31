"""WAKEEL agent: understands the gate keeper, decides, and drafts the actions.

Two engines share the same tools (tools.py):
  * "claude"  - Claude reads the Arabic sentence or delivery-note photo and drives the tools.
  * "offline" - the rule-based parser (parser.py); no API key or network needed.
The engine is "claude" when ANTHROPIC_API_KEY is set, unless WAKEEL_ENGINE=offline.
Nothing is saved, paid or sent until the gate keeper confirms (tools.confirm_delivery).
"""
import json
import os

from dotenv import load_dotenv

from . import parser, tools

load_dotenv()  # reads ANTHROPIC_API_KEY from the .env file in the project folder

MODEL = os.environ.get("WAKEEL_MODEL", "claude-opus-5-5")
EFFORT = os.environ.get("WAKEEL_EFFORT", "medium")


def engine_name():
    forced = os.environ.get("WAKEEL_ENGINE")
    if forced:
        return forced
    return "claude" if os.environ.get("ANTHROPIC_API_KEY") else "offline"


def _respond(conn, delivery_id, match, message=None):
    if match["status"] == "needs_clarification":
        conn.execute("UPDATE deliveries SET clarifications = clarifications + 1 WHERE id=?", (delivery_id,))
        conn.commit()
        return {"type": "question", "message": message or match["question"], "question_en": match.get("question_en"),
                "options": match["options"]}
    proposal = json.loads(conn.execute("SELECT proposal FROM deliveries WHERE id=?", (delivery_id,)).fetchone()[0])
    return {"type": "proposal", "message": message or summarize_ar(proposal), "proposal": proposal}


def summarize_ar(p):
    status_ar = {"received": "استُلم", "partial": "استلام جزئي", "missing": "لم يصل", "damaged": "تالف", "wrong_model": "موديل مختلف"}
    out = []
    for r in p["records"]:
        out.append(f"الفاتورة {r['bill_id']}:")
        for l in r["lines"]:
            detail = []
            if l["qty_ok"]:
                detail.append(f"{l['qty_ok']} سليم")
            if l["qty_damaged"]:
                detail.append(f"{l['qty_damaged']} تالف")
            if l["qty_wrong_model"]:
                detail.append(f"{l['qty_wrong_model']} موديل مختلف ({l['delivered_model']})")
            if l["qty_missing"]:
                detail.append(f"{l['qty_missing']} لم يصل")
            out.append(f"  • {l['item_ar']} ×{l['qty_outstanding']}: {status_ar[l['status']]} ({'، '.join(detail)})")
        out.append(f"  ادفع {r['pay']:,.0f} ريال، وعلّق {r['hold']:,.0f} ريال.")
    for f in p["flags"]:
        out.append("⚠ " + f["message"])
    out.append("تأكيد؟")
    return "\n".join(out)


# ---------------------------------------------------------------- offline engine

def run_offline(conn, delivery_id, text=None, supplier_id=None, prefer_bills=None, items=None):
    if items is None:
        parsed = parser.parse_receiving_note(conn, text or "", supplier_id)
        supplier_id = parsed["supplier_id"]
        items = parsed["items"]
        prefer_bills = (prefer_bills or []) + parsed["bill_hints"]
        if not supplier_id and parsed["bill_hints"]:
            row = conn.execute("SELECT supplier_id FROM bills WHERE id=?", (parsed["bill_hints"][0],)).fetchone()
            supplier_id = row and row[0]
    if not supplier_id:
        return {"type": "question", "message": "من أي شركة وصل التوريد؟", "options": [
            {"supplier_id": s["id"], "label": s["name_ar"]} for s in tools.list_suppliers(conn)]}
    if not items:
        return {"type": "question", "message": "ما فهمت الأصناف اللي وصلت. ممكن تعيد الجملة؟ مثال: وصلت ثلاث شاشات وطابعة", "options": []}
    match = tools.match_bill(conn, supplier_id, items, prefer_bills)
    if match["status"] == "matched":
        tools.propose_receiving(conn, delivery_id, supplier_id, match["allocations"], match["flags"])
    return _respond(conn, delivery_id, match) | {"parsed": {"supplier_id": supplier_id, "items": items}}


# ---------------------------------------------------------------- Claude engine

SYSTEM = """You are WAKEEL, the goods-receiving agent at a Saudi company's warehouse gate.
The gate keeper tells you, usually in Saudi Arabic dialect, what a supplier delivered (or sends a photo of the delivery note, or an RFID scan arrives).

Your job:
1. Understand which supplier it is and every item mentioned: quantity delivered, how many are damaged, a different brand/model than ordered, and items that did not arrive.
2. Call find_open_bills to see what this supplier still owes, so you map words to the right item codes.
3. Call match_bill with the structured items. It picks the right bill(s), splits across bills if needed, flags duplicates, and saves a draft receiving record.
4. If match_bill says needs_clarification, ask the gate keeper that one short question in Arabic and stop. When they answer, call match_bill again with prefer_bills.
5. When match_bill returns matched, reply with at most 2 short lines saying what you will do next. The screen shows the full table, amounts and a Confirm button, so do not repeat lines or numbers.

Language: every reply to the gate keeper is in simple Saudi Arabic, even if the input, a photo or an RFID scan is in English.

Rules:
- Always call match_bill with every item the gate keeper mentioned, even when an item is not on any open bill or looks already received. match_bill also checks closed bills and receiving history: it is the only way duplicates and unexpected items get flagged before anyone pays. Do not ask the gate keeper about such items yourself.
- Never invent items, quantities or bills.
- qty_delivered is null only when the gate keeper says an item came without a count ("الطابعة وصلت"); then the whole outstanding quantity is assumed.
- delivered_model only when they name a brand or model, or say it is the wrong model.
- You never confirm, pay or send anything yourself. The gate keeper confirms with one tap.

Voice input: when the text came from speech-to-text you also get the recogniser's other guesses. Words are often mis-heard
(e.g. "وصل" heard as "vessel", "بروجكتر" written as "projector", numbers merged). Use all the guesses together with the
supplier's open bills and the item catalogue to work out what was meant.

When you are not sure: if anything important is still unclear (the supplier, an item, a quantity, whether something is
damaged or missing, a brand), do not guess and do not call match_bill yet. Call ask_gatekeeper with 1 to 3 short
questions in Saudi Arabic, each with 2 to 4 short answer options built from the likely meanings
(e.g. "كم شاشة وصلت؟" with options ["2", "3", "4"]). Only ask about what is unclear. When the answers come back,
continue with match_bill."""

ITEM_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["item_code", "qty_delivered", "qty_damaged", "delivered_model", "not_delivered"],
    "properties": {
        "item_code": {"type": "string", "description": "Item code from find_open_bills, e.g. MON"},
        "qty_delivered": {"type": ["integer", "null"], "description": "Units that physically arrived (damaged ones included). null = whole outstanding quantity. 0 if it did not arrive."},
        "qty_damaged": {"type": "integer", "description": "How many of the delivered units are damaged"},
        "delivered_model": {"type": ["string", "null"], "description": "Brand/model actually delivered if the gate keeper mentioned one, else null"},
        "not_delivered": {"type": "boolean", "description": "True if the gate keeper said this item did not come"},
    },
}

TOOLS = [
    {
        "name": "list_suppliers",
        "description": "List all suppliers with their Arabic and English names.",
        "strict": True,
        "input_schema": {"type": "object", "additionalProperties": False, "properties": {}, "required": []},
    },
    {
        "name": "find_open_bills",
        "description": "All open bills for a supplier, with item codes, models, outstanding quantities and prices. Also lists the item catalogue (codes and Arabic names).",
        "strict": True,
        "input_schema": {"type": "object", "additionalProperties": False, "required": ["supplier_id"],
                         "properties": {"supplier_id": {"type": "string"}}},
    },
    {
        "name": "match_bill",
        "description": "Match the delivered items to the right open bill line(s). Returns needs_clarification with one question and options, or matched (and saves the draft receiving record for the gate keeper to confirm).",
        "strict": True,
        "input_schema": {
            "type": "object", "additionalProperties": False,
            "required": ["supplier_id", "items", "prefer_bills"],
            "properties": {
                "supplier_id": {"type": "string"},
                "items": {"type": "array", "items": ITEM_SCHEMA},
                "prefer_bills": {"type": "array", "items": {"type": "string"},
                                 "description": "Bill ids the gate keeper pointed to (from a clarification answer or a bill number they said). Usually empty."},
            },
        },
    },
]

TOOLS.append({
    "name": "ask_gatekeeper",
    "description": "Ask the gate keeper 1-3 short questions when you are not sure what they said or meant. "
                   "Each question is shown with tap-to-answer buttons. Their answers arrive in the next message.",
    "strict": True,
    "input_schema": {
        "type": "object", "additionalProperties": False, "required": ["questions"],
        "properties": {"questions": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["question", "options"],
            "properties": {"question": {"type": "string", "description": "Short question in Saudi Arabic"},
                           "options": {"type": "array", "items": {"type": "string"},
                                       "description": "2-4 short answer options in Arabic"}}}}},
    },
})

_sessions = {}  # delivery_id -> Claude messages (demo only: in-memory)
_pending = {}   # delivery_id -> tool results waiting for the gate keeper's answers


def _run_tool(conn, delivery_id, name, args, state):
    if name == "list_suppliers":
        return tools.list_suppliers(conn)
    if name == "find_open_bills":
        return {"bills": tools.find_open_bills(conn, args["supplier_id"]),
                "catalogue": [{"code": i["code"], "ar": i["name_ar"], "en": i["name_en"]} for i in tools.list_items(conn)]}
    if name == "ask_gatekeeper":
        state["ask"] = args["questions"][:3]
        return "Shown to the gate keeper. Their answers arrive in the next message."
    if name == "match_bill":
        match = tools.match_bill(conn, args["supplier_id"], args["items"], args.get("prefer_bills"))
        state["match"] = match
        if match["status"] == "matched":
            p = tools.propose_receiving(conn, delivery_id, args["supplier_id"], match["allocations"], match["flags"])
            return {"status": "matched", "draft": p}
        return match
    raise ValueError(f"unknown tool {name}")


def run_claude(conn, delivery_id, text=None, supplier_id=None, image=None, items=None, speech_alternatives=None):
    import anthropic

    client = anthropic.Anthropic()
    messages = _sessions.setdefault(delivery_id, [])
    content = _pending.pop(delivery_id, [])  # results of an ask_gatekeeper call go first, before the answers
    if image:
        content.append({"type": "image", "source": {"type": "base64", "media_type": image["media_type"], "data": image["data"]}})
        content.append({"type": "text", "text": "This is a photo of the supplier's delivery note. Read every item and quantity from it."})
    if supplier_id:
        content.append({"type": "text", "text": f"Supplier picked on screen: {supplier_id}"})
    if items is not None:
        content.append({"type": "text", "text": "RFID gate scan event (structured): " + json.dumps(items)})
    if text and speech_alternatives:
        guesses = "\n".join(f"{i}. {g}" for i, g in enumerate(speech_alternatives, 1))
        content.append({"type": "text", "text": "The gate keeper spoke; speech-to-text may contain errors.\n"
                                                f"Best guess: {text}\nOther guesses:\n{guesses}"})
    elif text:
        content.append({"type": "text", "text": text})
    messages.append({"role": "user", "content": content})

    state = {}
    for _ in range(8):
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            system=SYSTEM,
            tools=TOOLS,
            messages=messages,
            thinking={"type": "adaptive"},
            output_config={"effort": EFFORT},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if response.stop_reason == "refusal":
            messages.pop()
            return {"type": "error", "message": "The model declined this request."}
        messages.append({"role": "assistant", "content": response.content})
        uses = [b for b in response.content if b.type == "tool_use"]
        if not uses:
            break
        results = []
        for u in uses:
            try:
                out = _run_tool(conn, delivery_id, u.name, u.input, state)
                results.append({"type": "tool_result", "tool_use_id": u.id, "content": json.dumps(out, ensure_ascii=False)})
            except Exception as e:  # report tool errors back to the model
                results.append({"type": "tool_result", "tool_use_id": u.id, "content": f"Error: {e}", "is_error": True})
        if "ask" in state:
            _pending[delivery_id] = results
            break
        messages.append({"role": "user", "content": results})

    reply = "\n".join(b.text for b in response.content if b.type == "text").strip()
    if "ask" in state:
        conn.execute("UPDATE deliveries SET clarifications = clarifications + 1 WHERE id=?", (delivery_id,))
        conn.commit()
        return {"type": "question", "message": reply or "ما سمعتك زين، عندي كم سؤال قبل ما أكمل:",
                "questions": state["ask"], "options": []}
    match = state.get("match")
    if match:
        return _respond(conn, delivery_id, match, reply or None)
    return {"type": "question", "message": reply or "ممكن توضح أكثر؟", "options": []}


# ---------------------------------------------------------------- entry point

def describe_ar(conn, items):
    """Short Arabic description of parsed items, used as an answer option."""
    names = {i["code"]: i["name_ar"] for i in tools.list_items(conn)}
    out = []
    for i in items:
        if i["not_delivered"]:
            out.append(f"{names[i['item_code']]} ما وصل")
            continue
        d = f"{names[i['item_code']]} ×{i['qty_delivered'] if i['qty_delivered'] is not None else 'الكل'}"
        if i["qty_damaged"]:
            d += f" ({i['qty_damaged']} تالف)"
        if i["delivered_model"]:
            d += f" ({i['delivered_model']})"
        out.append(d)
    return "، ".join(out)


def handle(conn, delivery_id, text=None, supplier_id=None, prefer_bills=None, image=None, items=None,
           speech_alternatives=None, replace_text=False):
    eng = engine_name()
    label = text or ("[photo]" if image else "[rfid scan]" if items is not None else None)
    conn.execute("UPDATE deliveries SET engine=? WHERE id=?", (eng, delivery_id))
    if label and replace_text:  # the gate keeper picked which of the heard sentences was right
        conn.execute("UPDATE deliveries SET raw_input=? WHERE id=?", (label, delivery_id))
    elif label:
        conn.execute("UPDATE deliveries SET raw_input=COALESCE(raw_input || ' | ', '') || ? WHERE id=?", (label, delivery_id))
    conn.commit()
    if eng == "claude":
        if prefer_bills and not text:
            text = "الفاتورة " + "، ".join(prefer_bills)
        return run_claude(conn, delivery_id, text, supplier_id, image, items, speech_alternatives) | {"engine": eng}
    if image:
        return {"type": "error", "engine": eng, "message": "Photo reading needs the Claude engine (set ANTHROPIC_API_KEY)."}
    if text and speech_alternatives:
        # Offline: if the speech guesses mean different things, show them and let the gate keeper pick.
        meanings = {}
        for guess in dict.fromkeys([text] + speech_alternatives):
            parsed = parser.parse_receiving_note(conn, guess, supplier_id)["items"]
            if parsed:
                meanings.setdefault(json.dumps(parsed, sort_keys=True), (guess, parsed))
        if len(meanings) > 1:
            conn.execute("UPDATE deliveries SET clarifications = clarifications + 1 WHERE id=?", (delivery_id,))
            conn.commit()
            return {"type": "question", "engine": eng, "message": "ما سمعتك زين. وش قصدك؟",
                    "options": [{"text": g, "label": describe_ar(conn, p)} for g, p in list(meanings.values())[:3]]}
        if len(meanings) == 1:
            best = next(iter(meanings.values()))[0]
            conn.execute("UPDATE deliveries SET raw_input = REPLACE(raw_input, ?, ?) WHERE id=?", (text, best, delivery_id))
            conn.commit()
    d = conn.execute("SELECT raw_input FROM deliveries WHERE id=?", (delivery_id,)).fetchone()
    # Offline: a clarification answer re-runs the original sentence with the chosen bill preferred.
    full_text = d["raw_input"] if items is None else None
    return run_offline(conn, delivery_id, full_text, supplier_id, prefer_bills, items) | {"engine": eng}
