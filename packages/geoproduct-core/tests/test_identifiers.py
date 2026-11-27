"""How deduplication compares identifiers.

- Phone numbers (audit GP-17): every digit must match, extensions are dropped, short numbers
  are ignored, and a comparison that cannot be settled without a numbering plan is unclear.
  A field can hold several numbers, compared one by one.
- Site against organisation identity (audit GP-16 and its follow-up): only site-level
  evidence merges; organisation-level details and Wikidata items of unknown meaning link;
  an identifier that records at distinct sites carry is organisation-level; only site-level
  differences keep records apart.

Every record is invented and sits near 0°, 0°. The numbers and Wikidata item ids are
invented too.
"""

from typing import Any

import pytest

from geoproduct.resolve.dedupe import (
    DedupeSettings,
    phone_key,
    phone_keys,
    phones_match,
    resolve,
)
from geoproduct.schemas import Record

STEP = 0.0009
"""About 100 m east-west near 0°, 0°."""

SITE_ITEM = "Q4115189"
AS_SITE = DedupeSettings(wikidata_sites=frozenset({SITE_ITEM}))
"""Settings in which the Wikidata item ``SITE_ITEM`` is known to name one establishment."""
REGISTER = DedupeSettings(site_keys=("register",))
"""Settings in which a health register code names one site."""
ADDRESS = {"address": {"house_number": "1", "street": "Hospital Road"}}


def place(rid: str, name: str | None, east: float, **fields: Any) -> Record:
    """A record ``east`` steps of about 100 m east of a fixed point."""
    where = {"type": "Point", "coordinates": [0.003 + east * STEP, 0.003]}
    return Record.model_validate({"id": rid, "name": name, "geometry": where, **fields})


def possible_of(record: Record) -> list[str]:
    return [x for x in record.limitations if x.startswith("Possible duplicate of ")]


def linked(records: list[Record]) -> set[str | None]:
    return {r.organisation_id for r in records}


# Phone numbers (GP-17) ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "key"),
    [
        ("+44 20 7946 0958", "+442079460958"),
        ("+44 (0)20 7946 0958", "+442079460958"),
        ("0044 20 7946 0958", "+442079460958"),
        ("tel:+44-20-7946-0958", "+442079460958"),
        ("020 7946 0958", "02079460958"),
        ("+44 20 7946 0958 ext. 12", "+442079460958"),
        ("+44 20 7946 0958 x12", "+442079460958"),
        ("+44 20 7946 0958;ext=12", "+442079460958"),
        ("020 7946 0958 #12", "02079460958"),
        ("+44 20 7946 0958 Extension: 4", "+442079460958"),
        ("946 0958", "9460958"),
        ("112", ""),
        ("999 999", ""),
        ("+44 999", ""),
    ],
)
def test_phone_keys_drop_extensions_and_ignore_short_numbers(raw: str, key: str) -> None:
    assert phone_key(raw) == key


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        # One number written in different ways: every digit matches.
        ("+44 20 7946 0958", "+44 (0)20 7946 0958", True),
        ("+44 20 7946 0958", "0044 20 7946 0958", True),
        ("020 7946 0958", "020-7946-0958", True),
        ("+44 20 7946 0958", "+44 20 7946 0958 ext. 12", True),
        # Different country codes, the same last eight digits.
        ("+44 20 7946 0958", "+61 2 7946 0958", False),
        ("+44 20 7946 0958", "+353 20 7946 0958", False),
        # Different area codes, the same last eight digits.
        ("020 7946 0958", "0117 946 0958", False),
        # Different numbers.
        ("020 7946 0958", "020 7946 0999", False),
        ("+44 20 7946 0958", "+44 20 7946 0999", False),
        ("+44 20 7946 0958", "0161 496 0000", False),
        # Maybe one number: settling these needs the numbering plan.
        ("+44 20 7946 0958", "020 7946 0958", None),
        ("+7 495 123 4567", "8 495 123 4567", None),
        ("+44 20 7946 0958", "7946 0958", None),
        ("020 7946 0958", "7946 0958", None),
        ("+44 020 7946 0958", "+44 20 7946 0958", None),
    ],
)
def test_phone_numbers_match_only_when_every_digit_does(
    a: str, b: str, expected: bool | None
) -> None:
    key_a, key_b = phone_key(a), phone_key(b)
    assert phones_match(key_a, key_b) is expected
    assert phones_match(key_b, key_a) is expected


