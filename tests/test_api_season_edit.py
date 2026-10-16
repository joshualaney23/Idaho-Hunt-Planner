"""T-15c: inline field edits on `PUT /api/documents/<id>/seasons/<sid>` (§8.4).

T-22b gave that endpoint the review action (`status`). The review screen also corrects a
row in place — unit chips, dates, tag/weapon/sex, restriction codes — and this is where
those edits are validated. D3 binds here: an edit may only name units the seed knows and
restriction codes §5.1 defines, so a typo can never become published data.
"""

from __future__ import annotations

import json

import pytest

from planner import db
from tests.test_api_availability import _doc, _season

SECTION = "deer_general"


@pytest.fixture
def conn(app):
    c = db.connect(app.config["DB_PATH"])
    yield c
    c.close()


@pytest.fixture
def staged(conn):
    """One staged deer row, as the importer would leave it."""
    doc = _doc(conn, status="needs_review")
    sid = _season(
        conn,
        doc,
        ["39", "43"],
        status="staged",
        section=SECTION,
        extract_agree=1,
        source_page=12,
    )
    return doc, sid


def _put(client, doc: int, sid: int, **body):
    resp = client.put(f"/api/documents/{doc}/seasons/{sid}", json=body)
    return resp.status_code, resp.get_json()


def _row(conn, sid: int):
    return db.q1("SELECT * FROM seasons WHERE id = ?", sid, conn=conn)


# ───────── the happy path ─────────


def test_every_editable_field_round_trips(client, conn, staged) -> None:
    doc, sid = staged
    code, body = _put(
        client,
        doc,
        sid,
        units=["43", "39", "44"],
        start_date="2026-10-15",
        end_date="2026-11-03",
        tag_type_id="deer_whitetail",
        weapon="archery",
        sex="antlerless",
        restrictions=["whitetail_only", "antlerless_only"],
        zone="",
        notes="corrected from the printed page",
    )
    assert code == 200, body
    season = body["season"]
    assert season["units"] == ["39", "43", "44"]  # stored sorted, de-duplicated
    assert (season["start_date"], season["end_date"]) == ("2026-10-15", "2026-11-03")
    assert (season["tag_type_id"], season["weapon"], season["sex"]) == (
        "deer_whitetail",
        "archery",
        "antlerless",
    )
    assert season["restrictions"] == ["whitetail_only", "antlerless_only"]
    assert season["zone"] is None  # an empty string clears an optional text column
    assert season["notes"] == "corrected from the printed page"
    assert body["edited"] == [
        "end_date",
        "notes",
        "restrictions",
        "sex",
        "start_date",
        "tag_type_id",
        "units",
        "weapon",
        "zone",
    ]

    stored = _row(conn, sid)
    assert json.loads(stored["restrictions"]) == ["whitetail_only", "antlerless_only"]
    units = db.q(
        "SELECT unit_id FROM season_units WHERE season_id = ? ORDER BY unit_id", sid, conn=conn
    )
    assert sorted(u["unit_id"] for u in units) == ["39", "43", "44"]


def test_an_edit_alone_never_approves_the_row(client, conn, staged) -> None:
    """§8.4: correcting a row is not reviewing it — the gate still wants a decision."""
    doc, sid = staged
    code, body = _put(client, doc, sid, sex="antlered")
    assert code == 200
    assert body["season"]["status"] == "staged"
    assert _row(conn, sid)["reviewed_by_user"] == 0
    assert body["publish"]["blocked"] is True
    assert body["publish"]["blockers"]["rows"] == [sid]


def test_edit_and_approve_in_one_request(client, conn, staged) -> None:
    """What `e` → fix → `a` sends from the keyboard."""
    doc, sid = staged
    code, body = _put(client, doc, sid, end_date="2026-10-31", status="approved")
    assert code == 200
    assert body["season"]["end_date"] == "2026-10-31"
    assert body["season"]["status"] == "approved"
    assert _row(conn, sid)["reviewed_by_user"] == 1
    assert _row(conn, sid)["reviewed_at"] is not None
    assert body["publish"]["blocked"] is False


def test_status_only_still_works(client, staged) -> None:
    """T-22b's contract is unchanged: a bare status is a review."""
    doc, sid = staged
    code, body = _put(client, doc, sid, status="rejected")
    assert code == 200
    assert body["season"]["status"] == "rejected"
    assert body["edited"] == []


# ───────── D3: validation refuses to invent data ─────────


@pytest.mark.parametrize(
    ("payload", "code", "field"),
    [
        ({"units": ["39", "nope"]}, "unknown_unit", "units"),
        ({"units": []}, "validation_failed", "units"),
        ({"units": "39"}, "validation_failed", "units"),
        ({"restrictions": ["whitetail_only", "made_up"]}, "unknown_restriction", "restrictions"),
        ({"restrictions": "whitetail_only"}, "validation_failed", "restrictions"),
        ({"weapon": "trebuchet"}, "validation_failed", "weapon"),
        ({"sex": "unknown"}, "validation_failed", "sex"),
        ({"tag_type_id": "deer_imaginary"}, "validation_failed", "tag_type_id"),
        ({"start_date": "15/10/2026"}, "bad_date", "start_date"),
        ({"notes": 17}, "validation_failed", "notes"),
    ],
)
def test_invalid_edits_are_refused(client, conn, staged, payload, code, field) -> None:
    doc, sid = staged
    before = dict(_row(conn, sid))
    status, body = _put(client, doc, sid, **payload)
    assert status == 400, body
    assert body["error"]["code"] == code
    assert field in json.dumps(body["error"]["detail"])
    assert dict(_row(conn, sid)) == before  # nothing was written


def test_an_edit_cannot_invert_the_date_range(client, staged) -> None:
    doc, sid = staged
    status, body = _put(client, doc, sid, start_date="2026-12-01")  # stored end is 2026-10-24
    assert status == 400
    assert body["error"]["code"] == "validation_failed"
    assert body["error"]["detail"] == {"start_date": "invalid", "end_date": "invalid"}


def test_unknown_units_leave_season_units_untouched(client, conn, staged) -> None:
    """The unit rewrite is in the same transaction as the validation that guards it."""
    doc, sid = staged
    status, _ = _put(client, doc, sid, units=["39", "43", "not-a-unit"])
    assert status == 400
    rows = db.q("SELECT unit_id FROM season_units WHERE season_id = ?", sid, conn=conn)
    assert sorted(r["unit_id"] for r in rows) == ["39", "43"]


def test_an_empty_body_is_rejected(client, staged) -> None:
    doc, sid = staged
    status, body = _put(client, doc, sid)
    assert status == 400
    assert body["error"]["detail"] == {"status": "missing"}
    assert "units" in body["error"]["message"]


def test_a_published_row_can_no_longer_be_edited(client, conn) -> None:
    doc = _doc(conn)
    sid = _season(conn, doc, ["39"], status="published", section=SECTION)
    status, body = _put(client, doc, sid, sex="antlered")
    assert status == 409
    assert body["error"]["code"] == "already_published"
