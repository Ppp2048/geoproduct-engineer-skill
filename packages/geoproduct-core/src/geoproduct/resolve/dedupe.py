"""Finding and merging duplicate records, with the reasons for every merge.

Every identifier value has a level, decided value by value for the dataset:

- **site**: it names one physical establishment. The values of ``site_keys`` (register or
  facility codes the user names; none by default), and the Wikidata items listed in
  ``wikidata_sites``. A matching street address with a house number is site-level evidence
  too.
- **organisation**: the sites of one organisation can share it. The values of
  ``organisation_keys`` (default website, phone, email, ``brand:wikidata`` and
  ``operator:wikidata``), the Wikidata items listed in ``wikidata_organisations``, and a
  ``wikidata`` item that any record gives as its brand or operator.
- **unknown**: any other ``wikidata`` item. The field does not say whether the item is the
  establishment itself or a company, brand or chain, so it is never taken for a site.

A site-level or unknown value that records at **distinct sites** carry names more than one
site, so it is organisation-level for the dataset. Records are at distinct sites when they
are farther apart than ``max_distance_m``, when their street addresses differ, or when they
have different values of another site-level identifier. Only the Wikidata items listed in
``wikidata_sites`` keep their level whatever the data shows.

Two records are duplicates, and merge, only when all of these hold:

- **site evidence:** they share a site-level value, or their names are similar and their
  street addresses, house number included, agree;
- **compatible names:** the names are similar, or one record has none and a site-level
  value links them;
- **placed:** they are within ``max_distance_m`` of each other, or one has no location;
- **not contradicted by site-level evidence:** their street addresses do not differ, and no
  key has a different site-level or unknown value on each (two different Wikidata items of
  unknown meaning may name two establishments).

Differing organisation-level values (another phone number, website, email, brand or
operator) do not block a merge: one site can have a direct line and a central one. They are
listed in the reasons, and a differing attribute value becomes a conflict as usual.

Organisation-level and unknown values never merge records on their own, however close and
alike the records are: a chain's branches can share a website, a booking line or the
brand's Wikidata item and stand a street apart. Records with similar names that share such
a value are linked by ``organisation_id`` instead, at any distance.

A field can hold several values: ``;`` separates them (OpenStreetMap's separator), and phone
numbers can also be separated by ``,``, ``/``, "or" or "and" (``phone_keys``). Two fields
agree when they share a value. Phone numbers match only when every digit does
(``phones_match``). A comparison the digits cannot settle, such as an international against
a national form, is unclear: it neither confirms nor contradicts.

Look-alikes that do not merge (similar names nearby, or site evidence held back by a
conflict or a name) stay separate, each marked as a possible duplicate of the other in its
``limitations``, with the reason, for review.

Records merge in groups in which **every pair** is a duplicate. Links are taken strongest
first, and a record joins a group only if it is a duplicate of every member, so A and C are
never merged just because each matches B. A link that cannot join its group is reported
as a possible duplicate too.

Merging never picks a winner silently. Differing attribute values become a ``Conflict``
and the attribute's value is cleared, so nobody reads one source's value as settled.
"""

import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import JsonValue
from shapely import STRtree
from shapely.geometry import Point

from geoproduct.engine.fingerprint import short_id
from geoproduct.geo.metric import distance_m, metres_to_degrees
from geoproduct.geo.shapes import anchor, record_shape
from geoproduct.resolve.text import address_key, best_similarity, fold, name_key
from geoproduct.schemas import (
    Address,
    AttributeValue,
    Conflict,
    ConflictingValue,
    LocationKind,
    Merge,
    Record,
    RecordStatus,
)

MAX_WORD_BLOCK = 200
MAX_FLAGS_LISTED = 5
"""Possible duplicates listed in one record's limitations; the rest are counted."""
MIN_PHONE_DIGITS = 7
"""Phone numbers with fewer digits (short codes, fragments) are not compared at all."""
TRUNK_DIGITS = 2
"""At most this many leading digits of a national number can be a trunk prefix ("0", "8",
"06") that its international form leaves out."""
EXTENSION = re.compile(r"[,;]?\s*(?:ext(?:ension|n)?|x|#)\s*[.:=]?\s*\d{1,6}\s*$", re.I)
PHONE_SEPARATOR = re.compile(r"\s*(?:[,/\n]|\bor\b|\band\b)\s*", re.I)
"""Separators that may stand between two phone numbers in one field, besides ``;``."""
HELD_APART = "the records merged with one of them do not all match the other"