def test_numbers_that_share_only_their_last_digits_are_not_one_phone() -> None:
    result = resolve(
        [
            place("p", "Hill Pharmacy", 0, external_ids={"phone": "+44 20 7946 0958"}),
            place("q", "Town Bakery", 0.6, external_ids={"phone": "+61 2 7946 0958"}),
        ],
        DedupeSettings(),
    )
    assert [r.id for r in result.records] == ["p", "q"]
    assert not any(r.merged or r.organisation_id for r in result.records)
    assert result.merges == 0


def test_an_unclear_phone_match_confirms_nothing() -> None:
    result = resolve(
        [
            place("a", "Example Clinic", 0, external_ids={"phone": "+44 20 7946 0958"}),
            place("b", "Example Clinic", 0.2, external_ids={"phone": "020 7946 0958"}),
        ],
        DedupeSettings(),
    )
    a, b = result.records
    assert not a.merged
    assert not b.merged
    [line] = possible_of(a)
    assert "phone may match" in line
    assert "same phone" not in line


def test_an_unclear_phone_match_blocks_nothing() -> None:
    item = {"wikidata": SITE_ITEM}
    result = resolve(
        [
            place("a", "Example Clinic", 0, external_ids={**item, "phone": "+44 20 7946 0958"}),
            place("b", "Example Clinic", 0.2, external_ids={**item, "phone": "020 7946 0958"}),
        ],
        AS_SITE,
    )
    [merged] = result.records
    [merge] = merged.merged
    assert "same wikidata" in merge.reasons
    assert "phone may match" in merge.reasons


def test_an_extension_does_not_make_a_different_number() -> None:
    item = {"wikidata": SITE_ITEM}
    result = resolve(
        [
            place("a", "Example Clinic", 0, external_ids={**item, "phone": "+44 20 7946 0958"}),
            place(
                "b",
                "Example Clinic",
                0.1,
                external_ids={**item, "phone": "+44 20 7946 0958 ext. 12"},
            ),
        ],
        AS_SITE,
    )
    [merged] = result.records
    assert [m.record_id for m in merged.merged] == ["b"]
    assert "same phone" in merged.merged[0].reasons
    assert "phone~2" not in merged.external_ids


def test_short_numbers_neither_confirm_nor_block() -> None:
    same_short = resolve(
        [
            place("a", "Example Clinic", 0, external_ids={"phone": "112"}),
            place("b", "Example Clinic", 0.2, external_ids={"phone": "112"}),
        ],
        DedupeSettings(),
    )
    assert len(same_short.records) == 2
    assert "phone" not in possible_of(same_short.records[0])[0]

    item = {"wikidata": SITE_ITEM}
    different_short = resolve(
        [
            place("a", "Example Clinic", 0, external_ids={**item, "phone": "112"}),
            place("b", "Example Clinic", 0.2, external_ids={**item, "phone": "999"}),
        ],
        AS_SITE,
    )
    [merged] = different_short.records
    assert [m.record_id for m in merged.merged] == ["b"]


# Several phone numbers in one field (GP-16 follow-up) -----------------------------------------


