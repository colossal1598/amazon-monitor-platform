"""Admin config API: groups, filters, targets, selector profiles. Basic-auth protected."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException

from .. import db
from ..schemas import (
    GroupCreate,
    GroupUpdate,
    PdpTargetCreate,
    SelectorProfileCreate,
    SelectorProfileUpdate,
    SerpTargetCreate,
)
from ..schemas import default_filter
from ..security import require_admin

router = APIRouter(prefix="/api", dependencies=[Depends(require_admin)])


# ----------------------------- groups ----------------------------- #

@router.get("/groups")
def list_groups() -> list[dict]:
    return db.query(
        """
        SELECT g.*, row_to_json(f) AS filter,
            (SELECT count(*) FROM pdp_target t WHERE t.group_id = g.id) AS pdp_count,
            (SELECT count(*) FROM serp_target t WHERE t.group_id = g.id) AS serp_count
        FROM scrape_group g
        LEFT JOIN group_filter f ON f.group_id = g.id
        ORDER BY g.id
        """
    )


@router.get("/groups/{group_id}")
def get_group(group_id: int) -> dict:
    row = db.query_one(
        """
        SELECT g.*, row_to_json(f) AS filter
        FROM scrape_group g
        LEFT JOIN group_filter f ON f.group_id = g.id
        WHERE g.id = %s
        """,
        (group_id,),
    )
    if not row:
        raise HTTPException(404, "group not found")
    return row


@router.post("/groups")
def create_group(body: GroupCreate) -> dict:
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO scrape_group
                    (name, kind, niche, cadence, interval_minutes, enabled,
                     selector_profile_id, headless, max_concurrent, notify_channel)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
                """,
                (
                    body.name, body.kind, body.niche, body.cadence, body.interval_minutes,
                    body.enabled, body.selector_profile_id, body.headless,
                    body.max_concurrent, body.notify_channel,
                ),
            )
            group_id = cur.fetchone()["id"]
            f = body.filter
            cur.execute(
                """
                INSERT INTO group_filter
                    (group_id, accepted_sellers, required_keywords, blacklist_keywords,
                     blacklist_asins, min_price, max_price, require_free_shipping,
                     require_shipping_signal, require_shippable, price_drop_percent,
                     alert_new, alert_back_in_stock, alert_price_drop)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    group_id, json.dumps(f.accepted_sellers), json.dumps(f.required_keywords),
                    json.dumps(f.blacklist_keywords), json.dumps(f.blacklist_asins),
                    f.min_price, f.max_price, f.require_free_shipping, f.require_shipping_signal,
                    f.require_shippable, f.price_drop_percent, f.alert_new,
                    f.alert_back_in_stock, f.alert_price_drop,
                ),
            )
    return get_group(group_id)


@router.put("/groups/{group_id}")
def update_group(group_id: int, body: GroupUpdate) -> dict:
    existing = db.query_one("SELECT id FROM scrape_group WHERE id = %s", (group_id,))
    if not existing:
        raise HTTPException(404, "group not found")

    fields = body.model_dump(exclude_unset=True, exclude={"filter"})
    if fields:
        sets = ", ".join(f"{k} = %({k})s" for k in fields)
        fields["group_id"] = group_id
        db.execute(
            f"UPDATE scrape_group SET {sets}, updated_at = now() WHERE id = %(group_id)s", fields
        )
    if body.filter is not None:
        f = body.filter
        db.execute(
            """
            INSERT INTO group_filter
                (group_id, accepted_sellers, required_keywords, blacklist_keywords,
                 blacklist_asins, min_price, max_price, require_free_shipping,
                 require_shipping_signal, require_shippable, price_drop_percent,
                 alert_new, alert_back_in_stock, alert_price_drop)
            VALUES (%(group_id)s, %(accepted_sellers)s, %(required_keywords)s, %(blacklist_keywords)s,
                    %(blacklist_asins)s, %(min_price)s, %(max_price)s, %(require_free_shipping)s,
                    %(require_shipping_signal)s, %(require_shippable)s, %(price_drop_percent)s,
                    %(alert_new)s, %(alert_back_in_stock)s, %(alert_price_drop)s)
            ON CONFLICT (group_id) DO UPDATE SET
                accepted_sellers = EXCLUDED.accepted_sellers,
                required_keywords = EXCLUDED.required_keywords,
                blacklist_keywords = EXCLUDED.blacklist_keywords,
                blacklist_asins = EXCLUDED.blacklist_asins,
                min_price = EXCLUDED.min_price,
                max_price = EXCLUDED.max_price,
                require_free_shipping = EXCLUDED.require_free_shipping,
                require_shipping_signal = EXCLUDED.require_shipping_signal,
                require_shippable = EXCLUDED.require_shippable,
                price_drop_percent = EXCLUDED.price_drop_percent,
                alert_new = EXCLUDED.alert_new,
                alert_back_in_stock = EXCLUDED.alert_back_in_stock,
                alert_price_drop = EXCLUDED.alert_price_drop
            """,
            {
                "group_id": group_id,
                "accepted_sellers": json.dumps(f.accepted_sellers),
                "required_keywords": json.dumps(f.required_keywords),
                "blacklist_keywords": json.dumps(f.blacklist_keywords),
                "blacklist_asins": json.dumps(f.blacklist_asins),
                "min_price": f.min_price,
                "max_price": f.max_price,
                "require_free_shipping": f.require_free_shipping,
                "require_shipping_signal": f.require_shipping_signal,
                "require_shippable": f.require_shippable,
                "price_drop_percent": f.price_drop_percent,
                "alert_new": f.alert_new,
                "alert_back_in_stock": f.alert_back_in_stock,
                "alert_price_drop": f.alert_price_drop,
            },
        )
    return get_group(group_id)


@router.delete("/groups/{group_id}")
def delete_group(group_id: int) -> dict:
    db.execute("DELETE FROM scrape_group WHERE id = %s", (group_id,))
    return {"ok": True}


# ----------------------------- targets ----------------------------- #

@router.get("/groups/{group_id}/targets")
def list_targets(group_id: int) -> dict:
    return {
        "pdp": db.query("SELECT * FROM pdp_target WHERE group_id = %s ORDER BY asin", (group_id,)),
        "serp": db.query("SELECT * FROM serp_target WHERE group_id = %s ORDER BY id", (group_id,)),
    }


@router.post("/groups/{group_id}/pdp_targets")
def add_pdp_target(group_id: int, body: PdpTargetCreate) -> dict:
    asin = body.asin.strip().upper()
    db.execute(
        """
        INSERT INTO pdp_target (group_id, asin, enabled, notes) VALUES (%s, %s, %s, %s)
        ON CONFLICT (group_id, asin) DO UPDATE SET enabled = EXCLUDED.enabled, notes = EXCLUDED.notes
        """,
        (group_id, asin, body.enabled, body.notes),
    )
    return {"ok": True}


@router.delete("/pdp_targets/{target_id}")
def delete_pdp_target(target_id: int) -> dict:
    db.execute("DELETE FROM pdp_target WHERE id = %s", (target_id,))
    return {"ok": True}


@router.post("/groups/{group_id}/serp_targets")
def add_serp_target(group_id: int, body: SerpTargetCreate) -> dict:
    db.execute(
        """
        INSERT INTO serp_target (group_id, search_url, label, scrape_mode, max_pages, enabled)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (group_id, body.search_url, body.label, body.scrape_mode, body.max_pages, body.enabled),
    )
    return {"ok": True}


