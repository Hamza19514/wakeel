"""SQLite mock company data: suppliers, purchase orders, open bills, receiving history.

All bill numbers, items and prices are demo data.
"""
import json
import os
import sqlite3
from datetime import datetime

DB_PATH = os.environ.get("WAKEEL_DB", os.path.join(os.path.dirname(__file__), "..", "data", "wakeel.db"))

SCHEMA = """
CREATE TABLE suppliers (
    id TEXT PRIMARY KEY,
    name_en TEXT NOT NULL,
    name_ar TEXT NOT NULL,
    aliases TEXT NOT NULL,          -- JSON list of extra spellings
    email TEXT NOT NULL
);
CREATE TABLE items (
    code TEXT PRIMARY KEY,
    name_en TEXT NOT NULL,
    name_ar TEXT NOT NULL,
    aliases TEXT NOT NULL           -- JSON list of Arabic/English spellings
);
CREATE TABLE purchase_orders (
    id TEXT PRIMARY KEY,
    supplier_id TEXT NOT NULL REFERENCES suppliers(id),
    order_date TEXT NOT NULL
);
CREATE TABLE bills (
    id TEXT PRIMARY KEY,
    supplier_id TEXT NOT NULL REFERENCES suppliers(id),
    po_id TEXT NOT NULL REFERENCES purchase_orders(id),
    bill_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open'   -- open | closed
);
CREATE TABLE bill_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bill_id TEXT NOT NULL REFERENCES bills(id),
    item_code TEXT NOT NULL REFERENCES items(code),
    model TEXT NOT NULL,
    qty_ordered INTEGER NOT NULL,
    qty_received INTEGER NOT NULL DEFAULT 0,
    unit_price REAL NOT NULL,                       -- price for one item (SAR)
    line_total REAL GENERATED ALWAYS AS (qty_ordered * unit_price) STORED,  -- price for the whole quantity
    expected_delivery_date TEXT NOT NULL
);
-- One row per ordered item, in the order the team asked for:
-- company : item : quantity : order date : expected delivery : price per item : price for all quantity
CREATE VIEW order_lines AS
SELECT s.name_en          AS company,
       s.name_ar          AS company_ar,
       b.id               AS bill_id,
       i.name_en          AS item,
       i.name_ar          AS item_ar,
       bl.model           AS model,
       bl.qty_ordered     AS quantity,
       po.order_date      AS order_date,
       bl.expected_delivery_date AS expected_delivery_date,
       bl.unit_price      AS price_per_item,
       bl.line_total      AS price_for_all_quantity,
       bl.qty_received    AS quantity_received,
       b.status           AS bill_status
FROM bill_lines bl
JOIN bills b            ON b.id = bl.bill_id
JOIN purchase_orders po ON po.id = b.po_id
JOIN suppliers s        ON s.id = b.supplier_id
JOIN items i            ON i.code = bl.item_code;

CREATE TABLE receiving_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bill_id TEXT NOT NULL REFERENCES bills(id),
    delivery_id TEXT NOT NULL,
    source TEXT NOT NULL,            -- text | voice | photo | rfid
    raw_input TEXT,
    created_at TEXT NOT NULL,
    confirmed_by TEXT NOT NULL
);
CREATE TABLE receiving_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id INTEGER NOT NULL REFERENCES receiving_records(id),
    bill_line_id INTEGER NOT NULL REFERENCES bill_lines(id),
    qty_ok INTEGER NOT NULL,
    qty_damaged INTEGER NOT NULL,
    qty_wrong_model INTEGER NOT NULL,
    qty_missing INTEGER NOT NULL,
    delivered_model TEXT,
    status TEXT NOT NULL             -- received | partial | missing | damaged | wrong_model
);
CREATE TABLE finance_notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bill_id TEXT NOT NULL,
    record_id INTEGER REFERENCES receiving_records(id),
    report_path TEXT,                -- PDF receiving report
    email_status TEXT NOT NULL DEFAULT 'not_sent',  -- not_sent | sent | failed
    pay_amount REAL NOT NULL,
    hold_amount REAL NOT NULL,
    message TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'awaiting_finance_approval',
    created_at TEXT NOT NULL
);
CREATE TABLE supplier_emails (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_id TEXT NOT NULL,
    bill_id TEXT NOT NULL,
    subject TEXT NOT NULL,
    body_ar TEXT NOT NULL,
    body_en TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    created_at TEXT NOT NULL
);
CREATE TABLE followups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bill_id TEXT NOT NULL,
    bill_line_id INTEGER NOT NULL,
    reason TEXT NOT NULL,
    qty INTEGER NOT NULL,
    due_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open'
);
CREATE TABLE manager_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bill_id TEXT,
    kind TEXT NOT NULL,              -- substitution | duplicate | high_hold | unexpected_item
    message TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE deliveries (
    id TEXT PRIMARY KEY,
    supplier_id TEXT,
    source TEXT NOT NULL,
    raw_input TEXT,
    started_at TEXT NOT NULL,
    confirmed_at TEXT,
    clarifications INTEGER NOT NULL DEFAULT 0,
    engine TEXT,
    proposal TEXT,                   -- JSON draft shown to the gate keeper before confirming
    status TEXT NOT NULL DEFAULT 'in_progress'  -- in_progress | awaiting_confirmation | confirmed | cancelled
);
"""

