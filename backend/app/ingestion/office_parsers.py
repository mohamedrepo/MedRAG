"""Excel / CSV / DOCX / image extractors."""
from __future__ import annotations

import csv
import re
from pathlib import Path

import openpyxl

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

ROOM_CLASSES = {
    "جناح vip": "vip",
    "جناح": "vip",
    "vip": "vip",
    "اولى ممتاز": "first_class_premium",
    "أولى ممتاز": "first_class_premium",
    "اولى عادية": "first_class_standard",
    "أولى عادية": "first_class_standard",
    "اولي عاديه": "first_class_standard",
    "ثانيه مميز": "second_class",
    "ثانية مميز": "second_class",
    "ثانيه": "second_class",
    "cash": "cash",
    "كاش": "cash",
}


def _norm_label(s: str | None) -> str:
    if not s:
        return ""
    s = str(s).strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s


def _looks_like_serial(values: list[str]) -> bool:
    """Increasing near-consecutive integers (1,2,3…) — a row-number column."""
    from app.ingestion.pdf_parsers import _looks_like_serial as _ser
    return _ser(values)


def parse_xlsx(path: Path) -> ParsedDoc:
    wb = openpyxl.load_workbook(str(path), data_only=True)
    rows: list[RawRow] = []
    warnings: list[str] = []
    title = path.stem
    year_hint = None
    m = re.search(r"(20\d{2})", path.name)
    if m:
        year_hint = int(m.group(1))

    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        # Read all values
        grid: list[list] = []
        for row in ws.iter_rows(values_only=True):
            grid.append(list(row))
        if not grid:
            continue

        # Sheet title in first non-empty cell of row 1?
        sheet_title = None
        for c in grid[0]:
            if c and not parse_number(c):
                sheet_title = str(c).strip()
                break

        # Find header row: contains البيان / العملية / اسم
        header_i = None
        class_cols: dict[int, str] = {}
        for i, row in enumerate(grid[:6]):
            labels = [_norm_label(c) for c in row]
            if any("البيان" in x or "العملية" in x or "اسم" in x for x in labels if x):
                header_i = i
                for j, x in enumerate(labels):
                    if not x:
                        continue
                    for k, v in ROOM_CLASSES.items():
                        if k in x:
                            class_cols[j] = v
                            break
                break
        if header_i is None:
            # simple list: name + price
            _parse_excel_simple(grid, sheet_name, rows, sheet_title)
            continue

        # Package / package-total layout
        total_i = None
        item_rows: list[tuple[int, list]] = []
        for i, row in enumerate(grid[header_i + 1 :], start=header_i + 1):
            labels = " ".join(_norm_label(c) for c in row if c)
            if "اجمالي" in labels or "إجمالي" in labels or "الاجمالي" in labels:
                total_i = i
                break
            if row and row[0] and not parse_number(row[0]) and any(parse_number(c) is not None for c in row[1:]):
                item_rows.append((i, row))

        proc_name = clean_service_name(sheet_title or sheet_name) or sheet_name
        if total_i is not None and class_cols:
            total_row = grid[total_i]
            prices = []
            for col, cat in class_cols.items():
                v = parse_number(total_row[col] if col < len(total_row) else None)
                if v is not None:
                    prices.append(RawPrice(value=v, category=cat, currency="EGP"))
            # also capture a single total if no class cols matched
            if not prices:
                for c in total_row[1:]:
                    v = parse_number(c)
                    if v is not None:
                        prices.append(RawPrice(value=v, category="package_total", currency="EGP"))
            ar, en = split_name_bilingual(proc_name)
            rec = RawRow(
                name=proc_name,
                name_ar=ar,
                name_en=en,
                category="Procedure",
                subcategory=sheet_name,
                prices=prices,
                sheet=sheet_name,
                confidence=0.7,
                parser="xlsx_package",
            )
            # line items as modifiers
            for i, row in item_rows:
                label = clean_service_name(str(row[0])) or ""
                vals = [parse_number(c) for c in row[1:] if parse_number(c) is not None]
                if not vals:
                    continue
                rec.modifiers.append(RawModifier(
                    mod_type="component",
                    label=label,
                    value=vals[0],
                    source_page=None,
                ))
            rows.append(rec)
        else:
            _parse_excel_simple(grid, sheet_name, rows, sheet_title)

    return ParsedDoc(
        rows=rows,
        title=title,
        year_hint=year_hint,
        provider_hint="مستشفى الرحمة" if "الرحمة" in path.name or "الرحمة" in title else None,
        organization_hint="مستشفى الرحمة",
        warnings=warnings,
        parser="xlsx",
        page_count=len(wb.sheetnames),
    )


