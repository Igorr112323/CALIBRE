"""Deterministic, local retrieval over uploaded manual pages.

This module is intentionally a retriever, not a pretend-trained model: it ranks
page-level evidence and preserves document/page provenance for a configured
OpenAI-compatible language model to use later.
"""
from __future__ import annotations

import json
import math
import re
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Iterable

import snowballstemmer
from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.orm import Session

from calibre.models import AppSetting, DocumentPage, Machine, SourceDocument

TOKEN_RE = re.compile(r"[\w]+(?:[.,][\w]+)?", re.UNICODE)
STOP_WORDS = frozenset("""
а без более быть был была были было в во вот все вы где да для до его ее если есть еще же за и из или им их как к ко когда который ли либо мне меня может мы на над не него нет ни них но о об один она они оно от по под при про с со так такой там те то того тоже той только том тут ты у уже чем что чтобы эта эти это я
какой какая какое какие сколько почему зачем куда откуда ли можно нужно должен должна должны будет являются сделать сделать ли работать работает работаете использовать использоваться
""".split())

try:
    _RU_STEMMER = snowballstemmer.stemmer("russian")
except Exception:  # pragma: no cover - defensive fallback for minimal desktop builds
    _RU_STEMMER = None


@dataclass(frozen=True)
class EvidenceHit:
    document_id: int
    document_title: str
    filename: str
    page: int
    score: float
    coverage: float
    text: str
    section: str
    extraction_method: str
    text_quality: str
    citation_text: str | None = None

    def public_dict(self) -> dict:
        value = asdict(self)
        value.pop("citation_text", None)
        value["score"] = round(self.score, 4)
        value["coverage"] = round(self.coverage, 4)
        return value


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").casefold().replace("ё", "е")
    value = value.replace("\u00ad", "")
    return re.sub(r"\s+", " ", value).strip()


def terms(value: str) -> list[str]:
    output: list[str] = []
    for token in TOKEN_RE.findall(normalize(value)):
        token = token.strip(".,")
        if not token or token in STOP_WORDS:
            continue
        if token.isdigit() or any(char.isdigit() for char in token):
            output.append(token.replace(",", "."))
        elif len(token) > 2 and _RU_STEMMER:
            output.append(_RU_STEMMER.stemWord(token))
        else:
            output.append(token)
    return output