LOCATION_RANK = {
    LocationKind.building_footprint: 0,
    LocationKind.entrance: 1,
    LocationKind.address_point: 2,
    LocationKind.centroid: 3,
    LocationKind.approximate: 4,
    LocationKind.unknown: 5,
}


WIKIDATA = "wikidata"
"""The key whose values are classified item by item: a Wikidata item can be an
establishment or the company, brand or chain behind it."""
BRAND_KEYS = ("brand:wikidata", "operator:wikidata")
"""Keys whose values are the Wikidata items of a brand or an operator: organisations."""
CONTACT_KEYS = ("website", "phone", "email")
SITE_KEYS: tuple[str, ...] = ()
"""Identifiers that name one physical establishment, by default: none. Register and
facility codes are named per dataset."""
ORGANISATION_KEYS = (*CONTACT_KEYS, *BRAND_KEYS)
"""Identifiers an organisation shares across its sites, by default."""


class Level(StrEnum):
    site = "site"
    organisation = "organisation"
    unknown = "unknown"


def qid(value: str) -> str:
    """A Wikidata item id as compared: trimmed and upper case (``Q42``)."""
    return value.strip().upper()


@dataclass(frozen=True)
class DedupeSettings:
    name_threshold: float = 0.88
    max_distance_m: float = 150.0
    site_keys: tuple[str, ...] = SITE_KEYS
    organisation_keys: tuple[str, ...] = ORGANISATION_KEYS
    wikidata_sites: frozenset[str] = frozenset()
    """Wikidata items known to name one establishment, for example from the item's own
    statements (an instance of a hospital, with coordinates at the site)."""
    wikidata_organisations: frozenset[str] = frozenset()
    """Wikidata items known to name a company, brand, chain or operator."""
    same_category: bool = True

    def __post_init__(self) -> None:
        both = set(self.site_keys) & set(self.organisation_keys)
        if both:
            raise ValueError(f"{', '.join(sorted(both))}: a key is site- or organisation-level")
        classified = set(self.site_keys) & {WIKIDATA, *BRAND_KEYS}
        if classified:
            raise ValueError(
                f"{', '.join(sorted(classified))} cannot be a site key: whether a Wikidata "
                "item names an establishment is decided item by item (wikidata_sites)"
            )
        sites = frozenset(qid(q) for q in self.wikidata_sites)
        organisations = frozenset(qid(q) for q in self.wikidata_organisations)
        twice = sites & organisations
        if twice:
            raise ValueError(
                f"{', '.join(sorted(twice))}: an item is a site or an organisation, not both"
            )
        object.__setattr__(self, "wikidata_sites", sites)
        object.__setattr__(self, "wikidata_organisations", organisations)

    @property
    def keys(self) -> tuple[str, ...]:
        """Every identifier key compared."""
        return tuple(dict.fromkeys((*self.site_keys, *self.organisation_keys, WIKIDATA)))


@dataclass
class Link:
    a: int
    b: int
    score: float
    reasons: list[str]


@dataclass
class Possible:
    """Two records that may describe one entity but are kept apart, and why."""

    a: int
    b: int
    score: float
    reasons: list[str]
    why: str


@dataclass
class Resolution:
    records: list[Record]
    merges: int = 0
    organisations: int = 0
    possible: int = 0
    """Pairs kept apart and marked as possible duplicates."""
    spread: int = 0
    """Site-level or unknown identifier values compared as organisation-level, because
    records at distinct sites carry them."""
    unclassified: int = 0
    """Wikidata items of unknown meaning that two or more records share."""
    notes: list[str] = field(default_factory=list)


Values = dict[str, tuple[str, ...]]
"""Each identifier key's values on one record, reduced for comparison."""


def identifiers(record: Record, keys: Sequence[str]) -> Values:
    """A record's values for each key, from ``external_ids`` or else its attributes."""
    found: Values = {}
    for key in keys:
        value: Any = record.external_ids.get(key)
        if value is None and key in record.attributes:
            value = record.attributes[key].value
        if isinstance(value, str) and value.strip():
            values = id_values(key, value)
            if values:
                found[key] = values
    return found


