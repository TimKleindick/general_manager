"""Synthetic SIWC experiment datasets and their deterministic toy fixture."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from general_manager.chat.evals.runner import EvalCase


# The first six entries intentionally preserve the original SIWC suite order.
DATASETS = (
    "basic_queries",
    "edge_cases",
    "follow_ups",
    "multi_hop",
    "demo_readiness",
    "large_schema",
    "expanded_queries",
    "expanded_relations",
    "expanded_follow_ups",
)

_LEGACY_DATASETS = frozenset(DATASETS[:6])
_RICH_DATASETS = frozenset(DATASETS[6:])
_FIXTURE_SOURCE_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "general_manager"
    / "chat"
    / "evals"
    / "fixtures.py"
)


_MATERIAL_SPECS = (
    (1, "Steel", 7.8),
    (2, "Aluminum", 2.7),
    (3, "Cobalt", 8.9),
    (4, "Kupfer", 8.96),
    (5, "Messing", 8.4),
    (6, "Titan", 4.5),
    (7, "Graphit", 2.2),
    (8, "Edelstahl", 7.9),
    (9, "Bronze", 8.8),
    (10, "Glasfaser", 1.9),
    (11, "Keramik", 3.9),
    (12, "Nickel", 8.9),
    (13, "Polyamid", 1.1),
    (14, "Zink", 7.1),
    (15, "Magnesium", 1.74),
    (16, "Carbon Fiber", 1.6),
    (17, "Gusseisen", 7.2),
    (18, "Inconel", 8.44),
    (19, "Silizium", 2.33),
    (20, "Aramid", 1.44),
    (21, "Federstahl", 7.85),
    (22, "Kupfer", 8.96),
    (23, "Steel", 7.8),
    (24, "Aluminum", 2.7),
)

_PART_NAMES = (
    ("Bolt", "Drive pin", "Rahmen"),
    ("Bearing", "Lagerhülse", "Guide rail"),
    ("Gear", "Rotorblatt", "Cobalt sleeve"),
    ("Flansch", "Copper lug", "Kühlkörper"),
    ("Messing clip", "Valve seat", "Klemme"),
    ("Titan bracket", "Träger", "Light hinge"),
    ("Graphite seal", "Dichtung", "Brush block"),
    ("Steel plate", "Edelstahlring", "Schiene"),
    ("Bronze bushing", "Bronze pin", "Buchse"),
    ("Fiber panel", "Faserlasche", "Glass mount"),
    ("Ceramic nozzle", "Keramikring", "Heat shield"),
    ("Nickel mesh", "Nickel pin", "Drahtführung"),
    ("Polyamide cap", "Kunststoffclip", "Spacer"),
    ("Zinc washer", "Zinkhülse", "Lock plate"),
    ("Magnesium cover", "Leichtbau clip", "Magnesium pin"),
    ("Carbon panel", "Carbon lug", "Faserbügel"),
    ("Cast iron shoe", "Gusseisenring", "Heavy bracket"),
    ("Inconel spring", "Heat lug", "Turbine clip"),
    ("Silicon wafer", "Siliziumring", "Sensor mount"),
    ("Aramid strap", "Faserband", "Safety loop"),
    ("Federstahl clip", "Spring rail", "Feder"),
    ("Flansch", "Copper wire", "Kupferöse"),
    ("Bolt", "Steel guide", "Rahmen"),
    ("Bearing", "Aluminum rail", "Guide pin"),
)


def _build_fixture_data() -> dict[str, list[dict[str, Any]]]:
    materials = [
        {"id": identifier, "name": name, "density": density}
        for identifier, name, density in _MATERIAL_SPECS
    ]
    parts: list[dict[str, Any]] = []
    for material_id, names in enumerate(_PART_NAMES, start=1):
        for offset, name in enumerate(names):
            parts.append(
                {
                    "id": (material_id - 1) * 3 + offset + 1,
                    "name": name,
                    "material": material_id,
                }
            )

    project_specs = (
        (1, "Apollo Lab", (1, 7, 10, 13, 21, 40)),
        (2, "Mercury Werkstatt", (4, 13, 16, 25, 31, 45)),
        (3, "Nordstern", (10, 22, 28, 34, 46, 58)),
        (4, "Atlas", (3, 18, 27, 39, 51, 60)),
        (5, "Atlas", (6, 15, 24, 36, 48, 63)),
        (6, "Rhein Brücke", (9, 21, 30, 42, 54, 66)),
        (7, "Berlin Hub", (2, 12, 26, 33, 57, 69)),
        (8, "Valencia Plant", (5, 17, 32, 41, 50, 72)),
        (9, "Hafen Nord", (8, 20, 29, 38, 47, 61)),
        (10, "München Testfeld", (11, 23, 35, 44, 56, 68)),
        (11, "Oslo Prototype", (14, 19, 37, 49, 53, 70)),
        (12, "Lyon Montage", (16, 25, 31, 43, 59, 71)),
    )
    projects = [
        {"id": identifier, "name": name, "parts": list(part_ids)}
        for identifier, name, part_ids in project_specs
    ]
    return {"materials": materials, "parts": parts, "projects": projects}


RICH_FIXTURE_DATA = cast(Mapping[str, list[Mapping[str, Any]]], _build_fixture_data())


def _turn(
    *,
    result_set: dict[str, Any],
    answer_contains: list[str],
    answer_excludes: list[str] | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    expectation: dict[str, Any] = {
        "result_set": result_set,
        "answer_contains": answer_contains,
    }
    if answer_excludes:
        expectation["answer_excludes"] = answer_excludes
    if tool_calls:
        expectation["tool_calls"] = tool_calls
    return expectation


def _query(
    manager: str,
    *,
    filters: Mapping[str, Any] | None = None,
    fields: list[Any] | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "manager": manager,
        "filters": dict(filters or {}),
        "fields": fields or ["name"],
    }
    if limit is not None:
        payload["limit"] = limit
    if offset:
        payload["offset"] = offset
    return payload


def _result_set(
    manager: str, names: list[str], *, fields: list[str] | None = None
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "manager": manager,
        "rows": [{"name": name} for name in names],
    }
    if fields is not None:
        result["fields"] = fields
    return result


def _case(
    name: str,
    description: str,
    conversation: list[str],
    turns: list[dict[str, Any]],
    *,
    tags: list[str],
) -> dict[str, Any]:
    if len(conversation) != len(turns):
        raise ValueError(f"turn count mismatch for {name}")  # noqa: TRY003
    return {
        "name": name,
        "description": description,
        "conversation": [{"user": text} for text in conversation],
        "expectations": {"turns": turns},
        "tier": 1,
        "tags": tags,
    }


_EXPANDED_CASES = [
    _case(
        "expanded_material_page_middle",
        "Page through the middle of the expanded material catalog.",
        ["List materials five through eight by name."],
        [
            _turn(
                result_set=_result_set(
                    "MaterialManager", ["Messing", "Titan", "Graphit", "Edelstahl"]
                ),
                answer_contains=["Messing", "Titan", "Graphit", "Edelstahl"],
            )
        ],
        tags=["expanded", "pagination", "english"],
    ),
    _case(
        "expanded_density_above_upper_boundary",
        "Use a strict density threshold above the common 8.9 boundary.",
        ["Which materials have density greater than 8.9?"],
        [
            _turn(
                result_set=_result_set("MaterialManager", ["Kupfer", "Kupfer"]),
                answer_contains=["Kupfer"],
            )
        ],
        tags=["expanded", "numeric_boundary", "english"],
    ),
    _case(
        "expanded_density_just_below_boundary",
        "Distinguish a strict greater-than filter just below 8.9.",
        ["Welche Materialien haben eine Dichte über 8.89?"],
        [
            _turn(
                result_set=_result_set(
                    "MaterialManager", ["Cobalt", "Kupfer", "Nickel", "Kupfer"]
                ),
                answer_contains=["Cobalt", "Kupfer", "Nickel"],
            )
        ],
        tags=["expanded", "numeric_boundary", "german"],
    ),
    _case(
        "expanded_duplicate_material_name",
        "Return every record sharing a display name.",
        ["Find every material named Steel."],
        [
            _turn(
                result_set=_result_set("MaterialManager", ["Steel", "Steel"]),
                answer_contains=["Steel"],
            )
        ],
        tags=["expanded", "duplicate_names", "english"],
    ),
    _case(
        "expanded_german_name_contains",
        "Find German names with a shared substring.",
        ["Welche Materialien enthalten 'stahl' im Namen?"],
        [
            _turn(
                result_set=_result_set("MaterialManager", ["Edelstahl", "Federstahl"]),
                answer_contains=["Edelstahl", "Federstahl"],
            )
        ],
        tags=["expanded", "icontains", "german"],
    ),
    _case(
        "expanded_empty_material_filter",
        "Report a valid query with no matching rows.",
        ["Find materials named Unobtainium."],
        [
            _turn(
                result_set=_result_set("MaterialManager", [], fields=["name"]),
                answer_contains=[],
                answer_excludes=["Steel", "Aluminum", "Kupfer"],
            )
        ],
        tags=["expanded", "empty_result", "english"],
    ),
    _case(
        "expanded_kupfer_parts_exact",
        "Query both exact duplicate material relationships.",
        ["Welche Teile verwenden Kupfer als Material?"],
        [
            _turn(
                result_set=_result_set(
                    "PartManager",
                    [
                        "Flansch",
                        "Copper lug",
                        "Kühlkörper",
                        "Flansch",
                        "Copper wire",
                        "Kupferöse",
                    ],
                ),
                answer_contains=[
                    "Flansch",
                    "Copper lug",
                    "Kühlkörper",
                    "Copper wire",
                    "Kupferöse",
                ],
            )
        ],
        tags=["expanded", "nested_filter", "duplicate_names", "german"],
    ),
    _case(
        "expanded_steel_parts_contains",
        "Filter parts through a case-insensitive material relation.",
        ["Which parts use a material containing 'steel'?"],
        [
            _turn(
                result_set=_result_set(
                    "PartManager",
                    [
                        "Bolt",
                        "Drive pin",
                        "Rahmen",
                        "Bolt",
                        "Steel guide",
                        "Rahmen",
                    ],
                ),
                answer_contains=["Bolt", "Drive pin", "Rahmen", "Steel guide"],
            )
        ],
        tags=["expanded", "nested_filter", "icontains", "english"],
    ),
    _case(
        "expanded_duplicate_part_name",
        "Preserve duplicate part display names in an exact query.",
        ["Find every part named Bolt."],
        [
            _turn(
                result_set=_result_set("PartManager", ["Bolt", "Bolt"]),
                answer_contains=["Bolt"],
            )
        ],
        tags=["expanded", "duplicate_names", "english"],
    ),
    _case(
        "expanded_duplicate_project_name",
        "Return both projects named Atlas.",
        ["Welche Projekte heißen Atlas?"],
        [
            _turn(
                result_set=_result_set("ProjectManager", ["Atlas", "Atlas"]),
                answer_contains=["Atlas"],
            )
        ],
        tags=["expanded", "duplicate_names", "german"],
    ),
    _case(
        "expanded_project_stahl_filter",
        "Find projects containing a part made from a German steel name.",
        ["Which projects contain parts whose material includes 'stahl'?"],
        [
            _turn(
                result_set=_result_set(
                    "ProjectManager",
                    ["Nordstern", "Atlas", "Hafen Nord", "München Testfeld"],
                ),
                answer_contains=[
                    "Nordstern",
                    "Atlas",
                    "Hafen Nord",
                    "München Testfeld",
                ],
            )
        ],
        tags=["expanded", "nested_filter", "icontains", "english"],
    ),
    _case(
        "expanded_empty_project_part_filter",
        "Return an empty project result for an unmatched nested part.",
        ["Find projects that contain a part named Phantom Coupler."],
        [
            _turn(
                result_set=_result_set("ProjectManager", [], fields=["name"]),
                answer_contains=[],
                answer_excludes=["Apollo", "Atlas"],
            )
        ],
        tags=["expanded", "nested_filter", "empty_result", "english"],
    ),
    _case(
        "expanded_material_page_tail",
        "Handle a short page at the end of the material list.",
        ["Zeige die letzten drei Materialien der Liste."],
        [
            _turn(
                result_set=_result_set(
                    "MaterialManager", ["Kupfer", "Steel", "Aluminum"]
                ),
                answer_contains=["Kupfer", "Steel", "Aluminum"],
            )
        ],
        tags=["expanded", "pagination", "german"],
    ),
    _case(
        "expanded_low_density_boundary",
        "Use a low numeric boundary without rounding it away.",
        ["Which materials have density greater than 1.6?"],
        [
            _turn(
                result_set=_result_set(
                    "MaterialManager",
                    [
                        "Steel",
                        "Aluminum",
                        "Cobalt",
                        "Kupfer",
                        "Messing",
                        "Titan",
                        "Graphit",
                        "Edelstahl",
                        "Bronze",
                        "Glasfaser",
                        "Keramik",
                        "Nickel",
                        "Zink",
                        "Magnesium",
                        "Gusseisen",
                        "Inconel",
                        "Silizium",
                        "Federstahl",
                        "Kupfer",
                        "Steel",
                        "Aluminum",
                    ],
                ),
                answer_contains=[
                    "Steel",
                    "Aluminum",
                    "Cobalt",
                    "Kupfer",
                    "Messing",
                    "Titan",
                    "Graphit",
                    "Edelstahl",
                    "Bronze",
                    "Glasfaser",
                    "Keramik",
                    "Nickel",
                    "Zink",
                    "Magnesium",
                    "Gusseisen",
                    "Inconel",
                    "Silizium",
                    "Federstahl",
                ],
                answer_excludes=["Carbon Fiber", "Aramid", "Polyamid"],
            )
        ],
        tags=["expanded", "numeric_boundary", "english"],
    ),
    _case(
        "expanded_parts_page_offset",
        "Page across the part catalog with an offset and limit.",
        ["List parts 10 through 13 by name."],
        [
            _turn(
                result_set=_result_set(
                    "PartManager",
                    ["Flansch", "Copper lug", "Kühlkörper", "Messing clip"],
                ),
                answer_contains=[
                    "Flansch",
                    "Copper lug",
                    "Kühlkörper",
                    "Messing clip",
                ],
            )
        ],
        tags=["expanded", "pagination", "english"],
    ),
    _case(
        "expanded_projects_with_cobalt",
        "Traverse project parts to an exact material name.",
        ["Which projects use Cobalt parts?"],
        [
            _turn(
                result_set=_result_set(
                    "ProjectManager", ["Apollo Lab", "Rhein Brücke", "Hafen Nord"]
                ),
                answer_contains=["Apollo Lab", "Rhein Brücke", "Hafen Nord"],
            )
        ],
        tags=["expanded", "relation", "english"],
    ),
    _case(
        "expanded_projects_with_aluminum",
        "Traverse a relation with a duplicate material display name.",
        ["Welche Projekte enthalten Teile aus Aluminum?"],
        [
            _turn(
                result_set=_result_set(
                    "ProjectManager",
                    [
                        "Mercury Werkstatt",
                        "Atlas",
                        "Valencia Plant",
                        "Oslo Prototype",
                        "Lyon Montage",
                    ],
                ),
                answer_contains=[
                    "Mercury Werkstatt",
                    "Atlas",
                    "Valencia Plant",
                    "Oslo Prototype",
                    "Lyon Montage",
                ],
            )
        ],
        tags=["expanded", "relation", "duplicate_names", "german"],
    ),
    _case(
        "expanded_projects_with_kupfer",
        "Find projects containing either exact Kupfer record.",
        ["Which projects have parts made from Kupfer?"],
        [
            _turn(
                result_set=_result_set(
                    "ProjectManager",
                    [
                        "Apollo Lab",
                        "Nordstern",
                        "Rhein Brücke",
                        "Berlin Hub",
                        "München Testfeld",
                    ],
                ),
                answer_contains=[
                    "Apollo Lab",
                    "Nordstern",
                    "Rhein Brücke",
                    "Berlin Hub",
                    "München Testfeld",
                ],
            )
        ],
        tags=["expanded", "relation", "duplicate_names", "english"],
    ),
    _case(
        "expanded_project_parts_nested_fields",
        "Read a project's nested part names while checking its anchor row.",
        ["List the parts used by Apollo Lab."],
        [
            {
                "result_set": {
                    "manager": "ProjectManager",
                    "fields": ["name", "parts"],
                    "rows": [
                        {
                            "name": "Apollo Lab",
                            "parts": [
                                {"name": "Bolt"},
                                {"name": "Gear"},
                                {"name": "Flansch"},
                                {"name": "Messing clip"},
                                {"name": "Brush block"},
                                {"name": "Zinc washer"},
                            ],
                        }
                    ],
                },
                "answer_contains": [
                    "Apollo Lab",
                    "Bolt",
                    "Gear",
                    "Flansch",
                    "Messing clip",
                    "Brush block",
                    "Zinc washer",
                ],
            }
        ],
        tags=["expanded", "nested_fields", "english"],
    ),
    _case(
        "expanded_project_name_icontains",
        "Use a case-insensitive project name filter.",
        ["Welche Projekte enthalten 'werk' im Namen?"],
        [
            _turn(
                result_set=_result_set("ProjectManager", ["Mercury Werkstatt"]),
                answer_contains=["Mercury Werkstatt"],
            )
        ],
        tags=["expanded", "icontains", "german"],
    ),
    _case(
        "expanded_project_empty_material_relation",
        "Return no projects for an absent relation value.",
        ["Which projects use Unobtainium parts?"],
        [
            _turn(
                result_set=_result_set("ProjectManager", [], fields=["name"]),
                answer_contains=[],
                answer_excludes=["Apollo", "Mercury"],
            )
        ],
        tags=["expanded", "relation", "empty_result", "english"],
    ),
    _case(
        "expanded_part_material_page",
        "Page through parts that use a German material name.",
        ["Zeige die ersten drei Teile, deren Materialname 'stahl' enthält."],
        [
            _turn(
                result_set=_result_set(
                    "PartManager",
                    ["Steel plate", "Edelstahlring", "Schiene"],
                    fields=["name"],
                ),
                answer_contains=["Steel plate", "Edelstahlring", "Schiene"],
            )
        ],
        tags=["expanded", "pagination", "relation", "german"],
    ),
    _case(
        "expanded_high_density_materials",
        "List records above a high numeric density threshold.",
        ["List materials with density greater than 7.8."],
        [
            _turn(
                result_set=_result_set(
                    "MaterialManager",
                    [
                        "Cobalt",
                        "Kupfer",
                        "Messing",
                        "Edelstahl",
                        "Bronze",
                        "Nickel",
                        "Inconel",
                        "Federstahl",
                        "Kupfer",
                    ],
                ),
                answer_contains=[
                    "Cobalt",
                    "Kupfer",
                    "Messing",
                    "Edelstahl",
                    "Bronze",
                    "Nickel",
                    "Inconel",
                    "Federstahl",
                ],
                answer_excludes=["Steel"],
            )
        ],
        tags=["expanded", "numeric_boundary", "english"],
    ),
    _case(
        "expanded_duplicate_flange_parts",
        "Preserve duplicate part names across two materials.",
        ["Welche Teile heißen Flansch?"],
        [
            _turn(
                result_set=_result_set("PartManager", ["Flansch", "Flansch"]),
                answer_contains=["Flansch"],
            )
        ],
        tags=["expanded", "duplicate_names", "german"],
    ),
    _case(
        "expanded_project_page_middle",
        "Page through the middle of the project catalog.",
        ["Show projects three and four by name."],
        [
            _turn(
                result_set=_result_set("ProjectManager", ["Nordstern", "Atlas"]),
                answer_contains=["Nordstern", "Atlas"],
            )
        ],
        tags=["expanded", "pagination", "english"],
    ),
    _case(
        "expanded_follow_material_to_dense",
        "Start broad, then narrow to a strict density result.",
        [
            "Show me the first four materials.",
            "Now show only materials denser than 8.8.",
        ],
        [
            _turn(
                result_set=_result_set(
                    "MaterialManager",
                    ["Steel", "Aluminum", "Cobalt", "Kupfer"],
                ),
                answer_contains=["Steel", "Aluminum", "Cobalt", "Kupfer"],
            ),
            _turn(
                result_set=_result_set(
                    "MaterialManager", ["Cobalt", "Kupfer", "Nickel", "Kupfer"]
                ),
                answer_contains=["Cobalt", "Kupfer", "Nickel"],
            ),
        ],
        tags=["expanded", "follow_up", "numeric_boundary", "english"],
    ),
    _case(
        "expanded_follow_parts_to_steel",
        "Refine a part list using a relation filter in German.",
        [
            "List the first three parts.",
            "Jetzt alle Teile, deren Materialname 'stahl' enthält.",
        ],
        [
            _turn(
                result_set=_result_set(
                    "PartManager",
                    ["Bolt", "Drive pin", "Rahmen"],
                ),
                answer_contains=["Bolt", "Drive pin", "Rahmen"],
            ),
            _turn(
                result_set=_result_set(
                    "PartManager",
                    [
                        "Steel plate",
                        "Edelstahlring",
                        "Schiene",
                        "Federstahl clip",
                        "Spring rail",
                        "Feder",
                    ],
                ),
                answer_contains=[
                    "Steel plate",
                    "Edelstahlring",
                    "Schiene",
                    "Federstahl clip",
                    "Spring rail",
                    "Feder",
                ],
            ),
        ],
        tags=["expanded", "follow_up", "pagination", "german"],
    ),
    _case(
        "expanded_follow_project_to_parts",
        "Anchor on a project, then inspect its nested parts.",
        ["Find the Atlas projects.", "Which parts are used by those projects?"],
        [
            _turn(
                result_set=_result_set("ProjectManager", ["Atlas", "Atlas"]),
                answer_contains=["Atlas"],
            ),
            {
                "result_set": {
                    "manager": "ProjectManager",
                    "fields": ["name", "parts"],
                    "rows": [
                        {
                            "name": "Atlas",
                            "parts": [
                                {"name": "Rahmen"},
                                {"name": "Light hinge"},
                                {"name": "Buchse"},
                                {"name": "Spacer"},
                                {"name": "Heavy bracket"},
                                {"name": "Safety loop"},
                            ],
                        },
                        {
                            "name": "Atlas",
                            "parts": [
                                {"name": "Guide rail"},
                                {"name": "Klemme"},
                                {"name": "Schiene"},
                                {"name": "Drahtführung"},
                                {"name": "Faserbügel"},
                                {"name": "Feder"},
                            ],
                        },
                    ],
                },
                "answer_contains": [
                    "Atlas",
                    "Rahmen",
                    "Light hinge",
                    "Buchse",
                    "Spacer",
                    "Heavy bracket",
                    "Safety loop",
                    "Guide rail",
                    "Klemme",
                    "Schiene",
                    "Drahtführung",
                    "Faserbügel",
                    "Feder",
                ],
            },
        ],
        tags=["expanded", "follow_up", "nested_fields", "english"],
    ),
    _case(
        "expanded_follow_empty_then_kupfer",
        "Recover from an empty search with a concrete relation query.",
        [
            "Find projects using Phantom material.",
            "Instead, show projects using Kupfer.",
        ],
        [
            _turn(
                result_set=_result_set("ProjectManager", [], fields=["name"]),
                answer_contains=[],
            ),
            _turn(
                result_set=_result_set(
                    "ProjectManager",
                    [
                        "Apollo Lab",
                        "Nordstern",
                        "Rhein Brücke",
                        "Berlin Hub",
                        "München Testfeld",
                    ],
                ),
                answer_contains=[
                    "Apollo Lab",
                    "Nordstern",
                    "Rhein Brücke",
                    "Berlin Hub",
                    "München Testfeld",
                ],
            ),
        ],
        tags=["expanded", "follow_up", "empty_result", "german"],
    ),
    _case(
        "expanded_follow_three_step_materials",
        "Use three turns to move from a page to duplicate names and an empty filter.",
        [
            "List the first two materials.",
            "Now find every material named Aluminum.",
            "Finally find materials named Unobtainium.",
        ],
        [
            _turn(
                result_set=_result_set("MaterialManager", ["Steel", "Aluminum"]),
                answer_contains=["Steel", "Aluminum"],
            ),
            _turn(
                result_set=_result_set("MaterialManager", ["Aluminum", "Aluminum"]),
                answer_contains=["Aluminum"],
            ),
            _turn(
                result_set=_result_set("MaterialManager", [], fields=["name"]),
                answer_contains=[],
                answer_excludes=["Steel", "Aluminum"],
            ),
        ],
        tags=["expanded", "follow_up", "duplicate_names", "empty_result", "english"],
    ),
    _case(
        "expanded_follow_project_language_switch",
        "Switch language while retaining the project relation context.",
        [
            "Which projects contain Cobalt parts?",
            "Und welche Projekte enthalten Kupferteile?",
        ],
        [
            _turn(
                result_set=_result_set(
                    "ProjectManager", ["Apollo Lab", "Rhein Brücke", "Hafen Nord"]
                ),
                answer_contains=["Apollo Lab", "Rhein Brücke", "Hafen Nord"],
            ),
            _turn(
                result_set=_result_set(
                    "ProjectManager",
                    [
                        "Apollo Lab",
                        "Nordstern",
                        "Rhein Brücke",
                        "Berlin Hub",
                        "München Testfeld",
                    ],
                ),
                answer_contains=[
                    "Apollo Lab",
                    "Nordstern",
                    "Rhein Brücke",
                    "Berlin Hub",
                    "München Testfeld",
                ],
            ),
        ],
        tags=["expanded", "follow_up", "relation", "german", "english"],
    ),
    _case(
        "expanded_follow_page_then_material_relation",
        "Use a paged part query followed by a project relation query.",
        [
            "Show the first three Cobalt parts.",
            "Which projects use those Cobalt parts?",
        ],
        [
            _turn(
                result_set=_result_set(
                    "PartManager", ["Gear", "Rotorblatt", "Cobalt sleeve"]
                ),
                answer_contains=["Gear", "Rotorblatt", "Cobalt sleeve"],
            ),
            _turn(
                result_set=_result_set(
                    "ProjectManager", ["Apollo Lab", "Rhein Brücke", "Hafen Nord"]
                ),
                answer_contains=["Apollo Lab", "Rhein Brücke", "Hafen Nord"],
            ),
        ],
        tags=["expanded", "follow_up", "pagination", "relation", "english"],
    ),
]

# Keep the three public names separate so the SIWC suite can compare coverage
# slices independently while preserving one fixture and one deterministic case
# namespace.
_EXPANDED_DATASETS: dict[str, list[dict[str, Any]]] = {
    "expanded_queries": _EXPANDED_CASES[:14],
    "expanded_relations": _EXPANDED_CASES[14:25],
    "expanded_follow_ups": _EXPANDED_CASES[25:],
}


_ORACLE_QUERIES: dict[str, list[dict[str, Any]]] = {
    "expanded_material_page_middle": [_query("MaterialManager", limit=4, offset=4)],
    "expanded_density_above_upper_boundary": [
        _query("MaterialManager", filters={"density_Gt": 8.9})
    ],
    "expanded_density_just_below_boundary": [
        _query("MaterialManager", filters={"density_Gt": 8.89})
    ],
    "expanded_duplicate_material_name": [
        _query("MaterialManager", filters={"name": "Steel"})
    ],
    "expanded_german_name_contains": [
        _query("MaterialManager", filters={"name_Icontains": "stahl"})
    ],
    "expanded_empty_material_filter": [
        _query("MaterialManager", filters={"name": "Unobtainium"})
    ],
    "expanded_kupfer_parts_exact": [
        _query("PartManager", filters={"material_Name": "Kupfer"})
    ],
    "expanded_steel_parts_contains": [
        _query("PartManager", filters={"material_Name_Icontains": "steel"})
    ],
    "expanded_duplicate_part_name": [_query("PartManager", filters={"name": "Bolt"})],
    "expanded_duplicate_project_name": [
        _query("ProjectManager", filters={"name": "Atlas"})
    ],
    "expanded_project_stahl_filter": [
        _query("ProjectManager", filters={"parts_Material_Name_Icontains": "stahl"})
    ],
    "expanded_empty_project_part_filter": [
        _query("ProjectManager", filters={"parts_Name": "Phantom Coupler"})
    ],
    "expanded_material_page_tail": [_query("MaterialManager", limit=3, offset=21)],
    "expanded_low_density_boundary": [
        _query("MaterialManager", filters={"density_Gt": 1.6})
    ],
    "expanded_parts_page_offset": [_query("PartManager", limit=4, offset=9)],
    "expanded_projects_with_cobalt": [
        _query("ProjectManager", filters={"parts_Material_Name": "Cobalt"})
    ],
    "expanded_projects_with_aluminum": [
        _query("ProjectManager", filters={"parts_Material_Name": "Aluminum"})
    ],
    "expanded_projects_with_kupfer": [
        _query("ProjectManager", filters={"parts_Material_Name": "Kupfer"})
    ],
    "expanded_project_parts_nested_fields": [
        _query(
            "ProjectManager",
            filters={"name": "Apollo Lab"},
            fields=["name", {"parts": ["name"]}],
        )
    ],
    "expanded_project_name_icontains": [
        _query("ProjectManager", filters={"name_Icontains": "werk"})
    ],
    "expanded_project_empty_material_relation": [
        _query("ProjectManager", filters={"parts_Material_Name": "Unobtainium"})
    ],
    "expanded_part_material_page": [
        _query("PartManager", filters={"material_Name_Icontains": "stahl"}, limit=3)
    ],
    "expanded_high_density_materials": [
        _query("MaterialManager", filters={"density_Gt": 7.8})
    ],
    "expanded_duplicate_flange_parts": [
        _query("PartManager", filters={"name": "Flansch"})
    ],
    "expanded_project_page_middle": [_query("ProjectManager", limit=2, offset=2)],
    "expanded_follow_material_to_dense": [
        _query("MaterialManager", limit=4),
        _query("MaterialManager", filters={"density_Gt": 8.8}),
    ],
    "expanded_follow_parts_to_steel": [
        _query("PartManager", limit=3),
        _query("PartManager", filters={"material_Name_Icontains": "stahl"}),
    ],
    "expanded_follow_project_to_parts": [
        _query("ProjectManager", filters={"name": "Atlas"}),
        _query(
            "ProjectManager",
            filters={"name": "Atlas"},
            fields=["name", {"parts": ["name"]}],
        ),
    ],
    "expanded_follow_empty_then_kupfer": [
        _query("ProjectManager", filters={"parts_Material_Name": "Phantom"}),
        _query("ProjectManager", filters={"parts_Material_Name": "Kupfer"}),
    ],
    "expanded_follow_three_step_materials": [
        _query("MaterialManager", limit=2),
        _query("MaterialManager", filters={"name": "Aluminum"}),
        _query("MaterialManager", filters={"name": "Unobtainium"}),
    ],
    "expanded_follow_project_language_switch": [
        _query("ProjectManager", filters={"parts_Material_Name": "Cobalt"}),
        _query("ProjectManager", filters={"parts_Material_Name": "Kupfer"}),
    ],
    "expanded_follow_page_then_material_relation": [
        _query("PartManager", filters={"material_Name": "Cobalt"}, limit=3),
        _query("ProjectManager", filters={"parts_Material_Name": "Cobalt"}),
    ],
}


def load_experiment_dataset(name: str) -> list[EvalCase]:
    """Load a legacy YAML dataset or one of the expanded deterministic sets."""
    if name in _LEGACY_DATASETS:
        from general_manager.chat.evals.runner import load_dataset

        return load_dataset(name)
    try:
        cases = _EXPANDED_DATASETS[name]
    except KeyError as error:
        raise KeyError(  # noqa: TRY003
            f"Unknown SIWC experiment dataset: {name}"
        ) from error
    from general_manager.chat.evals.runner import EvalCase

    return [EvalCase(**case) for case in deepcopy(cases)]


def setup_experiment_dataset(name: str) -> None:
    """Install the fixture matching an experiment dataset name."""
    from general_manager.chat.evals.fixtures import setup_large_schema, setup_toy_schema

    if name == "large_schema":
        setup_large_schema()
        return
    if name in _LEGACY_DATASETS or name in _RICH_DATASETS:
        setup_toy_schema(data=RICH_FIXTURE_DATA if name in _RICH_DATASETS else None)
        return
    raise KeyError(f"Unknown SIWC experiment dataset: {name}")  # noqa: TRY003


def fixture_fingerprint(name: str) -> str:
    """Hash the synthetic fixture and schema setup identity for ``name``."""
    if name not in DATASETS:
        raise KeyError(f"Unknown SIWC experiment dataset: {name}")  # noqa: TRY003
    try:
        fixture_source = _FIXTURE_SOURCE_PATH.read_bytes()
    except OSError:
        fixture_source = b"general-manager-chat-eval-fixtures-v1"
    schema_source_sha256 = hashlib.sha256(fixture_source).hexdigest()
    if name == "large_schema":
        fixture: Any = {
            "kind": "large_schema",
            "manager_count": 150,
            "chain_length": 8,
            "schema_version": "chat-eval-large-v1",
            "schema_source_sha256": schema_source_sha256,
        }
    elif name in _RICH_DATASETS:
        fixture = {
            "kind": "toy_schema",
            "schema_version": "chat-eval-toy-v2",
            "schema_source_sha256": schema_source_sha256,
            "data": RICH_FIXTURE_DATA,
        }
    else:
        fixture = {
            "kind": "toy_schema",
            "schema_version": "chat-eval-toy-v1",
            "schema_source_sha256": schema_source_sha256,
            "data": {
                "materials": [
                    {"id": 1, "name": "Steel", "density": 7.8},
                    {"id": 2, "name": "Aluminum", "density": 2.7},
                    {"id": 3, "name": "Cobalt", "density": 8.9},
                ],
                "parts": [
                    {"id": 1, "name": "Bolt", "material": 1},
                    {"id": 2, "name": "Bearing", "material": 2},
                    {"id": 3, "name": "Gear", "material": 3},
                ],
                "projects": [
                    {"id": 1, "name": "Apollo", "parts": [3]},
                    {"id": 2, "name": "Mercury", "parts": [2]},
                ],
            },
        }
    encoded = json.dumps(
        fixture, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def oracle_queries(case_name: str) -> list[dict[str, Any]]:
    """Return the explicit fixture query script for an expanded case."""
    try:
        return deepcopy(_ORACLE_QUERIES[case_name])
    except KeyError as error:
        raise KeyError(  # noqa: TRY003
            f"Unknown expanded SIWC case: {case_name}"
        ) from error


__all__ = [
    "DATASETS",
    "RICH_FIXTURE_DATA",
    "fixture_fingerprint",
    "load_experiment_dataset",
    "oracle_queries",
    "setup_experiment_dataset",
]
