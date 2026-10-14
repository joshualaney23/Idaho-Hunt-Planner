"""Availability engine, DESIGN.md §7 (T-06).

T-04 defines only the restriction vocabulary (§5.1); the engine logic arrives in T-06.
"""

from __future__ import annotations

from typing import Final, Literal, TypedDict

Severity = Literal["narrowing", "informational"]


class Restriction(TypedDict):
    label: str
    severity: Severity


# §5.1 closed vocabulary. `narrowing` codes make a unit *restricted* (yellow);
# `informational` codes show as badges but don't change the color. Mirrored to the
# frontend via `/api/meta`. `youth_only` narrows only for adult profiles (T-06 decides).
# Animal-limit codes (antlered/antlerless/two-point/spike/brow-tined) limit which animal you
# may take, not where or when you may hunt, so they're informational (badge only).
RESTRICTIONS: Final[dict[str, Restriction]] = {
    "whitetail_only": {"label": "White-tailed deer only", "severity": "narrowing"},
    "mule_deer_only": {"label": "Mule deer only", "severity": "narrowing"},
    "antlered_only": {"label": "Antlered only", "severity": "informational"},
    "antlerless_only": {"label": "Antlerless only", "severity": "informational"},
    "two_point_only": {"label": "Two-point or less only", "severity": "informational"},
    "spike_only": {"label": "Spike bull only", "severity": "informational"},
    "brow_tined": {"label": "Brow-tined bull only", "severity": "informational"},
    "youth_only": {"label": "Youth only", "severity": "narrowing"},
    "private_land_only": {"label": "Private land only", "severity": "narrowing"},
    "portion_of_unit": {"label": "Portion of unit only", "severity": "narrowing"},
    "outfitter_only": {"label": "Outfitted hunters only", "severity": "narrowing"},
    "cwd_testing": {"label": "CWD testing required", "severity": "informational"},
    "motorized_rule": {"label": "Motorized hunting rule", "severity": "informational"},
    "second_tag_prohibited": {"label": "Second tag prohibited", "severity": "informational"},
    "see_notes": {"label": "See notes", "severity": "narrowing"},
}