@pytest.mark.parametrize(
    ("raw", "keys"),
    [
        ("+44 20 7946 0958; +44 20 7946 0959", ["+442079460958", "+442079460959"]),
        ("+44 20 7946 0958;+44 20 7946 0958", ["+442079460958"]),
        ("+44 20 7946 0958, +44 20 7946 0959", ["+442079460958", "+442079460959"]),
        ("0300 123 4567 / 0300 123 4568", ["03001234567", "03001234568"]),
        ("0300 123 4567 or 0161 496 0000", ["03001234567", "01614960000"]),
        # A separator belongs to the number when a part is too short to be one.
        ("030/1234567", ["0301234567"]),
        ("+44 20 7946 0958, ext. 12", ["+442079460958"]),
        ("+44 20 7946 0958;ext=12", ["+442079460958"]),
        ("+44 20 7946 0958; 112", ["+442079460958"]),
        ("112; 999", []),
    ],
)
def test_a_phone_field_can_hold_several_numbers(raw: str, keys: list[str]) -> None:
    assert phone_keys(raw) == keys


def test_numbers_in_one_phone_field_are_compared_one_by_one() -> None:
    """One record lists two numbers, the other one of them. Before, the two numbers were
    read as one long number, which matched nothing."""
    both = {"phone": "+44 20 7946 0958; +44 20 7946 0959"}
    one = {"phone": "+44 20 7946 0959"}
    same_site = resolve(
        [
            place("a", "Example Clinic", 0, external_ids=both, **ADDRESS),
            place("b", "Example Clinic", 0.2, external_ids=one, **ADDRESS),
        ],
        DedupeSettings(),
    )
    [merged] = same_site.records
    assert merged.merged[0].reasons[:2] == ["same address", "same phone"]
    assert merged.external_ids["phone"] == both["phone"]
    assert "phone~2" not in merged.external_ids
    assert not merged.conflicts

    no_site = resolve(
        [
            place("a", "Example Clinic", 0, external_ids={"phone": "0300 123 4567, 0161 496 0000"}),
            place("b", "Example Clinic", 0.2, external_ids={"phone": "0161 496 0000"}),
        ],
        DedupeSettings(),
    )
    a, b = no_site.records
    assert not a.merged
    assert a.organisation_id is not None
    assert a.organisation_id == b.organisation_id
    assert possible_of(a)[0].startswith("Possible duplicate of b (same phone; ")


def test_fields_with_no_number_in_common_differ() -> None:
    result = resolve(
        [
            place("a", "Example Clinic", 0, external_ids={"phone": "0300 123 4567; 0300 123 4568"}),
            place("b", "Example Clinic", 0.2, external_ids={"phone": "0161 496 0000"}),
        ],
        REGISTER,
    )
    a, _ = result.records
    assert not a.organisation_id
    assert "different phone" in possible_of(a)[0]


# Site against organisation identity (GP-16) ---------------------------------------------------

BRAND = {"website": "https://corner-market.example.com"}


def test_branches_sharing_a_brand_website_are_linked_not_merged() -> None:
    """Two branches 80 m apart with the brand's website and nothing else: one organisation,
    two sites."""
    result = resolve(
        [
            place("a", "Corner Market", 0, external_ids=BRAND),
            place("b", "Corner Market", 0.8, external_ids=BRAND),
        ],
        DedupeSettings(),
    )
    a, b = result.records
    assert not a.merged
    assert not b.merged
    assert result.organisations == 1
    assert len(linked(result.records)) == 1
    assert None not in linked(result.records)
    [line] = possible_of(a)
    assert line == (
        "Possible duplicate of b (same website; name similarity 1.00; 80 m apart; not merged "
        "because they share only organisation-level details (website), which do not identify "
        "a site)."
    )


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("website", "https://corner-market.example.com"),
        ("phone", "0300 123 4567"),
        ("email", "hello@corner-market.example.com"),
        ("brand:wikidata", "Q1000001"),
    ],
    ids=["brand-website", "booking-line", "email", "brand-wikidata"],
)
def test_a_row_of_branches_sharing_a_chain_identifier_never_merges(key: str, value: str) -> None:
    """Three branches 60 m apart, each pair close and alike, sharing the chain's identifier."""
    result = resolve(
        [
            place(rid, "Corner Market", k * 0.6, external_ids={key: value})
            for k, rid in enumerate("abc")
        ],
        DedupeSettings(),
    )
    assert [r.id for r in result.records] == ["a", "b", "c"]
    assert result.merges == 0
    assert len(linked(result.records)) == 1
    assert None not in linked(result.records)
    assert [len(possible_of(r)) for r in result.records] == [2, 2, 2]
    assert possible_of(result.records[0])[0].endswith(
        f"not merged because they share only organisation-level details ({key}), which do not "
        "identify a site)."
    )