def id_values(key: str, value: str) -> tuple[str, ...]:
    """Every value in one identifier field, reduced for comparison, without repeats."""
    if key == "phone":
        return tuple(phone_keys(value))
    return tuple(dict.fromkeys(k for part in value.split(";") if (k := _id_key(key, part))))


def _id_key(key: str, value: str) -> str:
    if key == "phone":
        return phone_key(value)
    if key in (WIKIDATA, *BRAND_KEYS):
        return qid(value)
    text = value.strip().casefold()
    if key == "website":
        text = re.sub(r"^[a-z]+://", "", text).removeprefix("www.").rstrip("/")
    return text


def phone_keys(value: str) -> list[str]:
    """Every phone number in one field, each reduced by ``phone_key``.

    Numbers are separated by ``;``. A ``,``, ``/``, "or" or "and" separates numbers only
    when every part has at least ``MIN_PHONE_DIGITS`` digits; otherwise it belongs to the
    number, as in ``030/1234567`` or ``0958, ext. 12``.
    """
    found: list[str] = []
    for chunk in value.split(";"):
        parts = PHONE_SEPARATOR.split(chunk)
        if len(parts) > 1 and not all(
            _digits(EXTENSION.sub("", p)) >= MIN_PHONE_DIGITS for p in parts
        ):
            parts = [chunk]
        for part in parts:
            key = phone_key(part)
            if key and key not in found:
                found.append(key)
    return found


def _digits(text: str) -> int:
    return sum(c.isdigit() for c in text)


def phone_key(value: str) -> str:
    """A phone number reduced for comparison: its digits, with ``+`` in front when it is
    written in international form (``+44 …`` or ``0044 …``). An extension is dropped, and so
    is a ``(0)`` inside an international number. Numbers shorter than
    ``MIN_PHONE_DIGITS`` give ``""`` and are never compared."""
    text = re.sub(r"^tel:", "", value.strip(), flags=re.I)
    text = EXTENSION.sub("", text)
    international = text.startswith("+")
    if international:
        text = text.replace("(0)", "")
    digits = re.sub(r"\D", "", text)
    if not international and digits.startswith("00"):
        international, digits = True, digits[2:]
    if len(digits) < MIN_PHONE_DIGITS:
        return ""
    return f"+{digits}" if international else digits


def phones_match(a: str, b: str) -> bool | None:
    """Whether two phone keys are one number.

    ``True`` only when every digit matches and both are in the same form. ``False`` when
    they cannot be one number. ``None`` (unclear) when they might be but the digits alone
    cannot settle it: an international and a national form, a national number with and
    without its area code or trunk prefix, or a trunk ``0`` kept after a country code.
    Settling these needs the country's numbering plan, which this comparison does not have.
    """
    if a == b:
        return True
    international = (a.startswith("+"), b.startswith("+"))
    x, y = a.lstrip("+"), b.lstrip("+")
    if all(international):
        return None if _one_zero_apart(x, y) else False
    if not any(international):
        short, long = sorted((x.lstrip("0"), y.lstrip("0")), key=len)
        return None if long.endswith(short) else False
    national, full = (x, y) if international[1] else (y, x)
    common = _common_suffix(national, full)
    maybe = common >= MIN_PHONE_DIGITS and len(national) - common <= TRUNK_DIGITS
    return None if maybe else False


def _one_zero_apart(x: str, y: str) -> bool:
    """Whether two international numbers differ only by a ``0`` just after the country code
    (one to three digits), as in ``+44 020 …`` against ``+44 20 …``."""
    short, long = sorted((x, y), key=len)
    if len(long) != len(short) + 1:
        return False
    return any(long[k] == "0" and long[:k] + long[k + 1 :] == short for k in range(1, 4))


def _common_suffix(x: str, y: str) -> int:
    n = 0
    while n < min(len(x), len(y)) and x[-1 - n] == y[-1 - n]:
        n += 1
    return n


def same_identifier(key: str, a: Iterable[str], b: Iterable[str]) -> bool | None:
    """Whether two records' values for one key agree: ``True`` when they share a value,
    ``None`` when no value is shared but a phone comparison is unclear, ``False``
    otherwise."""
    xs, ys = tuple(a), tuple(b)
    results = {(phones_match(x, y) if key == "phone" else x == y) for x in xs for y in ys}
    if True in results:
        return True
    return None if None in results else False


