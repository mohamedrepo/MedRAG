"""Multi-stage Arabic / English medical name normalization.

Never destroys the original name; callers keep the raw string and use the
normalized form only for matching, indexing and deduplication.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Character classes
# ---------------------------------------------------------------------------
ARABIC_RANGES = (
    (0x0600, 0x06FF),
    (0x0750, 0x077F),
    (0x08A0, 0x08FF),
    (0xFB50, 0xFDFF),
    (0xFE70, 0xFEFF),
)

TASHKEEL = set(
    "\u064B\u064C\u064D\u064E\u064F\u0650\u0651\u0652\u0653\u0654\u0655"
    "\u0670\u06D6\u06D7\u06D8\u06D9\u06DA\u06DB\u06DC\u06DF\u06E0\u06E1"
    "\u06E2\u06E3\u06E4\u06E5\u06E6\u06E7\u06E8\u06EA\u06EB\u06EC\u06ED"
)
TATWEEL = "\u0640"

# Alef variants → ا ; Ya variants → ي ; etc.
ARABIC_CHAR_MAP = {
    "أ": "ا",
    "إ": "ا",
    "آ": "ا",
    "ٱ": "ا",
    "ى": "ي",
    "ئ": "ي",
    "ؤ": "و",
    "ة": "ه",
    "ك": "ك",
    "‌": "",
    "‍": "",
}

# Arabic-Indic digits → ASCII
AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")

PUNCT_RE = re.compile(
    r"[\.\,\;\:\!\?\(\)\[\]\{\}\<\>\"'`´\‘\’\“\”\-\–\—\_\/\\|\+\=\*\&\^\%\$\#\@\!\~\؟\،\؛\·\•\u2010-\u2027]+"
)
SPACE_RE = re.compile(r"\s+")
NON_ALNUM_SPACE = re.compile(r"[^a-z0-9\u0600-\u06ff\s]+")

# OCR / keyboard confusions
OCR_MAP = str.maketrans({
    "0": "o",
    "1": "l",
    "5": "s",
    "8": "b",
    "6": "b",
    "2": "z",
    "$": "s",
    "@": "a",
    "!": "i",
    "|": "l",
})


def has_arabic(s: str) -> bool:
    return any(0x0600 <= ord(ch) <= 0x06FF for ch in s)


def has_latin(s: str) -> bool:
    return any("a" <= ch.lower() <= "z" for ch in s)


def detect_language(s: str) -> str:
    ar = sum(1 for ch in s if 0x0600 <= ord(ch) <= 0x06FF)
    la = sum(1 for ch in s if ch.isascii() and ch.isalpha())
    if ar and la:
        return "mixed"
    if ar:
        return "ar"
    return "en"


def strip_diacritics(s: str) -> str:
    return "".join(ch for ch in s if ch not in TASHKEEL and unicodedata.category(ch) != "Mn")


def normalize_arabic_chars(s: str) -> str:
    out = []
    for ch in s:
        if ch in TATWEEL or ch in TASHKEEL:
            continue
        out.append(ARABIC_CHAR_MAP.get(ch, ch))
    return "".join(out)


def fix_rtl_visual_order(s: str) -> str:
    """pdfplumber often emits Arabic in visual (reversed) order.

    Reverse each multi-word Arabic phrase as a whole so both letter order
    and word order are restored. Latin / digits outside the phrase are kept.
    """
    if not has_arabic(s):
        return s

    def _rev(m: re.Match) -> str:
        run = m.group(0)
        if len(run) < 2:
            return run
        return run[::-1]

    # Contiguous Arabic words (spaces only between Arabic tokens).
    return re.sub(r"[\u0600-\u06FF]+(?:\s+[\u0600-\u06FF]+)*", _rev, s)


def normalize_whitespace(s: str) -> str:
    return SPACE_RE.sub(" ", s).strip()


def normalize_english(s: str) -> str:
    s = s.lower()
    s = s.translate(AR_DIGITS)
    s = NON_ALNUM_SPACE.sub(" ", s)
    s = normalize_whitespace(s)
    return s


def normalize_arabic(s: str) -> str:
    s = strip_diacritics(s)
    s = s.translate(AR_DIGITS)
    s = normalize_arabic_chars(s)
    s = PUNCT_RE.sub(" ", s)
    s = normalize_whitespace(s)
    return s


def normalize_mixed(s: str) -> str:
    s = strip_diacritics(s)
    s = s.translate(AR_DIGITS)
    s = normalize_arabic_chars(s)
    s = s.lower()
    s = NON_ALNUM_SPACE.sub(" ", s)
    s = normalize_whitespace(s)
    return s


def normalize_query(raw: str) -> str:
    """Public entry: stable normalized key for any query or alias."""
    if not raw:
        return ""
    s = unicodedata.normalize("NFKC", raw)
    s = s.replace("\u00a0", " ")
    lang = detect_language(s)
    if lang == "ar":
        return normalize_arabic(s)
    if lang == "mixed":
        return normalize_mixed(s)
    return normalize_english(s)


def collapse_separators(raw: str) -> str:
    """cbc / c.b.c / c-b-c / c b c → cbc   (acronym-friendly)."""
    s = normalize_query(raw)
    return re.sub(r"[\s\-_.]", "", s)


def acronym_of(name: str) -> str | None:
    """Complete Blood Picture → cbp ; Magnetic Resonance Imaging → mri."""
    s = normalize_english(name)
    if not s:
        return None
    # Prefer existing parenthesised acronym
    m = re.search(r"\(([a-z0-9 .\-]{2,12})\)", s)
    if m:
        ac = collapse_separators(m.group(1))
        if 2 <= len(ac) <= 8 and ac.isalpha():
            return ac
    words = [w for w in s.split() if w not in STOPWORDS_EN]
    if not words:
        return None
    if len(words) >= 2:
        ac = "".join(w[0] for w in words)
        if 2 <= len(ac) <= 6:
            return ac
    return None


def expands_acronym(normalized: str, full_name: str) -> bool:
    ac = acronym_of(full_name)
    if not ac:
        return False
    return collapse_separators(normalized) == ac


STOPWORDS_EN = {
    "of", "the", "and", "or", "with", "without", "for", "in", "on", "a", "an",
    "test", "examination", "exam", "procedure", "service",
}

STOPWORDS_AR = {
    "على", "في", "من", "و", "او", "أو", "تحليل", "فحص", "اشعة", "أشعة",
    "صورة", "كاملة", "الدم", "الام", "آلام",
}

# Tokens that alone must not drive a merge (Vitamin A vs Vitamin D).
GENERIC_FAMILY_TOKENS = {
    "vitamin", "vit", "فيامين", "فيتامين", "هرمون", "hormone",
    "total", "free", "serum", "blood", "urine",
}


@dataclass
class NormalizedTerm:
    raw: str
    normalized: str
    collapsed: str
    language: str
    tokens: list[str] = field(default_factory=list)
    is_acronym: bool = False

    def __post_init__(self) -> None:
        self.tokens = self.normalized.split() if self.normalized else []
        compact = self.collapsed.replace(" ", "")
        self.is_acronym = (
            len(compact) <= 8
            and compact.isalpha()
            and self.normalized.replace(" ", "") != compact
            or (len(compact) <= 6 and self.normalized == compact and compact.isalpha() and len(self.tokens) == 1)
        )


def normalize_term(raw: str) -> NormalizedTerm:
    norm = normalize_query(raw)
    return NormalizedTerm(
        raw=raw,
        normalized=norm,
        collapsed=collapse_separators(raw),
        language=detect_language(raw),
    )


def token_set(s: str) -> frozenset[str]:
    return frozenset(w for w in normalize_query(s).split() if w)


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def dice(a: str, b: str) -> float:
    """Character bigram Dice coefficient — robust for OCR / spelling noise."""
    if a == b:
        return 1.0
    if len(a) < 2 or len(b) < 2:
        return 1.0 if a == b else 0.0

    def bigrams(x: str) -> dict[str, int]:
        d: dict[str, int] = {}
        for i in range(len(x) - 1):
            d[x[i : i + 2]] = d.get(x[i : i + 2], 0) + 1
        return d

    da, db = bigrams(a), bigrams(b)
    overlap = sum(min(c, db.get(g, 0)) for g, c in da.items())
    return 2 * overlap / (sum(da.values()) + sum(db.values()))


def levenshtein(a: str, b: str, max_dist: int = 4) -> int:
    if a == b:
        return 0
    if abs(len(a) - len(b)) > max_dist:
        return max_dist + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
        if min(prev) > max_dist:
            return max_dist + 1
    return prev[-1]


def similar(a: str, b: str) -> float:
    """Combined lexical similarity in [0,1]."""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    # Separator-insensitive comparison (C.B.C vs cbc vs c b c)
    ca, cb = collapse_separators(a).lower(), collapse_separators(b).lower()
    if ca == cb:
        return 1.0
    lev = 1.0 - min(levenshtein(a, b, 6), 6) / 6.0
    dg = dice(a, b)
    jac = jaccard(token_set(a), token_set(b))
    # collapsed-form similarity as additional evidence
    lev_c = 1.0 - min(levenshtein(ca, cb, 6), 6) / 6.0
    return max(lev, dg, 0.85 * jac, 0.95 * lev_c if abs(len(ca) - len(cb)) <= 2 else 0)


# Common Egyptian / Levantine transliterations of English exam names
TRANSLIT_RULES: list[tuple[str, str]] = [
    ("مري", "mri"),
    ("رنين مغناطيسي", "mri"),
    ("رنين", "mri"),
    ("سي تي", "ct"),
    ("اشعة مقطعية", "ct"),
    ("اشعة مقطعيه", "ct"),
    ("مقطعية", "ct"),
    ("ايكو", "echo"),
    ("سونار", "ultrasound"),
    ("موجات صوتية", "ultrasound"),
    ("اشعة جاما", "gamma"),
    ("دم", "blood"),
    ("بول", "urine"),
    ("براز", "stool"),
]

CONTRAST_HINTS = {
    "contrast", "with contrast", "without contrast", "بالصبغة", "بدون صبغة",
    "صبغة", "contrast enhanced", "ce",
}

VIEW_HINTS = {"one view", "two views", "3 views", "وضع واحد", "وضعين", "وضعان", "three views"}

LATERALITY = {
    "right": "right",
    "left": "left",
    "bilateral": "bilateral",
    "يمين": "right",
    "يسار": "left",
    "ثنائي": "bilateral",
    "الجهتين": "bilateral",
}


def extract_attributes(name: str) -> dict[str, Any]:
    """Pull contrast / views / laterality so we never merge distinct variants."""
    s = normalize_query(name)
    attrs: dict[str, Any] = {}
    if any(h in s or h in normalize_arabic(name) for h in ("with contrast", "بالصبغة", "صبغة", "contrast")):
        if "without" in s or "بدون" in normalize_arabic(name) or "غير" in name:
            attrs["contrast"] = "without"
        elif "contrast" in s or "صبغ" in name or "بالصبغة" in name:
            attrs["contrast"] = "with"
    if "بدون صبغة" in name or "without contrast" in s:
        attrs["contrast"] = "without"
    if "بالصبغة" in name or "with contrast" in s:
        attrs["contrast"] = "with"

    if any(v in s for v in ("two view", "2 view", "وضعين", "وضعان", "ap and lateral", "ap & lateral", "ap/lat", "pa and lateral")):
        attrs["views"] = 2
    elif any(v in s for v in ("one view", "1 view", "وضع واحد")):
        attrs["views"] = 1
    elif any(v in s for v in ("three view", "3 view")):
        attrs["views"] = 3
    elif re.search(r"\bap\b", s) and not re.search(r"\blat(er(al)?)?\b", s):
        attrs["views"] = 1
    elif re.search(r"\blat(er(al)?)?\b", s) and re.search(r"\bap\b", s):
        attrs["views"] = 2

    low = name.lower()
    if "bilateral" in s or "الجهتين" in name or "ثنائي" in name:
        attrs["laterality"] = "bilateral"
    elif "right" in s or "يمين" in name:
        attrs["laterality"] = "right"
    elif "left" in s or "يسار" in name:
        attrs["laterality"] = "left"

    if "quantitative" in s or "كمي" in name:
        attrs["quantification"] = "quantitative"
    if "qualitative" in s or "نوعي" in name:
        attrs["quantification"] = "qualitative"
    if "differential" in s or "نوعي" in name:
        attrs.setdefault("differential", True)
    return attrs


def attributes_compatible(a: dict | None, b: dict | None) -> bool:
    """False when the two variants must stay separate (contrast, views, side…)."""
    a = a or {}
    b = b or {}
    keys = ("contrast", "views", "laterality", "quantification")
    for k in keys:
        va, vb = a.get(k), b.get(k)
        if va is not None and vb is not None and va != vb:
            return False
        # one says with contrast, other silent → still allow merge only if both silent
        # If one explicitly says "without" and other is silent, keep separate
        # unless caller explicitly allows.
    return True


def split_bilingual(raw: str) -> tuple[str | None, str | None]:
    """Split 'صورة دم كاملة CBC' or 'Complete Blood Picture صورة دم' into parts."""
    if not raw:
        return None, None
    ar_parts, en_parts = [], []
    buf_ar, buf_en = [], []
    for ch in raw:
        if 0x0600 <= ord(ch) <= 0x06FF:
            if buf_en:
                en_parts.append("".join(buf_en))
                buf_en = []
            buf_ar.append(ch)
        else:
            if buf_ar:
                ar_parts.append("".join(buf_ar))
                buf_ar = []
            if ch.isascii() and (ch.isalnum() or ch in " .-()"):
                buf_en.append(ch)
    if buf_ar:
        ar_parts.append("".join(buf_ar))
    if buf_en:
        en_parts.append("".join(buf_en))
    ar = normalize_whitespace(" ".join(ar_parts)) if ar_parts else None
    en = normalize_whitespace(" ".join(en_parts)) if en_parts else None
    if en and not has_arabic(en) and len(en) < 2:
        en = None
    return ar, en