SUPPLIERS = [
    ("SUP-A", "Company A", "شركة أ", ["شركة ا", "شركة الف", "company a", "a"], "orders@company-a.example"),
    ("SUP-B", "Company B", "شركة ب", ["شركة باء", "company b", "b"], "sales@company-b.example"),
    ("SUP-C", "Company C", "شركة ج", ["شركة جيم", "company c", "c"], "support@company-c.example"),
]

ITEMS = [
    ("PRN", "Printer", "طابعة", ["طابعه", "الطابعة", "الطابعه", "طابعات", "printer", "printers"]),
    ("DSK", "Desk", "مكتب", ["المكتب", "مكاتب", "desk", "desks"]),
    ("MON", "Monitor", "شاشة", ["شاشه", "الشاشة", "الشاشه", "شاشات", "الشاشات", "monitor", "monitors", "screen", "screens"]),
    ("MSE", "Mouse", "ماوس", ["الماوس", "ماوسات", "فارة", "الفارة", "mouse", "mice"]),
    ("KBD", "Keyboard", "كيبورد", ["الكيبورد", "كيبوردات", "لوحة مفاتيح", "لوحات مفاتيح", "keyboard", "keyboards"]),
    ("LAP", "Laptop", "لابتوب", ["اللابتوب", "لابتوبات", "اللابتوبات", "لاب توب", "laptop", "laptops"]),
    ("CHR", "Office chair", "كرسي", ["الكرسي", "كراسي", "الكراسي", "chair", "chairs"]),
    ("PRJ", "Projector", "بروجكتر", ["البروجكتر", "بروجكتور", "جهاز عرض", "projector"]),
    ("HDM", "HDMI cable", "كيبل HDMI", ["hdmi", "كيبل", "كيبلات", "كابل", "كابلات", "الكيبلات", "سلك", "اسلاك", "أسلاك", "الاسلاك", "cable", "cables"]),
    ("UPS", "UPS unit", "يو بي اس", ["UPS", "ups", "مزود طاقة", "بطارية احتياطية"]),
    ("RTR", "Router", "راوتر", ["الراوتر", "راوترات", "الراوترات", "router", "routers"]),
    ("SWT", "Network switch", "سويتش", ["السويتش", "سويتشات", "switch", "switches"]),
]