def _block_key(key: str, value: str) -> str:
    """The part of an identifier that blocking groups on. A block only proposes a pair for
    comparison; the comparison itself uses every digit. Phone numbers block on their last
    ``MIN_PHONE_DIGITS`` digits, so that the forms of one number meet."""
    return value.lstrip("+")[-MIN_PHONE_DIGITS:] if key == "phone" else value


def _street_key(address: Address | None) -> str:
    """An address without its postcode, for comparing two addresses."""
    if address is None:
        return ""
    if address.street:
        return name_key(f"{address.house_number or ''} {address.street}", drop=())
    return name_key(address.full or "", drop=())


def addresses_agree(a: Address | None, b: Address | None) -> bool | None:
    """``True`` if two addresses name the same place, ``False`` if they name different ones,
    and ``None`` if either is missing or one only says less than the other (no house
    number, or a full address against a street)."""
    key_a, key_b = _street_key(a), _street_key(b)
    if not key_a or not key_b:
        return None
    if key_a == key_b:
        postcodes = {fold(x.postcode) for x in (a, b) if x is not None and x.postcode}
        return len(postcodes) < 2
    words_a, words_b = set(key_a.split()), set(key_b.split())
    if words_a <= words_b or words_b <= words_a:
        return None
    return False


def classify(
    records: list[Record],
    values: list[Values],
    positions: list[Point | None],
    settings: DedupeSettings,
) -> tuple[dict[tuple[str, str], Level], set[tuple[str, str]]]:
    """The level of every identifier value in the dataset, and the site-level or unknown
    values moved to organisation-level because records at distinct sites carry them."""
    brands = {v for r in records for vs in identifiers(r, BRAND_KEYS).values() for v in vs}
    carriers: dict[tuple[str, str], list[int]] = defaultdict(list)
    for i, ids in enumerate(values):
        for key, vs in ids.items():
            for v in vs:
                carriers[(key, v)].append(i)
    level = {item: _base_level(*item, settings, brands) for item in carriers}
    sites = [
        {
            k: vs
            for k, vs in (
                (k, tuple(v for v in vs if level[(k, v)] == Level.site)) for k, vs in ids.items()
            )
            if vs
        }
        for ids in values
    ]

    def distinct(i: int, j: int, key: str) -> bool:
        p, q = positions[i], positions[j]
        if p is not None and q is not None and distance_m(p, q) > settings.max_distance_m:
            return True
        if addresses_agree(records[i].address, records[j].address) is False:
            return True
        return any(
            same_identifier(k, sites[i][k], sites[j][k]) is False
            for k in sites[i].keys() & sites[j].keys()
            if k != key
        )

    spread: set[tuple[str, str]] = set()
    for item, members in carriers.items():
        kept = item[0] == WIKIDATA and item[1] in settings.wikidata_sites
        if level[item] == Level.organisation or kept or len(members) < 2:
            continue
        if any(distinct(i, j, item[0]) for k, i in enumerate(members) for j in members[k + 1 :]):
            level[item] = Level.organisation
            spread.add(item)
    return level, spread


def _base_level(key: str, value: str, settings: DedupeSettings, brands: set[str]) -> Level:
    """A value's level before the data is looked at for distinct sites."""
    if key == WIKIDATA:
        if value in settings.wikidata_sites:
            return Level.site
        if value in settings.wikidata_organisations or value in brands:
            return Level.organisation
        return Level.organisation if WIKIDATA in settings.organisation_keys else Level.unknown
    return Level.site if key in settings.site_keys else Level.organisation


def _by_level(ids: Values, level: dict[tuple[str, str], Level]) -> dict[Level, Values]:
    split: dict[Level, dict[str, list[str]]] = {lv: defaultdict(list) for lv in Level}
    for key, vs in ids.items():
        for v in vs:
            split[level[(key, v)]][key].append(v)
    return {lv: {k: tuple(vs) for k, vs in by_key.items()} for lv, by_key in split.items()}