def _parse_excel_simple(grid: list[list], sheet: str, rows: list[RawRow], sheet_title: str | None) -> None:
    if not grid:
        return
    # find name col and price col from first data-ish rows
    ncols = max(len(r) for r in grid[:8])
    name_i = price_i = code_i = None
    for i, row in enumerate(grid[:8]):
        for j, c in enumerate(row):
            s = _norm_label(c)
            if any(x in s for x in ("اسم", "الخدمة", "البيان", "name", "العملية")) and name_i is None:
                name_i = j
            if any(x in s for x in ("سعر", "قيمة", "price", "القيمه")) and price_i is None:
                price_i = j
            if any(x in s for x in ("كود", "code", "id")) and code_i is None:
                code_i = j
    if name_i is None:
        # first long text column
        for j in range(ncols):
            vals = [grid[i][j] for i in range(min(8, len(grid)))]
            if any(isinstance(v, str) and len(v) > 6 for v in vals):
                name_i = j
                break
    if name_i is None:
        return
    if price_i is None:
        # first numeric column after name that is not the code column and does
        # not look like a serial run (1,2,3…), which would be row numbers
        for j in range(ncols):
            if j == name_i or j == code_i:
                continue
            vals = [grid[i][j] if j < len(grid[i]) else None for i in range(min(15, len(grid)))]
            hits = sum(1 for v in vals if parse_number(v) is not None)
            if hits < 3:
                continue
            if _looks_like_serial([str(v) for v in vals if v is not None]):
                continue
            price_i = j
            break
    for i, row in enumerate(grid):
        name = clean_service_name(str(row[name_i]) if name_i < len(row) and row[name_i] else None)
        if not name or len(name) < 4:
            continue
        if looks_like_code(name):
            continue
        price = parse_number(row[price_i]) if price_i is not None and price_i < len(row) else None
        if price is None:
            continue
        code = None
        if code_i is not None and code_i < len(row) and row[code_i] is not None:
            code = str(row[code_i])
            if code.endswith(".0") and code[:-2].isdigit():
                code = code[:-2]  # Excel numeric cells arrive as 123.0
        ar, en = split_name_bilingual(name)
        rows.append(RawRow(
            name=name,
            code=code,
            name_ar=ar,
            name_en=en,
            prices=[RawPrice(value=price, category="cash", currency="EGP")],
            sheet=sheet,
            cell=f"{sheet}!R{i + 1}",
            confidence=0.65,
            parser="xlsx_simple",
        ))


def parse_csv(path: Path) -> ParsedDoc:
    rows: list[RawRow] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        sample = f.read(4096)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample)
        except csv.Error:
            dialect = csv.excel
        reader = csv.reader(f, dialect)
        grid = list(reader)
    if not grid:
        return ParsedDoc(rows=[], parser="csv", title=path.stem)
    header = [clean_service_name(h) or "" for h in grid[0]]
    name_i = price_i = code_i = None
    for i, h in enumerate(header):
        hl = h.lower()
        if any(x in hl for x in ("name", "اسم", "خدمة", "test")) and name_i is None:
            name_i = i
        if any(x in hl for x in ("price", "سعر", "قيمة")) and price_i is None:
            price_i = i
        if any(x in hl for x in ("code", "كود", "id")) and code_i is None:
            code_i = i
    if name_i is None:
        name_i = 0
    for row in grid[1:]:
        name = clean_service_name(row[name_i] if name_i < len(row) else None)
        if not name:
            continue
        price = None
        if price_i is None:
            for j, c in enumerate(row):
                if j == name_i:
                    continue
                v = parse_number(c)
                if v is not None and not looks_like_code(str(c)):
                    price = v
                    price_i = j
                    break
        else:
            price = parse_number(row[price_i]) if price_i < len(row) else None
        if price is None:
            continue
        code = row[code_i] if code_i is not None and code_i < len(row) else None
        ar, en = split_name_bilingual(name)
        cat = map_price_category(header[price_i] if price_i is not None and price_i < len(header) else None)
        rows.append(RawRow(
            name=name, code=code, name_ar=ar, name_en=en,
            prices=[RawPrice(value=price, category=cat, currency="EGP")],
            confidence=0.7, parser="csv",
        ))
    return ParsedDoc(rows=rows, title=path.stem, parser="csv")