def _scope_for(session: Session, document_id: int) -> dict:
    setting = session.get(AppSetting, f"document_scope:{document_id}")
    if not setting:
        return {}
    try:
        data = json.loads(setting.value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _classify_page(page_number: int, scope: dict) -> str:
    catalog_start = scope.get("catalog_start_page")
    catalog_end = scope.get("catalog_end_page")
    if isinstance(catalog_start, int) and isinstance(catalog_end, int) and catalog_start <= page_number <= catalog_end:
        return "parts_catalog"
    guide_start = scope.get("guide_start_page")
    guide_end = scope.get("guide_end_page")
    if isinstance(guide_start, int) and isinstance(guide_end, int) and guide_start <= page_number <= guide_end:
        return "manual"
    return "other"


def is_parts_query(question: str) -> bool:
    query = normalize(question)
    parts_terms = ("каталог", "каталож", "запчаст", "артикул", "номер детали", "позиция детали", "детал", "подшип", "чертеж", "рисунк", "позици", "назначени", "функци", "отвеча", "за что отвечает")
    adjustment_terms = ("регулиров", "зазор", "момент затяж", "норм", "давлен", "скорост", "смаз", "интервал", "настрой")
    code_like = bool(re.search(r"(?:грн|грп)[-–]?\d", query))
    return (any(term in query for term in parts_terms) or code_like) and not any(term in query for term in adjustment_terms)


def search_pages(
    session: Session,
    question: str,
    *,
    machine_id: int | None = None,
    limit: int = 6,
    include_catalog: bool | None = None,
) -> list[EvidenceHit]:
    """Return ranked page evidence; by default parts catalogs are not norm sources."""
    query_terms = list(dict.fromkeys(terms(question)))
    if not query_terms:
        return []
    if include_catalog is None:
        include_catalog = is_parts_query(question)

    stmt = (
        select(DocumentPage, SourceDocument, Machine)
        .join(SourceDocument, DocumentPage.source_document_id == SourceDocument.id)
        .join(Machine, SourceDocument.machine_id == Machine.id)
        .where(Machine.is_demo.is_(False), Machine.status != "archived")
    )
    if machine_id is not None:
        stmt = stmt.where(Machine.id == machine_id)
    rows = session.execute(stmt).all()
    candidates: list[tuple[DocumentPage, SourceDocument, Machine, dict]] = []
    for page, document, machine in rows:
        scope = _scope_for(session, document.id)
        section = _classify_page(page.page_number, scope)
        if section == "parts_catalog" and not include_catalog:
            continue
        candidates.append((page, document, machine, scope))
    if not candidates:
        return []

    token_lists = [terms(page.extracted_text) for page, _doc, _machine, _scope in candidates]
    lengths = [len(tokens) for tokens in token_lists]
    average_length = sum(lengths) / max(1, len(lengths))
    document_frequency: Counter[str] = Counter()
    counters: list[Counter[str]] = []
    for tokens_for_page in token_lists:
        frequencies = Counter(tokens_for_page)
        counters.append(frequencies)
        document_frequency.update(frequencies.keys())

    query_set = set(query_terms)
    raw_question = normalize(question)
    hits: list[EvidenceHit] = []
    for index, (page, document, _machine, scope) in enumerate(candidates):
        text = page.extracted_text or ""
        page_tokens = token_lists[index]
        frequencies = counters[index]
        coverage = len(query_set.intersection(frequencies)) / len(query_set)
        score = 0.0
        for term in query_set:
            frequency = frequencies.get(term, 0)
            if frequency:
                df = document_frequency[term]
                idf = math.log(1 + (len(candidates) - df + 0.5) / (df + 0.5))
                denominator = frequency + 1.2 * (1 - 0.75 + 0.75 * len(page_tokens) / max(1, average_length))
                score += idf * (frequency * 2.2 / denominator)
        normalized_text = normalize(text)
        compact_query = " ".join(query_terms)
        if len(compact_query) >= 9 and compact_query in " ".join(page_tokens):
            score += 2.0
        # Common inflection or one-character typo: reward close matches, never
        # treat this fuzzy score as source proof without returning the source.
        if query_set and coverage < 0.5:
            page_vocab = set(page_tokens)
            fuzzy_hits = sum(
                1 for term in query_set
                if term not in page_vocab and len(term) >= 5
                and any(fuzz.ratio(term, candidate_term) >= 86 for candidate_term in page_vocab)
            )
            coverage = max(coverage, fuzzy_hits / len(query_set))
            score += fuzzy_hits * 0.15
        if score <= 0:
            continue
        if len(raw_question) >= 12 and raw_question in normalized_text:
            score += 1.5
        if page.text_quality in {"low", "empty", "unreadable"}:
            score *= 0.65
        section = _classify_page(page.page_number, scope)
        hits.append(EvidenceHit(
            document_id=document.id,
            document_title=document.title,
            filename=document.original_filename,
            page=page.page_number,
            score=score,
            coverage=coverage,
            text=text[:12000],
            section=section,
            extraction_method=page.extraction_method,
            text_quality=page.text_quality,
        ))
    hits.sort(key=lambda hit: (hit.score, hit.coverage), reverse=True)
    return hits[:max(1, min(int(limit), 12))]


def search_part_graph(session: Session, question: str, *, machine_id: int, limit: int = 4) -> list[EvidenceHit]:
    """Search a reviewed local catalogue graph and return citable PDF evidence.

    The graph is an index, not an answer source. It distinguishes an item-level
    function from a responsibility stated only for the enclosing assembly.
    """
    from calibre.part_graph import CATALOG_SHA256

    query = normalize(question)
    query_terms = set(terms(question))
    if not query_terms:
        return []
    documents = session.scalars(
        select(SourceDocument).join(Machine).where(
            SourceDocument.machine_id == machine_id,
            SourceDocument.sha256 == CATALOG_SHA256,
            Machine.is_demo.is_(False),
            Machine.status != "archived",
        )
    ).all()
    candidates: list[tuple[float, SourceDocument, dict, dict]] = []
    for document in documents:
        setting = session.get(AppSetting, f"part_graph:{document.id}")
        if not setting:
            continue
        try:
            graph = json.loads(setting.value)
        except (TypeError, json.JSONDecodeError):
            continue
        if graph.get("record_status") != "draft_for_owner_review_not_for_mobile_publication":
            continue
        for part in graph.get("parts", []):
            figure_refs = part.get("figure_refs", [part.get("figure")])
            location = f"рисунок {','.join(str(value) for value in figure_refs)} позиция {part.get('position')}"
            fields = [part.get("designation") or "", part.get("name", ""),
                      part.get("assembly", ""), part.get("part_id", ""), location]
            indexed = " ".join(str(value) for value in fields)
            indexed_normalized = normalize(indexed)
            indexed_terms = set(terms(indexed))
            score = len(query_terms & indexed_terms) / max(1, len(query_terms))
            designation = normalize(part.get("designation") or "")
            if designation and designation in query:
                score += 3.0
            if normalize(location) in query:
                score += 3.0
            name = normalize(part.get("name", ""))
            if len(name) > 3 and name in query:
                score += 1.5
            # Queries usually say a quantity/name; the part designation itself
            # may be absent. Keep a small exact phrase bonus without guessing.
            if len(query) >= 8 and query in indexed_normalized:
                score += 0.5
            if score > 0:
                candidates.append((score, document, part, graph))
    candidates.sort(key=lambda row: (row[0], row[2].get("designation") is not None), reverse=True)
    exact_codes = [row for row in candidates if row[2].get("designation")
                   and normalize(row[2]["designation"]) in query]
    exact_positions = [row for row in candidates if normalize(
        f"рисунок {','.join(str(value) for value in row[2].get('figure_refs', []))} позиция {row[2].get('position')}") in query]
    if exact_codes:
        candidates = exact_codes
    elif exact_positions:
        candidates = exact_positions

    hits: list[EvidenceHit] = []
    seen_parts: set[tuple[int, str]] = set()
    seen_pages: set[tuple[int, int]] = set()
    for score, document, part, graph in candidates:
        part_key = (document.id, part["part_id"])
        if part_key in seen_parts:
            continue
        catalog_page = int(part["catalog_page"])
        page = session.scalar(select(DocumentPage).where(
            DocumentPage.source_document_id == document.id,
            DocumentPage.page_number == catalog_page,
        ))
        if page is None:
            continue
        identity = (document.id, catalog_page)
        structured = {
            "part_id": part["part_id"], "figure": part["figure_refs"],
            "position": part["position"], "designation": part["designation"],
            "name": part["name"], "quantity": part["quantity"], "note": part["note"],
            "assembly": part["assembly"], "catalog_page": catalog_page,
            "catalog_quote": part.get("catalog_quote"),
            "individual_role_status": part["individual_role_status"],
            "individual_role": part["individual_role"],
            "individual_role_caveat": part.get("individual_role_caveat"),
            "role_source": part.get("individual_role_source"),
            "expert_assessment": part.get("expert_assessment"),
            "drawing_pages": part.get("figure_pages"),
            "reviewed_manual_pages": graph.get("source_scope", {}).get("guide_pdf_pages"),
        }
        catalog_context = (
            "STRUCTURED CATALOG INDEX (derived lookup, NOT a verbatim source quote):\n"
            + json.dumps(structured, ensure_ascii=False, sort_keys=True)
            + "\nRule: do not turn an assembly-level responsibility into a separately confirmed function of this individual item."
        )
        hits.append(EvidenceHit(
            document_id=document.id, document_title=document.title,
            filename=document.original_filename, page=catalog_page, score=score,
            coverage=min(1.0, score), text=(catalog_context + "\n\nVERBATIM PAGE TEXT:\n" + (page.extracted_text or ""))[:14000],
            section="parts_catalog", extraction_method=page.extraction_method,
            text_quality=page.text_quality, citation_text=page.extracted_text or "",
        ))
        seen_parts.add(part_key)
        seen_pages.add(identity)

        role_source = part.get("individual_role_source")
        if role_source and role_source.get("page"):
            role_page_no = int(role_source["page"])
            role_page = session.scalar(select(DocumentPage).where(
                DocumentPage.source_document_id == document.id,
                DocumentPage.page_number == role_page_no,
            ))
            role_identity = (document.id, role_page_no)
            if role_page and role_identity not in seen_pages:
                note = ("CURATED ROLE NOTE (not a verbatim quotation): "
                        + str(part.get("individual_role", ""))
                        + (" " + part["individual_role_caveat"] if part.get("individual_role_caveat") else ""))
                hits.append(EvidenceHit(
                    document_id=document.id, document_title=document.title,
                    filename=document.original_filename, page=role_page_no, score=score * 0.9,
                    coverage=min(1.0, score), text=(note + "\n\nVERBATIM PAGE TEXT:\n" + (role_page.extracted_text or ""))[:14000],
                    section="manual", extraction_method=role_page.extraction_method,
                    text_quality=role_page.text_quality, citation_text=role_page.extracted_text or "",
                ))
                seen_pages.add(role_identity)

        assessment = part.get("expert_assessment") or {}
        for drawing_page_no in assessment.get("drawing_pages", []):
            drawing_identity = (document.id, int(drawing_page_no))
            if drawing_identity in seen_pages:
                continue
            drawing_page = session.scalar(select(DocumentPage).where(
                DocumentPage.source_document_id == document.id,
                DocumentPage.page_number == int(drawing_page_no),
            ))
            if drawing_page:
                hits.append(EvidenceHit(
                    document_id=document.id, document_title=document.title,
                    filename=document.original_filename, page=int(drawing_page_no), score=score * 0.85,
                    coverage=min(1.0, score),
                    text=("EXPLODED VIEW REFERENCE: visual basis for the separate engineering hypothesis; "
                          "the drawing does not itself confirm the inferred function.\n\nVERBATIM PAGE TEXT:\n"
                          + (drawing_page.extracted_text or ""))[:14000],
                    section="parts_drawing", extraction_method=drawing_page.extraction_method,
                    text_quality=drawing_page.text_quality, citation_text=drawing_page.extracted_text or "",
                ))
                seen_pages.add(drawing_identity)
        if len(seen_parts) >= max(1, min(limit, 8)):
            break
    return hits


def find_document_page(session: Session, document_id: int, page_number: int) -> tuple[SourceDocument, DocumentPage] | None:
    row = session.execute(
        select(SourceDocument, DocumentPage)
        .join(DocumentPage, DocumentPage.source_document_id == SourceDocument.id)
        .where(SourceDocument.id == document_id, DocumentPage.page_number == page_number)
    ).first()
    return row if row else None


def validate_document_scope(page_count: int, guide_start: int | None, guide_end: int | None,
                            catalog_start: int | None, catalog_end: int | None) -> dict:
    """Validate page ranges so the spare-parts catalog cannot leak into norms."""
    values = (guide_start, guide_end, catalog_start, catalog_end)
    if all(value is None for value in values):
        return {}
    if guide_start is None or guide_end is None or guide_start > guide_end:
        raise ValueError("Укажите корректный диапазон страниц руководства.")
    if guide_start < 1 or guide_end > page_count:
        raise ValueError("Диапазон руководства выходит за пределы PDF.")
    if (catalog_start is None) != (catalog_end is None):
        raise ValueError("Для каталога нужно указать обе границы страниц.")
    if catalog_start is not None:
        if catalog_start < 1 or catalog_end > page_count or catalog_start > catalog_end:
            raise ValueError("Диапазон каталога выходит за пределы PDF.")
        if not (guide_end < catalog_start or catalog_end < guide_start):
            raise ValueError("Диапазоны руководства и каталога не должны пересекаться.")
    result = {"guide_start_page": guide_start, "guide_end_page": guide_end}
    if catalog_start is not None:
        result.update(catalog_start_page=catalog_start, catalog_end_page=catalog_end)
    return result