def resolve(records: list[Record], settings: DedupeSettings) -> Resolution:
    positions = [_position(r) for r in records]
    names = [[n for n in (r.name, *r.aliases) if n] for r in records]
    values = [identifiers(r, settings.keys) for r in records]
    level, spread = classify(records, values, positions, settings)
    tiers = [_by_level(ids, level) for ids in values]
    shared_unknown: dict[str, int] = defaultdict(int)
    for tier in tiers:
        for v in tier[Level.unknown].get(WIKIDATA, ()):
            shared_unknown[v] += 1

    duplicates: list[Link] = []
    organisations: list[Link] = []
    possible: list[Possible] = []
    for i, j in _candidate_pairs(records, positions, values, settings):
        a, b = records[i], records[j]
        if settings.same_category and a.category and b.category and a.category != b.category:
            continue
        ti, tj = tiers[i], tiers[j]
        site_same, _, site_unclear = _compared(ti[Level.site], tj[Level.site])
        unknown_same, _, unknown_unclear = _compared(ti[Level.unknown], tj[Level.unknown])
        org_same, org_differing, org_unclear = _compared(
            ti[Level.organisation], tj[Level.organisation]
        )
        contradicting = _contradicting(ti, tj)
        shared = sorted((set(unknown_same) | set(org_same)) - set(site_same))
        unclear = sorted(
            (set(site_unclear) | set(unknown_unclear) | set(org_unclear))
            - set(site_same)
            - set(shared)
        )
        differing = sorted(set(org_differing) - set(site_same) - set(shared) - set(unclear))
        sim = best_similarity(names[i], names[j]) if names[i] and names[j] else 0.0
        similar = sim >= settings.name_threshold
        pos_i, pos_j = positions[i], positions[j]
        dist = distance_m(pos_i, pos_j) if pos_i is not None and pos_j is not None else None
        near = dist is not None and dist <= settings.max_distance_m
        address = addresses_agree(a.address, b.address)
        site_address = address is True and any(c.isdigit() for c in _street_key(a.address))

        reasons: list[str] = []
        if site_same:
            reasons.append(f"same {', '.join(site_same)}")
        if site_address:
            reasons.append("same address")
        elif address is True:
            reasons.append("same street, no house number")
        if shared:
            reasons.append(f"same {', '.join(shared)}")
        if unclear:
            reasons.append(f"{_listed(unclear)} may match")
        if differing:
            reasons.append(f"different {_listed(differing)}")
        if sim:
            reasons.append(f"name similarity {sim:.2f}")
        if dist is not None:
            reasons.append(f"{dist:.0f} m apart")

        site_evidence = bool(site_same) or (similar and site_address)
        names_fit = similar or (bool(site_same) and not (names[i] and names[j]))
        placed = dist is None or near
        blocking = contradicting + (["address"] if address is False else [])
        closeness = 0.0 if dist is None else max(0.0, 1.0 - dist / settings.max_distance_m)
        evidence = 0.2 if site_evidence else 0.1 if shared else 0.0
        score = round(min(1.0, 0.5 * sim + 0.3 * closeness + evidence), 3)

        if site_evidence and names_fit and placed and not blocking:
            duplicates.append(Link(i, j, score, reasons))
            continue
        if shared and similar:
            organisations.append(Link(i, j, score, reasons))
        if (site_evidence and placed) or (similar and (near or address is True)):
            if blocking:
                why = f"they differ in {_listed(blocking)}"
            elif site_evidence and not names_fit:
                why = "their names do not match"
            elif not site_evidence and shared:
                why = _only_shared(org_same, unknown_same)
            elif not site_evidence:
                why = "no site-level identifier or matching address confirms it"
            else:
                why = f"they are {dist:.0f} m apart"
            possible.append(Possible(i, j, score, reasons, why))

    groups, held = _groups(len(records), duplicates)
    possible += [Possible(link.a, link.b, link.score, link.reasons, HELD_APART) for link in held]
    group_of = {m: g for g, members in enumerate(groups) for m in members}
    links_by_group: dict[int, list[Link]] = defaultdict(list)
    for link in duplicates:
        if group_of[link.a] == group_of[link.b]:
            links_by_group[group_of[link.a]].append(link)

    resolved: list[Record] = []
    merges = 0
    index_of: dict[int, int] = {}
    for g, members in enumerate(groups):
        for m in members:
            index_of[m] = len(resolved)
        if len(members) == 1:
            resolved.append(records[members[0]])
            continue
        resolved.append(_merge([records[m] for m in members], members, links_by_group[g]))
        merges += len(members) - 1

    org_groups = _components(len(records), organisations)
    linked = 0
    for group in org_groups:
        targets = sorted({index_of[m] for m in group})
        if len(targets) < 2:
            continue
        group_values = sorted({v for m in group for vs in values[m].values() for v in vs})
        org_id = resolved[targets[0]].organisation_id or short_id("org", group_values)
        for t in targets:
            if resolved[t].organisation_id is None:
                resolved[t] = resolved[t].model_copy(update={"organisation_id": org_id})
        linked += 1

    flagged = _flag_possible(resolved, possible, index_of)
    return Resolution(
        records=resolved,
        merges=merges,
        organisations=linked,
        possible=flagged,
        spread=len(spread),
        unclassified=sum(1 for count in shared_unknown.values() if count > 1),
    )


