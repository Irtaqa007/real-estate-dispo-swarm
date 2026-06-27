"""Pydantic schemas (request/response models) for the application."""

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, model_validator


class BuyerBase(BaseModel):
    """Shared fields for buyer CRUD operations."""

    full_name: str = Field(..., min_length=1, max_length=255)
    email: str = Field(..., max_length=255)
    affiliation: Optional[str] = None
    buy_box: str = Field(..., min_length=1)
    buyer_tier: str = Field(default="C-List")
    status: str = Field(default="Active")
    notes: Optional[str] = None
    price_min: Optional[float] = Field(None, ge=0)
    price_max: Optional[float] = Field(None, ge=0)
    pref_property_type: Optional[str] = None  # House, Land, or NULL (both)
    pref_cities: Optional[list[str]] = None  # Preferred cities/areas


class BuyerCreate(BuyerBase):
    """Schema for creating a new buyer. Inherits all BuyerBase fields."""
    pass


class BuyerUpdate(BaseModel):
    """Schema for updating a buyer. All fields are optional."""

    full_name: Optional[str] = Field(None, min_length=1, max_length=255)
    email: Optional[str] = Field(None, max_length=255)
    affiliation: Optional[str] = None
    buy_box: Optional[str] = Field(None, min_length=1)
    buyer_tier: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None
    price_min: Optional[float] = Field(None, ge=0)
    price_max: Optional[float] = Field(None, ge=0)
    pref_property_type: Optional[str] = None
    pref_cities: Optional[list[str]] = None


class BuyerResponse(BuyerBase):
    """Schema for buyer responses. Includes all DB-generated and computed fields."""

    id: UUID
    email_verified: bool = False
    email_verification_status: Optional[str] = None
    response_rate: Optional[float] = 0
    avg_response_time_hours: Optional[float] = None
    deals_viewed: Optional[int] = 0
    deals_offered_on: Optional[int] = 0
    offers_accepted: Optional[int] = 0
    offers_rejected: Optional[int] = 0
    deals_closed: Optional[int] = 0
    deads_fell_through: Optional[int] = 0
    avg_spread_closed: Optional[float] = None
    total_lifetime_spread: Optional[float] = 0
    engagement_score: Optional[float] = 0
    email_verified_at: Optional[datetime] = None
    last_pitch_sent_at: Optional[datetime] = None
    last_reply_at: Optional[datetime] = None
    pitches_this_week: Optional[int] = 0
    pitches_this_week_reset_at: Optional[datetime] = None
    unsubscribed_at: Optional[datetime] = None
    portfolio_insights: Optional[dict] = None
    additional_emails: List[str] = []
    has_embedding: bool = False
    price_min: Optional[float] = None
    price_max: Optional[float] = None
    pref_property_type: Optional[str] = None
    pref_cities: Optional[list[str]] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class JVPartnerBase(BaseModel):
    """Shared fields for JV partner CRUD operations."""

    name: str = Field(..., min_length=1, max_length=255)
    email: str = Field(..., max_length=255)
    phone: Optional[str] = None
    source: Optional[str] = None


class JVPartnerCreate(JVPartnerBase):
    """Schema for creating a new JV partner."""
    pass


class JVPartnerUpdate(BaseModel):
    """Schema for updating a JV partner. All fields are optional."""

    name: Optional[str] = Field(None, min_length=1, max_length=255)
    email: Optional[str] = Field(None, max_length=255)
    phone: Optional[str] = None
    source: Optional[str] = None


class JVPartnerResponse(JVPartnerBase):
    """Schema for JV partner responses. Includes all DB-generated fields."""

    id: UUID
    deals_linked: list[UUID] = []
    total_deals_submitted: int = 0
    total_deals_closed: int = 0
    total_revenue_generated: float = 0
    avg_buyer_feedback_score: float = 0
    title_issue_rate: float = 0
    overprice_flag_count: int = 0
    total_split_revenue: float = 0
    created_at: datetime

    model_config = {"from_attributes": True}