def test_website_phone_and_name_within_25_m_are_not_enough() -> None:
    """The organisation-level evidence all agrees, the names are the same and the points are
    20 m apart, but nothing identifies the site."""
    shared = {"website": "example.org", "phone": "+44 20 7946 0958", "email": "info@example.org"}
    result = resolve(
        [
            place("a", "Example Clinic", 0, external_ids=shared),
            place("b", "Example Clinic", 0.2, external_ids=shared),
        ],
        DedupeSettings(),
    )
    a, b = result.records
    assert not a.merged
    assert not b.merged
    assert a.organisation_id is not None
    assert a.organisation_id == b.organisation_id
    assert possible_of(a)[0].endswith(
        "not merged because they share only organisation-level details (email, phone and "
        "website), which do not identify a site)."
    )


def test_a_shared_booking_line_merges_nothing() -> None:
    result = resolve(
        [
            place("m", "Example Clinic North", 0, external_ids={"phone": "0300 123 4567"}),
            place("n", "Example Clinic South", 1.0, external_ids={"phone": "0300 123 4567"}),
        ],
        DedupeSettings(),
    )
    assert [r.id for r in result.records] == ["m", "n"]
    assert result.merges == 0


@pytest.mark.parametrize(
    ("first", "second", "settings", "reason"),
    [
        (
            {"external_ids": {"wikidata": SITE_ITEM}},
            {"external_ids": {"wikidata": SITE_ITEM.lower()}},
            AS_SITE,
            "same wikidata",
        ),
        (
            {"external_ids": {"register": "HF-0042"}},
            {"external_ids": {"register": "hf-0042"}},
            REGISTER,
            "same register",
        ),
        (
            {"address": {"house_number": "1", "street": "Hospital Road"}},
            {"address": {"full": "1 Hospital Rd"}},
            DedupeSettings(),
            "same address",
        ),
    ],
    ids=["wikidata-site-item", "register-code", "address"],
)
def test_site_level_evidence_merges_and_keeps_disagreements(
    first: dict[str, Any], second: dict[str, Any], settings: DedupeSettings, reason: str
) -> None:
    result = resolve(
        [
            place("a", "Example Hospital", 0, attributes={"beds": {"value": 110}}, **first),
            place("b", "Example Hosp.", 0.2, attributes={"beds": {"value": 120}}, **second),
        ],
        settings,
    )
    [merged] = result.records
    [merge] = merged.merged
    assert reason in merge.reasons
    assert merged.name == "Example Hospital"
    assert [c.field for c in merged.conflicts] == ["attributes.beds"]
    assert merged.attributes["beds"].value is None
    assert not possible_of(merged)