def _compared(x: Values, y: Values) -> tuple[list[str], list[str], list[str]]:
    """The identifier keys two records share a value of, those whose values all differ, and
    those whose comparison is unclear."""
    same: list[str] = []
    differing: list[str] = []
    unclear: list[str] = []
    for key in sorted(x.keys() & y.keys()):
        match = same_identifier(key, x[key], y[key])
        (same if match else unclear if match is None else differing).append(key)
    return same, differing, unclear


def _contradicting(x: dict[Level, Values], y: dict[Level, Values]) -> list[str]:
    """Keys whose values, site-level or of unknown meaning on both records, all differ: the
    records may be two establishments."""

    def possibly_site(tier: dict[Level, Values]) -> Values:
        found: dict[str, list[str]] = defaultdict(list)
        for lv in (Level.site, Level.unknown):
            for key, vs in tier[lv].items():
                found[key].extend(vs)
        return {k: tuple(vs) for k, vs in found.items()}

    a, b = possibly_site(x), possibly_site(y)
    return [k for k in sorted(a.keys() & b.keys()) if same_identifier(k, a[k], b[k]) is False]


def _only_shared(org_same: list[str], unknown_same: list[str]) -> str:
    """Why records that share only organisation-level or unknown values stay apart."""
    item = "a Wikidata item that is not known to name a site"
    org = [k for k in org_same if k not in unknown_same]
    if org and unknown_same:
        return f"they share only organisation-level details ({_listed(org)}) and {item}"
    if unknown_same:
        return f"they share only {item}"
    return (
        f"they share only organisation-level details ({_listed(org)}), which do not identify a site"
    )


def _groups(size: int, links: list[Link]) -> tuple[list[list[int]], list[Link]]:
    """Groups in which every pair of members is linked, built strongest link first; and the
    links that could not join their two groups. Groups are in order of their first member."""
    linked = {(link.a, link.b) for link in links}
    group_of = list(range(size))
    members: dict[int, list[int]] = {i: [i] for i in range(size)}
    held: list[Link] = []
    for link in sorted(links, key=lambda k: (-k.score, k.a, k.b)):
        ga, gb = group_of[link.a], group_of[link.b]
        if ga == gb:
            continue
        if all((min(x, y), max(x, y)) in linked for x in members[ga] for y in members[gb]):
            keep, gone = min(ga, gb), max(ga, gb)
            for m in members.pop(gone):
                group_of[m] = keep
                members[keep].append(m)
        else:
            held.append(link)
    return [sorted(m) for _, m in sorted(members.items())], held


def _listed(words: list[str]) -> str:
    """``["phone", "address"]`` -> ``"phone and address"``."""
    return words[0] if len(words) == 1 else f"{', '.join(words[:-1])} and {words[-1]}"


def _flag_possible(
    resolved: list[Record], possible: list[Possible], index_of: dict[int, int]
) -> int:
    """Mark records kept apart as possible duplicates of each other; the pairs marked."""
    notes: dict[int, dict[str, str]] = defaultdict(dict)
    pairs: set[tuple[int, int]] = set()
    for pair in sorted(possible, key=lambda p: (-p.score, p.a, p.b)):
        x, y = index_of[pair.a], index_of[pair.b]
        if x == y or (min(x, y), max(x, y)) in pairs:
            continue
        pairs.add((min(x, y), max(x, y)))
        detail = f"{'; '.join(pair.reasons) or 'similar'}; not merged because {pair.why}"
        notes[x].setdefault(resolved[y].id, detail)
        notes[y].setdefault(resolved[x].id, detail)
    for index, others in notes.items():
        record = resolved[index]
        lines = [f"Possible duplicate of {rid} ({detail})." for rid, detail in others.items()]
        if len(lines) > MAX_FLAGS_LISTED:
            more = len(lines) - MAX_FLAGS_LISTED
            lines = [*lines[:MAX_FLAGS_LISTED], f"Possible duplicate of {more} more record(s)."]
        added = [line for line in lines if line not in record.limitations]
        resolved[index] = record.model_copy(update={"limitations": [*record.limitations, *added]})
    return len(pairs)


