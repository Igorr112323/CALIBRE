"""Source-grounded part/assembly graph and separately labelled hypotheses.

Manual-confirmed functions, assembly-level functions, and engineering
hypotheses are stored in separate fields; visual/name-based interpretations
are never represented as manufacturer-confirmed facts.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import pdfplumber

from calibre.part_hypotheses import assess_part

CATALOG_SHA256 = "85fbee1fe620910860161bfc582b2c6c5a2f724953af5412d459de69a2a768de"
CATALOG_PAGE_RANGE = [34, 56]
GUIDE_PAGE_RANGE = [4, 33]

# Official catalogue drawings/specification tables. The specification sheets
# are followed by an alphabetical/numeric index, which is not another set of
# unique physical parts.
EXPECTED_POSITIONS = {1: 36, 3: 11, 4: 12, 5: 13, 6: 10, 7: 7, 8: 8, 9: 27}

FIGURES: dict[int, dict[str, Any]] = {
    1: {"name": "Грабли ГРН-471.00.000 — общий вид", "code": "ГРН-471.00.000", "figure_refs": [1, 2], "table_pages": [38, 39], "drawing_pages": [36, 37], "parent_figure": None, "role_id": "machine"},
    3: {"name": "Навеска", "code": "ГРН-471.04.000", "table_pages": [41], "drawing_pages": [40], "parent_figure": 1, "role_id": "hitch"},
    4: {"name": "Каретка", "code": "ГРН-471.02.000", "table_pages": [43], "drawing_pages": [42], "parent_figure": 1, "role_id": "carriage"},
    5: {"name": "Колесо каретки", "code": "ГРН-471.02.060", "table_pages": [45], "drawing_pages": [44], "parent_figure": 4, "role_id": "wheel"},
    6: {"name": "Граблина", "code": "ГРП-811.05.03.030", "table_pages": [47], "drawing_pages": [46], "parent_figure": 1, "role_id": "tine"},
    7: {"name": "Контрпривод", "code": "ГРН-471.00.060", "table_pages": [49], "drawing_pages": [48], "parent_figure": 1, "role_id": "counterdrive"},
    8: {"name": "Ограждение правое", "code": "ГРН-471.05.000", "table_pages": [51], "drawing_pages": [50], "parent_figure": 1, "role_id": "guard"},
    9: {"name": "Ограждение левое", "code": "ГРН-471.06.000А", "table_pages": [53, 54], "drawing_pages": [52], "parent_figure": 1, "role_id": "left_guard"},
}

# These are controlled, source-audited statements about assembly/component
# duties. The short exact quote is checked against the supplied manual pages at
# import time; a mismatch aborts indexing instead of silently changing meaning.
ROLE_EVIDENCE: dict[str, dict[str, Any]] = {
    "machine": {"name": "Грабли в сборе", "page": 5,
                "quote": "Грабли предназначены для сгребания трав из прокосов в валки, оборачивания и сдваивания валков сена.",
                "responsibility": "Машина сгребает траву в валки, оборачивает валки и сдваивает их."},
    "hitch": {"name": "Навеска", "page": 10,
              "quote": "Посредством навески 5 производится навешивание граблей на трактор.",
              "responsibility": "Навешивает грабли на трактор; соединена с рамой шарнирно."},
    "carriage": {"name": "Каретка", "page": 10,
                 "quote": "Каретка предназначена для передвижения граблей при работе, регулировки положения граблин относительно поверхности поля, и является опорой для ротора.",
                 "responsibility": "Обеспечивает опору ротора, перемещение граблей при работе и регулировку положения граблин относительно поля."},
    "wheel": {"name": "Колесо каретки", "page": 10,
              "quote": "Каретка предназначена для передвижения граблей при работе, регулировки положения граблин относительно поверхности поля, и является опорой для ротора.",
              "responsibility": "Руководство описывает каретку в целом: перемещение граблей при работе, регулировка положения граблин и опора ротора. Каталог относит деталь к колёсному узлу каретки, но отдельную функцию именно этого колеса руководство не устанавливает.",
              "support_level": "assembly_context_only"},
    "tine": {"name": "Граблина", "page": 7,
             "quote": "При вращении, граблины захватывают скошенную массу или валки сена и отбрасывают их на левую сторону.",
             "responsibility": "Рабочий зуб захватывает скошенную массу или валок и перемещает его влево."},
    "counterdrive": {"name": "Контрпривод", "page": 7,
                      "quote": "Привод ротора от трактора осуществляется через карданные валы 7, 10 и контрпривод 4.",
                      "responsibility": "Участвует в передаче привода от трактора к ротору совместно с карданными валами."},
    "guard": {"name": "Ограждения", "page": 10,
              "quote": "предназначены для обозначения зоны вращающихся частей ротора.",
              "responsibility": "Обозначают зону вращающихся частей; руководство также указывает, что они придают раме дополнительную жёсткость."},
    "left_guard": {"name": "Левое ограждение и отражатель", "page": 11,
                   "quote": "Левое ограждение оснащено отражателем 7, который обеспечивает укладку сена в валок необходимой ширины.",
                   "responsibility": "Левое ограждение несёт отражатель, который формирует валок нужной ширины."},
}

# Full manual component inventory from the operator's manual. Components that
# only appear in the illustration/name list are marked as undocumented; this
# is deliberately different from guessing a function from their name.
MANUAL_COMPONENTS = [
    {"name": "Грабли в сборе / общий вид", "pages": [5, 7], "role_id": "machine"},
    {"name": "Отражатель (дефлектор валка)", "pages": [5, 7, 11], "role_id": "reflector"},
    {"name": "Ограждение левое", "pages": [5, 7, 10, 11], "role_id": "left_guard"},
    {"name": "Ограждение правое", "pages": [5, 7, 10, 11], "role_id": "guard"},
    {"name": "Каретка опорная", "pages": [5, 7, 10], "role_id": "carriage"},
    {"name": "Контрпривод", "pages": [5, 7, 10], "role_id": "counterdrive"},
    {"name": "Навеска", "pages": [5, 7, 10, 21], "role_id": "hitch"},
    {"name": "Опора / парковочная стойка", "pages": [5], "role_id": None},
    {"name": "Валы карданные (тракторный и межмашинный привод)", "pages": [5, 7, 12, 21, 22, 26, 27], "role_id": "cardan"},
    {"name": "Распорка транспортная", "pages": [5, 7, 11, 23, 24], "role_id": "transport_brace"},
    {"name": "Рама", "pages": [5, 7, 10], "role_id": "frame"},
    {"name": "Ротор", "pages": [5, 7, 8, 9, 10], "role_id": "rotor"},
    {"name": "Редуктор ротора", "pages": [8, 10, 26, 30], "role_id": "gearbox"},
    {"name": "Штанги ротора", "pages": [7, 8, 9, 10], "role_id": "rotor_arms"},
    {"name": "Граблины / пружинные зубья", "pages": [5, 7, 8, 9, 10, 11, 12, 21, 23, 30], "role_id": "tine"},
    {"name": "Ролики и кольцевая направляющая дорожка ротора", "pages": [8, 9], "role_id": "guide_track"},
    {"name": "Каретка: балка, стойки, винты, коромысла, колёса, обойма", "pages": [10], "role_id": "carriage_parts"},
    {"name": "Ограждения: телескопический механизм, фиксатор, распорка, стопор", "pages": [10, 11], "role_id": "guard_lock"},
    {"name": "Кронштейн крепления/перемещения отражателя", "pages": [11], "role_id": "reflector_bracket"},
    {"name": "Пульт управления: гидрораспределитель и ВОМ трактора", "pages": [20, 23], "role_id": "controls"},
]

COMPONENT_EVIDENCE: dict[str, dict[str, Any]] = {
    "reflector": {"page": 11, "quote": "Левое ограждение оснащено отражателем 7, который обеспечивает укладку сена в валок необходимой ширины.",
                  "responsibility": "Укладывает сено в валок; его перемещением регулируют ширину валка."},
    "cardan": {"page": 7, "quote": "Привод ротора от трактора осуществляется через карданные валы 7, 10 и контрпривод 4.",
               "responsibility": "Передаёт привод от трактора в цепи привода ротора; руководство перечисляет в этой цепи два карданных вала и контрпривод."},
    "transport_brace": {"page": 23, "quote": "Перед подъемом граблей в транспортное положение зафиксировать распорку 1 в транспортном положении (рисунок 6.1 А)!",
                         "responsibility": "Перед подъёмом граблей в транспортное положение распорку следует зафиксировать в транспортном положении. Подробную механику детали руководство не описывает."},
    "frame": {"page": 10, "quote": "Рама 11 (рисунок 1.1) представляет собой сварную конструкцию и является несущим элементом машины.",
              "responsibility": "Несущий элемент; на раме закреплены ротор, ограждения и контрпривод."},
    "rotor": {"page": 7, "quote": "Рабочим органом граблей является ротор 12, с помощью которого выполняются все технологические операции, выполняемые граблями.", "responsibility": "Главный рабочий орган, выполняющий все технологические операции граблей."},
    "gearbox": {"page": 8, "quote": "Внутри одноступенчатого конического редуктора на неподвижном корпусе встроена кольцевая направляющая дорожка для роликов, жестко соединенных со штангами 2. Этим обеспечивается поворот штанг относительно своей оси симметрии, и соответственно осуществляется подъём и опускание в определенный момент граблин, закрепленных на штангах.", "responsibility": "Корпус редуктора содержит кольцевую направляющую дорожку; она задаёт поворот штанг и своевременный подъём/опускание закреплённых граблин."},
    "rotor_arms": {"page": 8, "quote": "Этим обеспечивается поворот штанг относительно своей оси симметрии, и соответственно осуществляется подъём и опускание в определенный момент граблин, закрепленных на штангах.", "responsibility": "Штанги поворачиваются по направляющей дорожке; это обеспечивает подъём и опускание закреплённых граблин."},
    "guide_track": {"page": 8, "quote": "Внутри одноступенчатого конического редуктора на неподвижном корпусе встроена кольцевая направляющая дорожка для роликов, жестко соединенных со штангами 2. Этим обеспечивается поворот штанг относительно своей оси симметрии, и соответственно осуществляется подъём и опускание в определенный момент граблин, закрепленных на штангах.", "responsibility": "Кольцевая дорожка направляет соединённые со штангами ролики; это обеспечивает поворот штанг и подъём/опускание граблин."},
    "carriage_parts": {"page": 10, "quote": "Каретка состоит из балки 5, стоек 3, винтов 6, коромысел 2 и колес 1 (рисунок 2.4).", "responsibility": "Руководство перечисляет балку, стойки, винты, коромысла и колёса как состав каретки, но не раскрывает отдельную функцию каждого элемента."},
    "carriage_screw": {"page": 10, "quote": "Винтами 6 регулируется расстояние от граблин до поверхности земли.", "responsibility": "Регулирует расстояние от граблин до поверхности земли."},
    "guard_lock": {"page": 11, "quote": "ограждения фиксируются с помощью телескопического механизма, состоящего из фиксатора 3, распорки 2 и стопора 4. При транспортировании граблей в агрегате с трактором ограждения должны быть подняты в вертикальное положение. В рабочем положении граблей они должны быть зафиксированы в горизонтальном положении.", "responsibility": "Телескопический механизм совместно фиксирует ограждения в рабочем и транспортном положениях; руководство не распределяет отдельные действия между фиксатором, распоркой и стопором."},
    "reflector_bracket": {"page": 11, "quote": "Расположение отражателя относительно торца граблин регулируется перемещением кронштейна 6 относительно ограждения и фиксируется стопором 4.", "responsibility": "Перемещает/удерживает отражатель относительно ограждения; положение фиксируется стопором."},
    "controls": {"page": 20, "quote": "Грабли управляются из кабины трактора рукоятками гидрораспределителя и включения и отключения ВОМ трактора.", "responsibility": "Рукоятки гидрораспределителя управляют граблями, а включение/отключение ВОМ подаёт привод; детали органов управления в каталоге узлового чертежа не разложены."},
}

ROLE_HINTS = {
    "Граблина": "tine", "Граблины": "tine", "ГРП-811.05.03.030": "tine",
    "Отражатель": "reflector", "ГРН-471.06.070": "reflector",
    "Контрпривод": "counterdrive", "ГРН-471.00.060": "counterdrive",
    "Каретка": "carriage", "ГРН-471.02.000": "carriage",
    "Навеска": "hitch", "ГРН-471.04.000": "hitch",
    "Ограждение правое": "guard", "Ограждение левое": "left_guard",
    "Рама": "frame", "ГРН-471.03.000А": "frame",
    "Вал карданный": "cardan", "Карданный вал": "cardan",
}


def _space(value: Any) -> str:
    value = re.sub(r"\s+", " ", str(value or "")).strip()
    value = re.sub(r"\s+([,.;:!?])", r"\1", value)
    value = re.sub(r"([([{])\s+", r"\1", value)
    value = re.sub(r"\s+([)\]}])", r"\1", value)
    return value


def _quote_matches(quote: str, page_text: str) -> bool:
    # PDF text extraction occasionally inserts spaces inside printed words
    # (for example, "о рганом"). Ignore whitespace only for validation; keep
    # the user-facing quote readable and do not alter any words or numbers.
    return re.sub(r"\s+", "", _space(quote)).casefold() in re.sub(r"\s+", "", _space(page_text)).casefold()


def _verified_quote(role_id: str, manual_pages: dict[int, str]) -> tuple[int, str] | None:
    role = COMPONENT_EVIDENCE.get(role_id) or ROLE_EVIDENCE.get(role_id)
    if role is None:
        return None
    page_no = int(role["page"])
    quote = _space(role["quote"])
    page_text = _space(manual_pages.get(page_no, ""))
    if not _quote_matches(quote, page_text):
        raise ValueError(f"Source quote for {role_id} not found on PDF page {page_no}: {quote}")
    return page_no, quote


def _find_catalog_quote(part: dict[str, Any], page_text: str) -> str | None:
    lines = (page_text or "").splitlines()
    start_re = re.compile(rf"^{int(part['position'])}\s+")
    for index, raw in enumerate(lines):
        line = raw.strip()
        if not start_re.match(line):
            continue
        block = [line]
        for following in lines[index + 1:index + 4]:
            candidate = following.strip()
            if re.match(r"^\d+\s+", candidate):
                break
            block.append(candidate)
        quote = _space(" ".join(block))
        if (_quote_matches(part["name"], quote)
                and (not part.get("designation") or _quote_matches(part["designation"], quote))):
            return quote
    return None


def _table_rows(pdf_path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for figure_no, spec in FIGURES.items():
            for page_no in spec["table_pages"]:
                table_list = pdf.pages[page_no - 1].extract_tables()
                if len(table_list) != 1:
                    raise ValueError(f"Expected exactly one parts table on PDF page {page_no}; got {len(table_list)}")
                table = table_list[0]
                if not table or "Номер" not in _space(table[0][1]):
                    raise ValueError(f"Unexpected catalogue table header on PDF page {page_no}")
                for row in table[1:]:
                    if len(row) < 5 or not _space(row[1]).isdigit():
                        continue
                    position = int(_space(row[1]))
                    designation = _space(row[2])
                    name = _space(row[3])
                    quantity = _space(row[4])
                    note = _space(row[5] if len(row) > 5 else "")
                    if not name or not quantity:
                        raise ValueError(f"Incomplete catalogue row at page {page_no}, figure {figure_no}, position {position}")
                    figure_refs = list(spec.get("figure_refs", [figure_no]))
                    figure_label = ",".join(str(value) for value in figure_refs)
                    rows.append({
                        "part_id": f"fig-{figure_label.replace(',', '-')}-pos-{position}",
                        "figure": figure_no,
                        "figure_refs": figure_refs,
                        "figure_pages": list(spec["drawing_pages"]),
                        "position": position,
                        "designation": designation or None,
                        "name": name,
                        "quantity": quantity,
                        "catalog_page": page_no,
                        "note": note,
                        "assembly": spec["name"],
                        "assembly_code": spec["code"],
                        "parent_figure": spec["parent_figure"],
                        "role_id": spec["role_id"],
                    })
    return rows


def build_part_graph(pdf_path: str | Path, page_texts: list[str], sha256: str | None = None) -> dict[str, Any]:
    """Build and self-audit a source-grounded GRN-471 component/parts graph."""
    pdf_path = Path(pdf_path)
    actual_hash = sha256 or hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    if actual_hash != CATALOG_SHA256:
        raise ValueError("This reviewed draft applies only to the exact Kolibri GRN-471 v4 PDF fingerprint.")
    if len(page_texts) != 56:
        raise ValueError(f"Expected the 56-page GRN-471 v4 PDF, got {len(page_texts)} pages")
    manual_pages = {n: page_texts[n - 1] for n in range(1, len(page_texts) + 1)}

    role_evidence: dict[str, dict[str, Any]] = {}
    role_ids = ({item["role_id"] for item in MANUAL_COMPONENTS if item["role_id"]}
                | {spec["role_id"] for spec in FIGURES.values()}
                | set(ROLE_HINTS.values()) | {"carriage_screw"})
    for role_id in role_ids:
        verified = _verified_quote(role_id, manual_pages)
        role = COMPONENT_EVIDENCE.get(role_id) or ROLE_EVIDENCE.get(role_id)
        if verified:
            role_evidence[role_id] = {"status": role.get("support_level", "verified_in_manual"), "page": verified[0],
                                      "quote": verified[1], "responsibility": role["responsibility"]}
        else:
            role_evidence[role_id] = {"status": "not_individually_described", "page": None,
                                      "quote": None, "responsibility": None}

    parts = _table_rows(pdf_path)
    figure_keys = {figure for item in parts for figure in item["figure_refs"]}
    if figure_keys != set(range(1, 10)):
        raise ValueError(f"Catalog figure coverage mismatch: found {sorted(figure_keys)}")
    if len(parts) != 124:
        raise ValueError(f"Expected 124 catalogue positions; parsed {len(parts)}")
    for figure, expected_count in EXPECTED_POSITIONS.items():
        positions = sorted(part["position"] for part in parts if part["figure"] == figure)
        expected = list(range(1, expected_count + 1))
        if positions != expected:
            raise ValueError(f"Position coverage mismatch for figure {figure}: found {positions}")
    for part in parts:
        catalog_quote = _find_catalog_quote(part, manual_pages.get(part["catalog_page"], ""))
        if not catalog_quote:
            raise ValueError(f"Could not verify catalogue row quote: {part['part_id']} on page {part['catalog_page']}")
        part["catalog_quote"] = catalog_quote

    for part in parts:
        role_id = part["role_id"]
        name = part["name"]
        direct_role = "carriage_screw" if part["figure"] == 4 and "винт" in name.casefold() else None
        for marker, candidate_role in ROLE_HINTS.items():
            if direct_role:
                break
            if marker.casefold() in name.casefold() or (part["designation"] and marker.casefold() in part["designation"].casefold()):
                direct_role = candidate_role
                break
        if direct_role and role_evidence.get(direct_role, {}).get("status") == "verified_in_manual":
            part["individual_role_status"] = "explicit_or_component_named_in_manual"
            part["individual_role_id"] = direct_role
            part["individual_role"] = role_evidence[direct_role]["responsibility"]
            part["individual_role_source"] = {key: role_evidence[direct_role][key] for key in ("page", "quote")}
        elif (part["figure"] != 1
              and role_evidence.get(role_id, {}).get("status") in {"verified_in_manual", "assembly_context_only"}):
            part["individual_role_status"] = "assembly_role_only"
            part["individual_role_id"] = role_id
            part["individual_role"] = role_evidence[role_id]["responsibility"]
            part["individual_role_source"] = {key: role_evidence[role_id][key] for key in ("page", "quote")}
            part["individual_role_caveat"] = "Эта цитата описывает узел целиком; индивидуальная функция данной позиции отдельно не подтверждена."
        else:
            part["individual_role_status"] = "catalog_membership_only"
            part["individual_role_id"] = None
            part["individual_role"] = "Индивидуальное назначение в тексте руководства не установлено; каталог подтверждает только название, обозначение, позицию, количество и принадлежность к указанной сборке."
            part["individual_role_source"] = None
        part["expert_assessment"] = assess_part(part)

    component_records = []
    for item in MANUAL_COMPONENTS:
        evidence = role_evidence.get(item["role_id"]) if item["role_id"] else None
        component_records.append({
            **item,
            "function_status": evidence["status"] if evidence else "not_described_in_manual",
            "responsibility": evidence["responsibility"] if evidence else None,
            "source": ({"page": evidence["page"], "quote": evidence["quote"]}
                       if evidence and evidence["page"] else None),
            "instruction": ("Не додумывать: руководство перечисляет компонент на схеме, но отдельно не описывает его функцию."
                            if not evidence or evidence["status"] != "verified_in_manual" else None),
        })

    assemblies = []
    for figure, metadata in FIGURES.items():
        evidence = role_evidence[metadata["role_id"]]
        assemblies.append({"figure": figure, **metadata, "responsibility_status": evidence["status"],
                           "responsibility": evidence["responsibility"],
                           "source": {"page": evidence["page"], "quote": evidence["quote"]}
                           if evidence["page"] else None,
                           "positions": sum(1 for part in parts if part["figure"] == figure)})

    status_counts: dict[str, int] = {}
    for part in parts:
        key = part["individual_role_status"]
        status_counts[key] = status_counts.get(key, 0) + 1
    blank_codes = sum(1 for part in parts if not part["designation"])
    return {
        "schema_version": 2,
        "record_status": "draft_for_owner_review_not_for_mobile_publication",
        "machine": {"manufacturer": "Kolibri", "model": "ГРН-471", "machine_type": "грабли роторные навесные",
                    "manual_version": 4, "sha256": actual_hash},
        "source_scope": {"guide_pdf_pages": GUIDE_PAGE_RANGE, "parts_catalog_pdf_pages": CATALOG_PAGE_RANGE,
                         "parts_catalog_excluded_from_adjustment_norms": True},
        "extraction": {"tool": "pdfplumber table extraction", "positions_total": len(parts),
                       "figures": sorted(figure_keys), "unique_nonblank_designations": len({p["designation"] for p in parts if p["designation"]}),
                       "blank_designations": blank_codes, "role_status_counts": status_counts,
                       "expert_assessment_count": sum(bool(p.get("expert_assessment")) for p in parts),
                       "unique_designations_are_not_unique_instances": True},
        "assemblies": assemblies,
        "manual_components": component_records,
        "parts": parts,
        "known_catalog_gaps": [
            {"component": "Опора / парковочная стойка", "manual_page": 5,
             "status": "named_on_overall_illustration_but_no_separate_catalog_position_found",
             "instruction": "Не назначать каталожный номер без дополнительного источника."},
            {"component": "Распорка транспортная", "manual_pages": [5, 23, 24],
             "status": "described_in_manual_but_not_found_as_a_separate_row_in_extracted_figures_1_to_9",
             "instruction": "Не приравнивать её автоматически к распорке телескопического механизма ограждения."},
        ],
    }


def validate_part_graph(graph: dict[str, Any], page_texts: list[str]) -> dict[str, Any]:
    """Strict audit of counts, stable IDs, parent assemblies, roles and citations."""
    errors: list[str] = []
    parts = graph.get("parts", [])
    if len(parts) != 124:
        errors.append(f"expected 124 positions, got {len(parts)}")
    ids = [part.get("part_id") for part in parts]
    if len(ids) != len(set(ids)):
        errors.append("duplicate figure-position IDs")
    for part in parts:
        if part.get("figure") not in FIGURES or not part.get("position") or not part.get("name"):
            errors.append(f"incomplete identity: {part.get('part_id')}")
        if part.get("catalog_page") not in range(34, 57):
            errors.append(f"catalog source outside pp.34-56: {part.get('part_id')}")
        if part.get("individual_role_status") not in {
            "explicit_or_component_named_in_manual", "assembly_role_only", "catalog_membership_only"
        }:
            errors.append(f"missing role confidence state: {part.get('part_id')}")
        catalog_quote = part.get("catalog_quote")
        if not catalog_quote or not _quote_matches(catalog_quote, page_texts[part["catalog_page"] - 1]):
            errors.append(f"bad catalogue row citation: {part.get('part_id')}")
        source = part.get("individual_role_source")
        if source:
            page = source.get("page")
            quote = _space(source.get("quote"))
            if not isinstance(page, int) or not (1 <= page <= len(page_texts)) or not _quote_matches(quote, page_texts[page - 1]):
                errors.append(f"bad function citation: {part.get('part_id')}")
        assessment = part.get("expert_assessment")
        needs_assessment = part.get("individual_role_status") != "explicit_or_component_named_in_manual"
        if needs_assessment and (not assessment or assessment.get("status") != "engineering_hypothesis"
                                 or assessment.get("manufacturer_confirmed") is not False
                                 or assessment.get("confidence") not in {"low", "medium", "high"}
                                 or not assessment.get("hypothesis") or not assessment.get("basis")):
            errors.append(f"missing/unsafe expert hypothesis: {part.get('part_id')}")
        if not needs_assessment and assessment is not None:
            errors.append(f"manual-confirmed position has redundant hypothesis: {part.get('part_id')}")
        if assessment:
            drawing_pages = assessment.get("drawing_pages") or []
            if drawing_pages != part.get("figure_pages", []) or any(
                    not isinstance(page, int) or not (1 <= page <= len(page_texts))
                    or not re.search(rf"Рисунок\s+[12]\b" if part["figure"] == 1
                                     else rf"Рисунок\s+{part['figure']}\b",
                                     page_texts[page - 1], re.IGNORECASE)
                    for page in drawing_pages):
                errors.append(f"bad exploded-view citation: {part.get('part_id')}")
    for component in graph.get("manual_components", []):
        source = component.get("source")
        if component.get("function_status") == "verified_in_manual":
            if not source:
                errors.append(f"verified component has no source: {component.get('name')}")
            else:
                page = source["page"]
                if not _quote_matches(source["quote"], page_texts[page - 1]):
                    errors.append(f"component quote does not match PDF: {component.get('name')}")
        elif component.get("responsibility"):
            errors.append(f"undocumented component incorrectly has a duty: {component.get('name')}")
    return {"positions_total": len(parts), "unique_position_ids": len(set(ids)),
            "manual_components_total": len(graph.get("manual_components", [])),
            "manual_components_verified": sum(c.get("function_status") == "verified_in_manual" for c in graph.get("manual_components", [])),
            "manual_components_explicitly_unknown": sum(c.get("function_status") != "verified_in_manual" for c in graph.get("manual_components", [])),
            "role_status_counts": graph.get("extraction", {}).get("role_status_counts", {}),
            "catalog_pages_outside_scope": sum(not (34 <= part.get("catalog_page", 0) <= 56) for part in parts),
            "errors": errors,
            "passed": not errors}
