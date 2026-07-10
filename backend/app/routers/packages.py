"""Package Deal API endpoints.

Package deals bundle 2-5 deals into a single offering at a discounted price.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.models import ActivityLog, Buyer, Campaign, Deal, DealPackage, DealPackageItem
from app.schemas import PackageCreate, PackageResponse, PackageUpdate
from app.services.email_generator import generate_package_email
from app.services.gmail_service import send_email
from app.services.matching_service import find_top_matches_for_deal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/packages", tags=["packages"])


def _build_package_response(package: DealPackage) -> dict:
    """Build a PackageResponse-compatible dict from a package."""
    item_deals = []
    total_individual = 0.0
    for item in package.items:
        deal = item.deal
        asking = float(item.individual_asking_price) if item.individual_asking_price else (float(deal.asking_price) if deal.asking_price else 0)
        total_individual += asking
        item_deals.append({
            "deal_id": str(deal.id),
            "address": deal.address,
            "city": deal.city,
            "state": deal.state,
            "property_type": deal.property_type,
            "beds": deal.beds,
            "baths": deal.baths,
            "sqft": deal.sqft,
            "asking_price": asking,
            "arv": float(deal.arv) if deal.arv else 0,
        })

    campaign_stats = None

    return {
        "id": package.id,
        "name": package.name,
        "package_price": float(package.package_price),
        "package_arv": float(package.package_arv) if package.package_arv else None,
        "floor_price": float(package.floor_price),
        "status": package.status,
        "description": package.description,
        "expiry_date": package.expiry_date,
        "created_at": package.created_at,
        "deals": item_deals,
        "total_individual_price": round(total_individual, 2),
        "savings": round(total_individual - float(package.package_price), 2),
        "campaign_stats": campaign_stats,
    }


# ---------------------------------------------------------------------------
# CRUD endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=List[dict])
async def list_packages(
    db: AsyncSession = Depends(get_db),
):
    """List all packages with deal count, status, and pricing."""
    result = await db.execute(
        select(DealPackage).order_by(DealPackage.created_at.desc())
    )
    packages = result.scalars().all()
    return [_build_package_response(p) for p in packages]


@router.post("", response_model=dict, status_code=status.HTTP_201_CREATED)
async def create_package(
    package_in: PackageCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create a new package deal.

    Validates:
    - 2-5 deals
    - All deal_ids exist
    - floor_price < package_price
    """
    # Validate deal_ids exist
    deal_result = await db.execute(
        select(Deal).where(Deal.id.in_(package_in.deal_ids))
    )
    existing_deals = {d.id: d for d in deal_result.scalars().all()}
    missing = [str(did) for did in package_in.deal_ids if did not in existing_deals]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Deal(s) not found: {', '.join(missing)}",
        )

    # Auto-calculate package_arv if not provided
    package_arv = package_in.package_arv
    if package_arv is None:
        package_arv = sum(float(d.arv or 0) for d in existing_deals.values())

    package = DealPackage(
        id=uuid.uuid4(),
        name=package_in.name,
        package_price=package_in.package_price,
        package_arv=package_arv,
        floor_price=package_in.floor_price,
        description=package_in.description,
        expiry_date=package_in.expiry_date,
    )
    db.add(package)
    await db.flush()

    # Create DealPackageItem for each deal (snapshot individual asking price)
    for did in package_in.deal_ids:
        deal = existing_deals[did]
        item = DealPackageItem(
            package_id=package.id,
            deal_id=did,
            individual_asking_price=float(deal.asking_price) if deal.asking_price else None,
        )
        db.add(item)

    await db.commit()
    await db.refresh(package)

    logger.info("Package %s created: '%s' with %d deals at $%.2f",
                package.id, package.name, len(package_in.deal_ids), package.package_price)

    return _build_package_response(package)


@router.get("/{package_id}", response_model=dict)
async def get_package(
    package_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get full package details with deals and campaign stats."""
    result = await db.execute(
        select(DealPackage).where(DealPackage.id == package_id)
    )
    package = result.scalar_one_or_none()
    if not package:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Package with id '{package_id}' not found",
        )

    resp = _build_package_response(package)

    # Fetch campaign stats for all deals in the package
    deal_ids = [item.deal_id for item in package.items]
    if deal_ids:
        from sqlalchemy import func as sa_func
        stats_result = await db.execute(
            select(
                sa_func.count(Campaign.id).label("total"),
                sa_func.count(Campaign.id).filter(Campaign.status == "Sent").label("sent"),
                sa_func.count(Campaign.id).filter(Campaign.status == "Replied").label("replied"),
                sa_func.count(Campaign.id).filter(Campaign.status == "Passed").label("passed"),
            ).where(Campaign.deal_id.in_(deal_ids))
        )
        row = stats_result.one()
        resp["campaign_stats"] = {
            "total": row.total,
            "sent": row.sent,
            "replied": row.replied,
            "passed": row.passed,
        }

    return resp


@router.put("/{package_id}", response_model=dict)
async def update_package(
    package_id: uuid.UUID,
    package_in: PackageUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update a package. Cannot change deal_ids."""
    result = await db.execute(
        select(DealPackage).where(DealPackage.id == package_id)
    )
    package = result.scalar_one_or_none()
    if not package:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Package with id '{package_id}' not found",
        )

    update_data = package_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(package, field, value)

    await db.commit()
    await db.refresh(package)
    return _build_package_response(package)