def _position(record: Record) -> Point | None:
    geom = record_shape(record)
    return None if geom is None else Point(anchor(geom))


def _candidate_pairs(
    records: list[Record],
    positions: list[Point | None],
    values: list[Values],
    settings: DedupeSettings,
) -> list[tuple[int, int]]:
    pairs: set[tuple[int, int]] = set()
    located = [i for i, p in enumerate(positions) if p is not None]
    if located:
        points = [p for p in positions if p is not None]
        tree = STRtree(points)
        for k, i in enumerate(located):
            point = points[k]
            assert point is not None
            reach = metres_to_degrees(settings.max_distance_m, float(point.y))
            for hit in tree.query(point.buffer(reach)):
                j = located[int(hit)]
                if i < j:
                    pairs.add((i, j))
    # Pairs that are far apart, or lack a location, can only be found by name or identifier:
    # block them by each significant word of the name and by each identifier value. Very
    # common words carry little signal and would compare everything with everything, so
    # large word blocks are skipped; identifier blocks are always kept.
    blocks: dict[str, list[int]] = defaultdict(list)
    for i, record in enumerate(records):
        for word in set(name_key(record.name or "").split()):
            if len(word) > 2:
                blocks[f"w:{word}"].append(i)
        for key, vs in values[i].items():
            for block in dict.fromkeys(_block_key(key, v) for v in vs):
                blocks[f"id:{key}:{block}"].append(i)
    for block, members in blocks.items():
        if block.startswith("w:") and len(members) > MAX_WORD_BLOCK:
            continue
        for x, i in enumerate(members):
            for j in members[x + 1 :]:
                pairs.add((min(i, j), max(i, j)))
    return sorted(pairs)


