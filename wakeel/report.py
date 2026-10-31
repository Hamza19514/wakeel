"""Receiving report: one PDF per confirmed receiving record (one bill), in Arabic and English.

    python -m wakeel.report 1      # build the report for receiving record 1
"""
import os

from fpdf import FPDF

from . import db

REPORTS_DIR = os.path.join(os.path.dirname(os.path.abspath(db.DB_PATH)), "reports")

# Fonts with Arabic letters: Windows, macOS, Linux. WAKEEL_FONT / WAKEEL_FONT_BOLD override.
FONT_CANDIDATES = [
    (r"C:\Windows\Fonts\tahoma.ttf", r"C:\Windows\Fonts\tahomabd.ttf"),
    (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\arialbd.ttf"),
    ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
]

STATUS = {"received": ("Received", "استُلم"), "partial": ("Partial", "جزئي"), "missing": ("Missing", "لم يصل"),
          "damaged": ("Damaged", "تالف"), "wrong_model": ("Wrong model", "موديل مختلف")}

GREEN, AMBER, GREY, LINE = (15, 107, 79), (154, 98, 0), (104, 113, 109), (210, 214, 208)


def _fonts():
    if os.environ.get("WAKEEL_FONT"):
        return os.environ["WAKEEL_FONT"], os.environ.get("WAKEEL_FONT_BOLD", os.environ["WAKEEL_FONT"])
    for regular, bold in FONT_CANDIDATES:
        if os.path.exists(regular):
            return regular, bold if os.path.exists(bold) else regular
    raise FileNotFoundError("No font with Arabic letters found. Set WAKEEL_FONT to a .ttf file (e.g. Tahoma or Arial).")


def _load(conn, record_id):
    rec = conn.execute(
        "SELECT r.*, b.po_id, b.status AS bill_status, po.order_date, s.name_en, s.name_ar, s.id AS supplier_id "
        "FROM receiving_records r JOIN bills b ON b.id=r.bill_id JOIN purchase_orders po ON po.id=b.po_id "
        "JOIN suppliers s ON s.id=b.supplier_id WHERE r.id=?", (record_id,)).fetchone()
    if not rec:
        raise ValueError(f"No receiving record {record_id}")
    lines = conn.execute(
        "SELECT rl.*, bl.model, bl.unit_price, bl.expected_delivery_date, i.name_en, i.name_ar "
        "FROM receiving_lines rl JOIN bill_lines bl ON bl.id=rl.bill_line_id JOIN items i ON i.code=bl.item_code "
        "WHERE rl.record_id=? ORDER BY rl.id", (record_id,)).fetchall()
    finance = conn.execute("SELECT * FROM finance_notifications WHERE record_id=?", (record_id,)).fetchone()
    followups = conn.execute("SELECT f.*, i.name_en FROM followups f JOIN bill_lines bl ON bl.id=f.bill_line_id "
                             "JOIN items i ON i.code=bl.item_code WHERE f.bill_id=? AND f.bill_line_id IN "
                             "(SELECT bill_line_id FROM receiving_lines WHERE record_id=?) AND f.status='open'",
                             (rec["bill_id"], record_id)).fetchall()
    return rec, lines, finance, followups


def build_report(conn, record_id):
    """Write data/reports/receiving-<id>-<bill>.pdf and return its path."""
    rec, lines, finance, followups = _load(conn, record_id)
    regular, bold = _fonts()

    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(True, margin=15)
    pdf.add_font("R", "", regular)
    pdf.add_font("R", "B", bold)
    pdf.set_text_shaping(True)  # joins Arabic letters and writes them right to left
    pdf.add_page()
    pdf.set_margins(14, 14, 14)
    w = pdf.epw

    def text(s, size=10, style="", color=(29, 35, 33), align="L", h=6, width=0):
        pdf.set_font("R", style, size)
        pdf.set_text_color(*color)
        pdf.cell(width or w, h, s, align=align, new_x="LMARGIN" if not width else "RIGHT", new_y="NEXT" if not width else "TOP")

    # Header band
    pdf.set_fill_color(*GREEN)
    pdf.rect(0, 0, 210, 26, "F")
    pdf.set_xy(14, 7)
    text("WAKEEL  ·  Receiving Report", 16, "B", (255, 255, 255), width=w / 2)
    text("تقرير استلام  ·  وكيل", 16, "B", (255, 255, 255), "R", width=w / 2)
    pdf.set_xy(14, 32)

    # Facts
    received = rec["created_at"].replace("T", " ")
    facts = [
        ("Report no.", f"RR-{rec['id']:05d}", "رقم التقرير"),
        ("Supplier", f"{rec['name_en']}  ({rec['name_ar']})", "المورد"),
        ("Bill / PO", f"{rec['bill_id']}  /  {rec['po_id']}", "الفاتورة / أمر الشراء"),
        ("Order date", rec["order_date"], "تاريخ الطلب"),
        ("Expected delivery", lines[0]["expected_delivery_date"] if lines else "-", "تاريخ التسليم المتوقع"),
        ("Received", received, "تاريخ الاستلام"),
        ("Confirmed by", f"{rec['confirmed_by']}  (input: {rec['source']})", "أكّده"),
    ]
    for en, value, ar in facts:
        text(en, 9, "", GREY, width=42)
        text(value, 10, "B", width=w - 84)
        text(ar, 9, "", GREY, "R", width=42)
        pdf.ln(6)
    if rec["raw_input"]:
        pdf.ln(1)
        text("Gate keeper said", 9, "", GREY, width=w / 2)
        text("قال حارس البوابة", 9, "", GREY, "R", width=w / 2)
        pdf.ln(5)
        pdf.set_font("R", "", 9.5)
        pdf.set_text_color(29, 35, 33)
        pdf.multi_cell(w, 5, rec["raw_input"][:300], align="R")
    pdf.ln(3)

    # Line table
    cols = [("Item  الصنف", 40), ("Model", 34), ("Outst.", 13), ("OK", 10), ("Dmg", 11), ("Wrong", 13),
            ("Miss", 11), ("Unit SAR", 18), ("Status  الحالة", w - 150)]
    pdf.set_font("R", "B", 8.5)
    pdf.set_fill_color(238, 241, 236)
    pdf.set_text_color(29, 35, 33)
    for title, cw in cols:
        pdf.cell(cw, 8, title, border="B", fill=True, align="C")
    pdf.ln()
    pdf.set_draw_color(*LINE)
    for l in lines:
        outstanding = l["qty_ok"] + l["qty_damaged"] + l["qty_wrong_model"] + l["qty_missing"]
        en, ar = STATUS[l["status"]]
        cells = [f"{l['name_en']}  {l['name_ar']}", l["model"], outstanding, l["qty_ok"], l["qty_damaged"],
                 l["qty_wrong_model"], l["qty_missing"], f"{l['unit_price']:,.0f}", f"{en}  {ar}"]
        pdf.set_font("R", "", 8.5)
        for (title, cw), v in zip(cols, cells):
            color = GREEN if v == f"{en}  {ar}" and l["status"] == "received" else (179, 38, 30) if v == f"{en}  {ar}" else (29, 35, 33)
            pdf.set_text_color(*color)
            pdf.cell(cw, 8, str(v), border="B", align="L" if title.startswith(("Item", "Model")) else "C")
        pdf.ln()
    pdf.ln(4)

    # Money
    pay = sum(l["qty_ok"] * l["unit_price"] for l in lines)
    hold_rows = [(l["name_en"], reason, q, l["unit_price"]) for l in lines
                 for reason, q in (("damaged", l["qty_damaged"]), ("wrong model", l["qty_wrong_model"]), ("missing", l["qty_missing"])) if q]
    hold = sum(q * p for _, _, q, p in hold_rows)
    y = pdf.get_y()
    for i, (label, ar, amount, color) in enumerate((("PAY NOW", "ادفع الآن", pay, GREEN), ("HOLD", "تعليق", hold, AMBER))):
        x = 14 + i * (w / 2 + 2)
        pdf.set_fill_color(*((227, 244, 234) if i == 0 else (253, 241, 216)))
        pdf.rect(x, y, w / 2 - 2, 18, "F")
        pdf.set_xy(x + 4, y + 2)
        pdf.set_font("R", "B", 9)
        pdf.set_text_color(*color)
        pdf.cell(w / 2 - 10, 5, f"{label}  ·  {ar}", align="C")
        pdf.set_xy(x + 4, y + 8)
        pdf.set_font("R", "B", 15)
        pdf.cell(w / 2 - 10, 8, f"{amount:,.0f} SAR", align="C")
    pdf.set_xy(14, y + 22)

    if hold_rows:
        text("Hold breakdown (bill unit prices)  ·  تفصيل المبلغ المعلّق", 10, "B")
        for name, reason, q, price in hold_rows:
            text(f"•  {name} {reason}:  {q} × {price:,.0f} = {q * price:,.0f} SAR", 9.5, h=5.5)
        pdf.ln(2)
    if followups:
        text("Follow-ups  ·  المتابعات", 10, "B")
        for f in followups:
            text(f"•  {f['name_en']} – {f['reason'].replace('_', ' ')} ×{f['qty']}, due {f['due_date']}", 9.5, h=5.5)
        pdf.ln(2)
    if finance:
        text("Message to finance  ·  رسالة للمالية", 10, "B")
        pdf.set_font("R", "", 9.5)
        pdf.multi_cell(w, 5, finance["message"])
        pdf.ln(2)

    pdf.set_auto_page_break(False)
    pdf.set_y(-26)
    pdf.set_draw_color(*LINE)
    pdf.line(14, pdf.get_y(), 196, pdf.get_y())
    pdf.ln(2)
    text("Prepared by WAKEEL after the gate keeper confirmed. WAKEEL recommends; finance approves the actual payment.", 8, "", GREY)
    text("أعدّه وكيل بعد تأكيد حارس البوابة. وكيل يوصي فقط، والمالية تعتمد الدفع.", 8, "", GREY, "R")

    os.makedirs(REPORTS_DIR, exist_ok=True)
    path = os.path.join(REPORTS_DIR, f"receiving-{rec['id']}-{rec['bill_id']}.pdf")
    pdf.output(path)
    conn.execute("UPDATE finance_notifications SET report_path=? WHERE record_id=?", (path, record_id))
    conn.commit()
    return path


if __name__ == "__main__":
    import sys
    print(build_report(db.connect(), int(sys.argv[1])))