class DealFields(BaseModel):
    """Pure field definitions for a deal, with NO validators.

    DealBase (below) extends this with create/update-time validation
    rules. DealResponse inherits directly from DealFields instead of
    DealBase, so reading existing rows from the database never fails
    validation just because they predate a rule, or were written by a
    path that didn't enforce it. Validation belongs on the way IN
    (create/update), never on the way OUT (response) -- a read
    endpoint should reflect what's actually in the database, not
    reject it.
    """

    address: str = Field(..., min_length=1)
    city: Optional[str] = None
    state: Optional[str] = None
    zip: Optional[str] = None
    county: Optional[str] = None
    property_type: str = Field(..., pattern=r"^(House|Land)$")
    beds: Optional[int] = None
    baths: Optional[float] = None
    sqft: Optional[int] = None
    year_built: Optional[int] = None
    occupancy_status: Optional[str] = None
    repair_estimate: Optional[float] = None
    lot_size: Optional[str] = None
    zoning: Optional[str] = None
    utilities_available: Optional[list[str]] = None
    topography_access: Optional[str] = None
    condition_description: str = Field(..., min_length=1)
    arv: float = Field(...)
    asking_price: float = Field(...)
    floor_price: float = Field(...)
    contract_price: float = Field(...)
    title_status: str = Field(...)
    photos: Optional[list[str]] = None
    jv_partner_id: UUID = Field(...)  # Required — every deal must have a JV partner
    jv_split_percentage: Optional[float] = Field(default=50, ge=0, le=100)


class DealBase(DealFields):
    """Shared fields + validation for deal CREATE/UPDATE operations only.

    Validates conditional fields based on property_type:
    - House: beds, baths, sqft required
    - Land: lot_size, zoning required

    Do NOT use this as a base for response schemas — see DealFields
    and DealResponse for why.
    """

    @model_validator(mode="after")
    def validate_property_fields(self) -> "DealBase":
        if self.property_type == "House":
            if self.beds is None:
                raise ValueError("beds is required when property_type is 'House'")
            if self.baths is None:
                raise ValueError("baths is required when property_type is 'House'")
            if self.sqft is None:
                raise ValueError("sqft is required when property_type is 'House'")
        elif self.property_type == "Land":
            if not self.lot_size:
                raise ValueError("lot_size is required when property_type is 'Land'")
            if not self.zoning:
                raise ValueError("zoning is required when property_type is 'Land'")
        return self

    @model_validator(mode="after")
    def validate_prices(self) -> "DealBase":
        """Validate price hierarchy: contract < floor < asking.

        Ensures the assignment fee (spread = asking - contract) is protected:
        - floor_price must be above contract_price (so there's margin)
        - floor_price must be below asking_price (so offers can beat the floor)
        """
        if self.floor_price is not None and self.asking_price is not None:
            if self.floor_price >= self.asking_price:
                raise ValueError(
                    f"floor_price (${self.floor_price:,.2f}) must be less than "
                    f"asking_price (${self.asking_price:,.2f})"
                )
        if self.floor_price is not None and self.contract_price is not None:
            if self.floor_price <= self.contract_price:
                raise ValueError(
                    f"floor_price (${self.floor_price:,.2f}) must be greater than "
                    f"contract_price (${self.contract_price:,.2f}) — the spread needs room"
                )
        return self


class DealCreate(DealBase):
    """Schema for creating a new deal. Inherits all DealBase fields with validation."""
    pass


class DealUpdate(BaseModel):
    """Schema for updating a deal. All fields are optional."""

    address: Optional[str] = Field(None, min_length=1)
    city: Optional[str] = None
    state: Optional[str] = None
    zip: Optional[str] = None
    county: Optional[str] = None
    property_type: Optional[str] = Field(None, pattern=r"^(House|Land)$")
    beds: Optional[int] = None
    baths: Optional[float] = None
    sqft: Optional[int] = None
    year_built: Optional[int] = None
    occupancy_status: Optional[str] = None
    repair_estimate: Optional[float] = None
    lot_size: Optional[str] = None
    zoning: Optional[str] = None
    utilities_available: Optional[list[str]] = None
    topography_access: Optional[str] = None
    condition_description: Optional[str] = Field(None, min_length=1)
    arv: Optional[float] = None
    asking_price: Optional[float] = None
    floor_price: Optional[float] = None
    contract_price: Optional[float] = None
    title_status: Optional[str] = None
    photos: Optional[list[str]] = None
    jv_partner_id: Optional[UUID] = None
    jv_split_percentage: Optional[float] = Field(None, ge=0, le=100)

    @model_validator(mode="after")
    def validate_prices(self) -> "DealUpdate":
        """Validate price hierarchy when prices are being updated.

        Only validates the fields that were actually provided.
        floor < asking and floor > contract.
        """
        fp = self.floor_price
        ap = self.asking_price
        cp = self.contract_price

        if fp is not None and ap is not None:
            if fp >= ap:
                raise ValueError(
                    f"floor_price (${fp:,.2f}) must be less than asking_price (${ap:,.2f})"
                )
        if fp is not None and cp is not None:
            if fp <= cp:
                raise ValueError(
                    f"floor_price (${fp:,.2f}) must be greater than contract_price (${cp:,.2f})"
                )
        return self


