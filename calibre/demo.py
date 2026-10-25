"""Seed the explicit, source-cited GRN-471 Kolibri demo slice."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from calibre.models import (
    Adjustment,
    AppSetting,
    CommonMistake,
    Machine,
    MachineCharacteristic,
    Node,
    NodeLink,
    Norm,
    Problem,
    ProblemStep,
)

DEMO_MACHINE_CODE = "GRN-471-KOLIBRI-V4-DEMO"
DEMO_SEED_VERSION = "4"


def demo_data_path() -> Path:
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return bundle_root / "web" / "app" / "grn471-demo.json"


def load_demo_data() -> dict:
    data = json.loads(demo_data_path().read_text(encoding="utf-8"))
    if data.get("machine", {}).get("code") != DEMO_MACHINE_CODE or data.get("auto_checked_norm_count") != 59:
        raise RuntimeError("GRN-471 demo catalog failed its packaged-data integrity check")
    return data


def seed_demo_data(session: Session) -> None:
    """Create/refresh only the isolated demonstration record, never real machines."""
    setting = session.get(AppSetting, "demo_seed_version")
    existing = session.scalar(select(Machine).where(Machine.code == DEMO_MACHINE_CODE))
    legacy = session.scalar(select(Machine).where(Machine.code == "DEMO-ONLY-01"))
    if setting and setting.value == DEMO_SEED_VERSION and existing is not None:
        if legacy is not None:
            session.delete(legacy)
            session.commit()
        return
    if existing is not None:
        session.delete(existing)
        session.flush()
    if legacy is not None:
        session.delete(legacy)
        session.flush()

    data = load_demo_data()
    machine_data = data["machine"]
    machine = Machine(
        code=DEMO_MACHINE_CODE,
        manufacturer=machine_data["manufacturer"],
        model=machine_data["model"],
        machine_type=machine_data["machine_type"],
        description=machine_data["description"],
        tractor_requirements=machine_data["tractor_requirements"],
        status="published",
        version=int(DEMO_SEED_VERSION),
        is_demo=True,
    )

    node_by_key: dict[str, Node] = {}
    for item in data["nodes"]:
        map_point = item.get("map") or {}
        node = Node(
            number=item["number"], title=item["title"], purpose=item["purpose"],
            source_page=item.get("page"), source_quote=item.get("quote"),
            status="published", map_x=map_point.get("x"), map_y=map_point.get("y"),
        )
        node_by_key[item["key"]] = node
        machine.nodes.append(node)

    # The section-6 procedures are evidence-bearing content; each printed page
    # stays attached to its physical component rather than becoming a new screen.
    adjustments_by_node: dict[str, Adjustment] = {}
    for index, item in enumerate(data["adjustments"], start=1):
        node = node_by_key[item["node_key"]]
        steps = [str(step) for step in item.get("instructions", [])]
        if item.get("pages"):
            steps.append("Источник: руководство по эксплуатации, стр. " + ", ".join(map(str, item["pages"])) + ".")
        if item.get("safety_pages"):
            steps.append("Ограничения безопасности: руководство, стр. " + ", ".join(map(str, item["safety_pages"])) + ".")
        node.adjustments.append(Adjustment(
            title=item["title"], instructions=steps, tool="", status="published",
        ))

    # Each numeric fact is stored once as a Norm. The public catalog derives its
    # characteristics table from these same rows; there are no hand-copied values.
    grouped: dict[str, list[dict]] = {}
    for fact in data["norms"]:
        grouped.setdefault(fact["node_key"], []).append(fact)
    for key, facts in grouped.items():
        adjustment = Adjustment(
            title="Числовые нормы и значения из руководства",
            instructions=[], tool="", status="published",
        )
        node_by_key[key].adjustments.append(adjustment)
        for fact in facts:
            adjustment.norms.append(Norm(
                name=fact["name"], condition=fact.get("condition", ""),
                verification_note=fact.get("verification_note", ""),
                value_original=fact["value"], unit_original=fact.get("unit") or None,
                source_kind="packaged_demo", source_document_id=None,
                source_page_start=fact["page"], source_page_end=fact["page"],
                source_quote=fact["quote"], review_status="auto_verified",
            ))

    # General facts, maintenance, safety and documented gaps stay visible in the
    # existing machine-characteristics panel and remain searchable offline.
    for group_name, entries in (
        ("Из руководства", data.get("general_facts", [])),
        ("Обслуживание", data.get("maintenance", [])),
        ("Ограничение безопасности", data.get("safety_warnings", [])),
        ("Не указано в документе", data.get("document_gaps", [])),
    ):
        for entry in entries:
            page = entry.get("page")
            label = f"{group_name}: {entry['label']}" + (f" · стр. {page}" if page else "")
            machine.characteristics.append(MachineCharacteristic(
                label=label,
                display_value=entry["value"],
                source_page=page,
                source_quote=None if entry.get("human_visual_only") else entry.get("quote"),
                auto_verified=bool(entry.get("quote")) and not entry.get("human_visual_only"),
                human_verification_required=bool(entry.get("human_visual_only") or entry.get("human_review_required")),
                status="published",
            ))

    for item in data["nodes"]:
        if item.get("related_keys"):
            node = node_by_key[item["key"]]
            for target in item["related_keys"]:
                if target in node_by_key:
                    node.related_links.append(NodeLink(related_node=node_by_key[target], relation_label="Связанный узел"))

    for item in data["problems"]:
        problem = Problem(
            title=item["title"], symptom=(
                f"Проявление: {item['symptom']}\nВероятная причина: {item['cause']}"
            ),
            source_page=item["page"],
            source_quote="\n".join(f"«{quote}»" for quote in item["quotes"]),
            status="published",
        )
        problem.steps.extend([
            ProblemStep(step_number=1, instruction=item["remedy"],
                        source_page=item["page"], source_quote=item["quotes"][2],
                        linked_node=node_by_key.get(item.get("linked_node_key"))),
        ])
        machine.problems.append(problem)

    session.add(machine)
    session.merge(AppSetting(key="demo_seed_version", value=DEMO_SEED_VERSION))
    session.commit()
