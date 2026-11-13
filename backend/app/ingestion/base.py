"""Shared ingestion types and helpers."""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.normalization import has_arabic, normalize_whitespace


@dataclass
class RawPrice:
    value: float
    category: str
    currency: str = "EGP"
    modifier_type: str = "base"
    conditions: str | None = None
    raw_text: str | None = None


@dataclass
class RawModifier:
    mod_type: str
    label: str
    value: float | None = None
    is_percentage: bool = False
    conditions: str | None = None
    source_page: int | None = None


@dataclass
class RawRow:
    """One examination line extracted from a source document."""

    name: str
    code: str | None = None
    name_en: str | None = None
    name_ar: str | None = None
    category: str | None = None
    subcategory: str | None = None
    attributes: dict | None = None
    prices: list[RawPrice] = field(default_factory=list)
    modifiers: list[RawModifier] = field(default_factory=list)
    page: int | None = None
    sheet: str | None = None
    cell: str | None = None
    confidence: float = 0.5
    conditions: str | None = None
    notes: str | None = None
    parser: str = "generic"


@dataclass
class ParsedDoc:
    rows: list[RawRow]
    title: str | None = None
    organization_hint: str | None = None
    provider_hint: str | None = None
    year_hint: int | None = None
    city_hint: str | None = None
    geographic_scope: str | None = None
    effective_date: str | None = None
    expiration_date: str | None = None
    currency: str = "EGP"
    warnings: list[str] = field(default_factory=list)
    parser: str = "generic"
    page_count: int = 0
    needs_ocr_pages: list[int] = field(default_factory=list)


PRICE_CAT_SYNONYMS = {
    "cash": "cash",
    "كاش": "cash",
    "نقدي": "cash",
    "member": "member",
    "العضو": "member",
    "عضو": "member",
    "العضو وأسرته": "member_family",
    "عضو وأسرته": "member_family",
    "الوالدين": "parents",
    "والدين": "parents",
    "المشروع": "project",
    "مشروع": "project",
    "insurance": "insurance",
    "تأمين": "insurance",
    "تامين": "insurance",
    "contracted": "contracted",
    "تعاقد": "contracted",
    "متعاقد": "contracted",
    "vip": "vip",
    "جناح vip": "vip",
    "جناح": "vip",
    "اولى ممتاز": "first_class_premium",
    "أولى ممتاز": "first_class_premium",
    "اولى عادية": "first_class_standard",
    "أولى عادية": "first_class_standard",
    "اولي عاديه": "first_class_standard",
    "ثانيه مميز": "second_class",
    "ثانية مميز": "second_class",
    "ثانيه": "second_class",
    # generic price-column headers found in hospital lists
    "القيمة": "cash",
    "القيمه": "cash",
    "سعر": "cash",
    "price": "cash",
    # Compound syndicate/project categories — these must be checked before the
    # short keys above, otherwise "member_project_50" collapses to "member"
    # and the 50% share distinction is lost.
    "member_share_50": "member_share_50",
    "member_project_50": "member_project_50",
    "member_parents_50": "member_parents_50",
    "parents_project_40": "parents_project_40",
    "parents_project_60": "parents_project_60",
    "package_total": "package_total",
}

# Longest keys first so "member_project_50" wins over "member".
_SYNONYM_KEYS_BY_LEN = sorted(
    ((k, v) for k, v in PRICE_CAT_SYNONYMS.items() if k),
    key=lambda kv: len(kv[0]),
    reverse=True,
)


def map_price_category(label: str | None) -> str:
    if not label:
        return "unspecified"
    s = label.strip().lower()
    s = s.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    s = re.sub(r"\s+", " ", s)
    if s in PRICE_CAT_SYNONYMS:
        return PRICE_CAT_SYNONYMS[s]
    for k, v in _SYNONYM_KEYS_BY_LEN:
        if k in s:
            return v
    # percentage project shares (Latin % and Arabic ٪)
    m = re.search(r"(\d+)\s*[%٪]", s)
    if m:
        return f"project_{m.group(1)}"
    return re.sub(r"[^a-z0-9\u0600-\u06ff]+", "_", s)[:60] or "unspecified"


