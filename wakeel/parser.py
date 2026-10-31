"""Rule-based Saudi-dialect parser: the offline fallback for parse_receiving_note.

The Claude engine reads the gate keeper's sentence itself; this parser lets the
demo and the test set run without an API key or network.
"""
import json
import re

from . import tools

DIACRITICS = re.compile(r"[ً-ْـ]")
ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


def normalize(text):
    t = DIACRITICS.sub("", text.translate(ARABIC_DIGITS)).lower()
    t = re.sub("[أإآ]", "ا", t)
    return t.replace("ة", "ه").replace("ى", "ي")


NUMBERS = {
    "واحد": 1, "واحده": 1, "وحده": 1, "حبه": 1,
    "اثنين": 2, "اثنتين": 2, "ثنتين": 2, "ثنين": 2, "حبتين": 2,
    "ثلاث": 3, "ثلاثه": 3, "اربع": 4, "اربعه": 4, "خمس": 5, "خمسه": 5,
    "ست": 6, "سته": 6, "سبع": 7, "سبعه": 7, "ثمان": 8, "ثمانيه": 8,
    "تسع": 9, "تسعه": 9, "عشر": 10, "عشره": 10,
}

# Dual forms imply two units.
DUALS = {"شاشتين": "MON", "طابعتين": "PRN", "مكتبين": "DSK", "كرسيين": "CHR", "راوترين": "RTR",
         "لابتوبين": "LAP", "سويتشين": "SWT", "ماوسين": "MSE", "كيبوردين": "KBD", "بروجكترين": "PRJ"}

BRANDS = {"ديل": "Dell", "dell": "Dell", "سامسونج": "Samsung", "samsung": "Samsung", "ال جي": "LG", "lg": "LG",
          "اتش بي": "HP", "hp": "HP", "كانون": "Canon", "canon": "Canon", "لينوفو": "Lenovo", "lenovo": "Lenovo",
          "ديل": "Dell", "ابسون": "Epson", "ايبسون": "Epson", "epson": "Epson", "لوجيتك": "Logitech", "logitech": "Logitech",
          "مايكروتك": "MikroTik", "mikrotik": "MikroTik", "تي بي لينك": "TP-Link", "سيسكو": "Cisco", "cisco": "Cisco",
          "ايسر": "Acer", "acer": "Acer", "اسوس": "Asus", "asus": "Asus", "هواوي": "Huawei", "ايكيا": "IKEA"}

DAMAGE = ["مكسور", "مكسوره", "مكسورات", "تالف", "تالفه", "خربان", "خربانه", "معطوب", "معطوبه", "مشخوط", "مشخوطه",
          "مضروب", "مضروبه", "damaged", "broken"]
MISSING = ["ما وصل", "ما وصلت", "ما وصلوا", "ماوصلوا", "ما جوا", "ما جو", "ما جابوها", "ما جابوه", "ماوصل", "ماوصلت", "ما جا", "ما جات", "ماجا", "ما جاء", "لم يصل", "لم تصل", "ناقص",
           "ناقصه", "مافي", "ما فيه", "ما جانا", "missing", "didn't come", "not delivered"]
WRONG = ["بدل", "بدال", "بدلا من", "بدل ما", "موديل غلط", "موديل ثاني", "موديل مختلف", "غير الموديل", "غلط", "wrong model", "instead of"]
PREFIXES = ("و", "ف", "ب", "ل")


def _contains(text, phrase):
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text) is not None


def _alias_table(conn):
    table = {}
    for it in tools.list_items(conn):
        for a in [it["name_ar"], it["name_en"]] + it["aliases"]:
            table[normalize(a)] = it["code"]
    for d, code in DUALS.items():
        table[d] = code
    return table


def _strip_prefix(tok, table):
    """Drop attached "و/ف/ب/ل" and "ال": "والشاشات" -> "شاشات", "الاربعه" -> "اربعه"."""
    forms = [tok] + [tok[1:] for p in PREFIXES if tok.startswith(p)]
    forms += [f[2:] for f in forms if f.startswith("ال")]
    for f in forms:
        if f in table or f in NUMBERS or f.isdigit():
            return f
    return tok


def _number(tok):
    if tok.isdigit():
        return int(tok)
    return NUMBERS.get(tok)


def _word_index(words, phrases):
    """Index of the first word where any of the phrases starts (an attached "و" is ignored)."""
    for i in range(len(words)):
        for p in phrases:
            pw = p.split()
            got = words[i:i + len(pw)]
            if got and got[0].startswith("و") and got[0][1:] == pw[0]:
                got = [got[0][1:]] + got[1:]
            if got == pw:
                return i
    return len(words)


def _nearest(clause_items, k):
    """The item just before word k, or the first one after it."""
    before = [e for i, e in clause_items if i < k]
    return before[-1] if before else next(e for i, e in clause_items if i >= k)


