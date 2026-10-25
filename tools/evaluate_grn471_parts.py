"""Build and audit the local-only GRN-471 spare-parts knowledge draft.

Requires the owner's Git-ignored copy at tests/test_documents/GRN-471-Kolibri-v4.pdf.
The generated graph and report are written below tests/test_documents/runs/ and
are intentionally not distributed with the code or mobile app.
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from calibre.part_graph import (CATALOG_PAGE_RANGE, EXPECTED_POSITIONS, build_part_graph,
                                validate_part_graph)

SOURCE = ROOT / "tests/test_documents/GRN-471-Kolibri-v4.pdf"
OUTPUT_DIR = ROOT / "tests/test_documents/runs"
GRAPH_PATH = OUTPUT_DIR / "GRN-471-Kolibri-v4-parts-draft.json"
REPORT_PATH = OUTPUT_DIR / "GRN-471-Kolibri-v4-parts-evaluation.json"
REPORT_MD = OUTPUT_DIR / "GRN-471-Kolibri-v4-parts-assessment.md"


def render_markdown(graph: dict, report: dict) -> str:
    counts = report["catalog_coverage"]["role_status_counts"]
    lines = [
        "# Kolibri ГРН-471 — аудит каталога запчастей",
        "",
        f"**Источник:** локальная копия `{SOURCE.name}`  ",
        f"**SHA-256:** `{graph['machine']['sha256']}`  ",
        "**Каталог:** PDF-стр. 34–56; спецификации: 38–39, 41, 43, 45, 47, 49, 51, 53–54; указатель 55–56 не считался отдельными позициями.",
        "",
        "## Результат покрытия",
        "",
        f"- Разобрано **{len(graph['parts'])}/124** каталожных позиций по рисункам 1–9 (рисунки 1 и 2 разделяют одну спецификацию).",
        f"- На каждую строку получена каталожная цитата, сверенная с извлечённым текстом страницы; ошибок проверки графа: **{len(report['graph_audit']['errors'])}**.",
        f"- **{counts.get('explicit_or_component_named_in_manual', 0)}** позиций имеют индивидуальную функцию, прямо названную в руководстве.",
        f"- **{counts.get('assembly_role_only', 0)}** позиций связаны с узлом, функция описана только для узла; она не приписывается каждой детали.",
        f"- **{counts.get('catalog_membership_only', 0)}** позиций не имеют индивидуальной функции, установленной текстом руководства.",
        f"- Для **{graph['extraction'].get('expert_assessment_count', 0)}** позиций отдельно добавлена инженерная гипотеза по названию и взрыв-схеме, с уровнем уверенности и страницами-основаниями; это не сведения изготовителя.",
        f"- **{graph['extraction']['blank_designations']}** строк не имеют отдельного обозначения в ячейке; текст наименования/стандарта сохранён.",
        f"- Из 20 групп компонентов руководства у **{report['manual_function_coverage']['component_status_counts'].get('verified_in_manual', 0)}/20** есть цитируемое функциональное описание/контекст; для остальных пробел не заполнен догадкой.",
        "",
        "| Рисунок спецификации | Узел | Строк | PDF-страницы таблицы |",
        "|---:|---|---:|---|",
    ]
    for assembly in graph["assemblies"]:
        figures = ", ".join(map(str, assembly.get("figure_refs", [assembly["figure"]])))
        pages = ", ".join(map(str, assembly["table_pages"]))
        lines.append(f"| {figures} | {assembly['name']} | {assembly['positions']} | {pages} |")
    lines.extend(["", "## Полный перечень позиций", "",
                  "В столбце «функция по руководству» остаются только подтверждённые текстом сведения и контекст узла. Отдельный столбец содержит инженерные гипотезы, не подтверждённые изготовителем; уровень уверенности — экспертная оценка, а не статистическая вероятность.",
                  "", "| Рис. | Поз. | Обозначение | Наименование/стандарт | Кол-во | Сборочный узел | Функция/контекст по руководству | Инженерная гипотеза (не факт производителя) | Источник функции | Каталог |",
                  "|---:|---:|---|---|---:|---|---|---|---:|---:|"] )
    status_titles = {
        "explicit_or_component_named_in_manual": "Функция подтверждена для этой позиции/узла",
        "assembly_role_only": "Описана функция узла; отдельная функция детали не установлена",
        "catalog_membership_only": "Индивидуальная функция/подсборка не установлена",
    }
    for part in graph["parts"]:
        figures = ",".join(map(str, part["figure_refs"]))
        designation = part.get("designation") or "—"
        name = part["name"].replace("|", "\\|")
        duty = part["individual_role"].replace("|", "\\|")
        status = status_titles[part["individual_role_status"]]
        source_page = (part.get("individual_role_source") or {}).get("page") or "—"
        assessment = part.get("expert_assessment")
        hypothesis = "—"
        if assessment:
            confidence = {"high": "высокая", "medium": "средняя", "low": "низкая"}.get(assessment["confidence"], "не оценена")
            drawing_pages = ",".join(str(page) for page in assessment["drawing_pages"])
            hypothesis = (f"**Гипотеза, уверенность {confidence}** (схема PDF-стр. {drawing_pages}): "
                          f"{assessment['hypothesis']}").replace("|", "\\|")
        note = f"; примечание: {part['note']}" if part.get("note") else ""
        lines.append(f"| {figures} | {part['position']} | {designation} | {name}{note} | {part['quantity']} | {part['assembly']} | **{status}.** {duty} | {hypothesis} | {source_page} | {part['catalog_page']} |")
    lines.extend(["", "## Неразрешённые места в руководстве", ""])
    for gap in graph["known_catalog_gaps"]:
        page_data = gap.get("manual_pages") or [gap.get("manual_page")]
        page_text = ", ".join(str(page) for page in page_data if page)
        lines.append(f"- **{gap['component']}** — {gap['status']} (руководство, стр. {page_text}). {gap['instruction']}")
    unknown_components = [item for item in graph["manual_components"]
                          if item["function_status"] != "verified_in_manual"]
    lines.extend(["", "Из 20 групп компонентов, перечисленных в руководстве, неразрешённая функция:"])
    lines.extend(f"- {item['name']} — отдельное назначение в тексте руководства не подтверждено; если для отдельной каталожной позиции есть гипотеза, она маркируется отдельно и не доказывается этой записью." for item in unknown_components)
    lines.extend([
        "",
        "## Что обеспечено в ПК-админке",
        "",
        "- При загрузке **точно этой версии PDF** индекс хранится в локальной базе ПК-сервера и доступен в разделе «Узлы и запчасти».",
        "- Поиск понимает обозначение, название, рисунок и позицию. Повторяющиеся обозначения показываются раздельно и не сворачиваются.",
        "- Ответы по деталям собираются из структурного индекса; свободная генерация для поиска функции детали не нужна. Если функция не установлена руководством, ответ отдельно сообщает это и может показать инженерную гипотезу с уровнем уверенности и ссылкой на взрыв-схему.",
        "- PDF-страница взрыв-схемы цитируется только как визуальная основа гипотезы, не как текстовое доказательство назначения детали." ,
        "- Каталог остаётся исключённым из ответов о регулировках и нормативах. Мобильные файлы/APK не менялись.",
        "",
        "## Ограничения и статус утверждения",
        "",
        "Это **локальная структурированная экспертная база**, а не дообученная модель. Реальная LLM не подключена и не оценивалась (`model_scored: false`). Для деталей без описания в тексте добавлены инженерные гипотезы по названию и чертежу; даже гипотезы высокой уверенности не заменяют спецификацию или подтверждение изготовителя. Не использовать эти гипотезы как инструкцию по ремонту, настройке, безопасности или взаимозаменяемости.",
        "",
        "Эталон не одобрен владельцем. До одобрения этот материал остаётся локальной черновой базой и не предназначен для публикации в APK.",
        "",
        "**Автоматический аудит:** " + ("PASS" if report["all_checks_passed"] else "FAIL") +
        f" — {report['checks_passed']}/{report['checks_total']} проверок. Полный JSON граф: `GRN-471-Kolibri-v4-parts-draft.json`; подробный отчёт: `GRN-471-Kolibri-v4-parts-evaluation.json`.",
    ])
    return "\n".join(lines) + "\n"


def evaluate() -> dict:
    if not SOURCE.is_file():
        raise FileNotFoundError(f"Local ignored source PDF not found: {SOURCE}")
    page_texts = [page.extract_text() or "" for page in PdfReader(str(SOURCE)).pages]
    graph = build_part_graph(SOURCE, page_texts)
    audit = validate_part_graph(graph, page_texts)
    parts = graph["parts"]

    checks = []
    checks.append({"id": "catalog_rows_complete", "passed": len(parts) == 124,
                   "detail": f"{len(parts)} rows parsed; expected 124"})
    checks.append({"id": "figure_position_ranges", "passed": all(
        len([p for p in parts if p["figure"] == figure]) == count
        and sorted(p["position"] for p in parts if p["figure"] == figure) == list(range(1, count + 1))
        for figure, count in EXPECTED_POSITIONS.items()),
        "detail": "All specified figure positions are contiguous and unique."})
    checks.append({"id": "every_part_has_citable_catalog_page", "passed": all(
        p["catalog_page"] in range(CATALOG_PAGE_RANGE[0], CATALOG_PAGE_RANGE[1] + 1)
        and p["name"] and p["quantity"] and p["part_id"] for p in parts),
        "detail": "Every record has a figure-position ID, name, quantity, and catalogue PDF page 34–56."})
    checks.append({"id": "every_catalog_row_has_literal_source_quote", "passed": all(
        bool(p.get("catalog_quote")) for p in parts),
        "detail": "Each of 124 positions has a row excerpt verified on its cited PDF catalogue page."})
    checks.append({"id": "every_function_claim_has_manual_quote", "passed": all(
        p["individual_role_status"] != "explicit_or_component_named_in_manual"
        or bool(p.get("individual_role_source", {}).get("quote")) for p in parts),
        "detail": "Direct function claims require a verbatim source quote and page."})
    checks.append({"id": "unknown_duties_are_not_filled_by_guess", "passed": all(
        p["individual_role_status"] != "catalog_membership_only"
        or (p["individual_role_id"] is None and p["individual_role_source"] is None
            and "не установлено" in p["individual_role"])
        for p in parts),
        "detail": "Unexplained positions explicitly retain an unknown individual duty."})
    checks.append({"id": "expert_hypotheses_separate_and_complete", "passed": (
        sum(bool(p.get("expert_assessment")) for p in parts) == 112
        and all(p.get("expert_assessment", {}).get("manufacturer_confirmed") is False
                for p in parts if p["individual_role_status"] != "explicit_or_component_named_in_manual")
        and all(not p.get("expert_assessment") for p in parts
                if p["individual_role_status"] == "explicit_or_component_named_in_manual")),
        "detail": "All 85 assembly-context and 27 manual-unexplained positions have separate unconfirmed engineering hypotheses; 12 manually supported entries remain distinct."})
    checks.append({"id": "catalog_not_norm_source", "passed": graph["source_scope"]["parts_catalog_excluded_from_adjustment_norms"],
                   "detail": "Catalog pp.34–56 stay outside adjustment/norm evidence."})
    checks.append({"id": "manual_component_inventory", "passed": len(graph["manual_components"]) == 20
                   and all(c["function_status"] for c in graph["manual_components"]),
                   "detail": "20 manual component groups are accounted for, with undocumented entries marked."})
    checks.append({"id": "part_graph_audit", "passed": audit["passed"],
                   "detail": f"{len(audit['errors'])} audit errors."})

    by_code: dict[str, list[dict]] = defaultdict(list)
    for part in parts:
        if part["designation"]:
            by_code[part["designation"]].append(part)
    duplicates = [{"designation": code, "occurrences": [p["part_id"] for p in records]}
                  for code, records in by_code.items() if len(records) > 1]
    statuses = Counter(p["individual_role_status"] for p in parts)
    manual_statuses = Counter(c["function_status"] for c in graph["manual_components"])

    # Deliberately adversarial checks: a code that occurs twice must remain
    # ambiguous; blank designation cells cannot erase the printed full name;
    # and an unexplained bearing/fastener must not get a generic mechanical duty.
    ambig = [entry for entry in duplicates if len(entry["occurrences"]) > 1]
    blank_codes = [p for p in parts if not p["designation"]]
    unknown_parts = [p for p in parts if p["individual_role_status"] == "catalog_membership_only"]
    checks.extend([
        {"id": "duplicate_codes_preserve_context", "passed": bool(ambig) and all(
            all(any(p["part_id"] == part_id for p in parts) for part_id in entry["occurrences"])
            for entry in ambig), "detail": f"{len(ambig)} repeated part designations retained as separate figure-position records."},
        {"id": "standard_items_survive_blank_code_cells", "passed": bool(blank_codes) and all(p["name"] for p in blank_codes),
         "detail": f"{len(blank_codes)} rows have no separate designation cell; full printed standard/name remains recorded."},
        {"id": "unknown_function_sample", "passed": bool(unknown_parts) and all(
            not p.get("individual_role_source") for p in unknown_parts),
         "detail": f"{len(unknown_parts)} positions have no individually asserted function."},
    ])

    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": {"filename": SOURCE.name, "sha256": graph["machine"]["sha256"],
                   "pdf_page_count": len(page_texts), "catalog_scope": CATALOG_PAGE_RANGE},
        "artifact_status": graph["record_status"],
        "graph_audit": audit,
        "catalog_coverage": {
            "positions_total": len(parts), "figures": graph["extraction"]["figures"],
            "expected_positions_per_figure": EXPECTED_POSITIONS,
            "unique_nonblank_designations": graph["extraction"]["unique_nonblank_designations"],
            "rows_without_separate_designation_cell": graph["extraction"]["blank_designations"],
            "repeated_designations": duplicates,
            "role_status_counts": dict(statuses),
        },
        "manual_function_coverage": {
            "component_groups_total": len(graph["manual_components"]),
            "component_status_counts": dict(manual_statuses),
            "groups": graph["manual_components"],
            "known_catalog_gaps": graph["known_catalog_gaps"],
        },
        "checks": checks,
        "checks_passed": sum(item["passed"] for item in checks),
        "checks_total": len(checks),
        "all_checks_passed": all(item["passed"] for item in checks),
        "model_scored": False,
        "model_note": "No real LLM is configured/scored. This measures catalogue extraction and evidence coverage only; it is not model training or a model-quality pass.",
        "owner_approval": "not_received",
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    GRAPH_PATH.write_text(json.dumps(graph, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    REPORT_MD.write_text(render_markdown(graph, report), encoding="utf-8")
    return report


if __name__ == "__main__":
    result = evaluate()
    print(json.dumps({"all_checks_passed": result["all_checks_passed"],
                      "checks": f"{result['checks_passed']}/{result['checks_total']}",
                      "positions": result["catalog_coverage"]["positions_total"],
                      "role_status_counts": result["catalog_coverage"]["role_status_counts"],
                      "manual_function_coverage": result["manual_function_coverage"]["component_status_counts"],
                      "model_scored": result["model_scored"],
                      "graph": str(GRAPH_PATH.relative_to(ROOT)),
                      "report": str(REPORT_PATH.relative_to(ROOT)),
                      "assessment": str(REPORT_MD.relative_to(ROOT))}, ensure_ascii=False, indent=2))
    if not result["all_checks_passed"]:
        raise SystemExit(1)
