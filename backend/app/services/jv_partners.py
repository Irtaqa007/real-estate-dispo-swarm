"""JV Partner helper functions."""
from app.models.models import Deal, JVPartner


async def get_jv_partner_for_contract(deal: Deal, db) -> JVPartner | None:
    if not deal.jv_partner_id:
        return None
    return await db.get(JVPartner, deal.jv_partner_id)
