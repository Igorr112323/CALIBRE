"""Safe serialization boundary: the phone receives only published, cleared content."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from calibre.models import Adjustment, Machine, Problem, Node


PUBLIC_NORM_STATES = {"auto_verified", "verified", "forced_accept"}
PUBLIC_PHOTO_RIGHTS = {"own", "manufacturer_permission"}


def _public_norm(norm):
    if norm.review_status not in PUBLIC_NORM_STATES:
        return None
    document = norm.source_document
    return {
        "id": norm.id,
        "name": norm.name,
        "condition": norm.condition,
        "value": norm.value_original,
        "unit": norm.unit_original,
        "minimum": str(norm.minimum_value) if norm.minimum_value is not None else None,
        "maximum": str(norm.maximum_value) if norm.maximum_value is not None else None,
        "tolerance": str(norm.tolerance_value) if norm.tolerance_value is not None else None,
        "source": {
            "kind": norm.source_kind,
            "document": document.title if document else (
                "Руководство по эксплуатации ГРН-471 Kolibri v4" if norm.source_kind == "packaged_demo" else None
            ),
            "page_start": norm.source_page_start,
            "page_end": norm.source_page_end,
            "quote": norm.source_quote,
            "measured_by": norm.measured_by,
            "measured_at": norm.measured_at.isoformat() if norm.measured_at else None,
        },
        "manual_accept_comment": norm.force_comment if norm.review_status == "forced_accept" else None,
        "review_status": norm.review_status,
        "auto_verified": norm.review_status == "auto_verified",
        "human_verified": norm.review_status in {"verified", "forced_accept"},
        "verification_note": norm.verification_note,
        "verification_label": (
            "Автопроверено по цитате и числу; человеком не сверено"
            if norm.review_status == "auto_verified" else
            "Проверено человеком" if norm.review_status == "verified" else
            "Принято человеком с замечанием" if norm.review_status == "forced_accept" else None
        ),
    }


def serialize_machine(machine: Machine) -> dict:
    nodes = []
    machine_photos = {photo.id: photo for photo in machine.photos}
    published_node_ids = {node.id for node in machine.nodes if node.status == "published"}
    for node in machine.nodes:
        if node.status != "published":
            continue
        published_adjustments = []
        for adjustment in node.adjustments:
            if adjustment.status != "published":
                continue
            norms = [
                public_norm
                for norm in adjustment.norms
                if (public_norm := _public_norm(norm)) is not None
            ]
            published_adjustments.append({
                "id": adjustment.id,
                "title": adjustment.title,
                "instructions": adjustment.instructions,
                "tool": adjustment.tool,
                "norms": norms,
                "department_checked": any(review.verdict in {"confirmed", "corrected"} for review in adjustment.reviews),
            })
        node_photos = list(node.photos)
        marker_photo = machine_photos.get(node.marker_photo_id)
        if marker_photo is not None and marker_photo not in node_photos:
            node_photos.append(marker_photo)
        nodes.append({
            "id": node.id,
            "number": node.number,
            "title": node.title,
            "purpose": node.purpose,
            "source_page": node.source_page,
            "source_quote": node.source_quote,
            "map": {"x": float(node.map_x) if node.map_x is not None else None,
                    "y": float(node.map_y) if node.map_y is not None else None},
            "adjustments": published_adjustments,
            "mistakes": [
                {"description": mistake.description, "consequence": mistake.consequence}
                for mistake in node.mistakes
            ],
            "related_node_ids": [
                link.related_node_id for link in node.related_links
                if link.related_node_id in published_node_ids
            ],
            "department_checked": any(review.verdict in {"confirmed", "corrected"} for review in node.reviews),
            "photos": [
                {
                    "id": photo.id,
                    "caption": photo.caption,
                    "internal_lan_only": photo.usage_rights == "internal_only",
                    "url": f"/media/{photo.id}" if photo.sync_to_phones and photo.usage_rights == "internal_only" else None,
                }
                for photo in node_photos
                if (photo.published and photo.usage_rights in PUBLIC_PHOTO_RIGHTS)
                or (photo.sync_to_phones and photo.usage_rights == "internal_only")
            ],
        })

    problems = []
    for problem in machine.problems:
        if problem.status != "published":
            continue
        steps = []
        for step in problem.steps:
            linked_node = step.linked_node
            steps.append({
                "number": step.step_number,
                "instruction": step.instruction,
                "source_page": step.source_page,
                "source_quote": step.source_quote,
                "linked_node_id": linked_node.id if linked_node and linked_node.id in published_node_ids else None,
            })
        problems.append({"id": problem.id, "title": problem.title, "symptom": problem.symptom,
                         "source_page": problem.source_page, "source_quote": problem.source_quote,
                         "steps": steps})

    characteristics = [
        {"label": spec.label, "value": spec.display_value,
         "source_page": spec.source_page, "source_quote": spec.source_quote,
         "auto_verified": spec.auto_verified, "human_verified": False if machine.is_demo else None,
         "human_verification_required": spec.human_verification_required,
         "verification_label": (
             "Автопроверено по цитате; человеком не сверено" if spec.auto_verified else
             "Требует ручной или визуальной проверки" if spec.human_verification_required else None
         )}
        for spec in machine.characteristics if spec.status == "published"
    ]
    for node in machine.nodes:
        if node.status != "published":
            continue
        for adjustment in node.adjustments:
            if adjustment.status != "published":
                continue
            for norm in adjustment.norms:
                public_norm = _public_norm(norm)
                if public_norm is None:
                    continue
                characteristics.append({
                    "label": public_norm["name"] or adjustment.title,
                    "value": " ".join(part for part in (
                        public_norm["value"], public_norm["unit"], public_norm["condition"]
                    ) if part),
                    "source_page": public_norm["source"]["page_start"],
                    "source_quote": public_norm["source"]["quote"],
                    "auto_verified": public_norm["auto_verified"],
                    "human_verified": public_norm["human_verified"],
                    "verification_label": public_norm["verification_label"],
                    "verification_note": public_norm["verification_note"],
                })

    return {
        "id": machine.id,
        "code": machine.code,
        "manufacturer": machine.manufacturer,
        "model": machine.model,
        "machine_type": machine.machine_type,
        "description": machine.description,
        "tractor_requirements": machine.tractor_requirements,
        "version": machine.version,
        "is_demo": machine.is_demo,
        "label": ("Демо-срез: автопроверено, человеком не сверено; не использовать вместо руководства" if machine.is_demo else None),
        "characteristics": characteristics,
        "nodes": nodes,
        "problems": problems,
    }


def mobile_catalog(session: Session) -> dict:
    """Keep demonstration and real published machines in separate collections."""
    statement = (
        select(Machine)
        .where(Machine.status == "published")
        .options(
            selectinload(Machine.characteristics),
            selectinload(Machine.photos),
            selectinload(Machine.nodes).selectinload(Node.adjustments).selectinload(Adjustment.norms),
            selectinload(Machine.nodes).selectinload(Node.adjustments).selectinload(Adjustment.reviews),
            selectinload(Machine.nodes).selectinload(Node.mistakes),
            selectinload(Machine.nodes).selectinload(Node.photos),
            selectinload(Machine.nodes).selectinload(Node.related_links),
            selectinload(Machine.nodes).selectinload(Node.reviews),
            selectinload(Machine.problems).selectinload(Problem.steps),
        )
        .order_by(Machine.is_demo.desc(), Machine.manufacturer, Machine.model)
    )
    machines = [serialize_machine(machine) for machine in session.scalars(statement).unique().all()]
    return {
        "schema_version": 1,
        "real_machines": [machine for machine in machines if not machine["is_demo"]],
        "demo_machines": [machine for machine in machines if machine["is_demo"]],
    }
