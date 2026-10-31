"""One mapping shared by many Posts and Reels: attach, detach and read its posts.

The mapping stays a normal route on one post. Other posts point at it through
instagram_media.custom_route_id, so editing that post's mapping changes them all.
"""
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from camellia.config import config


def _db():
    from camellia.database import db
    return db


def _matching_media(database, search, source_type, mapping_status=None):
    """Every card the gallery would list, so unverified media can never be attached."""
    page, items = 1, []
    while True:
        rows, total = database.list_instagram_media(
            page=page, page_size=100, search=search, source_type=source_type,
            mapping_status=mapping_status, owner_account_id=config.instagram_media_owner_id(),
        )
        items.extend(rows)
        if not rows or len(items) >= total:
            return items
        page += 1


def attach(database, routing_id, *, item_ids=None, excluded_ids=(), search=None, source_type=None):
    route = database.get_instagram_source_route(routing_id)
    if not route or route.get("archived_at") or route.get("source_type") not in {"post", "reel"}:
        raise HTTPException(404, "Choose a Post or Reel that has its own mapping.")
    wanted = None if item_ids is None else set(item_ids)
    to_attach, already, skipped = [], 0, 0
    # Select all covers what the picker counted, so mapped posts are not reported as skipped.
    for item in _matching_media(database, search, source_type, "unmapped" if wanted is None else None):
        if item["id"] in excluded_ids or (wanted is not None and item["id"] not in wanted):
            continue
        if item.get("mapping_route_id") == routing_id:
            already += 1
        elif item.get("mapping_route_id") or item.get("inventory_verification") == "LEGACY_UNVERIFIED":
            skipped += 1  # A post that already answers keeps its own mapping.
        else:
            to_attach.append(item["id"])
    attached = database.attach_instagram_media(to_attach, routing_id, route.get("topic_id"))
    return {"attached": attached, "already_attached": already, "skipped_mapped": skipped + len(to_attach) - attached}


def _id_list(value, label):
    if not isinstance(value, list) or any(not isinstance(item, int) or isinstance(item, bool) for item in value):
        raise HTTPException(400, f"Send {label} as a list of numbers")
    return value


def create_router(require_admin):
    router = APIRouter(prefix="/admin/instagram-media")
    shadow_router = APIRouter()

    @router.post("/attach-mapping")
    async def attach_mapping(request: Request, x_admin_key: Optional[str] = Header(None)):
        require_admin(x_admin_key)
        body = await request.json()
        routing_id = body.get("routing_id")
        if not isinstance(routing_id, int) or isinstance(routing_id, bool):
            raise HTTPException(400, "Choose the mapping to share")
        source_type = body.get("source_type") or None
        if source_type not in {None, "post", "reel"}:
            raise HTTPException(400, "source_type must be post or reel.")
        select_all = body.get("all_matching") is True
        if not select_all and not body.get("item_ids"):
            raise HTTPException(400, "Tick at least one post")
        # Off the event loop: hundreds of posts here must not hold up customers' webhooks.
        return await run_in_threadpool(
            attach, _db(), routing_id,
            item_ids=None if select_all else _id_list(body.get("item_ids"), "item_ids"),
            excluded_ids=set(_id_list(body.get("excluded_ids", []), "excluded_ids")),
            search=str(body.get("search") or "").strip() or None, source_type=source_type,
        )

    @router.post("/{item_id}/detach-mapping")
    def detach_mapping(item_id: int, x_admin_key: Optional[str] = Header(None)):
        require_admin(x_admin_key)
        database = _db()
        if not database.get_instagram_media(item_id):
            raise HTTPException(404, "Instagram media not found.")
        if database.get_instagram_media_mapping(item_id, for_edit=True):
            raise HTTPException(409, "This post has its own mapping. Use Delete Mapping instead.")
        database.update_instagram_media_route(
            item_id, route_mode="DEFAULT", topic_id=None, custom_route_id=None, needs_review=False,
        )
        return {"status": "success"}

    @router.get("/shared-mapping/{routing_id}")
    def shared_mapping(routing_id: int, x_admin_key: Optional[str] = Header(None)):
        require_admin(x_admin_key)
        database = _db()
        source = database.find_mapping_source_media(routing_id)
        return {"source_item_id": source["id"] if source else None,
                "shared_count": database.count_attached_media(routing_id)}

    @shadow_router.get("/admin/variation-shadow")
    def variation_shadow_report(days: int = 7, limit: int = 100, x_admin_key: Optional[str] = Header(None)):
        """What meaning match would have sent. Nothing here reached a customer."""
        require_admin(x_admin_key)
        report = _db().variation_shadow_report(days=min(max(days, 1), 90), limit=min(max(limit, 1), 500))
        report["shadow_enabled"] = config.VARIATION_SHADOW_ENABLED
        if not config.VARIATION_SHADOW_ENABLED and not report["checked"]:
            report["note"] = "VARIATION_SHADOW_ENABLED is off, so nothing is being checked."
        return report

    router.routes.extend(shadow_router.routes)
    return router