def parse_number(raw: Any) -> float | None:
    """Parse a price cell. Never invent a value; None means missing.

    Handles 1,234.56 / 1.234,56 / ١٢٣ / '460' / '460.00' / '4,600'.
    Does NOT treat '-' or empty as zero.
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        if isinstance(raw, float) and (raw != raw):  # NaN
            return None
        return float(raw)
    s = str(raw).strip()
    if not s or s in {"-", "–", "—", "N/A", "NA", "n/a", "؟", "?"}:
        return None
    s = s.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    s = s.replace("جنيه", "").replace("EGP", "").replace("ج.م", "")
    s = s.replace(" ", "")
    # reject if still contains letters (probably a code)
    if re.search(r"[A-Za-z\u0600-\u06ff]", s):
        # allow things like 1,000EGP already stripped
        if re.search(r"[A-Za-z\u0600-\u06ff]", s):
            return None
    # 1,234.56
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            # 1.234,56
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        # 1,234 or 123,4 — both read as a plain number once commas are dropped
        s = s.replace(",", "")
    elif re.fullmatch(r"\d{1,3}\.\d{3}", s):
        # European thousands dot: "1.250" means 1250, not 1.25.
        # A 3-decimal fraction is not a valid EGP price (piastres = 2 decimals).
        s = s.replace(".", "")
    try:
        v = float(s)
        if v != v or v in (float("inf"), float("-inf")):
            return None
        # sanity: medical prices rarely exceed 10 million
        if abs(v) > 10_000_000:
            return None
        return v
    except ValueError:
        return None


def looks_like_code(s: str) -> bool:
    s = s.strip()
    if not s:
        return False
    if re.fullmatch(r"\d{2,8}", s):
        return True
    if re.fullmatch(r"[A-Z]{1,4}[-\s]?\d{2,6}", s):
        return True
    return False


# Fee-note parentheticals duplicate the stored price (e.g. 'جنية 5000 شامل
# اجر الطبيب') — they are notes, not part of the service name.
_FEE_NOTE_RE = re.compile(r"جنيه?\s*\d+|شامل\s*(اجر|أجر|اجلر)")


def clean_service_name(s: str | None) -> str | None:
    if not s:
        return None
    s = str(s)
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("\u00a0", " ")
    # strip leading row numbers
    s = re.sub(r"^\s*\d{1,4}[\s\.\)]+", "", s)
    s = normalize_whitespace(s)
    s = _fix_visual_parens(s)
    if len(s) < 2:
        return None
    if looks_like_code(s) and len(s) <= 8:
        return None
    return s


def _fix_visual_parens(s: str) -> str:
    """Repair RTL visual-order paren damage and drop fee-note tails.

    Visual-order PDFs emit ')أ( ب' where the logical text is 'ب (أ)'. Also
    strip parentheticals that merely restate the price (fee notes).
    """
    s = s.strip()
    for _ in range(3):
        m = re.match(r"^\)\s*([^()]{2,80})\(\s*(.+)$", s)
        if m:
            s = f"{m.group(2).strip()} ({m.group(1).strip()})"
            continue
        m = re.match(r"^\(([^()]{2,80})\)\s*(.+)$", s)
        if m:
            s = f"{m.group(2).strip()} ({m.group(1).strip()})"
            continue
        break

    # mid-string visual damage ')أ(' → logical '(أ)' (same transform as the
    # leading rule, applied in place); fee-note groups are dropped outright
    def _visual_to_logical(m: re.Match) -> str:
        inner = m.group(1).strip()
        if _FEE_NOTE_RE.search(inner):
            return " "
        return f"({inner})"

    s = re.sub(r"\)\s*([^()]{2,80})\(", _visual_to_logical, s)

    def _drop_fee(m: re.Match) -> str:
        inner = m.group(1)
        return "" if _FEE_NOTE_RE.search(inner) else f" ({inner})"

    s = re.sub(r"\(([^()]{2,80})\)", _drop_fee, s)
    s = re.sub(r"\s{2,}", " ", s)
    # drop empty paren pairs anywhere
    s = re.sub(r"[\s]*\([\s\.]*\)[\s]*", " ", s).strip()

    # Edge runs of pure paren/space/dot debris (visual-order leftovers like
    # ') (' at an edge) are dropped only when the remainder keeps balanced
    # parens — a balanced name always keeps its closing paren.
    m = re.search(r"[\(\)\s\.]*[\(\)][\(\)\s\.]*$", s)
    if m and (m.start() > 0 or len(m.group(0).strip()) > 0):
        rest = s[: m.start()]
        if rest.count("(") == rest.count(")"):
            s = rest
    m = re.match(r"^[\(\)\s\.]*[\(\)][\(\)\s\.]*(?=.)", s)
    if m:
        rest = s[m.end():]
        if rest.count("(") == rest.count(")"):
            s = rest
    while s and s[-1] in " -–—:،,":
        s = s[:-1].rstrip()
    while s and s[0] in " -–—:،,":
        s = s[1:].lstrip()
    return s.strip()


def split_name_bilingual(name: str) -> tuple[str | None, str | None]:
    """'أشعة مقطعية على المخ C.T. Brain' → (ar, en)."""
    from app.normalization import split_bilingual

    ar, en = split_bilingual(name)
    if ar and en:
        return ar, en
    # try latin/arabic split more aggressively
    ar_buf, en_buf = [], []
    for token in name.split():
        if has_arabic(token):
            ar_buf.append(token)
        else:
            en_buf.append(token)
    ar = " ".join(ar_buf) if ar_buf else None
    en = " ".join(en_buf) if en_buf else None
    if en and not any(c.isalpha() for c in en):
        en = None
    return ar, en


def file_checksum(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sniff_format(filename: str) -> str:
    ext = Path(filename).suffix.lower().lstrip(".")
    return {
        "xlsx": "xlsx",
        "xls": "xls",
        "csv": "csv",
        "pdf": "pdf",
        "docx": "docx",
        "doc": "doc",
        "pptx": "pptx",
        "txt": "txt",
        "html": "html",
        "htm": "html",
        "jpg": "image",
        "jpeg": "image",
        "png": "image",
        "tiff": "image",
        "tif": "image",
    }.get(ext, ext or "unknown")