def detect_supplier(conn, text):
    t = normalize(text)
    best = None
    for s in tools.list_suppliers(conn):
        for a in [s["name_ar"], s["name_en"]] + s["aliases"]:
            a = normalize(a)
            if len(a) < 3:
                continue
            if _contains(t, a) and (best is None or len(a) > best[1]):
                best = (s["id"], len(a))
    return best[0] if best else None


def parse_receiving_note(conn, text, supplier_id=None):
    """Arabic (or English) sentence -> supplier, items, quantities, condition."""
    t = normalize(text)
    supplier_id = supplier_id or detect_supplier(conn, text)
    bill_hints = [m.upper() for m in re.findall(r"\b[a-c]-\d{4}\b", t)]
    table = _alias_table(conn)
    multi = sorted((a for a in table if " " in a), key=len, reverse=True)

    items, last = [], None
    for clause in re.split(r"[.،,؛;:\n!؟?]+", t):
        clause = clause.strip()
        if not clause:
            continue
        # Treat multi-word aliases ("لوحة مفاتيح") as single tokens.
        for a in multi:
            clause = re.sub(rf"(?<!\w){re.escape(a)}(?!\w)", a.replace(" ", "_"), clause)
        toks = [_strip_prefix(x, table) for x in clause.split()]
        toks = [x.replace("_", " ") for x in toks]

        is_missing = any(_contains(clause, m) for m in MISSING) and not _contains(clause, "الا")
        is_damaged = any(_contains(clause, d) for d in DAMAGE)
        is_wrong = any(_contains(clause, w) for w in WRONG)
        brand = None
        if is_wrong or any(_contains(clause, b) for b in BRANDS):
            hits = sorted(((clause.find(b), v) for b in BRANDS for v in [BRANDS[b]] if _contains(clause, b)))
            brand = hits[0][1] if hits else None
        if is_wrong and not brand:
            brand = "different model"

        clause_items = []
        for i, tok in enumerate(toks):
            code = table.get(tok)
            if not code:
                continue
            qty = 2 if tok in DUALS else None
            if qty is None and i > 0:
                qty = _number(toks[i - 1])
            # "كراسي ست" - a number after the item, unless it starts the next item ("المكتب وثلاث شاشات").
            if qty is None and i + 1 < len(toks) and not (i + 2 < len(toks) and toks[i + 2] in table):
                qty = _number(toks[i + 1])
            entry = {"item_code": code, "qty_delivered": qty, "qty_damaged": 0, "delivered_model": None, "not_delivered": False}
            clause_items.append((i, entry))

        if clause_items:
            # A condition word belongs to the item it follows: "البروجكتر وعشر كيبلات، البروجكتر مكسور".
            raw = clause.split()
            if is_missing:
                k = _word_index(raw, MISSING)
                before = [e for i, e in clause_items if i < k]
                for e in before or [_nearest(clause_items, k)]:
                    e["not_delivered"], e["qty_delivered"] = True, 0
            if is_damaged:
                e = _nearest(clause_items, _word_index(raw, DAMAGE))
                nums = [_number(x) for x in toks if _number(x)]
                e["qty_damaged"] = min(nums) if len(nums) > 1 else (e["qty_delivered"] or 1)
            if brand:
                _nearest(clause_items, _word_index(raw, list(BRANDS) + WRONG))["delivered_model"] = brand
            clause_items = [e for _, e in clause_items]
            items += clause_items
            last = clause_items[-1]
        elif last is not None:
            # "وحدة منها مكسورة" / "two of them broken" refers to the last item.
            nums = [_number(x) for x in toks if _number(x)]
            n = nums[0] if nums else None
            if is_damaged:
                last["qty_damaged"] = n or last["qty_delivered"] or 1
            elif is_missing and n and last["qty_delivered"]:
                last["qty_delivered"] = max(0, last["qty_delivered"] - n)
            if brand and (is_wrong or not last["delivered_model"]):
                last["delivered_model"] = brand

    # Same item mentioned twice ("ثلاث شاشات ... الشاشات سامسونج") -> merge.
    merged = {}
    for e in items:
        m = merged.get((e["item_code"], e["not_delivered"]))
        if not m:
            merged[(e["item_code"], e["not_delivered"])] = e
            continue
        if e["qty_delivered"] and not m["qty_delivered"]:
            m["qty_delivered"] = e["qty_delivered"]
        m["qty_damaged"] = max(m["qty_damaged"], e["qty_damaged"])
        m["delivered_model"] = m["delivered_model"] or e["delivered_model"]
    return {"supplier_id": supplier_id, "items": list(merged.values()), "bill_hints": bill_hints}


if __name__ == "__main__":
    import sys
    from . import db
    print(json.dumps(parse_receiving_note(db.connect(), " ".join(sys.argv[1:])), ensure_ascii=False, indent=2))