@pytest.mark.parametrize(
    ("first", "second", "settings", "why"),
    [
        (
            {"external_ids": {"register": "HF-0001"}, **ADDRESS},
            {"external_ids": {"register": "HF-0002"}, **ADDRESS},
            REGISTER,
            "they differ in register",
        ),
        (
            {"external_ids": {"wikidata": "Q1000001"}, **ADDRESS},
            {"external_ids": {"wikidata": "Q1000002"}, **ADDRESS},
            DedupeSettings(),
            "they differ in wikidata",
        ),
        (
            {"external_ids": {"wikidata": "Q1000001"}, **ADDRESS},
            {"external_ids": {"wikidata": "Q1000002"}, **ADDRESS},
            DedupeSettings(wikidata_sites=frozenset({"Q1000001", "Q1000002"})),
            "they differ in wikidata",
        ),
        (
            {"external_ids": {"wikidata": SITE_ITEM}, **ADDRESS},
            {"external_ids": {"wikidata": "Q1000002"}, **ADDRESS},
            AS_SITE,
            "they differ in wikidata",
        ),
        (
            {"address": {"house_number": "1", "street": "Hospital Road"}},
            {"address": {"house_number": "9", "street": "Hospital Road"}},
            DedupeSettings(),
            "they differ in address",
        ),
    ],
    ids=[
        "register-codes",
        "wikidata-items-of-unknown-meaning",
        "wikidata-site-items",
        "site-item-against-unknown-item",
        "addresses",
    ],
)
def test_conflicting_site_level_evidence_keeps_records_apart(
    first: dict[str, Any], second: dict[str, Any], settings: DedupeSettings, why: str
) -> None:
    """Each pair has other site evidence (the same address, or similar names close by), but
    a site-level difference says they may be two establishments."""
    result = resolve(
        [place("a", "Example Clinic", 0, **first), place("b", "Example Clinic", 0.2, **second)],
        settings,
    )
    a, b = result.records
    assert not a.merged
    assert not b.merged
    [line] = possible_of(a)
    assert line.endswith(f"not merged because {why}).")


@pytest.mark.parametrize(
    ("first", "second", "settings", "difference"),
    [
        (
            {"external_ids": {"register": "HF-0042", "phone": "020 7946 0958"}},
            {"external_ids": {"register": "HF-0042", "phone": "020 7946 0999"}},
            REGISTER,
            "different phone",
        ),
        (
            {"external_ids": {"website": "north.example.org", "email": "a@example.org"}, **ADDRESS},
            {"external_ids": {"website": "south.example.org", "email": "b@example.org"}, **ADDRESS},
            DedupeSettings(),
            "different email and website",
        ),
        (
            {"external_ids": {"wikidata": SITE_ITEM, "brand:wikidata": "Q1000001"}},
            {"external_ids": {"wikidata": SITE_ITEM, "brand:wikidata": "Q1000002"}},
            AS_SITE,
            "different brand:wikidata",
        ),
        (
            {"external_ids": {"wikidata": "Q1000001"}, **ADDRESS},
            {"external_ids": {"wikidata": "Q1000002"}, **ADDRESS},
            DedupeSettings(wikidata_organisations=frozenset({"Q1000001", "Q1000002"})),
            "different wikidata",
        ),
    ],
    ids=["phones", "websites-and-emails", "brands", "organisation-wikidata-items"],
)
def test_different_organisation_details_do_not_keep_one_site_apart(
    first: dict[str, Any], second: dict[str, Any], settings: DedupeSettings, difference: str
) -> None:
    """Strong site evidence (a register code, a Wikidata item known to name the site, or the
    same street address) with differing organisation-level details: one site can have a
    direct line and a central one. The records merge, and the difference is in the reasons
    and both values are kept."""
    result = resolve(
        [place("a", "Example Clinic", 0, **first), place("b", "Example Clinic", 0.2, **second)],
        settings,
    )
    [merged] = result.records
    [merge] = merged.merged
    assert difference in merge.reasons
    key = difference.split()[-1]
    assert merged.external_ids[key] == first["external_ids"][key]
    assert merged.external_ids[f"{key}~2"] == second["external_ids"][key]


