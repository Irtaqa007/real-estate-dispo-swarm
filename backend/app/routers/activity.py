"""Activity log API router.

Provides endpoints to query the activity_log table for the frontend
Activity page. Supports pagination, filtering by entity type / action,
and text search across metadata.
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.models import ActivityLog

router = APIRouter(tags=["activity"])

PAGE_SIZE = 30


@router.get("/api/activity")
async def get_activity(
    page: int = Query(0, ge=0, description="Page number (0-based)"),
    per_page: int = Query(PAGE_SIZE, ge=1, le=100, description="Items per page"),
    entity_type: Optional[str] = Query(None, description="Filter by entity type (buyer, deal, campaign, jv)"),
    action: Optional[str] = Query(None, description="Filter by action name"),
    search: Optional[str] = Query(None, description="Full-text search across metadata JSON"),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Fetch activity log entries with pagination and optional filtering.

    Args:
        page: 0-based page number.
        per_page: Items per page (max 100).
        entity_type: Optional filter (buyer, deal, campaign, jv).
        action: Optional filter by action name.
        search: Optional text search across serialized metadata.

    Returns:
        dict with: items (list), total (int), page (int), per_page (int).
    """
    # Build base query
    base_query = select(ActivityLog).order_by(desc(ActivityLog.created_at))

    # Build count query
    count_query = select(func.count(ActivityLog.id))

    # Apply filters
    if entity_type:
        base_query = base_query.where(ActivityLog.entity_type == entity_type)
        count_query = count_query.where(ActivityLog.entity_type == entity_type)

    if action:
        base_query = base_query.where(ActivityLog.action == action)
        count_query = count_query.where(ActivityLog.action == action)

    if search:
        search_filter = ActivityLog.metadata_json.cast(func.text).ilike(f"%{search}%")
        base_query = base_query.where(search_filter)
        count_query = count_query.where(search_filter)

    # Get total count
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Apply pagination
    offset = page * per_page
    paged_query = base_query.offset(offset).limit(per_page)

    result = await db.execute(paged_query)
    entries = result.scalars().all()

    # Serialize
    items: List[Dict[str, Any]] = []
    for entry in entries:
        metadata = entry.metadata_json or {}
        created = entry.created_at
        items.append({
            "id": str(entry.id),
            "entity_type": entry.entity_type,
            "entity_id": str(entry.entity_id) if entry.entity_id else None,
            "action": entry.action,
            "metadata": metadata,
            "created_at": created.isoformat() if created else None,
        })

    return {
        "items": items,
        "total": total,
        "page": page,
        "per_page": per_page,
    }


@router.get("/api/activity/entity/{entity_type}/{entity_id}")
async def get_entity_activity(
    entity_type: str,
    entity_id: str,
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> List[Dict[str, Any]]:
    """Fetch recent activity for a specific entity (e.g., all activity for a deal).

    Args:
        entity_type: Entity type (buyer, deal, campaign, jv).
        entity_id: UUID of the entity.
        limit: Max number of entries to return (max 100).

    Returns:
        List of activity log entries for the specified entity.
    """
    entity_uuid = uuid.UUID(entity_id)

    stmt = (
        select(ActivityLog)
        .where(
            ActivityLog.entity_type == entity_type,
            ActivityLog.entity_id == entity_uuid,
        )
        .order_by(desc(ActivityLog.created_at))
        .limit(limit)
    )

    result = await db.execute(stmt)
    entries = result.scalars().all()

    items: List[Dict[str, Any]] = []
    for entry in entries:
        metadata = entry.metadata_json or {}
        created = entry.created_at
        items.append({
            "id": str(entry.id),
            "entity_type": entry.entity_type,
            "entity_id": str(entry.entity_id) if entry.entity_id else None,
            "action": entry.action,
            "metadata": metadata,
            "created_at": created.isoformat() if created else None,
        })

    return items


@router.get("/api/activity/actions")
async def get_actions(
    db: AsyncSession = Depends(get_db),
) -> List[str]:
    """Get a list of all distinct action types in the activity log.

    Used by the frontend for the action filter dropdown.
    """
    stmt = select(ActivityLog.action).distinct().order_by(ActivityLog.action)
    result = await db.execute(stmt)
    actions = [row[0] for row in result.all() if row[0]]
    return actions


@router.get("/api/activity/entity-types")
async def get_entity_types(
    db: AsyncSession = Depends(get_db),
) -> List[str]:
    """Get a list of all distinct entity types in the activity log.

    Used by the frontend for the entity type filter dropdown.
    """
    stmt = select(ActivityLog.entity_type).distinct().order_by(ActivityLog.entity_type)
    result = await db.execute(stmt)
    types = [row[0] for row in result.all() if row[0]]
    return types
