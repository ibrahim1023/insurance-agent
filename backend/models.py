import math
from datetime import datetime
from decimal import Decimal
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .locales import validate_country, validate_currency


def normalize_country(value: str) -> str:
    return validate_country(value)


def normalize_currency(value: str) -> str:
    return validate_currency(value)


def _positive_finite_decimal(value: Decimal) -> Decimal:
    if not value.is_finite() or value <= 0:
        raise ValueError("amount must be a positive finite decimal")
    return value


def _aware_iso_timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError("expected an ISO-8601 timestamp") from None
    if parsed.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return value


def _https_listing_url(value: str) -> str:
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        raise ValueError("invalid url") from None
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or port not in (None, 443)
    ):
        raise ValueError("expected an https url on the default port")
    return value


class FrameRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    frame_ref: str
    timestamp_s: float = Field(ge=0, allow_inf_nan=False)
    width_px: int = Field(gt=0)
    height_px: int = Field(gt=0)
    sha256: str | None = None
    sharpness_score: float | None = Field(default=None, allow_inf_nan=False)


class CoverageDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    frame_id: str = Field(min_length=1)
    disposition: Literal["selected", "deferred", "rejected", "unresolved"]
    reason: str = Field(min_length=1)
    row_id: str | None = None
    covered_range: tuple[float, float] | None = None
    duplicate_of: str | None = None
    processing_status: Literal[
        "pending",
        "running",
        "completed",
        "cached",
        "failed",
        "skipped",
        "interrupted",
    ] = "pending"
    stage_refs: list[str] = Field(default_factory=list)

    @field_validator("covered_range")
    @classmethod
    def _range_finite(cls, value):
        if value is not None and (
            not all(math.isfinite(v) for v in value) or value[0] > value[1]
        ):
            raise ValueError("covered_range must be finite and ordered")
        return value


class CoverageGap(BaseModel):
    model_config = ConfigDict(extra="forbid")
    frame_ids: list[str] = Field(default_factory=list)
    row_id: str | None = None
    reason: str = Field(min_length=1)


class CoverageManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal["coverage-v1"] = "coverage-v1"
    entries: list[CoverageDecision] = Field(default_factory=list)
    gaps: list[CoverageGap] = Field(default_factory=list)
    spatial_coverage_status: Literal["unresolved", "supported"] = "unresolved"


class ShelfAtlasRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1)
    frame_ids: list[str] = Field(min_length=2)
    frame_transforms: dict[str, list[list[float]]]
    width_px: int = Field(gt=0)
    height_px: int = Field(gt=0)
    covered_ranges: list[tuple[float, float]]
    gaps: list[tuple[float, float]]
    status: Literal["supported", "unresolved"]


class RegionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1)
    excluded: bool = False


class UserTurnRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    turn_id: str = Field(min_length=1)
    text: str = Field(min_length=1, max_length=8000)
    source: Literal["typed_user", "provider_user_transcript"]

    @field_validator("text")
    @classmethod
    def _text_not_blank(cls, value):
        if not value.strip():
            raise ValueError("turn text must not be blank")
        return value


class CorrectionIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["exclude_region", "exclude_item", "assert_edition", "assert_art_print"]
    target_id: str = Field(min_length=1)
    value: str | bool | None = None
    source_turn_id: str = Field(min_length=1)
    source_frame_id: str | None = None


class ReviewItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ref_id: str
    reason: str


class CorrectionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    applied: bool
    revision: int
    review_items: list[ReviewItem]


class BBoxXYWH(BaseModel):
    model_config = ConfigDict(extra="forbid")
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    w: int = Field(gt=0)
    h: int = Field(gt=0)


class SourceQuote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount: Decimal
    currency: str
    condition: Literal["new", "used"]
    market_country: str
    url: str
    retrieved_at: str
    matched_title: str = Field(min_length=1)
    matched_author: str = Field(min_length=1)
    listing_title: str | None = None
    listing_author: str | None = None
    listing_format: str = Field(min_length=1)
    listing_availability: str = Field(min_length=1)
    market_evidence: str = Field(min_length=1)
    listing_isbn: str | None = None
    listing_edition: str | None = None
    source_snapshot_ref: str | None = None
    discovery_stage_ref: str | None = None

    @field_validator("amount")
    @classmethod
    def _amount_ok(cls, value):
        return _positive_finite_decimal(value)

    @field_validator("currency")
    @classmethod
    def _currency_ok(cls, value):
        return validate_currency(value)

    @field_validator("market_country")
    @classmethod
    def _market_ok(cls, value):
        return validate_country(value)

    @field_validator("url")
    @classmethod
    def _url_ok(cls, value):
        return _https_listing_url(value)

    @field_validator("retrieved_at")
    @classmethod
    def _retrieved_ok(cls, value):
        return _aware_iso_timestamp(value)


class FxEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["provider_feed", "currency_identity"]
    source_url: str | None = None
    data_at: str | None = None
    retrieved_at: str
    source_currency: str
    target_currency: str
    source_rate: Decimal
    target_rate: Decimal
    cross_rate: Decimal
    attribution: str | None = None
    attribution_url: str | None = None

    @field_validator("source_currency", "target_currency")
    @classmethod
    def _fx_currency_ok(cls, value):
        return validate_currency(value)

    @field_validator("retrieved_at")
    @classmethod
    def _fx_retrieved_ok(cls, value):
        return _aware_iso_timestamp(value)

    @field_validator("data_at")
    @classmethod
    def _fx_data_ok(cls, value):
        return None if value is None else _aware_iso_timestamp(value)

    @field_validator("source_url", "attribution_url")
    @classmethod
    def _fx_url_ok(cls, value):
        return None if value is None else _https_listing_url(value)

    @field_validator("source_rate", "target_rate", "cross_rate")
    @classmethod
    def _fx_rate_ok(cls, value):
        return _positive_finite_decimal(value)

    @model_validator(mode="after")
    def _fx_consistent(self):
        if self.cross_rate != self.target_rate / self.source_rate:
            raise ValueError("cross_rate inconsistent with source and target rates")
        if self.kind == "provider_feed" and (
            self.source_url is None
            or self.data_at is None
            or not self.attribution
            or self.attribution_url is None
        ):
            raise ValueError("provider FX evidence requires source url, data date and attribution")
        if self.kind == "currency_identity" and (
            self.source_currency != self.target_currency
            or self.source_rate != 1
            or self.target_rate != 1
            or self.cross_rate != 1
        ):
            raise ValueError("identity FX evidence requires equal currencies and unit rates")
        return self


class BookPrice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount: Decimal | None = None
    currency: str | None = None
    source: str | None = None
    url: str | None = None
    retrieved_at: str | None = None
    converted: bool = False
    condition_assumed: str | None = None
    foreign_market_fallback: bool = False
    foreign_market_same_currency: bool = False
    edition_equivalence: str | None = None
    source_quote: SourceQuote | None = None
    fx_evidence: FxEvidence | None = None
    valuation_stage_ref: str | None = None

    @field_validator("amount")
    @classmethod
    def _price_amount_ok(cls, value):
        return None if value is None else _positive_finite_decimal(value)

    @field_validator("currency")
    @classmethod
    def _price_currency_ok(cls, value):
        return None if value is None else validate_currency(value)

    @field_validator("url")
    @classmethod
    def _price_url_ok(cls, value):
        return None if value is None else _https_listing_url(value)

    @field_validator("retrieved_at")
    @classmethod
    def _price_retrieved_ok(cls, value):
        return None if value is None else _aware_iso_timestamp(value)

    @model_validator(mode="after")
    def _populated_requires_origin(self):
        if self.amount is not None and (
            self.url is None
            or self.retrieved_at is None
            or self.currency is None
            or not self.source
            or self.source_quote is None
        ):
            raise ValueError(
                "a populated price requires source, source_quote, url, retrieved_at and currency"
            )
        return self


class ItemPriceRange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    low: float | None = None
    high: float | None = None
    source: str | None = None
    url: str | None = None
    retrieved_at: str | None = None


class DimensionsCM(BaseModel):
    model_config = ConfigDict(extra="forbid")
    w: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    h: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    d: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class AssertionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str = Field(min_length=1)
    value: str | bool | None = None
    source_turn_id: str = Field(min_length=1)
    recorded_at: str
    needs_review: bool = False


class ObservationEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    frame_id: str
    frame_ref: str
    bbox: BBoxXYWH
    image_width: int = Field(gt=0)
    image_height: int = Field(gt=0)
    sharpness: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class CatalogueEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str = Field(min_length=1)
    url: str = Field(min_length=1)
    retrieved_at: str = Field(min_length=1)
    matched_title: str = ""
    matched_author: str = ""
    matched_publisher: str = ""
    matched_edition: str = ""
    listing_isbn: str = ""