class DealResponse(DealFields):
    """Schema for deal responses. Includes all DB-generated fields.

    Inherits from DealFields (fields only, no validators) rather than
    DealBase, so existing rows that predate a validation rule -- or
    have any null/incomplete field for any reason -- can still be
    read back successfully. Validation belongs on create/update, not
    on read.

    Several fields that are required on DealCreate are re-declared as
    Optional here for the same reason: a response schema should
    reflect what's actually in the database, not reject rows that
    don't (yet, or anymore) fully satisfy input-time rules.
    """

    id: UUID
    property_type: Optional[str] = None
    condition_description: Optional[str] = None
    arv: Optional[float] = None
    asking_price: Optional[float] = None
    floor_price: Optional[float] = None
    contract_price: Optional[float] = None
    title_status: Optional[str] = None
    status: str = "Available"
    assigned_buyer_id: Optional[UUID] = None
    jv_partner_id: Optional[UUID] = None
    spread: Optional[float] = None
    priority_score: Optional[float] = 0.0
    closed_at: Optional[datetime] = None
    closed_price: Optional[float] = None
    net_spread: Optional[float] = None
    jv_payout: Optional[float] = None
    my_payout: Optional[float] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Matching schemas
# ---------------------------------------------------------------------------


class BuyerMatchResult(BaseModel):
    """A single buyer match result with similarity score."""

    id: UUID
    full_name: str
    email: str
    buy_box: str
    affiliation: Optional[str] = None
    buyer_tier: Optional[str] = None
    similarity: float


class MatchResponse(BaseModel):
    """Response containing ranked buyer matches for a deal."""

    deal_id: UUID
    deal_address: str
    matches: List[BuyerMatchResult]


# ---------------------------------------------------------------------------
# Campaign / Email schemas
# ---------------------------------------------------------------------------


class CampaignResponse(BaseModel):
    """Schema for campaign responses. Includes all DB-generated fields."""

    id: UUID
    deal_id: UUID
    buyer_id: UUID
    touch_number: int
    status: str = "Queued"
    sent_at: Optional[datetime] = None
    scheduled_send_at: Optional[datetime] = None
    subject: Optional[str] = None
    body: Optional[str] = None
    reply_received_at: Optional[datetime] = None
    reply_body: Optional[str] = None
    reply_intent: Optional[str] = None
    ai_extracted_insights: Optional[str] = None
    buyer_profile_updated: bool = False
    question_round: int = 0
    created_at: datetime

    model_config = {"from_attributes": True}


class CampaignTouch(BaseModel):
    """A single generated touch email within a campaign."""

    touch: int
    subject: str
    body: str
    status: str
    scheduled_at: Optional[str] = None


class CampaignLaunchResult(BaseModel):
    """Result of a campaign launch for a single buyer."""

    buyer_id: UUID
    buyer_name: str
    buyer_email: str
    buyer_tier: str
    similarity_score: float
    touches: List[CampaignTouch]


class CampaignLaunchResponse(BaseModel):
    """Response from launching a campaign for a deal."""

    deal_id: UUID
    deal_address: str
    total_buyers: int
    total_campaigns_created: int
    results: List[CampaignLaunchResult]


class CampaignScheduleItem(BaseModel):
    """A scheduled touch in the campaign timeline."""

    touch: int
    delay_days: int
    scheduled_at: str
    arc: str
    subject_formula: str
    cta_type: str


# ---------------------------------------------------------------------------
# Email send schemas
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Deal status transition schemas
# ---------------------------------------------------------------------------


class UnderContractRequest(BaseModel):
    """Schema for moving a deal to Under Contract status."""

    assigned_buyer_id: Optional[UUID] = None


