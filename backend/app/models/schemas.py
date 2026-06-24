"""SQLAlchemy database models for the Real Estate Dispo Swarm application.

Tables:
- buyers: Contacts/potential buyers
- jv_partners: Joint venture partners
- deals: Real estate deals/properties
- campaigns: Email outreach campaigns
- email_verifications: Email verification records
- activity_log: Audit trail / activity log
"""

import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, Column, Computed, DateTime, Float, ForeignKey, Integer, Numeric, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import relationship

from app.database import Base


class Buyer(Base):
    __tablename__ = "buyers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    full_name = Column(Text, nullable=False)
    email = Column(Text, unique=True, nullable=False)
    email_verified = Column(Boolean, default=False)
    email_verified_at = Column(DateTime(timezone=True), nullable=True)
    email_verification_status = Column(Text, nullable=True)  # valid, invalid, catch_all, unknown
    affiliation = Column(Text, nullable=True)
    buy_box = Column(Text, nullable=False)
    buy_box_embedding = Column(Vector(1024), nullable=True)
    buyer_tier = Column(Text, default="C-List")  # A-List, B-List, C-List
    status = Column(Text, default="Active")  # Active, Paused, Do Not Contact
    # Structured fields extracted from buy_box for hard-filter matching
    price_min = Column(Numeric(19, 2), nullable=True)
    price_max = Column(Numeric(19, 2), nullable=True)
    pref_property_type = Column(Text, nullable=True)  # House, Land, or NULL (both)
    pref_cities = Column(ARRAY(Text), nullable=True)  # Preferred cities/areas
    response_rate = Column(Float, default=0)
    avg_response_time_hours = Column(Float, nullable=True)
    deals_viewed = Column(Integer, default=0)
    deals_offered_on = Column(Integer, default=0)
    offers_accepted = Column(Integer, default=0)
    offers_rejected = Column(Integer, default=0)
    deals_closed = Column(Integer, default=0)
    deads_fell_through = Column(Integer, default=0)
    avg_spread_closed = Column(Numeric(19, 2), nullable=True)
    total_lifetime_spread = Column(Numeric(19, 2), default=0)
    engagement_score = Column(Float, default=0)
    last_pitch_sent_at = Column(DateTime(timezone=True), nullable=True)
    last_reply_at = Column(DateTime(timezone=True), nullable=True)
    pitches_this_week = Column(Integer, default=0)
    pitches_this_week_reset_at = Column(DateTime(timezone=True), nullable=True)
    portfolio_insights = Column(JSONB, nullable=True)
    unsubscribed_at = Column(DateTime(timezone=True), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    campaigns = relationship("Campaign", back_populates="buyer")
    email_verifications = relationship("EmailVerification", back_populates="buyer")
    deals = relationship("Deal", back_populates="assigned_buyer", foreign_keys="Deal.assigned_buyer_id")
    buyer_emails = relationship("BuyerEmail", back_populates="buyer", cascade="all, delete-orphan")

    @property
    def additional_emails(self) -> list:
        """Return list of additional email addresses (excluding primary)."""
        if not self.buyer_emails:
            return []
        return [be.email for be in self.buyer_emails]

    @property
    def has_embedding(self) -> bool:
        """Whether this buyer has a buy_box_embedding (i.e. is matchable)."""
        return self.buy_box_embedding is not None

    def __repr__(self) -> str:
        return f"<Buyer(id={self.id}, email={self.email})>"


class BuyerEmail(Base):
    """Additional email addresses for a buyer.

    A buyer can have multiple active emails. The primary email is stored
    in the buyers.email column; additional emails are stored here.
    """

    __tablename__ = "buyer_emails"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    buyer_id = Column(UUID(as_uuid=True), ForeignKey("buyers.id", ondelete="CASCADE"), nullable=False, index=True)
    email = Column(Text, nullable=False, index=True)
    email_verified = Column(Boolean, default=False)
    email_verification_status = Column(Text, nullable=True)
    verified_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    buyer = relationship("Buyer", back_populates="buyer_emails")

    def __repr__(self) -> str:
        return f"<BuyerEmail(id={self.id}, email={self.email}, buyer_id={self.buyer_id})>"


class JVPartner(Base):
    __tablename__ = "jv_partners"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(Text, nullable=False)
    email = Column(Text, nullable=False)
    phone = Column(Text, nullable=True)
    source = Column(Text, nullable=True)
    deals_linked = Column(ARRAY(UUID(as_uuid=True)), default=list)
    total_deals_submitted = Column(Integer, default=0)
    total_deals_closed = Column(Integer, default=0)
    total_revenue_generated = Column(Numeric(19, 2), default=0)
    avg_buyer_feedback_score = Column(Float, default=0)
    title_issue_rate = Column(Float, default=0)
    title_issue_count = Column(Integer, default=0)
    condition_issue_count = Column(Integer, default=0)
    overprice_flag_count = Column(Integer, default=0)
    total_passes = Column(Integer, default=0)
    pass_reasons_breakdown = Column(JSONB, nullable=True)  # Tally: {"price_too_high": 7, "title_issue": 2}
    total_split_revenue = Column(Numeric(19, 2), default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    deals = relationship("Deal", back_populates="jv_partner", foreign_keys="Deal.jv_partner_id")

    def __repr__(self) -> str:
        return f"<JVPartner(id={self.id}, name={self.name})>"


class Deal(Base):
    __tablename__ = "deals"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    address = Column(Text, nullable=False)
    city = Column(Text, nullable=True)
    state = Column(Text, nullable=True)
    zip = Column(Text, nullable=True)
    county = Column(Text, nullable=True)
    property_type = Column(Text, nullable=False)  # House or Land
    beds = Column(Integer, nullable=True)
    baths = Column(Float, nullable=True)
    sqft = Column(Integer, nullable=True)
    year_built = Column(Integer, nullable=True)
    occupancy_status = Column(Text, nullable=True)  # Vacant, Tenant, Owner
    repair_estimate = Column(Numeric(19, 2), nullable=True)
    lot_size = Column(Text, nullable=True)
    zoning = Column(Text, nullable=True)
    utilities_available = Column(ARRAY(Text), nullable=True)
    topography_access = Column(Text, nullable=True)
    condition_description = Column(Text, nullable=False)
    deal_embedding = Column(Vector(1024), nullable=True)
    arv = Column(Numeric(19, 2), nullable=False)
    asking_price = Column(Numeric(19, 2), nullable=False)
    floor_price = Column(Numeric(19, 2), nullable=False)
    contract_price = Column(Numeric(19, 2), nullable=False)
    title_status = Column(Text, nullable=False)  # Clear, Liens, Probate, Other
    photos = Column(ARRAY(Text), nullable=True)
    status = Column(Text, default="Available")  # Available, Under Contract, Sold, Dead
    pass_count = Column(Integer, default=0)  # Total buyers who passed on this deal
    pass_reasons_summary = Column(JSONB, nullable=True)  # Tally: {"price_too_high": 3, "timing": 1}
    assigned_buyer_id = Column(UUID(as_uuid=True), ForeignKey("buyers.id"), nullable=True)
    jv_partner_id = Column(UUID(as_uuid=True), ForeignKey("jv_partners.id"), nullable=True)
    jv_split_percentage = Column(Numeric(5, 2), default=50)
    closed_at = Column(DateTime(timezone=True), nullable=True)
    closed_price = Column(Numeric(19, 2), nullable=True)
    net_spread = Column(Numeric(19, 2), nullable=True)
    jv_payout = Column(Numeric(19, 2), nullable=True)
    my_payout = Column(Numeric(19, 2), nullable=True)
    priority_score = Column(Float, default=0)
    market_velocity = Column(Float, default=0)
    spread = Column(Numeric(19, 2), Computed("asking_price - contract_price"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    net_spread_formula = Column(Numeric(19, 2), Computed("(asking_price - contract_price) - COALESCE(repair_estimate, 0)"), nullable=True)

    # Relationships
    assigned_buyer = relationship("Buyer", back_populates="deals", foreign_keys=[assigned_buyer_id])
    jv_partner = relationship("JVPartner", back_populates="deals", foreign_keys=[jv_partner_id])
    campaigns = relationship("Campaign", back_populates="deal")

    def __repr__(self) -> str:
        return f"<Deal(id={self.id}, address={self.address})>"


class Campaign(Base):
    __tablename__ = "campaigns"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    deal_id = Column(UUID(as_uuid=True), ForeignKey("deals.id"), nullable=False)
    buyer_id = Column(UUID(as_uuid=True), ForeignKey("buyers.id"), nullable=False)
    touch_number = Column(Integer, nullable=False)  # 1 to 6
    status = Column(Text, default="Queued")  # Queued, Sent, Opened, Replied, Bounced, Failed
    sent_at = Column(DateTime(timezone=True), nullable=True)
    scheduled_send_at = Column(DateTime(timezone=True), nullable=True)
    subject = Column(Text, nullable=True)
    body = Column(Text, nullable=True)
    reply_received_at = Column(DateTime(timezone=True), nullable=True)
    reply_body = Column(Text, nullable=True)
    reply_intent = Column(
        Text, nullable=True
    )  # Interested, Counter, Pass, Question, Unsubscribe, Buybox_Changed, Other
    ai_extracted_insights = Column(Text, nullable=True)
    buyer_profile_updated = Column(Boolean, default=False)
    question_round = Column(Integer, default=0)
    ghost_detected_at = Column(DateTime(timezone=True), nullable=True)
    ghost_recovery_touch = Column(Integer, default=0)
    ghost_recovery_sent_at = Column(DateTime(timezone=True), nullable=True)
    pass_reason_category = Column(Text, nullable=True)  # price_too_high, wrong_market, condition, etc.
    pass_reason_raw = Column(Text, nullable=True)  # Buyer's exact words (max 500 chars)
    pass_reason_confidence = Column(Text, nullable=True)  # high, medium, low
    passed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    deal = relationship("Deal", back_populates="campaigns")
    buyer = relationship("Buyer", back_populates="campaigns")
    failed_campaigns = relationship("FailedCampaign", back_populates="campaign")

    def __repr__(self) -> str:
        return f"<Campaign(id={self.id}, touch={self.touch_number})>"


class EmailVerification(Base):
    __tablename__ = "email_verifications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    buyer_id = Column(UUID(as_uuid=True), ForeignKey("buyers.id"), nullable=False)
    email = Column(Text, nullable=False)
    result = Column(Text, nullable=True)  # valid, invalid, catch_all, unknown
    score = Column(Float, nullable=True)
    verified_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    buyer = relationship("Buyer", back_populates="email_verifications")

    def __repr__(self) -> str:
        return f"<EmailVerification(id={self.id}, email={self.email})>"


class ActivityLog(Base):
    __tablename__ = "activity_log"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_type = Column(Text, nullable=True)  # buyer, deal, campaign, jv
    entity_id = Column(UUID(as_uuid=True), nullable=True)
    action = Column(Text, nullable=True)
    metadata_json = Column("metadata", JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return f"<ActivityLog(id={self.id}, action={self.action})>"


class QueuedDealMatch(Base):
    """A deal-to-buyer match that was queued because the buyer was at their
    max active deals cap. Released automatically by the scheduler when the
    buyer's active deal count drops below the cap.

    When a buyer's queued match is released, it should be re-validated
    against the buyer's CURRENT buy_box and hard filters before use.
    """

    __tablename__ = "queued_deal_matches"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    buyer_id = Column(UUID(as_uuid=True), ForeignKey("buyers.id", ondelete="CASCADE"), nullable=False, index=True)
    deal_id = Column(UUID(as_uuid=True), ForeignKey("deals.id", ondelete="CASCADE"), nullable=False, index=True)
    status = Column(Text, default="waiting", index=True)  # waiting, invalidated, released, expired
    similarity_score = Column(Float, nullable=True)  # Snapshot of similarity at queue time
    queued_at = Column(DateTime(timezone=True), server_default=func.now())
    released_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    buyer = relationship("Buyer")
    deal = relationship("Deal")

    def __repr__(self) -> str:
        return f"<QueuedDealMatch(id={self.id}, buyer={self.buyer_id}, deal={self.deal_id}, status={self.status})>"


class FailedCampaign(Base):
    __tablename__ = "failed_campaigns"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    campaign_id = Column(UUID(as_uuid=True), ForeignKey("campaigns.id"), nullable=False)
    error_message = Column(Text, nullable=False)
    retry_count = Column(Integer, default=0)
    last_retry_at = Column(DateTime(timezone=True), nullable=True)
    resolved = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    campaign = relationship("Campaign", back_populates="failed_campaigns")

    def __repr__(self) -> str:
        return f"<FailedCampaign(id={self.id}, campaign_id={self.campaign_id}, retry_count={self.retry_count})>"


class AppState(Base):
    """Persistent key-value store for in-memory subsystem state.

    Each row stores a serialized JSON blob for one subsystem:
    - "circuit_breaker_queue": CB queued emails
    - "metrics": Resilience metrics counters
    - "idempotency_store": Idempotency cache entries
    - "groq_daily_counter": Groq API daily call count

    Used to survive server restarts so critical state (queued emails,
    rate-limit counters, idempotency cache) is not lost.
    """

    __tablename__ = "app_state"

    key = Column(Text, primary_key=True)
    value = Column(JSONB, nullable=False, default=dict)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    def __repr__(self) -> str:
        return f"<AppState(key={self.key})>"
