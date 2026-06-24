"""JV Partner CRUD API endpoints."""

import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.schemas import Deal, JVPartner
from app.schemas import JVPartnerCreate, JVPartnerResponse, JVPartnerUpdate
from app.services.groq_client import groq_chat_completion

router = APIRouter(prefix="/api/jv-partners", tags=["jv-partners"])


@router.post("", response_model=JVPartnerResponse, status_code=status.HTTP_201_CREATED)
async def create_jv_partner(jv_in: JVPartnerCreate, db: AsyncSession = Depends(get_db)):
    """Create a new JV partner.

    Deduplication: checks for existing partner with same email (primary key)
    or same name + normalized email domain. If a match is found, returns 409.
    """
    # Check for duplicate email
    existing = await db.execute(select(JVPartner).where(JVPartner.email == jv_in.email))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"JV Partner with email '{jv_in.email}' already exists",
        )

    # Check for duplicate name (case-insensitive) with different email
    existing_name = await db.execute(
        select(JVPartner).where(JVPartner.name.ilike(jv_in.name))
    )
    if existing_name.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"JV Partner with name '{jv_in.name}' already exists",
        )

    jv_partner = JVPartner(
        id=uuid.uuid4(),
        name=jv_in.name,
        email=jv_in.email,
        phone=jv_in.phone,
        source=jv_in.source,
    )

    db.add(jv_partner)
    await db.commit()
    await db.refresh(jv_partner)
    return jv_partner


@router.get("", response_model=List[JVPartnerResponse])
async def list_jv_partners(
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    """List all JV partners with pagination."""
    result = await db.execute(
        select(JVPartner).offset(skip).limit(limit).order_by(JVPartner.created_at.desc())
    )
    jv_partners = result.scalars().all()
    return jv_partners


@router.get("/{jv_id}", response_model=JVPartnerResponse)
async def get_jv_partner(jv_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Get a single JV partner by UUID."""
    result = await db.execute(select(JVPartner).where(JVPartner.id == jv_id))
    jv_partner = result.scalar_one_or_none()
    if not jv_partner:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"JV Partner with id '{jv_id}' not found",
        )
    return jv_partner


@router.put("/{jv_id}", response_model=JVPartnerResponse)
async def update_jv_partner(
    jv_id: uuid.UUID,
    jv_in: JVPartnerUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update a JV partner. Only provided fields are updated."""
    result = await db.execute(select(JVPartner).where(JVPartner.id == jv_id))
    jv_partner = result.scalar_one_or_none()
    if not jv_partner:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"JV Partner with id '{jv_id}' not found",
        )

    # Update only the fields that were provided
    update_data = jv_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(jv_partner, field, value)

    await db.commit()
    await db.refresh(jv_partner)
    return jv_partner


@router.get("/{jv_id}/intelligence")
async def get_jv_partner_intelligence(
    jv_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get intelligence for a JV partner based on pass patterns across their deals.

    Returns:
        dict with:
            jv_partner_id, name, total_deals, total_closes, close_rate_pct,
            total_passes, overprice_flag_count, title_issue_count,
            condition_issue_count, pass_reasons_breakdown, risk_flags,
            recommendation (AI-generated if enough data).
    """
    result = await db.execute(select(JVPartner).where(JVPartner.id == jv_id))
    jv = result.scalar_one_or_none()
    if not jv:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"JV Partner with id '{jv_id}' not found",
        )

    # Compute derived stats
    total_deals = await db.execute(
        select(Deal).where(Deal.jv_partner_id == jv_id)
    )
    all_deals = total_deals.scalars().all()
    deal_count = len(all_deals)
    closed_count = sum(1 for d in all_deals if d.status == "Sold")
    close_rate = round(closed_count / deal_count * 100, 1) if deal_count > 0 else 0.0

    total_passes = jv.total_passes or 0
    overprice_count = jv.overprice_flag_count or 0
    title_count = jv.title_issue_count or 0
    condition_count = jv.condition_issue_count or 0
    breakdown = jv.pass_reasons_breakdown or {}

    # Compute risk flags
    risk_flags = []
    if total_passes > 0 and (overprice_count / total_passes) > 0.4:
        risk_flags.append(f"High overprice rate ({overprice_count} of {total_passes} deals flagged)")
    if title_count >= 2:
        risk_flags.append(f"{title_count} title issues reported")
    if condition_count >= 2:
        risk_flags.append(f"{condition_count} condition misrepresentation reports")

    # Generate AI recommendation if enough data
    recommendation = None
    if total_passes >= 5:
        try:
            breakdown_text = "; ".join(f"{k}: {v}" for k, v in sorted(breakdown.items(), key=lambda x: -x[1]))
            risk_text = "; ".join(risk_flags) if risk_flags else "No significant risk flags"
            messages = [
                {
                    "role": "system",
                    "content": "You are a real estate JV partner analyst. Provide one sentence assessing deal quality based on pass patterns.",
                },
                {
                    "role": "user",
                    "content": (
                        f"JV Partner: {jv.name}\n"
                        f"Total deals: {deal_count}\n"
                        f"Close rate: {close_rate}%\n"
                        f"Total buyer passes: {total_passes}\n"
                        f"Pass reasons: {breakdown_text}\n"
                        f"Risk flags: {risk_text}\n\n"
                        f"Provide one sentence assessing this JV partner's deal quality."
                    ),
                },
            ]
            response = await groq_chat_completion(
                messages=messages,
                model="llama-3.1-8b-instant",
                temperature=0.1,
                max_tokens=100,
            )
            recommendation = response.choices[0].message.content.strip()
        except Exception as e:
            logger.warning("Failed to generate JV partner intelligence recommendation: %s", e, exc_info=True)

    return {
        "jv_partner_id": str(jv_id),
        "name": jv.name,
        "total_deals": deal_count,
        "total_closes": closed_count,
        "close_rate_pct": close_rate,
        "total_passes": total_passes,
        "overprice_flag_count": overprice_count,
        "title_issue_count": title_count,
        "condition_issue_count": condition_count,
        "pass_reasons_breakdown": breakdown,
        "risk_flags": risk_flags,
        "recommendation": recommendation,
    }


@router.delete("/{jv_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_jv_partner(jv_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Delete a JV partner."""
    result = await db.execute(select(JVPartner).where(JVPartner.id == jv_id))
    jv_partner = result.scalar_one_or_none()
    if not jv_partner:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"JV Partner with id '{jv_id}' not found",
        )

    await db.delete(jv_partner)
    await db.commit()
    return None
