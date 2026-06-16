"""JV Partner CRUD API endpoints."""

import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.schemas import JVPartner
from app.schemas import JVPartnerCreate, JVPartnerResponse, JVPartnerUpdate

router = APIRouter(prefix="/api/jv-partners", tags=["jv-partners"])


@router.post("", response_model=JVPartnerResponse, status_code=status.HTTP_201_CREATED)
async def create_jv_partner(jv_in: JVPartnerCreate, db: AsyncSession = Depends(get_db)):
    """Create a new JV partner."""
    # Check for duplicate email
    existing = await db.execute(select(JVPartner).where(JVPartner.email == jv_in.email))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"JV Partner with email '{jv_in.email}' already exists",
        )

    jv_partner = JVPartner(
        id=uuid.uuid4(),
        name=jv_in.name,
        email=jv_in.email,
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
