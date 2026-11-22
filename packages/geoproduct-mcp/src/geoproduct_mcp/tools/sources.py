"""Sources: recording the agent's citations and keeping a checked copy of their content.

The server does not research. The agent finds a source, judges it against the published
criteria and registers it; the server computes the score from those ratings with fixed
weights, and can fetch the source so its content hash is recorded and evidence excerpts
can be checked against its text.
"""

from datetime import UTC, date, datetime
from hashlib import sha256
from typing import Annotated, Final

from pydantic import Field

from geoproduct import __version__
from geoproduct.engine.fingerprint import short_id
from geoproduct.errors import GeoProductError
from geoproduct.fetch import SUFFIX_KINDS, Fetched, Fetcher, normalise_for_match, plain_text
from geoproduct.schemas import (
    Contract,
    ErrorCode,
    Evidence,
    ExcerptCheck,
    Source,
    SourceEvaluation,
    SourceType,
)
from geoproduct.schemas._base import ShortText, Text
from geoproduct.workspace import Workspace
from geoproduct_mcp.tools.common import Done, Job, ServerState, invalid

DEFAULT_AGENT: Final = (
    f"geoproduct-core/{__version__} (+https://github.com/Ppp2048/geoproduct-engineer-skill)"
)
EXTENSIONS: Final = {
    "html": ".html",
    "text": ".txt",
    "csv": ".csv",
    "json": ".json",
    "geojson": ".geojson",
    "xml": ".xml",
    "pdf": ".pdf",
}
TABULAR: Final = frozenset({"csv", "json", "geojson"})


class SourceResult(Contract):
    source: Source
    created: bool
    """False when a source with the same type and URL was already registered and updated."""
    fetched: bool
    """Whether its content has been fetched (see fetch_source_data)."""


def register_source(
    state: ServerState,
    *,
    title: str,
    type: SourceType,
    url_or_ref: str,
    publisher: str | None = None,
    licence: str | None = None,
    attribution: str | None = None,
    data_as_of: date | None = None,
    coverage_note: str | None = None,
    evaluation: SourceEvaluation | None = None,
) -> Done:
    source_id = short_id("src", type.value, url_or_ref.strip())
    try:
        previous: Source | None = state.sources.get(source_id)
    except GeoProductError:
        previous = None
    source = Source(
        id=source_id,
        title=title,
        type=type,
        url_or_ref=url_or_ref.strip(),
        publisher=publisher,
        licence=licence,
        attribution=attribution,
        data_as_of=data_as_of,
        retrieved_at=previous.retrieved_at if previous else None,
        content_sha256=previous.content_sha256 if previous else None,
        coverage_note=coverage_note,
        evaluation=evaluation,
    )
    state.sources.save(source)
    fetched = state.sources.text(source_id) is not None or source.content_sha256 is not None
    if source.score is None:
        rating = "not evaluated, so it carries no source-quality weight"
    else:
        rating = f"score {source.score:.2f} ({source.tier.value if source.tier else '?'})"
    summary = f"{'Registered' if previous is None else 'Updated'} {source_id}: {rating}."
    if not fetched and url_or_ref.startswith(("http://", "https://")):
        summary += " Fetch it with fetch_source_data to confirm it exists and to check excerpts."
    return Done(SourceResult(source=source, created=previous is None, fetched=fetched), summary)


class FetchResult(Contract):
    source: Source
    url: Annotated[str, Field(max_length=2000)]
    """What was fetched, after redirects; or the file path."""
    content_type: ShortText
    kind: ShortText
    size_bytes: Annotated[int, Field(ge=0)]
    sha256: ShortText
    text_chars: Annotated[int, Field(ge=0)] | None
    """Length of the extracted text; ``None`` when no text could be extracted (PDF)."""
    excerpt: Text | None
    """The beginning of the text."""
    stored: list[ShortText]
    """Files kept in the workspace, relative to it."""
    loadable: bool
    """True for CSV, JSON and GeoJSON: create_dataset(source_id=...) can load it."""


