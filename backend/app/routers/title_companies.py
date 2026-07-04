"""Title company CRUD endpoints."""
import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from datetime import datetime

from app.database import get_db
from app.models.models import TitleCompany

router = APIRouter(prefix="/api/title-companies", tags=["title-companies"])


class TitleCompanyCreate(BaseModel):
    deal_id: uuid.UUID
    company_name: str
    contact_name: Optional[str] = None
    contact_email: str
    contact_phone: Optional[str] = None
    file_number: Optional[str] = None
    status: str = "opened"
    notes: Optional[str] = None


class TitleCompanyResponse(BaseModel):
    id: uuid.UUID
    deal_id: uuid.UUID
    company_name: str
    contact_name: Optional[str] = None
    contact_email: str
    contact_phone: Optional[str] = None
    file_number: Optional[str] = None
    status: str
    notes: Optional[str] = None
    chase_count: int = 0
    opened_at: datetime
    created_at: datetime

    model_config = {"from_attributes": True}


@router.get("/{deal_id}", response_model=List[TitleCompanyResponse])
async def get_title_companies(deal_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(TitleCompany).where(TitleCompany.deal_id == deal_id).order_by(TitleCompany.created_at.desc())
    )
    return result.scalars().all()


@router.post("", response_model=TitleCompanyResponse, status_code=201)
async def create_title_company(data: TitleCompanyCreate, db: AsyncSession = Depends(get_db)):
    tc = TitleCompany(
        id=uuid.uuid4(),
        deal_id=data.deal_id,
        company_name=data.company_name,
        contact_name=data.contact_name,
        contact_email=data.contact_email,
        contact_phone=data.contact_phone,
        file_number=data.file_number,
        status=data.status,
        notes=data.notes,
    )
    db.add(tc)
    await db.commit()
    await db.refresh(tc)
    return tc


@router.put("/{tc_id}", response_model=TitleCompanyResponse)
async def update_title_company(tc_id: uuid.UUID, data: dict, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(TitleCompany).where(TitleCompany.id == tc_id))
    tc = result.scalar_one_or_none()
    if not tc:
        raise HTTPException(status_code=404, detail="Title company not found")
    for k, v in data.items():
        if hasattr(tc, k):
            setattr(tc, k, v)
    db.add(tc)
    await db.commit()
    await db.refresh(tc)
    return tc
