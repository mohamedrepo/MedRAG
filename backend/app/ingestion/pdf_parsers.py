"""PDF table extractors.

Three layout families in the current corpus:

1. syndicate  — CODE | service (ar+en) | member | project 50% | parents 50% | 60% | 40%
                Text order is jumbled; we use pdfplumber tables + RTL repair.
2. lab_list   — Test Type | Test ID | Category | Test Name | Price
3. simple_4   — serial | code | service name | price (Kasr Al Ainy)
4. generic    — any table with a name column and ≥1 numeric price column
"""
from __future__ import annotations

import re
from pathlib import Path

import pdfplumber
import pymupdf as fitz

from app.ingestion.base import (
    ParsedDoc,
    RawModifier,
    RawPrice,
    RawRow,
    clean_service_name,
    looks_like_code,
    map_price_category,
    parse_number,
    split_name_bilingual,
)
from app.normalization import has_arabic


def _fix_cell(s: str | None) -> str | None:
    if s is None:
        return None
    s = str(s)
    if has_arabic(s):
        from app.normalization import fix_rtl_visual_order
        s = fix_rtl_visual_order(s)
    s = s.replace("\n", " ").strip()
    return s or None


def _page_text_chars(page) -> int:
    try:
        return len(page.extract_text() or "")
    except Exception:
        return 0


def _extract_tables(page) -> list[list[list[str | None]]]:
    tables = page.extract_tables() or []
    return [t for t in tables if t and len(t) >= 2]


def _cell_str(cell) -> str | None:
    if cell is None:
        return None
    s = str(cell).replace("\n", " ").strip()
    return s or None


# ---------------------------------------------------------------------------
# Header detection
# ---------------------------------------------------------------------------
HEADER_TOKENS = {
    "code": ("code", "كود", "code"),
    "name": ("نوع الخدمة", "الخدمة", "اسم", "أسم", "test name", "object name", "service", "اسم الخدمة", "البيان"),
    "price": ("price", "سعر", "قيمة", "القيمه", "القيمة", "قيمة الكشف", "الاجمالي", "الإجمالي"),
    "member": ("العضو", "عضو", "member"),
    "parents": ("والدين", "الوالدين", "parents"),
    "project": ("مشروع", "المشروع", "project"),
    "serial": ("مسلسل", "s.", "s no", "#", "م", "رقم"),
    "type": ("test type", "type", "النوع"),
    "id": ("test id", "id"),
    "category": ("category", "التصنيف"),
}


def _header_score(row: list[str | None]) -> tuple[str, int]:
    texts = []
    for c in row:
        t = _fix_cell(_cell_str(c)) or ""
        texts.append(t.lower())
    joined = " | ".join(texts)
    score = 0
    kind = "generic"
    if any(t in joined for t in ("نوع الخدمة", "type of service", "الخدمة المؤداة", "الخدمة")) or (
        "code" in joined and ("العضو" in joined or "member" in joined or "مشروع" in joined or "والدين" in joined)
    ):
        kind = "syndicate"
        score += 5
    if "test id" in joined or ("test type" in joined and "test name" in joined) or (
        "object name" in joined and "price" in joined
    ):
        kind = "lab_list"
        score += 5
    if any(t in joined for t in ("أسم الخدمة", "اسم الخدمة", "اسم الخدمه")) and any(
        t in joined for t in ("القيمه", "القيمة", "كود", "code")
    ):
        kind = "simple_4"
        score += 4
    if any(t in joined for t in ("price", "سعر", "قيمة", "القيمه")) and any(
        t in joined for t in ("name", "اسم", "أسم", "test", "خدمة")
    ):
        score += 2
    return kind, score


def _is_priceish(cell) -> bool:
    s = _cell_str(cell)
    if not s:
        return False
    if looks_like_code(s) and len(s) <= 8 and "." not in s:
        return False
    v = parse_number(s)
    return v is not None and not looks_like_code(s.replace(".", "").replace(",", ""))


