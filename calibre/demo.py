"""Clearly-marked, non-instructional sample content for UI demonstrations only."""
from __future__ import annotations

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
    Problem,
    ProblemStep,
)

DEMO_MACHINE_CODE = "DEMO-ONLY-01"
DEMO_SEED_VERSION = "3"

NODE_DEMOS = [
    ("height", 1, 0.70, 0.82, "Высота среза и положение аппарата", "ДЕМО-описание: карточка показывает место для проверенных сведений."),
    ("balance", 2, 0.83, 0.60, "Механизм уравновешивания", "ДЕМО-описание: никаких значений или действий для реальной машины."),
    ("hitch", 3, 0.15, 0.48, "Навеска на трактор", "ДЕМО-описание: конкретные требования появятся только из проверенного источника."),
    ("belts", 4, 0.42, 0.28, "Клиноременная передача", "ДЕМО-описание: не является инструкцией по натяжению или проверке ремней."),
    ("gear", 5, 0.57, 0.60, "Редуктор режущего аппарата", "ДЕМО-описание: не использовать как руководство к обслуживанию."),
    ("safety", 6, 0.32, 0.70, "Предохранительное устройство", "ДЕМО-описание: порядок проверки не задан."),
    ("transport", 7, 0.27, 0.18, "Транспортная фиксация", "ДЕМО-описание: порядок подготовки не задан."),
]

NODE_ADJUSTMENTS = {
    "height": ("Положение аппарата — ДЕМО", "ДЕМО: шаги появятся после проверки паспорта и допуска специалиста."),
    "balance": ("Уравновешивание — ДЕМО", "ДЕМО: не регулируйте пружины по этой карточке."),
    "hitch": ("Положение навески — ДЕМО", "ДЕМО: сведения о совместимости пока не подтверждены."),
    "belts": ("Состояние передачи — ДЕМО", "ДЕМО: инструкция отсутствует до проверки источника."),
    "gear": ("Проверка редуктора — ДЕМО", "ДЕМО: не разбирайте узел по этому примеру."),
    "safety": ("Предохранительное устройство — ДЕМО", "ДЕМО: параметры и процедура проверки отсутствуют."),
    "transport": ("Подготовка к перевозке — ДЕМО", "ДЕМО: используйте документацию изготовителя, не этот макет."),
}

PROBLEM_DEMOS = [
    ("Высокая или неровная стерня — ДЕМО", "ДЕМО-симптом. Текст нужен только для проверки интерфейса."),
    ("Аппарат касается грунта — ДЕМО", "ДЕМО-симптом. Это не диагностика реальной неисправности."),
    ("Шум в передаче — ДЕМО", "ДЕМО-симптом. Причины и порядок проверки не подтверждены."),
    ("Нагрев редуктора — ДЕМО", "ДЕМО-симптом. Не является советом по эксплуатации."),
    ("Срабатывает защита — ДЕМО", "ДЕМО-симптом. Остановите работу и обратитесь к проверенному руководству."),
]


def seed_demo_data(session: Session) -> None:
    """Create or refresh the one isolated demo machine; never touch real records."""
    setting = session.get(AppSetting, "demo_seed_version")
    if setting and setting.value == DEMO_SEED_VERSION:
        return
    existing = session.scalar(select(Machine).where(Machine.code == DEMO_MACHINE_CODE))
    if existing is not None:
        session.delete(existing)
        session.flush()

    demo = Machine(
        code=DEMO_MACHINE_CODE,
        manufacturer="ДЕМО",
        model="Учебный образец",
        machine_type="Демонстрационные данные",
        description="ДЕМО — вымышленные данные только для проверки интерфейса. Не применять на технике.",
        tractor_requirements="ДЕМО — требования не заданы.",
        status="published",
        version=int(DEMO_SEED_VERSION),
        is_demo=True,
    )
    demo.characteristics.extend([
        MachineCharacteristic(label="Тип машины", display_value="ДЕМО", status="published"),
        MachineCharacteristic(label="Совместимость", display_value="ДЕМО", status="published"),
        MachineCharacteristic(label="Технические значения", display_value="ДЕМО", status="published"),
    ])
    nodes_by_key: dict[str, Node] = {}
    for key, number, map_x, map_y, title, purpose in NODE_DEMOS:
        node = Node(number=number, title=title, purpose=purpose, status="published", map_x=map_x, map_y=map_y)
        adjustment_title, instruction = NODE_ADJUSTMENTS[key]
        node.adjustments.append(Adjustment(
            title=adjustment_title,
            instructions=[instruction],
            tool="ДЕМО",
            status="published",
        ))
        node.mistakes.append(CommonMistake(
            description="ДЕМО: пример типичного замечания.",
            consequence="Это не сведения о реальной машине.",
        ))
        nodes_by_key[key] = node
        demo.nodes.append(node)

    for source, targets in {
        "height": ("balance", "hitch"),
        "balance": ("hitch", "height"),
        "hitch": ("balance",),
        "belts": ("gear",),
        "gear": ("belts",),
        "safety": ("balance",),
    }.items():
        for target in targets:
            nodes_by_key[source].related_links.append(NodeLink(
                related_node=nodes_by_key[target], relation_label="Связанный пример ДЕМО"
            ))

    problem_steps = [
        [("ДЕМО: откройте карточку примера.", "height"), ("ДЕМО: проверьте раздел со статусом источника.", "hitch")],
        [("ДЕМО: сравните подпись со статусом карточки.", "balance"), ("ДЕМО: технические действия не заданы.", None)],
        [("ДЕМО: откройте демонстрационную карточку передачи.", "belts"), ("ДЕМО: порядок диагностики не задан.", "gear")],
        [("ДЕМО: подтверждённые значения отсутствуют.", "gear")],
        [("ДЕМО: прекратите работу и сверяйтесь с документацией изготовителя.", "safety")],
    ]
    for (title, symptom), steps in zip(PROBLEM_DEMOS, problem_steps):
        problem = Problem(title=title, symptom=symptom, status="published")
        problem.steps.extend([
            ProblemStep(step_number=index, instruction=text, linked_node=nodes_by_key.get(link) if link else None)
            for index, (text, link) in enumerate(steps, start=1)
        ])
        demo.problems.append(problem)

    session.add(demo)
    session.merge(AppSetting(key="demo_seed_version", value=DEMO_SEED_VERSION))
    session.commit()
