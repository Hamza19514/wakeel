"""Send WAKEEL's emails through Gmail: the payment decision to finance (with the PDF reports)
and the issue report to the supplier.

Settings in .env (nothing is sent if they are missing; the emails stay as drafts):
    WAKEEL_GMAIL_USER=wakeel.demo@gmail.com
    WAKEEL_GMAIL_APP_PASSWORD=abcd efgh ijkl mnop     # Gmail app password, not the normal password
    WAKEEL_FINANCE_EMAIL=finance-test@example.com     # inbox playing "finance"
    WAKEEL_SUPPLIER_TEST_EMAIL=supplier-test@example.com  # inbox playing every supplier

Supplier emails always go to WAKEEL_SUPPLIER_TEST_EMAIL, never to the supplier address in the
database, so the demo can never email a real company.
"""
import os
import smtplib
import ssl
from email.message import EmailMessage

from dotenv import load_dotenv

load_dotenv()


def settings():
    s = {k: os.environ.get(k, "").strip() for k in
         ("WAKEEL_GMAIL_USER", "WAKEEL_GMAIL_APP_PASSWORD", "WAKEEL_FINANCE_EMAIL", "WAKEEL_SUPPLIER_TEST_EMAIL")}
    s["WAKEEL_GMAIL_APP_PASSWORD"] = s["WAKEEL_GMAIL_APP_PASSWORD"].replace(" ", "")
    return s


def configured():
    s = settings()
    return bool(s["WAKEEL_GMAIL_USER"] and s["WAKEEL_GMAIL_APP_PASSWORD"] and s["WAKEEL_FINANCE_EMAIL"])


def _send(messages):
    s = settings()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context(), timeout=20) as smtp:
        smtp.login(s["WAKEEL_GMAIL_USER"], s["WAKEEL_GMAIL_APP_PASSWORD"])
        for m in messages:
            smtp.send_message(m)


def send_delivery(conn, confirm_result, report_paths):
    """Email finance (one email, all bills of the delivery, PDFs attached) and the supplier test inbox."""
    if not configured():
        return {"finance": "not sent (Gmail not set up in .env) - saved as draft", "supplier": "draft only"}
    s = settings()
    sender = f"WAKEEL وكيل <{s['WAKEEL_GMAIL_USER']}>"
    records = confirm_result["records"]
    notes = [conn.execute("SELECT * FROM finance_notifications WHERE record_id=?", (r,)).fetchone() for r in records]

    msgs = []
    if notes:
        pay, hold = sum(n["pay_amount"] for n in notes), sum(n["hold_amount"] for n in notes)
        bills = ", ".join(n["bill_id"] for n in notes)
        m = EmailMessage()
        m["From"], m["To"] = sender, s["WAKEEL_FINANCE_EMAIL"]
        m["Subject"] = f"WAKEEL · {bills}: pay {pay:,.0f} SAR, hold {hold:,.0f} SAR"
        m.set_content("Payment decision from the warehouse gate (confirmed by the gate keeper).\n\n"
                      + "\n\n".join(n["message"] for n in notes)
                      + "\n\nWAKEEL recommends; please approve the actual payment in your system."
                      + "\n\nقرار الدفع من بوابة المستودع. التقرير مرفق.")
        for path in report_paths:
            with open(path, "rb") as f:
                m.add_attachment(f.read(), maintype="application", subtype="pdf", filename=os.path.basename(path))
        msgs.append(("finance", [n["id"] for n in notes], m))

    to_supplier = s["WAKEEL_SUPPLIER_TEST_EMAIL"]
    for e in confirm_result["emails"]:
        if not to_supplier:
            break
        m = EmailMessage()
        m["From"], m["To"], m["Subject"] = sender, to_supplier, e["subject"]
        m.set_content(e["body_ar"] + "\n\n" + "-" * 40 + "\n\n" + e["body_en"])
        msgs.append(("supplier", [e["id"]], m))

    try:
        _send([m for _, _, m in msgs])
        status = "sent"
    except Exception as err:  # keep the delivery confirmed even if email fails
        status = f"failed: {err}"
    for kind, ids, _ in msgs:
        table = "finance_notifications SET email_status" if kind == "finance" else "supplier_emails SET status"
        for i in ids:
            conn.execute(f"UPDATE {table}=? WHERE id=?", ("sent" if status == "sent" else "failed", i))
    conn.commit()
    sent_to = lambda kind, addr: (f"sent to {addr}" if status == "sent" else status) if any(k == kind for k, _, _ in msgs) else "-"
    return {"finance": sent_to("finance", s["WAKEEL_FINANCE_EMAIL"]),
            "supplier": sent_to("supplier", to_supplier) if to_supplier else "draft only (WAKEEL_SUPPLIER_TEST_EMAIL not set)"}


if __name__ == "__main__":
    # Quick check: python -m wakeel.mailer  -> sends one test email to the finance inbox
    if not configured():
        raise SystemExit("Set WAKEEL_GMAIL_USER, WAKEEL_GMAIL_APP_PASSWORD and WAKEEL_FINANCE_EMAIL in .env first.")
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = settings()["WAKEEL_GMAIL_USER"], settings()["WAKEEL_FINANCE_EMAIL"], "WAKEEL test email"
    m.set_content("If you can read this, WAKEEL can send email. / وكيل يقدر يرسل إيميلات.")
    _send([m])
    print("Test email sent to", settings()["WAKEEL_FINANCE_EMAIL"])