def test_a_differing_phone_attribute_in_a_merge_is_kept_as_a_conflict() -> None:
    phone = "phone"
    result = resolve(
        [
            place(
                "a", "Example Clinic", 0, attributes={phone: {"value": "020 7946 0958"}}, **ADDRESS
            ),
            place(
                "b",
                "Example Clinic",
                0.2,
                attributes={phone: {"value": "020 7946 0999"}},
                **ADDRESS,
            ),
        ],
        DedupeSettings(),
    )
    [merged] = result.records
    assert "different phone" in merged.merged[0].reasons
    assert [c.field for c in merged.conflicts] == ["attributes.phone"]
    assert merged.attributes[phone].value is None


def test_a_street_without_a_house_number_does_not_identify_a_site() -> None:
    street = {"address": {"street": "Hospital Road"}}
    result = resolve(
        [place("a", "Example Clinic", 0, **street), place("b", "Example Clinic", 0.2, **street)],
        DedupeSettings(),
    )
    a, _ = result.records
    assert not a.merged
    [line] = possible_of(a)
    assert "same street, no house number" in line
    assert line.endswith(
        "not merged because no site-level identifier or matching address confirms it)."
    )


def test_a_site_identifier_needs_compatible_names() -> None:
    item = {"external_ids": {"wikidata": SITE_ITEM}}
    renamed = resolve(
        [
            place("a", "Example Clinic", 0, **item),
            place("b", "Harbour Dental Surgery", 0.2, **item),
        ],
        AS_SITE,
    )
    a, _ = renamed.records
    assert not a.merged
    assert possible_of(a)[0].endswith("not merged because their names do not match).")

    nameless = resolve(
        [place("a", "Example Clinic", 0, **item), place("b", None, 0.2, **item)], AS_SITE
    )
    [merged] = nameless.records
    assert [m.record_id for m in merged.merged] == ["b"]


def test_a_wikidata_item_on_far_apart_records_names_no_one_site() -> None:
    """A brand's Wikidata item tagged on every branch: two branches 50 m apart and one 2 km
    away all carry it, so it cannot identify a site. It links them as one organisation."""
    brand = {"external_ids": {"wikidata": SITE_ITEM}}
    result = resolve(
        [
            place("a", "Corner Market", 0, **brand),
            place("b", "Corner Market", 0.5, **brand),
            place("c", "Corner Market", 20, **brand),
        ],
        DedupeSettings(),
    )
    assert [r.id for r in result.records] == ["a", "b", "c"]
    assert result.merges == 0
    assert result.spread == 1
    assert len(linked(result.records)) == 1
    assert possible_of(result.records[0])[0].endswith(
        "not merged because they share only organisation-level details (wikidata), which do "
        "not identify a site)."
    )


def test_a_key_is_either_site_or_organisation_level() -> None:
    with pytest.raises(ValueError, match="website"):
        DedupeSettings(site_keys=("website",), organisation_keys=("website", "phone"))
    for key in ("wikidata", "brand:wikidata"):
        with pytest.raises(ValueError, match="cannot be a site key"):
            DedupeSettings(site_keys=(key,), organisation_keys=("phone",))
    with pytest.raises(ValueError, match="Q1: an item is a site or an organisation"):
        DedupeSettings(wikidata_sites=frozenset({"Q1"}), wikidata_organisations=frozenset({"q1"}))


# Wikidata items and identifiers repeated across sites (GP-16 follow-up) -----------------------


def test_branches_sharing_a_wikidata_item_of_unknown_meaning_are_not_merged() -> None:
    """Three branches 60 m apart carry one Wikidata item, and nothing says whether it is the
    establishment or the chain. Before, it counted as a site identifier and merged all three."""
    result = resolve(
        [
            place(rid, "Corner Market", k * 0.6, external_ids={"wikidata": "Q1000001"})
            for k, rid in enumerate("abc")
        ],
        DedupeSettings(),
    )
    assert [r.id for r in result.records] == ["a", "b", "c"]
    assert result.merges == 0
    assert result.unclassified == 1
    assert len(linked(result.records)) == 1
    assert None not in linked(result.records)
    assert possible_of(result.records[0])[0].endswith(
        "not merged because they share only a Wikidata item that is not known to name a site)."
    )