def fetch_source_data(state: ServerState, job: Job, *, source_id: str) -> Done:
    source = state.sources.get(source_id)
    target = source.url_or_ref
    if target.startswith(("http://", "https://")):
        agent = state.http.header() or DEFAULT_AGENT
        fetched = Fetcher(agent, transport=state.fetch_transport).fetch(target)
    else:
        fetched = _read_file(state, target)
    folder = state.sources.folder(source_id)
    content = folder / f"content{EXTENSIONS[fetched.kind]}"
    Workspace.write_bytes_atomic(content, fetched.body)
    stored = [content]
    text_path = folder / "text.txt"
    if fetched.text is not None:
        Workspace.write_bytes_atomic(text_path, fetched.text.encode("utf-8"))
        stored.append(text_path)
    else:
        text_path.unlink(missing_ok=True)
    meta = {
        "url": fetched.url,
        "content_type": fetched.content_type,
        "kind": fetched.kind,
        "size_bytes": len(fetched.body),
        "sha256": fetched.sha256,
        "retrieved_at": fetched.retrieved_at.isoformat(),
    }
    state.workspace.write_json(folder / "fetch.json", meta)
    updated = source.model_copy(
        update={"content_sha256": fetched.sha256, "retrieved_at": fetched.retrieved_at}
    )
    state.sources.save(updated)
    root = state.workspace.root
    result = FetchResult(
        source=updated,
        url=fetched.url[:2000],
        content_type=fetched.content_type[:200],
        kind=fetched.kind,
        size_bytes=len(fetched.body),
        sha256=fetched.sha256,
        text_chars=len(fetched.text) if fetched.text is not None else None,
        excerpt=fetched.text[:500] if fetched.text else None,
        stored=[p.relative_to(root).as_posix() for p in stored],
        loadable=fetched.kind in TABULAR,
    )
    size = f"{len(fetched.body):,} bytes of {fetched.kind}"
    summary = f"Fetched {source_id} ({size}), sha256 {fetched.sha256[:12]}."
    if fetched.text is None:
        summary += " Its text was not extracted, so excerpts from it cannot be checked."
    if result.loadable:
        summary += f" Load it as records with create_dataset(source_id='{source_id}')."
    return Done(result, summary)


def _read_file(state: ServerState, target: str) -> Fetched:
    path = state.workspace.user_path(target)
    kind = next((k for s, k in SUFFIX_KINDS.items() if path.name.lower().endswith(s)), None)
    if kind is None:
        raise invalid(
            "This file type cannot be stored as a source.",
            hint="Supported: " + ", ".join(sorted(SUFFIX_KINDS)),
            path=target[:500],
        )
    size = path.stat().st_size
    if size > 50 * 1024 * 1024:
        raise GeoProductError(
            ErrorCode.too_large, "The file is larger than 50 MB.", details={"path": target[:500]}
        )
    body = path.read_bytes()
    return Fetched(
        url=str(path),
        content_type="application/octet-stream",
        kind=kind,
        body=body,
        sha256=sha256(body).hexdigest(),
        retrieved_at=datetime.now(UTC),
        text=plain_text(body, kind),
    )


def check_excerpts(state: ServerState, evidence: list[Evidence]) -> list[Evidence]:
    """Evidence with ``excerpt_check`` recomputed; a value given in input is never trusted.

    An excerpt is ``verified`` when it appears, ignoring case, quote style and spacing, in
    the fetched text of its source; ``not_found`` when the text was fetched but does not
    contain it; ``not_checked`` otherwise.
    """
    texts: dict[str, str | None] = {}
    checked = []
    for item in evidence:
        status = ExcerptCheck.not_checked
        if item.excerpt:
            if item.source_id not in texts:
                try:
                    text = state.sources.text(item.source_id)
                except GeoProductError:
                    text = None
                texts[item.source_id] = normalise_for_match(text) if text else None
            haystack = texts[item.source_id]
            if haystack is not None:
                found = normalise_for_match(item.excerpt) in haystack
                status = ExcerptCheck.verified if found else ExcerptCheck.not_found
        update: dict[str, object] = {"excerpt_check": status}
        if status == ExcerptCheck.verified:
            update["last_verified"] = datetime.now(UTC)
        checked.append(item.model_copy(update=update))
    return checked