# (bill_id, supplier, po, order date, expected delivery date,
#  [(item, model, qty_ordered, qty_received, price per item)], status)
BILLS = [
    ("A-1038", "SUP-A", "PO-488", "2026-09-20", "2026-10-04", [
        ("MON", "Dell P2422H", 2, 0, 900),
        ("KBD", "Logitech K120", 5, 0, 150),
    ], "open"),
    ("A-1042", "SUP-A", "PO-501", "2026-10-01", "2026-10-30", [
        ("PRN", "HP LaserJet M404", 1, 0, 1200),
        ("DSK", "Steelcase Migration", 1, 0, 1200),
        ("MON", "Dell P2422H", 3, 0, 900),
        ("MSE", "Logitech M185", 1, 0, 200),
    ], "open"),
    ("A-1050", "SUP-A", "PO-517", "2026-10-15", "2026-11-05", [
        ("LAP", "Lenovo ThinkPad E14", 4, 0, 3800),
        ("MSE", "Logitech M185", 4, 0, 200),
    ], "open"),
    ("B-2001", "SUP-B", "PO-470", "2026-09-10", "2026-10-01", [
        ("CHR", "Herman Miller Sayl", 10, 0, 450),
        ("DSK", "IKEA Bekant", 2, 0, 1200),
    ], "open"),
    ("B-2007", "SUP-B", "PO-495", "2026-09-28", "2026-10-20", [
        ("PRJ", "Epson EB-X51", 1, 0, 2600),
        ("HDM", "Ugreen HDMI 2m", 10, 0, 40),
    ], "open"),
    ("B-2010", "SUP-B", "PO-503", "2026-10-05", "2026-10-19", [
        ("RTR", "TP-Link Archer AX55", 2, 2, 700),   # already received earlier
        ("SWT", "Cisco CBS110", 1, 0, 950),
    ], "open"),
    ("C-3001", "SUP-C", "PO-480", "2026-09-15", "2026-10-06", [
        ("UPS", "APC Back-UPS 1500", 3, 0, 1100),
        ("RTR", "MikroTik hAP ax2", 2, 0, 700),
    ], "open"),
    ("C-3005", "SUP-C", "PO-509", "2026-10-08", "2026-10-29", [
        ("RTR", "MikroTik hAP ax2", 1, 0, 700),
        ("SWT", "Cisco CBS110", 2, 0, 950),
    ], "open"),
    ("C-2990", "SUP-C", "PO-460", "2026-08-30", "2026-09-10", [
        ("UPS", "APC Back-UPS 1500", 2, 2, 1100),
    ], "closed"),
]


def connect(path=None):
    conn = sqlite3.connect(path or DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def reset(path=None):
    """Drop everything and load the demo data."""
    path = path or DB_PATH
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = connect(path)
    # Empty the file in place instead of deleting it: Windows can't delete a database another connection still has open.
    conn.execute("PRAGMA foreign_keys = OFF")
    for kind, name in conn.execute("SELECT type, name FROM sqlite_master WHERE type IN ('view', 'table') "
                                   "AND name NOT LIKE 'sqlite_%' ORDER BY type = 'table'").fetchall():
        conn.execute(f'DROP {kind.upper()} IF EXISTS "{name}"')
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='sqlite_sequence'").fetchone():
        conn.execute("DELETE FROM sqlite_sequence")  # restart auto-numbered ids at 1
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    for sid, en, ar, aliases, email in SUPPLIERS:
        conn.execute("INSERT INTO suppliers VALUES (?,?,?,?,?)", (sid, en, ar, json.dumps(aliases, ensure_ascii=False), email))
    for code, en, ar, aliases in ITEMS:
        conn.execute("INSERT INTO items VALUES (?,?,?,?)", (code, en, ar, json.dumps(aliases, ensure_ascii=False)))
    for bill_id, sup, po, date, expected, lines, status in BILLS:
        conn.execute("INSERT INTO purchase_orders VALUES (?,?,?)", (po, sup, date))
        conn.execute("INSERT INTO bills VALUES (?,?,?,?,?)", (bill_id, sup, po, date, status))
        for item, model, qty, rec, price in lines:
            conn.execute(
                "INSERT INTO bill_lines (bill_id, item_code, model, qty_ordered, qty_received, unit_price, expected_delivery_date) "
                "VALUES (?,?,?,?,?,?,?)",
                (bill_id, item, model, qty, rec, price, expected),
            )
    conn.commit()
    return conn


def now():
    return datetime.now().isoformat(timespec="seconds")


def show(conn=None):
    """Print every ordered item: company : item : quantity : dates : prices."""
    conn = conn or connect()
    print(f"{'Company':<10} {'Bill':<7} {'Item':<15} {'Qty':>3}  {'Ordered':<10}  {'Expected':<10}  {'Per item':>9}  {'All qty':>9}  Received")
    for r in conn.execute("SELECT * FROM order_lines ORDER BY bill_id"):
        print(f"{r['company']:<10} {r['bill_id']:<7} {r['item']:<15} {r['quantity']:>3}  {r['order_date']:<10}  "
              f"{r['expected_delivery_date']:<10}  {r['price_per_item']:>9,.0f}  {r['price_for_all_quantity']:>9,.0f}  "
              f"{r['quantity_received']}/{r['quantity']}")


if __name__ == "__main__":
    show(reset())
    print(f"\nDemo database created at {os.path.abspath(DB_PATH)}")