@pytest.mark.parametrize(
    ("fields", "settings", "keys"),
    [
        (
            {"attributes": {"brand:wikidata": {"value": "Q1000001"}}},
            DedupeSettings(),
            "brand:wikidata and wikidata",
        ),
        (
            {"attributes": {"operator:wikidata": {"value": "Q1000001"}}},
            DedupeSettings(),
            "operator:wikidata and wikidata",
        ),
        ({}, DedupeSettings(wikidata_organisations=frozenset({"q1000001"})), "wikidata"),
    ],
    ids=["given-as-brand", "given-as-operator", "classified-as-organisation"],
)
def test_branches_sharing_an_organisation_wikidata_item_are_linked_not_merged(
    fields: dict[str, Any], settings: DedupeSettings, keys: str
) -> None:
    """The shared item is known to be an organisation: the records give it as their brand or
    operator (as OpenStreetMap's ``brand:wikidata`` does), or it was looked up."""
    result = resolve(
        [
            place(rid, "Corner Market", k * 0.6, external_ids={"wikidata": "Q1000001"}, **fields)
            for k, rid in enumerate("abc")
        ],
        settings,
    )
    assert result.merges == 0
    assert result.unclassified == 0
    assert len(linked(result.records)) == 1
    assert possible_of(result.records[0])[0].endswith(
        f"not merged because they share only organisation-level details ({keys}), which do "
        "not identify a site)."
    )


def test_a_wikidata_item_merges_only_when_known_to_name_a_site() -> None:
    records = [
        place("a", "Example Hospital", 0, external_ids={"wikidata": SITE_ITEM}),
        place("b", "Example Hosp.", 0.2, external_ids={"wikidata": SITE_ITEM}),
    ]
    unknown = resolve(records, DedupeSettings())
    assert unknown.merges == 0
    assert unknown.unclassified == 1
    known = resolve(records, AS_SITE)
    assert known.merges == 1
    assert "same wikidata" in known.records[0].merged[0].reasons


@pytest.mark.parametrize(
    ("spots", "extra"),
    [
        ([0, 0.6, 1.2], [{"address": {"house_number": n, "street": "High Street"}} for n in "135"]),
        ([0, 0.5, 20], [{}, {}, {}]),
        ([0, 0.6, 1.2], [{"external_ids": {"licence": f"L-{n}"}} for n in "123"]),
    ],
    ids=["different-addresses", "far-apart", "different-site-identifiers"],
)
def test_a_site_identifier_repeated_at_distinct_sites_is_organisation_level(
    spots: list[float], extra: list[dict[str, Any]]
) -> None:
    """A register code on three branches that are shown to be distinct sites: it names more
    than one site, so it links them as one organisation and merges nothing."""
    records = []
    for rid, east, more in zip("abc", spots, extra, strict=True):
        ids = {"register": "HF-0042", **more.pop("external_ids", {})}
        records.append(place(rid, "Corner Market", east, external_ids=ids, **more))
    result = resolve(records, DedupeSettings(site_keys=("register", "licence")))
    assert result.merges == 0
    assert result.spread == 1
    assert len(linked(result.records)) == 1
    assert None not in linked(result.records)


def test_a_site_identifier_on_records_that_may_be_one_site_still_merges() -> None:
    """Three records 60 m apart share a register code the user named as site-level, and
    nothing shows they are distinct sites (no addresses, no other site identifier, within
    ``max_distance_m``). The code says they are one site, so they merge."""
    result = resolve(
        [
            place(rid, "Example Hospital", k * 0.6, external_ids={"register": "HF-0042"})
            for k, rid in enumerate("abc")
        ],
        REGISTER,
    )
    [merged] = result.records
    assert sorted(m.record_id for m in merged.merged) == ["b", "c"]
    assert result.spread == 0