class CloseDealRequest(BaseModel):
    """Schema for closing a deal (marking as Sold)."""

    closed_price: Optional[float] = None


class CloseDealResponse(BaseModel):
    """Response after closing a deal with calculated payouts."""

    id: UUID
    status: str
    closed_at: datetime
    closed_price: float
    net_spread: float
    jv_payout: Optional[float] = None
    my_payout: Optional[float] = None
    buyer_updated: bool = False
    jv_updated: bool = False


class SendResponse(BaseModel):
    """Response from sending a single campaign email."""

    campaign_id: UUID
    to_email: str
    subject: str
    message_id: str
    status: str
    sent_at: str


class SendAllItem(BaseModel):
    """Result of sending one email in a bulk send operation."""

    campaign_id: UUID
    touch_number: int
    to_email: str
    status: str
    message_id: Optional[str] = None
    error: Optional[str] = None


class SendAllResponse(BaseModel):
    """Response from sending all Ready campaigns for a deal."""

    deal_id: UUID
    total_ready: int
    sent_count: int
    failed_count: int
    results: List[SendAllItem]


# ---------------------------------------------------------------------------
# Reply checking schemas
# ---------------------------------------------------------------------------


class ReplyCheckItem(BaseModel):
    """Result of processing a single buyer reply."""

    from_email: str
    subject: str
    reply_intent: str
    campaign_id: Optional[UUID] = None
    deal_id: Optional[UUID] = None
    buyer_id: Optional[UUID] = None
    matched: bool = False
    campaigns_paused: int = 0
    error: Optional[str] = None


class CheckRepliesResponse(BaseModel):
    """Response from manually triggering a reply check."""

    total_replies_found: int
    replies_processed: int
    results: List[ReplyCheckItem]


# ---------------------------------------------------------------------------
# Title email coordination schemas
# ---------------------------------------------------------------------------


class TitleEmailCheckItem(BaseModel):
    """Result of processing a single title company email."""

    from_email: str
    subject: str
    intent: str
    deal_matched: bool = False
    deal_address: Optional[str] = None
    action_taken: bool = False
    summary: str = ""


class TitleCheckEmailsResponse(BaseModel):
    """Response from manually triggering a title email check."""

    total_found: int
    processed: int
    results: List[TitleEmailCheckItem]


# ---------------------------------------------------------------------------
# Dead Letter Queue schemas
# ---------------------------------------------------------------------------


class FailedCampaignResponse(BaseModel):
    """Schema for a failed campaign entry in the dead letter queue."""

    id: UUID
    campaign_id: UUID
    error_message: str
    retry_count: int = 0
    last_retry_at: Optional[datetime] = None
    resolved: bool = False
    created_at: datetime
    campaign_subject: Optional[str] = None
    buyer_email: Optional[str] = None
    buyer_name: Optional[str] = None

    model_config = {"from_attributes": True}


class FailedCampaignRetryResponse(BaseModel):
    """Response from retrying a failed campaign."""

    id: UUID
    campaign_id: UUID
    retry_count: int
    success: bool
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Multi-dimensional Reply Intent schemas
# ---------------------------------------------------------------------------


class ReplyIntentDetail(BaseModel):
    """Multi-dimensional reply intent classification."""

    primary_intent: str  # Interested, Counter, Pass, Question, Unsubscribe, Buybox_Changed, Other
    urgency: str = "Medium"  # High, Medium, Low
    sentiment: int = 3  # 1-5
    topics: List[str] = []  # Extracted topics like price, photos, walkthrough
    recommended_action: str = ""  # AI recommendation (send_photos, schedule_walkthrough, etc.)
    counter_price: Optional[float] = None  # If intent is Counter
    ai_summary: str = ""  # One-sentence summary


class NegotiationResponse(BaseModel):
    """Response from a smart negotiation handler."""

    action: str  # auto_approved, needs_manual_review, escalated
    ai_response: str  # The AI-generated response to send to the buyer
    counter_price: float
    floor_price: float
    auto_approved: bool
    contract_price: Optional[float] = None


# ---------------------------------------------------------------------------
# Payment confirmation & Drive cleanup schemas
# ---------------------------------------------------------------------------


