#!/usr/bin/env python3
"""Deterministic, local quality evaluator for the GRN-471 v4 benchmark.

No model/API is called here. Supply an AI analysis as JSON in the documented
schema; this tool checks its claims against the approved gold JSON and the PDF's
page text, then persists a timestamped report under tests/test_documents/runs/.
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from pypdf import PdfReader
except ImportError as exc:  # pragma: no cover - environment setup message
    raise SystemExit("Нужен pypdf: установите зависимости проекта командой python -m pip install -r requirements.txt") from exc

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PDF = ROOT / "tests/test_documents/GRN-471-Kolibri-v4.pdf"
DEFAULT_GOLD = ROOT / "tests/test_documents/GRN-471-Kolibri-v4-gold.json"
DEFAULT_RUNS = ROOT / "tests/test_documents/runs"

NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?(?:\s*±\s*\d+(?:[.,]\d+)?)?", re.UNICODE)
WORD_NUMBERS = {
    "один": "1", "одна": "1", "одно": "1", "одного": "1", "одной": "1", "одну": "1",
    "двух": "2", "две": "2", "два": "2", "двумя": "2",
    "трёх": "3", "трех": "3", "три": "3",
}
UNIT_ALIASES = {
    "км/час": "км/ч", "километр/час": "км/ч", "километров/час": "км/ч",
    "моточасов": "ч", "моточас": "ч", "часов работы": "ч", "часов": "ч", "часа": "ч",
    "минут": "мин", "минута": "мин", "мин": "мин", "градусы": "град", "градусов": "град", "град.": "град",
    "метров": "м", "метра": "м", "мм.": "мм", "кг/м3": "кг/м³", "кг/м^3": "кг/м³",
    "об/мин.": "об/мин", "чел.": "чел", "кгс/см^2": "кгс/см²",
}


def normalized(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold().replace("\u00ad", "")
    text = text.replace("ё", "е").replace("—", "-").replace("–", "-").replace("‑", "-")
    text = text.replace("…", "…")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def number_key(token: str) -> str:
    token = token.strip().replace(" ", "").replace(",", ".")
    token = token.replace("−", "-")
    return token


def value_numbers(value: Any) -> list[str]:
    if value is None:
        return []
    text = str(value)
    nums = [number_key(m.group(0)) for m in NUMBER_RE.finditer(text)]
    for word, number in WORD_NUMBERS.items():
        if re.search(rf"(?<!\w){word}(?!\w)", normalized(text)):
            nums.append(number)
    return list(dict.fromkeys(nums))


def text_numbers(text: str) -> list[str]:
    return [number_key(m.group(0)) for m in NUMBER_RE.finditer(text)]


def numeric_evidence_check(value: Any, quote: str) -> tuple[bool, list[str]]:
    wanted = value_numbers(value)
    available = text_numbers(quote)
    normalized_quote = normalized(quote)
    for word, number in WORD_NUMBERS.items():
        if re.search(rf"(?<!\w){word}(?!\w)", normalized_quote):
            available.append(number)
    missing = [num for num in wanted if num not in available]
    return not missing, missing


def quote_on_page(page_text: str, quote: str) -> bool:
    page_norm = normalized(page_text)
    quote_norm = normalized(quote)
    return bool(quote_norm) and quote_norm in page_norm


def unit_key(unit: Any) -> str:
    value = normalized(str(unit or ""))
    value = re.sub(r"\s+", "", value)
    return UNIT_ALIASES.get(value, value)


def similarity(left: str, right: str) -> float:
    a, b = normalized(left), normalized(right)
    if not a or not b:
        return 0.0
    at, bt = set(re.findall(r"[\w]+", a)), set(re.findall(r"[\w]+", b))
    overlap = len(at & bt) / max(1, len(at | bt))
    return max(difflib.SequenceMatcher(None, a, b).ratio(), overlap)


def load_pdf_pages(pdf_path: Path) -> list[str]:
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF не найден: {pdf_path}")
    reader = PdfReader(str(pdf_path))
    return [(page.extract_text() or "") for page in reader.pages]


def audit_gold(gold: dict[str, Any], pages: list[str]) -> dict[str, Any]:
    citations: list[dict[str, Any]] = []
    norms: list[dict[str, Any]] = []
    for norm in gold.get("norms", []):
        norm_citations = []
        machine_pass = False
        manual_visual = False
        for citation in norm.get("citations", []):
            page_no = citation.get("page")
            quote = str(citation.get("quote", ""))
            kind = citation.get("source_kind", "text")
            purpose = citation.get("purpose", "numeric")
            item: dict[str, Any] = {"norm_id": norm.get("id"), "page": page_no, "quote": quote, "source_kind": kind}
            if not isinstance(page_no, int) or not (1 <= page_no <= len(pages)):
                item.update(status="FAIL", reason="страница отсутствует или вне диапазона PDF")
            elif kind in {"image_only", "text_layer_mismatch"}:
                reason = ("надпись существует только в изображении; автоматическое чтение текста недоступно" if kind == "image_only"
                          else "визуальный текст проверен, но извлечение PDF искажает символы; это не машинная цифровая проверка")
                item.update(status="MANUAL_VISUAL", reason=reason)
                manual_visual = True
            elif not quote_on_page(pages[page_no - 1], quote):
                item.update(status="FAIL", reason="дословная цитата не найдена на указанной PDF-странице в извлечённом текстовом слое")
            elif purpose == "condition-only":
                item.update(status="PASS", reason="цитата условия найдена; число проверяется отдельной числовой цитатой")
                machine_pass = True
            else:
                numbers_ok, missing = numeric_evidence_check(norm.get("value"), quote)
                if numbers_ok:
                    item.update(status="PASS", reason="цитата найдена на странице; все числовые компоненты значения есть в цитате")
                    machine_pass = True
                else:
                    item.update(status="FAIL", reason="число(а) отсутствуют в цитате: " + ", ".join(missing))
            citations.append(item)
            norm_citations.append(item)
        norm_status = "PASS" if machine_pass else ("MANUAL_VISUAL" if manual_visual else "FAIL")
        norms.append({"id": norm.get("id"), "name": norm.get("name"), "status": norm_status,
                      "machine_verified": machine_pass, "has_visual_only_evidence": manual_visual,
                      "citation_count": len(norm_citations)})
    counts = {
        "norms_total": len(norms),
        "norms_machine_verified": sum(n["machine_verified"] for n in norms),
        "norms_manual_visual_or_text_mismatch": sum(n["status"] == "MANUAL_VISUAL" for n in norms),
        "norms_failed": sum(n["status"] == "FAIL" for n in norms),
        "citations_passed": sum(c["status"] == "PASS" for c in citations),
        "citations_manual_visual": sum(c["status"] == "MANUAL_VISUAL" for c in citations),
        "citations_failed": sum(c["status"] == "FAIL" for c in citations),
    }
    return {"counts": counts, "norms": norms, "citations": citations}


def validate_claim_citation(claim: dict[str, Any], pages: list[str], gold: dict[str, Any]) -> dict[str, Any]:
    page_no = claim.get("page")
    quote = str(claim.get("quote", ""))
    result = {"page": page_no, "quote": quote, "valid": False, "manual_visual_only": False, "reason": ""}

    def known_visual_value() -> bool:
        if not isinstance(page_no, int):
            return False
        claim_sig = set(value_numbers(claim.get("value")))
        if not claim_sig or not quote or not claim_sig.issubset(set(value_numbers(quote))):
            return False
        for norm in gold.get("norms", []):
            if not claim_sig.issubset(set(value_numbers(norm.get("value")))):
                continue
            for citation in norm.get("citations", []):
                if citation.get("page") == page_no and citation.get("source_kind") in {"image_only", "text_layer_mismatch"}:
                    return True
        return False
    if not isinstance(page_no, int) or not (1 <= page_no <= len(pages)):
        result["reason"] = "неверный или отсутствующий номер страницы"
        return result
    if not quote:
        result["reason"] = "нет дословной цитаты"
        return result
    if not quote_on_page(pages[page_no - 1], quote):
        if known_visual_value():
            result["manual_visual_only"] = True
            result["reason"] = "значение известно по визуально проверенной надписи эталона на этой странице, но OCR/текстовый слой не подтверждает цитату; отклонено для ручной проверки"
        else:
            result["reason"] = "цитата не найдена на указанной странице PDF (включая текстовый слой)"
        return result
    numbers_ok, missing = numeric_evidence_check(claim.get("value"), quote)
    if not numbers_ok:
        result["reason"] = "значение содержит числа, которых нет в цитате: " + ", ".join(missing)
        return result
    result["valid"] = True
    result["reason"] = "цитата и числовые компоненты подтверждены на указанной странице"
    return result


def compare_named(expected: list[dict[str, Any]], actual: list[dict[str, Any]], key: str,
                  threshold: float = 0.38) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    unmatched = set(range(len(expected)))
    matched, extras = [], []
    for ai_index, item in enumerate(actual):
        label = str(item.get(key, item.get("name", item.get("symptom", ""))))
        candidates = []
        for gold_index in unmatched:
            gold_item = expected[gold_index]
            gold_label = str(gold_item.get(key, gold_item.get("name", gold_item.get("symptom", ""))))
            score = similarity(label, gold_label)
            # A page intersection strengthens a semantic label match but is not mandatory.
            ai_pages = set(item.get("pages", []))
            if item.get("page"):
                ai_pages.add(item["page"])
            gold_pages = set(gold_item.get("pages", []))
            if gold_item.get("page"):
                gold_pages.add(gold_item["page"])
            if ai_pages and gold_pages and ai_pages & gold_pages:
                score = min(1.0, score + 0.08)
            candidates.append((score, gold_index))
        if not candidates:
            extras.append({"item": item, "reason": "нет соответствующего эталонного пункта"})
            continue
        score, gold_index = max(candidates)
        if score < threshold:
            extras.append({"item": item, "reason": f"не найдено соответствие в эталоне (сходство {score:.2f})"})
            continue
        unmatched.remove(gold_index)
        matched.append({"expected": expected[gold_index], "actual": item, "similarity": round(score, 3)})
    missing = [expected[i] for i in sorted(unmatched)]
    return matched, missing, extras


def norm_signature(value: Any) -> tuple[str, ...]:
    return tuple(sorted(value_numbers(value)))


def compare_norms(expected: list[dict[str, Any]], actual: list[dict[str, Any]], pages: list[str], gold: dict[str, Any]) -> dict[str, Any]:
    unmatched = set(range(len(expected)))
    found, missing, extra, rejected, visual_manual = [], [], [], [], []
    valid_claims: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for claim in actual:
        evidence = validate_claim_citation(claim, pages, gold)
        if not evidence["valid"]:
            item = {"claim": claim, "reason": evidence["reason"], "classification": "НЕ ПОДТВЕРЖДЕНО; требуется ручная проверка"}
            if evidence.get("manual_visual_only"):
                visual_manual.append(item)
            else:
                rejected.append(item)
            continue
        valid_claims.append((claim, evidence))
        claim_sig = norm_signature(claim.get("value"))
        candidates = []
        for idx in unmatched:
            expected_norm = expected[idx]
            gold_sig = norm_signature(expected_norm.get("value"))
            sig_score = 1.0 if claim_sig == gold_sig else 0.0
            unit_score = 1.0 if unit_key(claim.get("unit")) == unit_key(expected_norm.get("unit")) else 0.0
            name_score = similarity(str(claim.get("name", "")), str(expected_norm.get("name", "")))
            page_match = 0.15 if evidence["page"] in [c.get("page") for c in expected_norm.get("citations", [])] else 0.0
            score = 0.55 * sig_score + 0.20 * unit_score + 0.25 * name_score + page_match
            candidates.append((score, idx, sig_score, unit_score, name_score))
        if not candidates:
            extra.append({"claim": claim, "reason": "числовая цитата подтверждена PDF, но лишний пункт отсутствует в эталоне (проверить эталон вручную)"})
            continue
        score, idx, sig_score, unit_score, name_score = max(candidates)
        if sig_score and unit_score and name_score >= 0.30 and score >= 0.77:
            unmatched.remove(idx)
            found.append({"expected": expected[idx], "claim": claim, "similarity": round(name_score, 3), "citation": evidence})
        else:
            extra.append({"claim": claim, "reason": "цитата подтверждена, но название/значение/единица не совпали с эталоном; проверить ошибку анализа или пропуск в эталоне"})
    missing = [expected[i] for i in sorted(unmatched)]
    return {"found": found, "missing": missing, "extra": extra, "rejected": rejected, "manual_visual_only": visual_manual,
            "counts": {"expected": len(expected), "found": len(found), "missing": len(missing),
                       "extra": len(extra), "rejected": len(rejected), "manual_visual_only": len(visual_manual),
                       "coverage_percent": round(100 * len(found) / len(expected), 1) if expected else 100.0}}


def compare_adjustment_steps(matched: list[dict[str, Any]]) -> list[dict[str, Any]]:
    report = []
    for pair in matched:
        expected, actual = pair["expected"], pair["actual"]
        ai_steps = [str(s) for s in actual.get("steps", [])]
        unmatched = set(range(len(ai_steps)))
        covered = []
        for step in expected.get("steps", []):
            options = [(similarity(step, ai_steps[i]), i) for i in unmatched]
            score, idx = max(options, default=(0.0, -1))
            if score >= 0.32:
                unmatched.remove(idx)
                covered.append({"expected": step, "actual": ai_steps[idx], "similarity": round(score, 3)})
            else:
                covered.append({"expected": step, "actual": None, "similarity": 0.0})
        report.append({"name": expected["name"], "steps": covered,
                       "missing_steps": sum(s["actual"] is None for s in covered),
                       "extra_steps": [ai_steps[i] for i in sorted(unmatched)]})
    return report


def markdown_report(report: dict[str, Any]) -> str:
    s = report["summary"]
    lines = [
        f"# Отчёт проверки GRN-471 — {s['run_label']}", "",
        f"- Время UTC: `{s['timestamp_utc']}`",
        f"- Модель: `{s.get('model') or 'не указана'}`; версия указаний: `{s.get('prompt_version') or 'не указана'}`",
        f"- PDF SHA-256: `{s['pdf_sha256']}`",
        "", "## Итог", "",
        f"- Узлы: найдено **{s['components_found']} / {s['components_expected']}**; пропущено {s['components_missing']}; лишних {s['components_extra']}",
        f"- Регулировки: найдено **{s['adjustments_found']} / {s['adjustments_expected']}**; пропущено {s['adjustments_missing']}; лишних {s['adjustments_extra']}",
        f"- Числовые нормы: найдено **{s['norms_found']} / {s['norms_expected']} ({s['norm_coverage_percent']}%)**; пропущено {s['norms_missing']}; лишних подтверждённых {s['norms_extra']}",
        f"- Выдуманных/неподтверждённых числовых утверждений: **{s['fabricated_or_unsupported_numbers']}**",
        f"- Цифровых утверждений, отклонённых проверкой: **{s['rejected_numbers']}** (в том числе без подтверждения в текстовом слое: {s['fabricated_or_unsupported_numbers']}; визуальные надписи без OCR: {s['manual_visual_only_numbers']})",
        f"- Не машинно проверяемых эталонных значений (картинки/извлечение): **{s['gold_non_machine_verified_norms']}**",
        "", "## Пропуски", ""
    ]
    for key, title in [("components_missing_items", "Узлы"), ("adjustments_missing_items", "Регулировки"), ("norms_missing_items", "Нормы")]:
        items = report.get(key, [])
        lines.append(f"### {title}")
        lines.extend([f"- {i.get('name', i.get('title', i.get('id', 'пункт')))}" for i in items] or ["- Нет"])
        lines.append("")
    lines += ["## Лишние пункты", ""]
    for key, title in [("components_extra_items", "Узлы"), ("adjustments_extra_items", "Регулировки"), ("norms_extra_items", "Числовые нормы")]:
        lines.append(f"### {title}")
        items = report.get(key, [])
        lines.extend([f"- {i.get('item', i.get('claim', i)).get('name', i.get('item', i.get('claim', i)).get('value', 'пункт'))}: {i.get('reason', '')}" for i in items] or ["- Нет"])
        lines.append("")
    lines += ["## Отклонённые числовые утверждения", ""]
    for item in report.get("rejected_number_items", []):
        claim = item.get("claim", {})
        lines.append(f"- `{claim.get('name','без названия')}` = `{claim.get('value','?')} {claim.get('unit','')}`; стр. {claim.get('page','—')}: {item.get('reason','')}")
    if not report.get("rejected_number_items"):
        lines.append("- Нет")
    lines += ["", "## Не прошедшие машинную проверку источника в эталоне", ""]
    for item in report.get("gold_source_failures", []):
        lines.append(f"- {item['norm_id']} стр. {item.get('page')}: {item['reason']} — `{item.get('quote','')}`")
    if not report.get("gold_source_failures"):
        lines.append("- Нет")
    lines += ["", "## Цель этапа 6", "", "Цель: выдуманных цифр — 0; покрытие норм — не ниже 90%; для каждого отклонения есть причина.",
              f"Результат этого запуска по формальным условиям: **{'ДОСТИГНУТА' if s['fabricated_or_unsupported_numbers']==0 and s['norm_coverage_percent']>=90 and s['all_rejections_have_reason'] else 'НЕ ДОСТИГНУТА'}**.",
              "Неверифицируемые сканированные/графические подписи не считаются подтверждёнными автоматически и перечисляются отдельно; это ограничение не скрывается под нулём ошибок.", ""]
    return "\n".join(lines)


def save_audit(gold: dict[str, Any], pages: list[str], pdf_path: Path, runs_dir: Path, run_label: str) -> Path:
    audit = audit_gold(gold, pages)
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report = {
        "run": {"label": run_label, "kind": "gold-source-audit", "timestamp_utc": datetime.now(timezone.utc).isoformat()},
        "pdf": {"path": str(pdf_path), "pages": len(pages), "sha256": __import__("hashlib").sha256(pdf_path.read_bytes()).hexdigest()},
        "counts": audit["counts"], "gold_norms": audit["norms"], "citation_audit": audit["citations"],
    }
    runs_dir.mkdir(parents=True, exist_ok=True)
    out = runs_dir / f"{now}_{run_label}_gold-audit.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def evaluate(args: argparse.Namespace) -> Path:
    pdf_path, gold_path, result_path = map(Path, (args.pdf, args.gold, args.result))
    pages = load_pdf_pages(pdf_path)
    gold = json.loads(gold_path.read_text(encoding="utf-8"))
    ai = json.loads(result_path.read_text(encoding="utf-8"))
    audit = audit_gold(gold, pages)
    components = ai.get("components", [])
    adjustments = ai.get("adjustments", [])
    norms = ai.get("norms", [])
    faults = ai.get("faults", [])
    comp_match, comp_missing, comp_extra = compare_named(gold.get("components", []), components, "name", threshold=0.30)
    adj_match, adj_missing, adj_extra = compare_named(gold.get("adjustments", []), adjustments, "name", threshold=0.30)
    fault_match, fault_missing, fault_extra = compare_named(gold.get("faults", []), faults, "symptom", threshold=0.28)
    norm_report = compare_norms(gold.get("norms", []), norms, pages, gold)
    rejected = norm_report["rejected"]
    extra_numbers = norm_report["extra"]
    # A numeric claim with a false/missing source quote is conservatively counted
    # as fabricated/unsupported; a page quote that proves a real but unlisted norm
    # is an extra, not fabricated, and may indicate a gap in the gold.
    fabricated = len(rejected)
    run_label = re.sub(r"[^A-Za-z0-9._-]+", "-", args.run_label or result_path.stem).strip("-") or "run"
    timestamp = datetime.now(timezone.utc).isoformat()
    sha = __import__("hashlib").sha256(pdf_path.read_bytes()).hexdigest()
    summary = {
        "run_label": run_label, "timestamp_utc": timestamp, "model": ai.get("run", {}).get("model"),
        "prompt_version": ai.get("run", {}).get("prompt_version"), "pdf_sha256": sha,
        "components_expected": len(gold.get("components", [])), "components_found": len(comp_match),
        "components_missing": len(comp_missing), "components_extra": len(comp_extra),
        "adjustments_expected": len(gold.get("adjustments", [])), "adjustments_found": len(adj_match),
        "adjustments_missing": len(adj_missing), "adjustments_extra": len(adj_extra),
        "norms_expected": norm_report["counts"]["expected"], "norms_found": norm_report["counts"]["found"],
        "norms_missing": norm_report["counts"]["missing"], "norms_extra": len(extra_numbers),
        "norm_coverage_percent": norm_report["counts"]["coverage_percent"],
        "fabricated_or_unsupported_numbers": fabricated, "rejected_numbers": len(rejected) + len(norm_report["manual_visual_only"]),
        "manual_visual_only_numbers": len(norm_report["manual_visual_only"]),
        "gold_non_machine_verified_norms": audit["counts"]["norms_manual_visual_or_text_mismatch"] + audit["counts"]["norms_failed"],
        "all_rejections_have_reason": all(bool(r.get("reason")) for r in rejected + norm_report["manual_visual_only"]),
        "faults_expected": len(gold.get("faults", [])), "faults_found": len(fault_match),
        "faults_missing": len(fault_missing), "faults_extra": len(fault_extra),
    }
    report: dict[str, Any] = {
        "summary": summary,
        "components_missing_items": comp_missing, "components_extra_items": comp_extra,
        "adjustments_missing_items": adj_missing, "adjustments_extra_items": adj_extra,
        "adjustment_step_audit": compare_adjustment_steps(adj_match),
        "norms_missing_items": norm_report["missing"], "norms_extra_items": norm_report["extra"],
        "rejected_number_items": rejected + norm_report["manual_visual_only"],
        "fabricated_or_unsupported_number_items": rejected,
        "manual_visual_only_number_items": norm_report["manual_visual_only"],
        "faults_missing_items": fault_missing, "faults_extra_items": fault_extra,
        "gold_source_failures": [c for c in audit["citations"] if c["status"] != "PASS"],
        "gold_source_audit_counts": audit["counts"],
        "matched_components": comp_match, "matched_adjustments": adj_match,
        "matched_norms": norm_report["found"], "matched_faults": fault_match,
    }
    args.runs_dir.mkdir(parents=True, exist_ok=True)
    timestamp_slug = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = args.runs_dir / f"{timestamp_slug}_{run_label}.json"
    md_path = args.runs_dir / f"{timestamp_slug}_{run_label}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(markdown_report(report), encoding="utf-8")
    history = args.runs_dir / "history.csv"
    new_header = not history.exists()
    with history.open("a", encoding="utf-8") as f:
        if new_header:
            f.write("timestamp_utc,run_label,model,prompt_version,norms_found,norms_expected,norm_coverage_percent,fabricated_or_unsupported_numbers,rejected_numbers\n")
        f.write(",".join(str(summary[k]).replace(",", ";") if summary[k] is not None else "" for k in
                          ["timestamp_utc","run_label","model","prompt_version","norms_found","norms_expected","norm_coverage_percent","fabricated_or_unsupported_numbers","rejected_numbers"]) + "\n")
    print(f"JSON: {json_path}\nОтчёт: {md_path}\nИстория: {history}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return md_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", default=str(DEFAULT_PDF), help="Путь к исходному PDF")
    parser.add_argument("--gold", default=str(DEFAULT_GOLD), help="Путь к утверждаемому эталону JSON")
    parser.add_argument("--result", help="JSON результата ИИ для сравнения")
    parser.add_argument("--run-label", default=None, help="Метка запуска; обычно модель+версия промпта")
    parser.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS, help="Папка истории запусков")
    parser.add_argument("--audit-gold", action="store_true", help="Проверить цитаты эталона на страницах PDF")
    args = parser.parse_args()
    if args.audit_gold:
        pdf_path, gold_path = Path(args.pdf), Path(args.gold)
        pages = load_pdf_pages(pdf_path)
        gold = json.loads(gold_path.read_text(encoding="utf-8"))
        out = save_audit(gold, pages, pdf_path, args.runs_dir, "grn471")
        audit = audit_gold(gold, pages)
        print(json.dumps(audit["counts"], ensure_ascii=False, indent=2))
        print(f"Подробная проверка сохранена: {out}")
        return 0
    if not args.result:
        parser.error("укажите --result analysis.json или запустите --audit-gold")
    try:
        evaluate(args)
    except (OSError, ValueError, KeyError) as exc:
        print(f"Ошибка проверки: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