@router.delete("/serp_targets/{target_id}")
def delete_serp_target(target_id: int) -> dict:
    db.execute("DELETE FROM serp_target WHERE id = %s", (target_id,))
    return {"ok": True}


# ----------------------------- selector profiles ----------------------------- #

@router.get("/selector_profiles")
def list_profiles() -> list[dict]:
    return db.query("SELECT * FROM selector_profile ORDER BY id")


@router.post("/selector_profiles")
def create_profile(body: SelectorProfileCreate) -> dict:
    row = db.query_one(
        """
        INSERT INTO selector_profile (name, marketplace, locale, selectors, is_default)
        VALUES (%s, %s, %s, %s, %s) RETURNING *
        """,
        (body.name, body.marketplace, body.locale, json.dumps(body.selectors), body.is_default),
    )
    if body.is_default:
        db.execute("UPDATE selector_profile SET is_default = FALSE WHERE id <> %s", (row["id"],))
    return row


@router.put("/selector_profiles/{profile_id}")
def update_profile(profile_id: int, body: SelectorProfileUpdate) -> dict:
    fields = body.model_dump(exclude_unset=True)
    if "selectors" in fields:
        fields["selectors"] = json.dumps(fields["selectors"])
    if fields:
        sets = ", ".join(f"{k} = %({k})s" for k in fields)
        fields["pid"] = profile_id
        db.execute(
            f"UPDATE selector_profile SET {sets}, version = version + 1, updated_at = now() WHERE id = %(pid)s",
            fields,
        )
    if body.is_default:
        db.execute("UPDATE selector_profile SET is_default = FALSE WHERE id <> %s", (profile_id,))
    row = db.query_one("SELECT * FROM selector_profile WHERE id = %s", (profile_id,))
    if not row:
        raise HTTPException(404, "profile not found")
    return row


@router.get("/filter_defaults")
def filter_defaults() -> dict:
    return default_filter()