# ---------------------------------------------------------------------------
# Syndicate parser
# ---------------------------------------------------------------------------
def _parse_syndicate_table(table: list[list[str | None]], page_no: int, rows: list[RawRow]) -> None:
    header = [_fix_cell(_cell_str(c)) or "" for c in table[0]]
    ncols = max(len(r) for r in table if r)
    col_map: dict[str, int] = {}
    for i, t in enumerate(header):
        tl = t.lower()
        if "code" in tl or "كود" in t:
            col_map["code"] = i
        elif any(x in t for x in ("نوع الخدمة", "الخدمة", "name", "البيان", "المؤداة")):
            col_map["name"] = i
        elif "العضو" in t and "مشروع" not in t and "والدين" not in t:
            col_map.setdefault("member", i)
    # If header is the visual-order mess, use positional fallback based on x-order
    # pdfplumber left→right: 40% | 60% | 50% | 50% | member | name | code
    if "name" not in col_map:
        # find the column that is mostly non-numeric text
        text_cols = []
        for i in range(ncols):
            vals = [(_cell_str(r[i]) if i < len(r) else None) for r in table[1:6]]
            alpha = sum(1 for v in vals if v and not parse_number(v) and not looks_like_code(v))
            text_cols.append((alpha, i))
        text_cols.sort(reverse=True)
        if text_cols and text_cols[0][0] >= 2:
            col_map["name"] = text_cols[0][1]
        if "code" not in col_map:
            # rightmost numeric-only short ints
            for i in range(ncols - 1, -1, -1):
                if i == col_map.get("name"):
                    continue
                vals = [(_cell_str(r[i]) if i < len(r) else None) for r in table[1:6]]
                if vals and all(v is None or looks_like_code(v) or (parse_number(v) and "." not in str(v)) for v in vals):
                    if any(looks_like_code(v or "") for v in vals):
                        col_map["code"] = i
                        break

    # price columns = numeric columns other than code
    price_cols: list[int] = []
    for i in range(ncols):
        if i in (col_map.get("name"), col_map.get("code")):
            continue
        vals = [(_cell_str(r[i]) if i < len(r) else None) for r in table[1:]]
        numeric = sum(1 for v in vals if parse_number(v) is not None)
        if numeric >= max(2, len(vals) // 3):
            price_cols.append(i)
    price_cols.sort()

    # Category names from header row / second row
    # Default syndicate mapping (Cairo 2026 radiology / labs / clinics):
    # leftmost → parents_project_40, then parents_project_60,
    # then member_parents_50, member_project_50, then member (full)
    default_cats = [
        "parents_project_40",
        "parents_project_60",
        "member_parents_50",
        "member_project_50",
        "member",
        "member_extra",
        "member_extra2",
    ]
    # If 4 price cols: member, member_project_50, member_parents_50, parents
    # Detect from header percentages
    header_texts = header
    cats: list[str] = []
    for t in header_texts:
        if not t:
            cats.append("")
            continue
        if "40" in t:
            cats.append("parents_project_40")
        elif "60" in t:
            cats.append("parents_project_60")
        elif "50" in t:
            cats.append("member_share_50")
        elif "العضو" in t or "member" in t.lower():
            cats.append("member")
        elif "والدين" in t:
            cats.append("parents")
        elif "مشروع" in t:
            cats.append("project")
        else:
            cats.append("")

    # Build a better cat list for price_cols
    pct_cols = []
    for i, t in enumerate(header_texts):
        if re.search(r"\d+\s*%", t):
            pct_cols.append(i)

    for r_idx, row in enumerate(table[1:], start=2):
        if not row:
            continue
        name = clean_service_name(_fix_cell(_cell_str(row[col_map["name"]]) if "name" in col_map else None))
        code = None
        if "code" in col_map and col_map["code"] < len(row):
            code = _cell_str(row[col_map["code"]])
        if not name and not code:
            continue
        # skip section headers
        if name and not any(parse_number(_cell_str(c)) is not None for c in row):
            # could be a section title like "تابع الأشعة"
            if len(name) > 8 and not looks_like_code(name):
                # still store as row with no prices? skip unless it has a code
                if not code:
                    continue

        prices: list[RawPrice] = []
        for pi, col in enumerate(price_cols):
            val = parse_number(row[col] if col < len(row) else None)
            if val is None:
                continue
            ht = header_texts[col] if col < len(header_texts) else ""
            if re.search(r"\d+\s*%", ht):
                if re.search(r"40\s*%", ht):
                    cat = "parents_project_40"
                elif re.search(r"60\s*%", ht):
                    cat = "parents_project_60"
                else:
                    fifty_left = [j for j, t2 in enumerate(header_texts) if re.search(r"50\s*%", t2)]
                    cat = "member_parents_50" if (fifty_left and col == fifty_left[0]) else "member_project_50"
            elif cats[col]:
                # Header evidence beats positional guessing: the label in the
                # source column is the only reliable statement of what it is.
                cat = map_price_category(cats[col])
            elif pi < len(default_cats):
                cat = default_cats[pi]
            else:
                cat = f"price_{pi + 1}"
            prices.append(RawPrice(value=val, category=cat, currency="EGP"))

        if not name:
            continue
        ar, en = split_name_bilingual(name)
        # Adjacent unlabeled Arabic column (common in syndicate 8-col tables)
        if "name" in col_map:
            ni = col_map["name"]
            for off in (-1, 1, -2, 2):
                j = ni + off
                if 0 <= j < len(row) and j not in (col_map.get("code"), ni):
                    extra = clean_service_name(_fix_cell(_cell_str(row[j])))
                    if extra and has_arabic(extra) and len(extra) >= 4 and not looks_like_code(extra):
                        if not ar:
                            ar = extra
                        elif extra not in (name, en):
                            # don't duplicate
                            pass
                        break
        # First price column in this layout is the member full price when 5 numeric cols
        # and the last numeric before name is member.
        rows.append(RawRow(
            name=name,
            code=code,
            name_ar=ar,
            name_en=en,
            category="Radiology" if any(x in (en or ar or name).lower() for x in ("c.t", "ct ", "mri", "x-ray", "x ray", "ultrasound", "اشعة", "مقطعية", "رنين")) else None,
            prices=prices,
            page=page_no,
            confidence=0.75 if len(prices) >= 1 else 0.4,
            parser="syndicate",
        ))


# ---------------------------------------------------------------------------
# Lab list parser
# ---------------------------------------------------------------------------
def _parse_lab_table(table: list[list[str | None]], page_no: int, rows: list[RawRow], title_hint: str | None) -> None:
    header = [_fix_cell(_cell_str(c) or "") or "" for c in table[0]]
    idx = {"type": None, "id": None, "cat": None, "name": None, "price": None}
    for i, h in enumerate(header):
        hl = h.lower()
        if "test type" in hl or hl == "type":
            idx["type"] = i
        elif "test id" in hl or hl == "id":
            idx["id"] = i
        elif "category" in hl:
            idx["cat"] = i
        elif "test name" in hl or "object name" in hl or "name" in hl:
            idx["name"] = i
        elif "price" in hl:
            idx["price"] = i
    if idx["name"] is None:
        return
    for row in table[1:]:
        if not row:
            continue
        name = clean_service_name(_cell_str(row[idx["name"]]) if idx["name"] < len(row) else None)
        if not name:
            continue
        price = None
        if idx["price"] is not None and idx["price"] < len(row):
            price = parse_number(row[idx["price"]])
        code = _cell_str(row[idx["id"]]) if idx["id"] is not None and idx["id"] < len(row) else None
        cat = _cell_str(row[idx["cat"]]) if idx["cat"] is not None and idx["cat"] < len(row) else None
        ttype = _cell_str(row[idx["type"]]) if idx["type"] is not None and idx["type"] < len(row) else None
        prices = []
        if price is not None:
            prices.append(RawPrice(value=price, category="cash", currency="EGP"))
        rows.append(RawRow(
            name=name,
            code=code,
            name_en=name if not has_arabic(name) else None,
            name_ar=name if has_arabic(name) else None,
            category="Laboratory",
            subcategory=cat or ttype,
            prices=prices,
            page=page_no,
            confidence=0.85 if price is not None else 0.5,
            parser="lab_list",
        ))


# ---------------------------------------------------------------------------
# Simple 4-col (Kasr Al Ainy / generic hospital lists)
# ---------------------------------------------------------------------------
def _parse_simple4(table: list[list[str | None]], page_no: int, rows: list[RawRow]) -> None:
    header = [_fix_cell(_cell_str(c) or "") or "" for c in table[0]]
    # Try: serial | code | name | price  (RTL: price is leftmost in visual, but
    # pdfplumber already returns left→right so price may be first)
    # Detect by content of first data row.
    if len(table) < 2:
        return
    sample = table[1]
    # columns that look like price
    price_i = None
    name_i = None
    code_i = None
    for i, cell in enumerate(sample):
        s = _cell_str(cell) or ""
        if parse_number(s) is not None and ("." in s or re.fullmatch(r"\d{2,6}", s.replace(",", ""))):
            if price_i is None and not looks_like_code(s):
                price_i = i
    for i, h in enumerate(header):
        hl = h.lower()
        if any(x in hl for x in ("أسم الخدمة", "اسم الخدمة", "name")):
            name_i = i
        if "كود" in hl or "code" in hl:
            code_i = i
        if any(x in hl for x in ("القيمه", "القيمة", "price")):
            price_i = i
    if name_i is None:
        # pick the longest text column
        best, bi = 0, None
        for i in range(len(header)):
            texts = [(_cell_str(r[i]) if i < len(r) else "") or "" for r in table[1:6]]
            alpha = sum(1 for t in texts if len(t) > 8)
            if alpha > best:
                best, bi = alpha, i
        name_i = bi
    if name_i is None:
        return
    for row in table[1:]:
        if not row:
            continue
        name = clean_service_name(_cell_str(row[name_i]) if name_i < len(row) else None)
        if not name:
            continue
        if len(name) < 4:
            continue
        price = parse_number(row[price_i]) if price_i is not None and price_i < len(row) else None
        code = _cell_str(row[code_i]) if code_i is not None and code_i < len(row) else None
        if not code:
            # sometimes code sits next to name
            for i, c in enumerate(row):
                if i == name_i:
                    continue
                s = _cell_str(c)
                if s and looks_like_code(s):
                    code = s
                    break
        prices = [RawPrice(value=price, category="cash", currency="EGP")] if price is not None else []
        ar, en = split_name_bilingual(name)
        rows.append(RawRow(
            name=name,
            code=code,
            name_ar=ar,
            name_en=en,
            prices=prices,
            page=page_no,
            confidence=0.8 if price is not None else 0.45,
            parser="simple_4",
        ))


# ---------------------------------------------------------------------------
# Generic table
# ---------------------------------------------------------------------------
def _parse_generic(table: list[list[str | None]], page_no: int, rows: list[RawRow]) -> None:
    if len(table) < 2:
        return
    ncols = max(len(r) for r in table)
    # score columns
    text_scores = [0] * ncols
    num_scores = [0] * ncols
    code_scores = [0] * ncols
    for row in table[1:]:
        for i in range(ncols):
            s = _cell_str(row[i]) if i < len(row) else None
            if not s:
                continue
            if looks_like_code(s):
                code_scores[i] += 1
            elif parse_number(s) is not None:
                num_scores[i] += 1
            elif len(s) >= 4:
                text_scores[i] += 1
    name_i = max(range(ncols), key=lambda i: text_scores[i]) if any(text_scores) else None
    if name_i is None or text_scores[name_i] < 2:
        return
    price_cols = [i for i in range(ncols) if num_scores[i] >= 2 and i != name_i and code_scores[i] < num_scores[i]]
    code_i = max(range(ncols), key=lambda i: code_scores[i]) if any(code_scores) else None
    if code_i is not None and code_scores[code_i] < 2:
        code_i = None

    header = [_fix_cell(_cell_str(c) or "") or "" for c in table[0]]
    for row in table[1:]:
        name = clean_service_name(_cell_str(row[name_i]) if name_i < len(row) else None)
        if not name or len(name) < 3:
            continue
        prices = []
        for i in price_cols:
            v = parse_number(row[i] if i < len(row) else None)
            if v is None:
                continue
            cat = map_price_category(header[i] if i < len(header) else None)
            prices.append(RawPrice(value=v, category=cat, currency="EGP"))
        if not prices:
            continue
        code = _cell_str(row[code_i]) if code_i is not None and code_i < len(row) else None
        ar, en = split_name_bilingual(name)
        rows.append(RawRow(
            name=name, code=code, name_ar=ar, name_en=en,
            prices=prices, page=page_no, confidence=0.6, parser="generic",
        ))


# ---------------------------------------------------------------------------
# Document-level
# ---------------------------------------------------------------------------
def _parse_clinic_fees(text: str) -> list[RawRow]:
    """One-page examination-fee sheets (الكشف الطبي).

    Numbers are taken from the page text; nothing is invented.
    """
    rows: list[RawRow] = []
    if not ("الكشف" in text or "أستاذ" in text or "استشاري" in text):
        return rows
    # Pairs like 260 / 250 or 260/250
    pairs = re.findall(r"(\d{2,4})\s*/\s*(\d{2,4})", text)
    labels = [
        "كشف أستاذ / استشاري",
        "كشف أستاذ مساعد",
        "كشف مدرس / أخصائي",
        "كشف عيادة خارجية أو طوارئ",
    ]
    for i, (a, b) in enumerate(pairs[:4]):
        label = labels[i] if i < len(labels) else f"كشف درجة {i+1}"
        rows.append(RawRow(
            name=label,
            name_ar=label,
            category="Clinic",
            prices=[
                RawPrice(value=float(a), category="cash"),
                RawPrice(value=float(b), category="member"),
            ],
            confidence=0.55,
            parser="clinic_fees",
            notes=f"extracted pair {a}/{b} from examination-fee sheet",
        ))
    return rows


# ---------------------------------------------------------------------------
# Procedure component tables (surgeon / anesthesia / assistants / OR room)
# ---------------------------------------------------------------------------
# Some syndicate sections are not price lists. Each row is one procedure and the
# columns are the cost components of that procedure. A component is not a tariff
# price, so these land in pricing_modifiers and never in price_values.
COMPONENT_LABELS: dict[str, str] = {
    "مستوى الاجراء الطبي": "procedure_level",
    "فتح غرفة العمليات": "or_room",
    "الجراح": "surgeon_fee",
    "التخدير": "anesthesia",
    "المساعدين": "assistants",
}


def _ar_norm(s: str) -> str:
    # NFKC folds the presentation ligature `ﻻ` back to `لا`, which otherwise
    # survives reversal and stops "مستوى الإجراء" from ever matching.
    import unicodedata

    s = unicodedata.normalize("NFKC", s)
    return s.replace("إ", "ا").replace("أ", "ا").replace("آ", "ا").replace("\u200f", "")


def _component_key(header_cell) -> str | None:
    """Map a header cell to a component key, tolerating RTL visual order."""
    raw = _cell_str(header_cell) or ""
    fixed = _fix_cell(raw) or ""
    candidates = {raw, fixed, raw[::-1], fixed[::-1]}
    for cand in candidates:
        c = _ar_norm(cand)
        for label, key in COMPONENT_LABELS.items():
            if _ar_norm(label) in c:
                return key
    return None


def _is_component_table(header: list) -> bool:
    """Three or more known component columns is a component breakdown table.

    The procedure-name column is expected to be the one left unmatched.
    """
    keys = {_component_key(c) for c in header}
    return len({k for k in keys if k}) >= 3


def _parse_component_table(table: list[list], page_no: int, rows: list) -> None:
    from app.normalization import fix_rtl_visual_order

    header = list(table[0])
    col_keys = {i: _component_key(c) for i, c in enumerate(header)}
    name_cols = [i for i, k in col_keys.items() if k == "procedure_level"]
    if name_cols:
        name_col = name_cols[0]
    else:
        # the descriptive column is whatever is not a component amount
        candidates = [i for i in range(len(header)) if col_keys.get(i) is None]
        if not candidates:
            return
        if len(candidates) > 1:
            def _text_ratio(i: int) -> float:
                cells = [_cell_str(r[i]) if i < len(r) else None for r in table[1:]]
                cells = [c for c in cells if c]
                if not cells:
                    return 0.0
                return sum(1 for c in cells if parse_number(c) is None) / len(cells)
            candidates.sort(key=_text_ratio, reverse=True)
        name_col = candidates[0]
    labeled = len({k for k in col_keys.values() if k}) >= 3
    if labeled:
        mod_cols: dict[int, str | None] = {
            i: k for i, k in col_keys.items() if k and k != "procedure_level"
        }
    else:
        # Most pages repeat no header row at all. Never guess a fee type here:
        # keep every numeric column, label it by its own header if any, and let
        # the review queue decide what it is.
        mod_cols = {}
        for i in range(len(header)):
            if i == name_col:
                continue
            cells = [_cell_str(r[i]) if i < len(r) else None for r in table[1:]]
            numeric = [c for c in cells if parse_number(c) is not None]
            if not numeric:
                continue
            codeish = sum(1 for c in numeric if looks_like_code(c or "")) / len(numeric)
            if codeish > 0.6:
                continue  # procedure code column, not an amount
            mod_cols[i] = None
    if not mod_cols:
        return

    for row in table[1:]:
        if not row:
            continue
        raw_name = _cell_str(row[name_col]) if name_col < len(row) else None
        # these tables come out in visual order: restore logical order for display
        name = clean_service_name(fix_rtl_visual_order(raw_name) if raw_name else None)
        if not name:
            continue

        mods: list[RawModifier] = []
        for col, key in sorted(mod_cols.items()):
            if col >= len(row):
                continue
            val = parse_number(row[col])
            if val is None:
                continue
            label = _fix_cell(_cell_str(header[col])) or key or f"عمود {col + 1}"
            if key:
                mods.append(RawModifier(mod_type=key, label=label, value=val, source_page=page_no))
            else:
                mods.append(RawModifier(
                    mod_type="unlabeled_component",
                    label=label,
                    value=val,
                    source_page=page_no,
                    conditions="عمود بلا عنوان في المصدر — يحتاج مراجعة لتحديد نوع الرسم",
                ))
        if not mods:
            # section heading or a note row, not a priced procedure
            continue

        ar, en = split_name_bilingual(name)
        rows.append(RawRow(
            name=name,
            name_ar=ar,
            name_en=en,
            category="Procedure",
            prices=[],           # no base price exists in this table — none invented
            modifiers=mods,
            page=page_no,
            confidence=0.8 if labeled else 0.5,
            parser="components",
        ))


def parse_pdf(path: Path) -> ParsedDoc:
    warnings: list[str] = []
    needs_ocr: list[int] = []
    rows: list[RawRow] = []
    title: str | None = None
    org_hint = provider_hint = None
    year_hint = None
    city_hint = None

    with pdfplumber.open(str(path)) as pdf:
        page_count = len(pdf.pages)
        # title / org from first 2 pages text
        first_text = ""
        try:
            first_text = pdf.pages[0].extract_text() or ""
        except Exception:
            pass
        if len(first_text) < 40 and page_count > 1:
            try:
                first_text += "\n" + (pdf.pages[1].extract_text() or "")
            except Exception:
                pass
        t = first_text
        if "قصر العينى" in t or "قصر العيني" in t or "kasralainy" in t.lower():
            org_hint = "مستشفى قصر العينى التعليمى الجديد"
            provider_hint = org_hint
            city_hint = "القاهرة"
        if "نقابة" in t or "النقابة" in t or "CODE" in t[:200]:
            org_hint = org_hint or "نقابة الأطباء المصرية"
        year_m = re.search(r"(20\d{2})", t)
        if year_m:
            year_hint = int(year_m.group(1))
        if "القاهرة" in t or "القاهره" in t:
            city_hint = city_hint or "القاهرة"

        # choose layout from page 0 / 1 tables
        layout = "generic"
        sample_tables = []
        for pi in range(min(3, page_count)):
            try:
                ts = _extract_tables(pdf.pages[pi])
            except Exception:
                ts = []
            sample_tables.extend(ts)
        # component breakdown tables carry no tariff price at all
        component_mode = any(_is_component_table(t[0]) for t in sample_tables if t and t[0])
        kind_scores = {"syndicate": 0, "lab_list": 0, "simple_4": 0, "generic": 0}
        for t in sample_tables[:6]:
            k, s = _header_score(t[0])
            kind_scores[k] += s
        layout = max(kind_scores, key=kind_scores.get)
        if kind_scores[layout] == 0:
            # structural fallback: 7-col with CODE on the right → syndicate
            for t in sample_tables[:2]:
                joined = " ".join(_fix_cell(_cell_str(c)) or "" for c in (t[0] if t else []))
                if "CODE" in joined or "كود" in joined:
                    layout = "syndicate"
                    break
            else:
                layout = "generic"

        for pi, page in enumerate(pdf.pages, start=1):
            nchars = _page_text_chars(page)
            if nchars < 80:
                needs_ocr.append(pi)
            try:
                tables = _extract_tables(page)
            except Exception as e:
                warnings.append(f"page {pi}: table extract failed: {e}")
                continue
            if not tables:
                continue
            # per-page layout refine
            k, s = _header_score(tables[0][0])
            use = k if s >= 3 else layout
            for table in tables:
                if len(table) < 2:
                    continue
                if component_mode:
                    _parse_component_table(table, pi, rows)
                    continue
                before = len(rows)
                if use == "syndicate":
                    _parse_syndicate_table(table, pi, rows)
                elif use == "lab_list":
                    _parse_lab_table(table, pi, rows, title)
                elif use == "simple_4":
                    _parse_simple4(table, pi, rows)
                else:
                    _parse_generic(table, pi, rows)
                if len(rows) == before:
                    _parse_generic(table, pi, rows)

    # category inference from filename / title
    fname = path.stem
    if "أشعة" in fname or "radiology" in fname.lower():
        default_cat = "Radiology"
    elif "تحاليل" in fname or "lab" in fname.lower() or "المختبر" in fname:
        default_cat = "Laboratory"
    elif "أسنان" in fname or "dental" in fname.lower():
        default_cat = "Dental"
    else:
        default_cat = None
    if default_cat:
        for r in rows:
            if not r.category:
                r.category = default_cat

    # Fallback: 1-page clinic examination fee sheets (الكشف الطبي)
    if not rows and first_text:
        rows.extend(_parse_clinic_fees(first_text))
        if rows:
            layout = "clinic_fees"

    return ParsedDoc(
        rows=rows,
        title=title or path.stem,
        organization_hint=org_hint,
        provider_hint=provider_hint,
        year_hint=year_hint,
        city_hint=city_hint,
        geographic_scope=city_hint,
        warnings=warnings,
        parser=f"pdf_{layout}",
        page_count=page_count,
        needs_ocr_pages=needs_ocr,
    )


def parse_pdf_first_pages_text(path: Path, n: int = 3) -> str:
    d = fitz.open(str(path))
    out = []
    for i in range(min(n, d.page_count)):
        out.append(d[i].get_text())
    d.close()
    return "\n".join(out)