@router.delete("/{package_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_package(
    package_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Delete a package. Only Active packages can be deleted."""
    result = await db.execute(
        select(DealPackage).where(DealPackage.id == package_id)
    )
    package = result.scalar_one_or_none()
    if not package:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Package with id '{package_id}' not found",
        )

    if package.status != "Active":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot delete package with status '{package.status}'. Only Active packages can be deleted.",
        )

    await db.delete(package)
    await db.commit()
    return None


# ---------------------------------------------------------------------------
# Action endpoints
# ---------------------------------------------------------------------------


@router.post("/{package_id}/launch")
async def launch_package(
    package_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Launch a package campaign.

    Finds buyers matching ANY deal in the package, deduplicates by buyer_id,
    generates package pitch emails, and creates campaigns with package_id set.
    """
    result = await db.execute(
        select(DealPackage).where(DealPackage.id == package_id)
    )
    package = result.scalar_one_or_none()
    if not package:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Package with id '{package_id}' not found",
        )

    if package.status != "Active":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Package status is '{package.status}', must be 'Active' to launch",
        )

    # Collect all deals
    deal_ids = [item.deal_id for item in package.items]
    deals_result = await db.execute(
        select(Deal).where(Deal.id.in_(deal_ids))
    )
    deals = {d.id: d for d in deals_result.scalars().all()}

    # Find buyers matching ANY deal in the package
    from app.services.matching_service import find_top_matches_for_deal
    matched_buyers: dict[uuid.UUID, dict] = {}
    for item in package.items:
        if item.deal_id not in deals:
            continue
        try:
            match_result = await find_top_matches_for_deal(
                db=db,
                deal=deals[item.deal_id],
                limit=50,
            )
            for m in match_result.matches:
                if m.id not in matched_buyers:
                    matched_buyers[m.id] = {
                        "id": m.id,
                        "full_name": m.full_name,
                        "email": m.email,
                        "buy_box": m.buy_box,
                        "buyer_tier": m.buyer_tier or "C-List",
                        "similarity": float(m.similarity),
                    }
        except Exception as e:
            logger.warning("Matching failed for deal %s in package %s: %s", item.deal_id, package_id, e)

    if not matched_buyers:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No matched buyers found for any deal in this package",
        )

    buyer_list = list(matched_buyers.values())
    campaigns_created = 0

    # Compute totals
    total_individual = sum(
        float(item.individual_asking_price) if item.individual_asking_price else
        (float(deals[item.deal_id].asking_price) if item.deal_id in deals else 0)
        for item in package.items
    )
    savings = total_individual - float(package.package_price)
    total_profit = float(package.package_arv or 0) - float(package.package_price)

    # Generate package email and create campaigns for each matched buyer
    from app.services.campaign_launcher import launch_package_campaign
    launch_result = await launch_package_campaign(
        db=db,
        package=package,
        deals=list(deals.values()),
        matched_buyers=buyer_list,
        total_individual=total_individual,
        savings=savings,
        total_profit=total_profit,
    )

    campaigns_created = launch_result.get("campaigns_created", 0)

    # Update package status
    package.status = "Launched"
    db.add(package)

    # Log to activity_log
    log_entry = ActivityLog(
        id=uuid.uuid4(),
        entity_type="deal",
        entity_id=package_id,
        action="package_launched",
        metadata_json={
            "package_name": package.name,
            "buyers_matched": len(buyer_list),
            "campaigns_created": campaigns_created,
            "total_individual": total_individual,
            "savings": savings,
        },
    )
    db.add(log_entry)

    await db.commit()

    logger.info(
        "Package %s launched: %d buyers matched, %d campaigns created",
        package_id, len(buyer_list), campaigns_created,
    )

    return {
        "buyers_matched": len(buyer_list),
        "campaigns_created": campaigns_created,
    }


@router.post("/{package_id}/close")
async def close_package(
    package_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Close a package. Sets status to Sold, pauses queued campaigns."""
    result = await db.execute(
        select(DealPackage).where(DealPackage.id == package_id)
    )
    package = result.scalar_one_or_none()
    if not package:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Package with id '{package_id}' not found",
        )

    if package.status != "Launched":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Package status is '{package.status}', must be 'Launched' to close",
        )

    # Pause all Queued package campaigns
    deal_ids = [item.deal_id for item in package.items]
    paused_result = await db.execute(
        select(Campaign).where(
            Campaign.deal_id.in_(deal_ids),
            Campaign.status == "Queued",
        )
    )
    paused_count = 0
    for c in paused_result.scalars().all():
        c.status = "Paused"
        paused_count += 1

    package.status = "Sold"
    db.add(package)

    log_entry = ActivityLog(
        id=uuid.uuid4(),
        entity_type="deal",
        entity_id=package_id,
        action="package_sold",
        metadata_json={
            "package_name": package.name,
            "campaigns_paused": paused_count,
        },
    )
    db.add(log_entry)

    await db.commit()

    logger.info("Package %s closed: %d campaigns paused", package_id, paused_count)

    return {"success": True}