class SpineReading(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ref_id: str = Field(min_length=1)
    stage_ref: str | None = None
    title: str = ""
    author: str = ""
    publisher: str = ""
    edition: str = ""
    isbn: str = ""
    title_legible: bool = False
    author_legible: bool = False
    confidence: float = Field(default=0, ge=0, le=1, allow_inf_nan=False)
    visible_text: list[str] = Field(default_factory=list)
    status: Literal["accepted", "low_confidence", "unreadable", "failed", "contradicted"] = "unreadable"
    catalogue: CatalogueEvidence | None = None


class BookTrack(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1)
    shelf: str | None = None
    shelf_reason: str | None = None
    position: int | None = Field(default=None, ge=1)
    frame_ref: str
    status: Literal["identified", "unidentified", "needs_appraisal"] = "unidentified"
    title: str = ""
    author: str = ""
    publisher: str = ""
    edition: str = ""
    isbn: str = ""
    spine_height_cm: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    spine_thickness_cm: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    id_confidence: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    replacement_cost: BookPrice | None = None
    used_value: BookPrice | None = None
    bbox: BBoxXYWH
    image_width: int = Field(gt=0)
    image_height: int = Field(gt=0)
    observed_fragments: list[dict] = Field(default_factory=list)
    evidence: list[ObservationEvidence] = Field(default_factory=list)
    excluded: bool = False
    assertions: list[AssertionRecord] = Field(default_factory=list)
    identification_evidence: list[SpineReading] = Field(default_factory=list)
    potential_duplicate: bool = False
    crop_refs: list[str] = Field(default_factory=list)
    track_histogram: list[float] | None = None


class ItemTrack(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1)
    category: str = Field(min_length=1)
    description: str = ""
    material: str = ""
    brand_model: str = ""
    frame_ref: str
    dimensions_cm: DimensionsCM | None = None
    status: Literal["priced", "range", "needs_appraisal"] = "range"
    valuation_state: Literal["unpriced", "priced", "appraisal_pending"] = "unpriced"
    replacement_cost: ItemPriceRange | None = None
    confidence: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    bbox: BBoxXYWH
    image_width: int = Field(gt=0)
    image_height: int = Field(gt=0)
    evidence: list[ObservationEvidence] = Field(default_factory=list)
    excluded: bool = False
    assertions: list[AssertionRecord] = Field(default_factory=list)
    potential_duplicate: bool = False
    track_histogram: list[float] | None = None


class VisionStageRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    frame_id: str
    status: Literal["completed", "failed", "cached", "skipped"]
    persisted_ref: str | None = None
    model: str | None = None
    cost_usd_estimate: str | None = None
    elapsed_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    error: str | None = None


class ReadingStageRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ref_ids: list[str] = Field(default_factory=list)
    status: Literal["completed", "failed", "skipped", "running", "interrupted"]
    persisted_ref: str | None = None
    model: str | None = None
    cost_usd_estimate: str | None = None
    elapsed_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    error: str | None = None


class PricingStageRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["discovery", "source", "valuation"]
    ref_ids: list[str] = Field(default_factory=list)
    status: Literal["completed", "failed", "skipped", "running", "interrupted", "cached"]
    persisted_ref: str | None = None
    model: str | None = None
    usage: dict | None = None
    cost_usd_estimate: str | None = None
    cost_evidence: dict | None = None
    elapsed_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    error: str | None = None


class VisionObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    frame_id: str
    source_frame_id: str
    observation: dict
    box_mapping: dict
    stage_ref: str
    model: str | None = None
    cost_usd_estimate: str | None = None
    elapsed_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    usage: dict | None = None
    image_width: int = Field(gt=0)
    image_height: int = Field(gt=0)
    sent_image_width: int = Field(gt=0)
    sent_image_height: int = Field(gt=0)
    coordinate_system: str = "normalized_1000"
    cached: bool = False


class SweepState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    revision: int
    status: Literal["capturing", "processing", "partial", "complete", "interrupted", "failed"]
    captured_at: str
    ended_at: str | None
    country: str
    currency: str
    device: str = Field(max_length=200)
    duration_s: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    frames: list[FrameRecord] = Field(default_factory=list)
    regions: list[RegionRecord] = Field(default_factory=lambda: [RegionRecord(id="room")])
    books: list[BookTrack] = Field(default_factory=list)
    unresolved_books: list[BookTrack] = Field(default_factory=list)
    scene_reconciliation: dict | None = None
    items: list[ItemTrack] = Field(default_factory=list)
    user_turns: list[UserTurnRecord] = Field(default_factory=list)
    corrections: list[CorrectionIntent] = Field(default_factory=list)
    review_queue: list[ReviewItem] = Field(default_factory=list)
    vision_stages: list[VisionStageRecord] = Field(default_factory=list)
    coverage_manifest: CoverageManifest | None = None
    reading_stages: list[ReadingStageRecord] = Field(default_factory=list)
    price_stages: list[PricingStageRecord] = Field(default_factory=list)
    latest_observation: VisionObservation | None = None
    last_analyzed_frame_id: str | None = None
    reading_deadline_at: str | None = None
    pricing_deadline_at: str | None = None
    postwork_status: Literal[
        "not_started",
        "running",
        "completed",
        "failed",
        "timed_out",
        "interrupted",
    ] = "not_started"
    postwork_stage: Literal["vision", "reading", "pricing", "packet"] | None = None
    postwork_started_at: str | None = None
    postwork_finished_at: str | None = None
    processing_timings_ms: dict = Field(default_factory=dict)
    inventory_complete: bool = False
    processing_complete: bool = False
    packet_ref: str | None = None
    report_ref: str | None = None