def _components(size: int, links: list[Link]) -> list[set[int]]:
    parent = list(range(size))

    def root(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for link in links:
        ra, rb = root(link.a), root(link.b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)
    groups: dict[int, set[int]] = defaultdict(set)
    for i in range(size):
        groups[root(i)].add(i)
    return list(groups.values())


def _rank(record: Record) -> tuple[int, int, float, int, int, str]:
    return (
        0 if record.geometry is not None else 1,
        LOCATION_RANK.get(record.location.kind, 9),
        record.location.precision_m if record.location.precision_m is not None else 1e9,
        -len(record.evidence_ids),
        -len(record.attributes),
        record.id,
    )


IDENTIFIER_KEYS = frozenset({*CONTACT_KEYS, WIKIDATA, *BRAND_KEYS})
"""Keys whose values are compared as identifiers when records merge."""


def _same(key: str, a: JsonValue, b: JsonValue) -> bool:
    """Whether two stated values say the same. For identifier fields, one value says the
    same as another when every value in it is in the other, or the other way round, so a
    field listing two phone numbers agrees with one listing either of them."""
    if isinstance(a, str) and isinstance(b, str):
        if key in IDENTIFIER_KEYS:
            x, y = id_values(key, a), id_values(key, b)
            if x and y:
                return _covers(key, x, y) or _covers(key, y, x)
        return fold(a) == fold(b)
    return a == b


def _covers(key: str, x: tuple[str, ...], y: tuple[str, ...]) -> bool:
    """Whether every value in ``y`` is one of ``x``."""
    return all(any(same_identifier(key, (u,), (v,)) is True for u in x) for v in y)


def _fuller(key: str, kept: str, other: str) -> str:
    """Of two spellings of the same identifier, the one that says more: a website with its
    scheme (``https://…``) rather than without, or a field with more values."""
    if key == "website" and "://" in other and "://" not in kept:
        return other
    if key in IDENTIFIER_KEYS and len(id_values(key, other)) > len(id_values(key, kept)):
        return other
    return kept


def _display_name(primary: Record, others: list[Record]) -> str | None:
    """The fullest spelling of the primary record's name, e.g. "Hospital" over "Hosp."."""
    if not primary.name:
        return next((r.name for r in others if r.name), None)
    key = name_key(primary.name)
    spellings = [primary.name, *(r.name for r in others if r.name and name_key(r.name) == key)]
    return max(spellings, key=len)


def _merge(records: list[Record], indexes: list[int], links: list[Link]) -> Record:
    order = sorted(range(len(records)), key=lambda k: _rank(records[k]))
    primary = records[order[0]]
    others = [records[k] for k in order[1:]]
    position_of = {index: k for k, index in enumerate(indexes)}
    linked: dict[str, Link] = {}
    for link in links:
        for end in (link.a, link.b):
            rid = records[position_of[end]].id
            if rid != primary.id and rid not in linked:
                linked[rid] = link

    aliases = list(primary.aliases)
    seen_names = {name_key(n) for n in (primary.name, *primary.aliases) if n}
    external_ids = dict(primary.external_ids)
    attributes = {k: v.model_copy() for k, v in primary.attributes.items()}
    sources_of: dict[str, list[tuple[JsonValue, list[str]]]] = {
        k: [(v.value, list(v.evidence_ids))] for k, v in primary.attributes.items()
    }
    evidence_ids = list(primary.evidence_ids)
    limitations = list(primary.limitations)
    conflicts = list(primary.conflicts)
    merged_notes = list(primary.merged)

    for other in others:
        for name in (other.name, *other.aliases):
            if name and name_key(name) not in seen_names:
                seen_names.add(name_key(name))
                aliases.append(name)
        for key, value in other.external_ids.items():
            if key not in external_ids:
                external_ids[key] = value
            elif _same(key, external_ids[key], value):
                external_ids[key] = _fuller(key, external_ids[key], value)
            else:
                n = 2
                while f"{key}~{n}" in external_ids:
                    n += 1
                external_ids[f"{key}~{n}"] = value
        for key, attr in other.attributes.items():
            sources_of.setdefault(key, []).append((attr.value, list(attr.evidence_ids)))
            if key not in attributes:
                attributes[key] = attr.model_copy()
        evidence_ids += [e for e in other.evidence_ids if e not in evidence_ids]
        limitations += [x for x in other.limitations if x not in limitations]
        conflicts += other.conflicts
        merged_notes += other.merged
        via = linked.get(other.id)
        merged_notes.append(
            Merge(
                record_id=other.id,
                score=via.score if via else 0.0,
                reasons=via.reasons if via and via.reasons else ["linked through the cluster"],
            )
        )

    for key, values in sources_of.items():
        present = [(v, ev) for v, ev in values if v is not None]
        distinct: list[tuple[JsonValue, list[str]]] = []
        for stated, ev in present:
            match = next((d for d in distinct if _same(key, d[0], stated)), None)
            if match is None:
                distinct.append((stated, list(ev)))
            else:
                match[1].extend(e for e in ev if e not in match[1])
        all_evidence = [e for _, ev in distinct for e in ev]
        if len(distinct) > 1:
            conflicts.append(
                Conflict(
                    field=f"attributes.{key}",
                    values=[ConflictingValue(value=v, evidence_ids=ev) for v, ev in distinct],
                    note="sources disagree; the value is cleared until resolved",
                )
            )
            attributes[key] = AttributeValue(
                value=None,
                basis=attributes[key].basis,
                evidence_ids=all_evidence,
                note="sources disagree; see conflicts",
            )
        elif distinct:
            attributes[key] = attributes[key].model_copy(update={"evidence_ids": all_evidence})

    addresses = {address_key(r.address): r.address for r in records if address_key(r.address)}
    if len(addresses) > 1:
        conflicts.append(
            Conflict(
                field="address",
                values=[
                    ConflictingValue(value=a.model_dump(mode="json", exclude_none=True))
                    for a in addresses.values()
                    if a is not None
                ],
                note="sources give different addresses",
            )
        )

    name = _display_name(primary, others)
    if name != primary.name:
        if primary.name and primary.name not in aliases:
            aliases.insert(0, primary.name)
        aliases = [a for a in aliases if a != name]

    status = RecordStatus.conflicting if conflicts else primary.status
    return primary.model_copy(
        update={
            "name": name,
            "aliases": aliases,
            "external_ids": external_ids,
            "attributes": attributes,
            "evidence_ids": evidence_ids,
            "limitations": limitations,
            "conflicts": conflicts,
            "merged": merged_notes,
            "status": status,
            "confidence": None,
        }
    )
