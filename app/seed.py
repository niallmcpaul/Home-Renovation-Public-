from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models as m
from app.services import Actor, create, link_items, set_setting

ROOMS = [
    "Whole house", "Exterior and roof", "Front garden", "Rear garden", "Hall and stairs",
    "Lounge", "Dining room", "Kitchen", "Shower room", "Bedroom 1", "Bedroom 2",
    "Bedroom 3", "Study", "Landing", "Loft", "Garage",
]

LATER = {"loft", "ldc", "cctv", "garage_shell", "garage_wetroom", "garage_sauna", "solar", "insulation_ext", "kitchen"}


def gbp(low: float | None, high: float | None) -> tuple[int | None, int | None]:
    if low is None:
        return None, None
    return round(low * 100), round(high * 100)


# key, title, description, size, category, room, (low, high) in GBP or None, decision_group,
# needs_building_control, needs_planning_check, compliance_notes, blocked_by keys
ITEMS = [
    ("survey", "Import building survey findings", None, "task", "urgent", "Whole house",
     None, None, False, False, None, []),
    ("article4", "Check Article 4 directions for the street with Bristol City Council", None, "task", "enabling",
     "Whole house", None, None, False, False, None, []),
    ("knock", "Knock through reception rooms incl. chimney breast removal", None, "project", "enabling", "Lounge",
     (6000, 12000), None, True, False,
     "Structural engineer input and Building Control sign-off required for chimney breast removal.", ["article4"]),
    ("ufh", "Ground-floor underfloor heating, between-joist wet system", None, "project", "enabling", "Whole house",
     (5700, 11400), None, True, False,
     "Building Control notification required if electrical work (pump/manifold wiring) is involved.", ["knock"]),
    ("oak_floor", "New engineered oak ground floor", None, "project", "improvement", "Whole house",
     (3000, 5800), "Ground floor finish", False, False, None, ["ufh"]),
    ("sand_floor", "Sand and varnish existing floorboards", None, "task", "improvement", "Whole house",
     (700, 1400), "Ground floor finish", False, False, None, []),
    ("kitchen", "Kitchen refit", None, "project", "improvement", "Kitchen",
     (12000, 25000), None, True, False,
     "Building Control sign-off likely required if electrics or drainage change.", ["knock"]),
    ("dishwasher", "Buy and install dishwasher", None, "task", "wish", "Kitchen", None, None, False, False, None, []),
    ("bathroom", "Shower room / bathroom refit", None, "project", "improvement", "Shower room",
     (5000, 9000), None, True, False,
     "Building Control sign-off likely required if electrics or drainage change.", []),
    ("insulation_int", "Solid wall insulation, internal", None, "project", "improvement", "Whole house",
     (7000, 12000), "Wall insulation", False, False, None, ["survey"]),
    ("insulation_ext", "Solid wall insulation, external", None, "project", "improvement", "Exterior and roof",
     (12000, 25000), "Wall insulation", False, False, None, ["survey", "article4"]),
    ("windows", "Windows: repair or replace", None, "project", "improvement", "Whole house",
     None, None, True, False,
     "Building Control sign-off (or FENSA-registered installer) required if windows are replaced.",
     ["survey", "article4"]),
    ("repaint", "Repaint throughout", "Estimate: £3,450–£7,500 (labour + 15–25% materials, 2026 estimate).",
     "project", "improvement", "Whole house", (3450, 7500), None, False, False, None,
     ["knock", "insulation_int", "insulation_ext", "windows"]),
    ("carpets", "New carpets (bedrooms, stairs)", None, "project", "improvement", "Whole house",
     (2000, 4000), None, False, False, None, ["repaint"]),
    ("loft", "Loft conversion (rear dormer with en-suite)", None, "project", "improvement", "Loft",
     (35000, 55000), None, True, False,
     "Building Control sign-off required for the dormer and habitable-room conversion.", ["survey"]),
    ("ldc", "Lawful Development Certificate for garage use",
     "Confirm the current LDC application fee with Bristol City Council; not a fixed estimate.",
     "task", "enabling", "Garage", None, None, False, False, None, ["article4"]),
    ("cctv", "CCTV drainage survey, garage to main sewer", None, "task", "enabling", "Garage",
     None, None, False, False, None, []),
    ("garage_shell", "Garage conversion shell (insulation, floor, electrics)", None, "project", "improvement",
     "Garage", (15000, 25000), None, True, False,
     "Building Control sign-off required for insulation, floor and electrical work.", ["ldc"]),
    ("garage_wetroom", "Garage wet room incl. drainage extension", None, "project", "improvement", "Garage",
     (8000, 15000), None, True, False,
     "Building Control sign-off required for drainage and electrical work.", ["garage_shell", "cctv"]),
    ("garage_sauna", "Garage sauna and dedicated circuit", None, "project", "wish", "Garage",
     (4000, 10000), None, True, False,
     "Building Control sign-off required for the dedicated electrical circuit.", ["garage_shell"]),
    ("solar", "Solar panels", None, "project", "improvement", "Exterior and roof",
     None, None, False, False, None, ["survey"]),
    ("front_storage", "Front garden bike and bin storage", None, "project", "improvement", "Front garden",
     None, None, False, True,
     "Outbuildings forward of the principal elevation fall outside householder permitted development; "
     "check with Bristol City Council before building.", ["article4"]),
    ("rear_garden", "Rear garden landscaping", None, "project", "improvement", "Rear garden",
     None, None, False, False, None, []),
    ("mirror", "Buy a mirror", None, "wish", "wish", None, None, None, False, False, None, []),
]


def seed(db: Session) -> int:
    if db.scalar(select(m.Room).limit(1)):
        return 0

    actor = Actor(user_id=None, via="web")

    rooms = {name: create(db, actor, m.Room, name=name, sort_order=i) for i, name in enumerate(ROOMS)}

    phase1 = create(db, actor, m.Phase, name="Phase 1", budget_min=1_000_000, budget_max=2_000_000)
    later = create(db, actor, m.Phase, name="Later",
                   notes="Funded from savings or a possible future lump sum, one project at a time.")

    set_setting(db, "quote_threshold", 100000)
    set_setting(db, "monthly_saving_target", 0)

    objs: dict[str, m.Item] = {}
    for key, title, desc, size, category, room_name, estimate, decision_group, nbc, npc, notes, _ in ITEMS:
        low, high = gbp(*estimate) if estimate else (None, None)
        objs[key] = create(
            db, actor, m.Item,
            title=title, description=desc, size=size, category=category,
            room_id=rooms[room_name].id if room_name else None,
            phase_id=(later if key in LATER else phase1).id,
            estimate_low=low, estimate_high=high, decision_group=decision_group,
            needs_building_control=nbc, needs_planning_check=npc, compliance_notes=notes,
        )

    for key, *_rest, blocked_by in ITEMS:
        for blocker_key in blocked_by:
            link_items(db, actor, objs[blocker_key].id, objs[key].id)

    return len(objs)