class MarkPaidRequest(BaseModel):
    """Schema for confirming payment on a closed deal."""

    payment_amount: float = Field(..., gt=0, description="Actual amount received")
    notes: Optional[str] = Field(None, max_length=500, description="Optional payment notes")


class MarkPaidResponse(BaseModel):
    """Response after confirming payment and archiving Drive folder."""

    deal_id: UUID
    address: str
    payment_confirmed: bool = True
    payment_confirmed_at: Optional[datetime] = None
    payment_amount: Optional[float] = None
    drive_archived: bool = False
    drive_archived_at: Optional[datetime] = None
    drive_archive_folder_id: Optional[str] = None
    shared_links_revoked: int = 0
    message: str = "Payment confirmed and deal folder archived."


class RevenueDealItem(BaseModel):
    """A single deal in the revenue dashboard."""

    deal_id: UUID
    address: str
    closed_at: Optional[datetime] = None
    closed_price: Optional[float] = None
    net_spread: Optional[float] = None
    my_payout: Optional[float] = None
    payment_confirmed: bool = False
    payment_confirmed_at: Optional[datetime] = None
    payment_amount: Optional[float] = None
    jv_partner_name: Optional[str] = None
    status: str


class RevenueDashboardResponse(BaseModel):
    """Revenue dashboard aggregation."""

    total_deals_closed: int = 0
    total_assignment_fees: float = 0.0
    total_my_payout: float = 0.0
    total_my_payout_confirmed: float = 0.0
    total_my_payout_pending: float = 0.0
    deals: List[RevenueDealItem] = []


class DealResponse(DealFields):
    """Schema for deal responses. Includes all DB-generated fields.

    Inherits from DealFields (fields only, no validators) rather than
    DealBase, so existing rows that predate a validation rule -- or
    have any null/incomplete field for any reason -- can still be
    read back successfully. Validation belongs on create/update, not
    on read.

    Several fields that are required on DealCreate are re-declared as
    Optional here for the same reason: a response schema should
    reflect what's actually in the database, not reject rows that
    don't (yet, or anymore) fully satisfy input-time rules.
    """

    id: UUID
    property_type: Optional[str] = None
    condition_description: Optional[str] = None
    arv: Optional[float] = None
    asking_price: Optional[float] = None
    floor_price: Optional[float] = None
    contract_price: Optional[float] = None
    title_status: Optional[str] = None
    status: str = "Available"
    assigned_buyer_id: Optional[UUID] = None
    jv_partner_id: Optional[UUID] = None
    spread: Optional[float] = None
    priority_score: Optional[float] = 0.0
    closed_at: Optional[datetime] = None
    closed_price: Optional[float] = None
    net_spread: Optional[float] = None
    jv_payout: Optional[float] = None
    my_payout: Optional[float] = None
    payment_confirmed: bool = False
    payment_confirmed_at: Optional[datetime] = None
    payment_amount: Optional[float] = None
    drive_folder_id: Optional[str] = None
    drive_archived: bool = False
    drive_archived_at: Optional[datetime] = None
    drive_archive_folder_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Buyer Reengagement schemas
# ---------------------------------------------------------------------------


class BuyerReengagementScheduleResponse(BaseModel):
    """Schema for a buyer reengagement schedule entry."""

    id: UUID
    buyer_id: UUID
    deal_id: Optional[UUID] = None
    stated_window_raw: str
    target_date: datetime
    context_summary: Optional[str] = None
    status: str = "waiting"
    created_at: datetime
    fired_at: Optional[datetime] = None
    cancelled_at: Optional[datetime] = None
    cancellation_reason: Optional[str] = None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Contract Alert schemas
# ---------------------------------------------------------------------------


class ContractAlertItem(BaseModel):
    """A resolved/unresolved contract-ready alert for the dashboard."""

    alert_id: UUID
    created_at: datetime
    buyer_name: Optional[str] = None
    buyer_email: Optional[str] = None
    deal_address: Optional[str] = None
    deal_state: Optional[str] = None
    negotiated_price: Optional[float] = None
    my_payout: Optional[float] = None
    jv_partner_name: Optional[str] = None
    resolved: bool = False
    resolved_at: Optional[datetime] = None
    full_metadata: Optional[dict] = None


class ContractAlertResolveRequest(BaseModel):
    """Schema for resolving a contract alert."""

    notes: Optional[str] = Field(None, max_length=1000)