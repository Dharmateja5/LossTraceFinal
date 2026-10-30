"""Source-backed passages: preserve text structure; never let the model quote.

Small hackathon retriever for already-verified, already-located PDF sections.
Each passage text is a literal slice of the PDF section; IDs bind artifact hash,
section, and character offsets. No embeddings or new dependencies.
"""
from dataclasses import dataclass
from hashlib import sha256
import re


@dataclass(frozen=True)
class SourcePassage:
    passage_id: str
    artifact_id: str
    artifact_sha256: str
    section_id: str
    page: int
    start_offset: int
    end_offset: int
    text: str

    def model_view(self) -> dict[str, str]:
        return {"passage_id": self.passage_id, "text": self.text}


def _paragraph_spans(text: str) -> list[tuple[int, int]]:
    """Split on blank lines; fall back to sentences for a long paragraph."""
    return [(m.start(), m.end()) for m in
            re.finditer(r"\S[\s\S]*?(?=\n[ \t]*\n|\Z)", text)]


def _sentence_spans(text: str, start: int, stop: int) -> list[tuple[int, int]]:
    """Only used when a paragraph exceeds max_words. Keep exact offsets."""
    block = text[start:stop]
    boundaries = [start]
    for match in re.finditer(r"(?<=[.!?])\s+(?=\S)", block):
        boundaries.append(start + match.end())
    boundaries.append(stop)
    result = []
    for left, right in zip(boundaries, boundaries[1:]):
        while left < right and text[left].isspace():
            left += 1
        while right > left and text[right - 1].isspace():
            right -= 1
        if left < right:
            result.append((left, right))
    return result or [(start, stop)]


def build_passages(
    document: dict,
    section_id: str,
    section_text: str,
    *,
    max_words: int = 120,
    min_words: int = 8,
) -> list[SourcePassage]:
    """Create source-anchored, coherent passages from verified PDF section text.

    Prefer complete paragraphs (and sentences when too long). max_words is a
    *soft* limit: never cut a sentence or strand a short qualification just
    to hit a target length. Oversized single sentences stay intact.
    """
    if not (8 <= min_words <= max_words):
        raise ValueError("Invalid min_words or max_words")
    if len(section_text.split()) < min_words:
        return []

    paragraphs = _paragraph_spans(section_text)
    units: list[tuple[int, int]] = []
    for start, stop in paragraphs:
        if len(section_text[start:stop].split()) > max_words:
            units.extend(_sentence_spans(section_text, start, stop))
        else:
            units.append((start, stop))

    grouped: list[tuple[int, int]] = []
    for start, stop in units:
        if not grouped:
            grouped.append((start, stop))
            continue
        prev_start, prev_stop = grouped[-1]
        combined_words = len(section_text[prev_start:stop].split())
        prev_words = len(section_text[prev_start:prev_stop].split())
        if combined_words <= max_words or prev_words < min_words:
            grouped[-1] = (prev_start, stop)
        else:
            grouped.append((start, stop))

    # Short trailers often contain crucial qualifications. Attach them to the
    # preceding passage rather than omitting them or creating a tiny citation.
    if len(grouped) > 1 and len(section_text[grouped[-1][0]:grouped[-1][1]].split()) < min_words:
        _, tail_stop = grouped.pop()
        previous_start, _ = grouped.pop()
        grouped.append((previous_start, tail_stop))

    passages = []
    for start, stop in grouped:
        raw = section_text[start:stop]
        if len(raw.split()) < min_words:
            continue
        source_key = f"{document['artifact_id']}|{document['sha256']}|{section_id}|{start}|{stop}"
        passage_id = "p-" + sha256(source_key.encode("utf-8")).hexdigest()[:20]
        passages.append(SourcePassage(
            passage_id=passage_id,
            artifact_id=document["artifact_id"],
            artifact_sha256=document["sha256"],
            section_id=section_id,
            page=document["locators"][section_id]["page"],
            start_offset=start,
            end_offset=stop,
            text=raw,
        ))
    return passages


def resolve_passage(
    passages: list[SourcePassage],
    passage_id: str,
    *,
    artifact_id: str,
    section_id: str,
) -> SourcePassage:
    """Fail closed on unknown IDs, ambiguous IDs or wrong document/section."""
    matches = [p for p in passages if p.passage_id == passage_id]
    if len(matches) != 1:
        raise ValueError(f"Unknown or ambiguous passage ID: {passage_id}")
    passage = matches[0]
    if passage.artifact_id != artifact_id or passage.section_id != section_id:
        raise ValueError("Passage belongs to a different source or section")
    return passage
