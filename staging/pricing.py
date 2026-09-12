"""Retail price calculation from the per-supplier markup rules.

Suppliers send their own price; the catalogue needs a retail one. The markup is
a coefficient the client sets per supplier and, for some of them, per category
(LED Crystal: tape x2, everything else x1.5). ViaSvet is the exception - its
price file already carries an RRC column that we take verbatim.

Rules live in `supplier_pricing_rules` (see database/add_pricing_rules.sql), so
changing a coefficient is a row edit plus scripts/recalc_prices.py.

The engine runs at batch upsert time, the single place every importer passes
through, and it only touches `price_retail` - `price` stays exactly what the
supplier sent.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal, DecimalException, ROUND_HALF_UP
from typing import Any

import pymysql
from pymysql.connections import Connection

from staging.db import fetch_all

KOPECK = Decimal("0.01")

# Supplier files mix alphabets: the Jazzway price list writes its luminaire
# section as "Cветильники" with a Latin C. Folding the lookalikes to Cyrillic on
# both sides of the comparison keeps one rule from missing a whole section.
_HOMOGLYPHS = str.maketrans(
    {
        "a": "а", "b": "в", "c": "с", "e": "е", "h": "н", "k": "к",
        "m": "м", "o": "о", "p": "р", "t": "т", "x": "х", "y": "у",
    }
)


def fold(value: Any) -> str:
    """Lowercase, fold Latin lookalikes to Cyrillic, collapse whitespace."""
    if value is None:
        return ""
    return " ".join(str(value).lower().translate(_HOMOGLYPHS).split())


def as_decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value).strip().replace(" ", "").replace(",", "."))
    except (DecimalException, ValueError):
        return None


def _round(value: Decimal, round_to: Decimal | None) -> Decimal:
    if round_to and round_to > 0:
        steps = (value / round_to).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        return (steps * round_to).quantize(KOPECK)
    return value.quantize(KOPECK, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class PricingRule:
    id: int
    supplier_id: int
    rule_name: str
    match_type: str
    match_value: str
    base_field: str
    coefficient: Decimal
    round_to: Decimal | None
    priority: int

    @property
    def is_fallback(self) -> bool:
        return self.match_type == "all"

    @property
    def sort_key(self) -> tuple[int, int, int]:
        # Lower priority first; a fallback never beats a real match at the same
        # priority; a longer pattern wins over a shorter one it contains.
        return (self.priority, 1 if self.is_fallback else 0, -len(self.match_value))

    def matches(self, item: dict[str, Any]) -> bool:
        if self.is_fallback:
            return True
        if not self.match_value:
            return False
        if self.match_type == "name":
            haystack = fold(item.get("name"))
        else:
            category = item.get("supplier_category") or ""
            path = item.get("supplier_category_path") or ""
            haystack = fold(f"{path} {category}")
        return fold(self.match_value) in haystack

    def retail(self, base: Decimal) -> Decimal:
        return _round(base * self.coefficient, self.round_to)

    def describe(self) -> str:
        if self.is_fallback:
            return f"{self.rule_name} [все прочие]"
        return f"{self.rule_name} [{self.match_type}~{self.match_value}]"


class PricingRules:
    """The active rules of one supplier, in evaluation order."""

    def __init__(self, supplier_id: int, rules: list[PricingRule]) -> None:
        self.supplier_id = supplier_id
        self.rules = sorted(rules, key=lambda rule: rule.sort_key)

    def __bool__(self) -> bool:
        return bool(self.rules)

    def __len__(self) -> int:
        return len(self.rules)

    def match(self, item: dict[str, Any]) -> PricingRule | None:
        for rule in self.rules:
            if rule.matches(item):
                return rule
        return None

    def retail_for(self, item: dict[str, Any]) -> tuple[Decimal | None, PricingRule | None]:
        """(retail price, rule used). Price is None when the rule has no base value."""
        rule = self.match(item)
        if rule is None:
            return None, None
        base = as_decimal(item.get(rule.base_field))
        if base is None or base <= 0:
            return None, rule
        return rule.retail(base), rule


_EMPTY = PricingRules(0, [])
_CACHE: dict[int, PricingRules] = {}
_MISSING_TABLE_WARNED = False


def clear_rules_cache() -> None:
    """Drop the per-process cache after editing rules in the same session."""
    _CACHE.clear()


def load_rules(conn: Connection, supplier_id: int, *, use_cache: bool = True) -> PricingRules:
    """Active rules for one supplier.

    Cached for the life of the process: an import asks once per batch and rules
    do not change mid-run. Call clear_rules_cache() after editing them.
    """
    global _MISSING_TABLE_WARNED

    if use_cache and supplier_id in _CACHE:
        return _CACHE[supplier_id]

    try:
        rows = fetch_all(
            conn,
            """
            SELECT id, supplier_id, rule_name, match_type, match_value,
                   base_field, coefficient, round_to, priority
            FROM supplier_pricing_rules
            WHERE supplier_id = %s AND is_active = 1
            """,
            (supplier_id,),
        )
    except pymysql.err.ProgrammingError as exc:
        # 1146: table missing, i.e. the migration has not been applied yet.
        # Importing without markup beats an import that cannot run at all.
        if exc.args and exc.args[0] == 1146:
            if not _MISSING_TABLE_WARNED:
                print(
                    "  Warning: supplier_pricing_rules is missing - importing without "
                    "markup. Apply database/add_pricing_rules.sql.",
                    flush=True,
                )
                _MISSING_TABLE_WARNED = True
            return _EMPTY
        raise

    rules = PricingRules(
        supplier_id,
        [
            PricingRule(
                id=int(row["id"]),
                supplier_id=int(row["supplier_id"]),
                rule_name=row["rule_name"],
                match_type=row["match_type"],
                match_value=row["match_value"] or "",
                base_field=row["base_field"],
                coefficient=as_decimal(row["coefficient"]) or Decimal("1"),
                round_to=as_decimal(row["round_to"]),
                priority=int(row["priority"]),
            )
            for row in rows
        ],
    )
    if use_cache:
        _CACHE[supplier_id] = rules
    return rules


def _rehash(base_hash: str | None, retail: Decimal) -> str:
    """Fold the computed retail price into the row's change-detection hash.

    Without this, changing a coefficient would leave every hash untouched and
    the next import would skip the rows as unchanged.
    """
    payload = json.dumps(
        {"base": base_hash, "price_retail": str(retail)}, ensure_ascii=False, sort_keys=True
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def apply_to_batch(rules: PricingRules, batch: list[dict[str, Any]]) -> int:
    """Set price_retail and pricing_rule_id on normalized rows; return rows changed.

    A rule that leaves the price alone (ViaSvet's verbatim RRC) does not touch
    the hash, so identity rules cause no re-import churn.
    """
    if not rules:
        return 0

    changed = 0
    for item in batch:
        retail, rule = rules.retail_for(item)
        item["pricing_rule_id"] = rule.id if rule else None
        if retail is None:
            continue
        if as_decimal(item.get("price_retail")) == retail:
            continue
        item["price_retail"] = retail
        item["content_hash"] = _rehash(item.get("content_hash"), retail)
        changed += 1
    return changed