def parse_docx(path: Path) -> ParsedDoc:
    from docx import Document

    doc = Document(str(path))
    rows: list[RawRow] = []
    title = doc.core_properties.title or path.stem
    for ti, table in enumerate(doc.tables):
        if len(table.rows) < 2:
            continue
        grid = []
        for r in table.rows:
            grid.append([c.text for c in r.cells])
        # reuse generic
        from app.ingestion.pdf_parsers import _parse_generic

        before = len(rows)
        _parse_generic(grid, ti + 1, rows)  # page = table index
        if len(rows) == before:
            # try simple
            header = grid[0]
            name_i = 0
            for i, h in enumerate(header):
                if any(x in h.lower() for x in ("name", "اسم", "خدمة")):
                    name_i = i
                    break
            price_i = None
            for i, h in enumerate(header):
                if any(x in h.lower() for x in ("price", "سعر", "قيمة")):
                    price_i = i
                    break
            if price_i is None and len(header) > 1:
                price_i = 1 if name_i == 0 else 0
            for row in grid[1:]:
                name = clean_service_name(row[name_i]) if name_i < len(row) else None
                if not name:
                    continue
                price = parse_number(row[price_i]) if price_i is not None and price_i < len(row) else None
                if price is None:
                    continue
                ar, en = split_name_bilingual(name)
                rows.append(RawRow(
                    name=name, name_ar=ar, name_en=en,
                    prices=[RawPrice(value=price, category="cash")],
                    page=ti + 1, confidence=0.55, parser="docx",
                ))
    # also paragraphs as notes / conditions — skipped for now
    return ParsedDoc(rows=rows, title=title, parser="docx")


def parse_image(path: Path) -> ParsedDoc:
    """OCR an image if tesseract is available; otherwise flag for review."""
    rows: list[RawRow] = []
    warnings: list[str] = []
    try:
        import pytesseract
        from PIL import Image

        img = Image.open(path)
        text = pytesseract.image_to_string(img, lang="ara+eng")
        # extract name+price lines
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            # last number on the line as price
            m = re.search(r"(\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?)\s*$", line)
            if not m:
                continue
            price = parse_number(m.group(1))
            name = clean_service_name(line[: m.start()])
            if not name or price is None:
                continue
            ar, en = split_name_bilingual(name)
            rows.append(RawRow(
                name=name, name_ar=ar, name_en=en,
                prices=[RawPrice(value=price, category="cash")],
                confidence=0.35, parser="ocr", notes="OCR extracted",
            ))
    except Exception as e:
        warnings.append(f"OCR unavailable or failed: {e}")
    return ParsedDoc(rows=rows, title=path.stem, parser="image_ocr", warnings=warnings)


def parse_txt_or_html(path: Path) -> ParsedDoc:
    raw = path.read_text(encoding="utf-8", errors="replace")
    rows: list[RawRow] = []
    if path.suffix.lower() in {".html", ".htm"}:
        from lxml import html as lhtml

        tree = lhtml.fromstring(raw)
        tables = tree.xpath("//table")
        for ti, tbl in enumerate(tables):
            grid = []
            for tr in tbl.xpath(".//tr"):
                cells = ["".join(td.itertext()).strip() for td in tr.xpath("./td|./th")]
                if cells:
                    grid.append(cells)
            if len(grid) < 2:
                continue
            from app.ingestion.pdf_parsers import _parse_generic

            _parse_generic(grid, ti + 1, rows)
    else:
        for line in raw.splitlines():
            m = re.search(r"(\d+(?:[.,]\d+)?)\s*$", line)
            if not m:
                continue
            name = clean_service_name(line[: m.start()])
            price = parse_number(m.group(1))
            if not name or price is None:
                continue
            ar, en = split_name_bilingual(name)
            rows.append(RawRow(
                name=name, name_ar=ar, name_en=en,
                prices=[RawPrice(value=price, category="cash")],
                confidence=0.4, parser="txt",
            ))
    return ParsedDoc(rows=rows, title=path.stem, parser="txt_html")
