#!/usr/bin/env python3
"""Score a source from its seven ratings, with the same weights as the GeoProduct server.

Standard library only. Prints JSON to stdout; exits 0 on success, 2 on bad input.

Examples:
    python score_source.py --authority 3 --accuracy 2 --freshness 1 --coverage 3 \\
        --licensing 3 --geographic-precision 2 --verifiability 3
    echo '{"authority": 3, "accuracy": 2, ...}' | python score_source.py --json -
"""

import argparse
import json
import sys

WEIGHTS = {
    "authority": 0.20,
    "accuracy": 0.20,
    "freshness": 0.10,
    "coverage": 0.10,
    "licensing": 0.15,
    "geographic_precision": 0.15,
    "verifiability": 0.10,
}
MAX_RATING = 3


def score(ratings: dict[str, int]) -> dict[str, object]:
    """The weighted score (0-1) and tier; licensing 0 makes a source unusable."""
    missing = sorted(set(WEIGHTS) - set(ratings))
    unknown = sorted(set(ratings) - set(WEIGHTS))
    if missing or unknown:
        raise ValueError(f"missing ratings: {missing}; unknown names: {unknown}")
    for name, value in ratings.items():
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= MAX_RATING:
            raise ValueError(f"{name} must be a whole number from 0 to 3")
    result = round(sum(WEIGHTS[n] * ratings[n] for n in WEIGHTS) / MAX_RATING, 4)
    if ratings["licensing"] == 0:
        tier = "unusable"
    elif result >= 0.75:
        tier = "high"
    elif result >= 0.5:
        tier = "medium"
    else:
        tier = "low"
    return {"score": result, "tier": tier, "ratings": ratings, "weights": WEIGHTS}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Score a source from seven 0-3 ratings (GeoProduct weights).",
        epilog="Tiers: high >= 0.75, medium >= 0.5, low below; licensing 0 is unusable.",
    )
    for name in WEIGHTS:
        parser.add_argument(f"--{name.replace('_', '-')}", type=int, metavar="0-3")
    parser.add_argument(
        "--json", metavar="FILE", help="read the ratings from a JSON object ('-' for stdin)"
    )
    args = parser.parse_args(argv)
    try:
        if args.json:
            if args.json == "-":
                text = sys.stdin.read()
            else:
                with open(args.json, encoding="utf-8") as handle:
                    text = handle.read()
            ratings = json.loads(text)
            if not isinstance(ratings, dict):
                raise ValueError("the JSON must be an object of ratings")
        else:
            ratings = {n: getattr(args, n) for n in WEIGHTS if getattr(args, n) is not None}
        result = score(ratings)
    except (ValueError, OSError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
