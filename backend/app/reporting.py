"""Reporting rules: which price category is *reported* for a given centre.

Nothing here deletes data. Every extracted price stays in `price_values` with its
provenance; these rules only decide which categories appear in comparison results.

Owner rule (2026 syndicate lists): the centre is named after the folder that holds
the price list, and the member price is the only price reported.
"""
from __future__ import annotations

from sqlalchemy import or_

from app.models import PriceValue, Provider

# Centre name == folder name on disk.
SYNDICATE_2026 = "نقابة الاطباء 2026"

# centre name -> categories that may be reported for that centre
REPORTED_CATEGORIES: dict[str, frozenset[str]] = {
    SYNDICATE_2026: frozenset({"member"}),
}

# Human-facing label for a centre whose folder is the syndicate's.
REPORTED_CATEGORY_LABELS = {"member": "سعر الأعضاء"}


def restricted_centres() -> list[str]:
    return sorted(REPORTED_CATEGORIES)


def reported_categories_filter():
    """SQL predicate that keeps only reportable categories for restricted centres.

    Centres without a rule keep everything. A price record with no provider is
    left untouched: it is reported as "provider not specified in source" rather
    than silently dropped.
    """
    from app.models import Provider as _Provider  # local alias keeps call sites short

    names = restricted_centres()
    allowed = sorted({c for cats in REPORTED_CATEGORIES.values() for c in cats})
    return or_(
        Provider.name.is_(None),
        ~Provider.name.in_(names),
        PriceValue.price_category.in_(allowed),
    )
