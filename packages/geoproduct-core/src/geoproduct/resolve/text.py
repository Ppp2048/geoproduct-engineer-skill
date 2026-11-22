"""Comparison keys for names and addresses, and name similarity.

These keys are for *comparing*, never for display: records keep the names their sources
gave. The word lists are deliberately short and generic (English abbreviations and common
legal suffixes); pipelines add their own through stage parameters.
"""

import re
import unicodedata
from collections.abc import Iterable, Mapping

from rapidfuzz import fuzz

from geoproduct.schemas import Address

ABBREVIATIONS: Mapping[str, str] = {
    "&": "and",
    "ave": "avenue",
    "av": "avenue",
    "blvd": "boulevard",
    "bldg": "building",
    "ctr": "centre",
    "center": "centre",
    "dept": "department",
    "dr": "drive",
    "gen": "general",
    "hosp": "hospital",
    "intl": "international",
    "ln": "lane",
    "mt": "mount",
    "natl": "national",
    "rd": "road",
    "sq": "square",
    "st": "street",
    "univ": "university",
}

LEGAL_SUFFIXES = frozenset(
    {
        "co",
        "company",
        "corp",
        "corporation",
        "gmbh",
        "inc",
        "llc",
        "llp",
        "ltd",
        "limited",
        "plc",
        "pte",
        "pvt",
        "sa",
        "sarl",
        "srl",
    }
)

_NON_WORD = re.compile(r"[^\w&]+")


def fold(text: str) -> str:
    """Lower case, without accents, punctuation or repeated spaces."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(_NON_WORD.sub(" ", stripped.casefold()).split())


def name_key(
    name: str,
    *,
    abbreviations: Mapping[str, str] = ABBREVIATIONS,
    drop: Iterable[str] = LEGAL_SUFFIXES,
) -> str:
    """A comparison key: folded, abbreviations expanded, legal suffixes removed."""
    dropped = set(drop)
    words = [abbreviations.get(word, word) for word in fold(name).split()]
    kept = [word for word in words if word not in dropped]
    return " ".join(kept or words)


def address_key(address: Address | None) -> str:
    if address is None:
        return ""
    if address.street:
        parts = [address.house_number or "", address.street, address.postcode or ""]
        return name_key(" ".join(p for p in parts if p), drop=())
    return name_key(address.full or "", drop=())


def similarity(a: str, b: str) -> float:
    """Similarity of two comparison keys, 0-1. Word order does not matter.

    The mean of token-set and token-sort similarity: token-set alone scores 1.0 whenever
    one name contains the other ("Central Park" and "Central Park Cafe"), so extra words
    must lower the score.
    """
    if not a or not b:
        return 0.0
    return (fuzz.token_set_ratio(a, b) + fuzz.token_sort_ratio(a, b)) / 200.0


def best_similarity(names_a: Iterable[str], names_b: Iterable[str]) -> float:
    """Highest similarity between any name (or alias) of one entity and any of another."""
    keys_b = [name_key(n) for n in names_b if n]
    best = 0.0
    for name in names_a:
        if not name:
            continue
        key = name_key(name)
        for other in keys_b:
            best = max(best, similarity(key, other))
    return best
