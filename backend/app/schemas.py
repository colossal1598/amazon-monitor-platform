"""Pydantic request/response models."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class GroupFilterModel(BaseModel):
    accepted_sellers: list[str] = Field(default_factory=list)
    required_keywords: list[str] = Field(default_factory=list)
    blacklist_keywords: list[str] = Field(default_factory=list)
    blacklist_asins: list[str] = Field(default_factory=list)
    min_price: Optional[float] = None
    max_price: Optional[float] = None
    require_free_shipping: bool = False
    require_shipping_signal: bool = False
    require_shippable: bool = True
    price_drop_percent: float = 10
    alert_new: bool = True
    alert_back_in_stock: bool = True
    alert_price_drop: bool = True


def default_filter() -> dict[str, Any]:
    """Default group filter values (dashboard / filter_defaults endpoint)."""
    return GroupFilterModel().model_dump()


class GroupCreate(BaseModel):
    name: str
    kind: Literal["pdp", "serp"]
    niche: Optional[str] = None
    cadence: Literal["short", "long"] = "short"
    interval_minutes: Optional[int] = None
    enabled: bool = True
    selector_profile_id: Optional[int] = None
    headless: bool = True
    max_concurrent: int = 2
    notify_channel: Optional[str] = None
    filter: GroupFilterModel = Field(default_factory=GroupFilterModel)


class GroupUpdate(BaseModel):
    name: Optional[str] = None
    niche: Optional[str] = None
    cadence: Optional[Literal["short", "long"]] = None
    interval_minutes: Optional[int] = None
    enabled: Optional[bool] = None
    selector_profile_id: Optional[int] = None
    headless: Optional[bool] = None
    max_concurrent: Optional[int] = None
    notify_channel: Optional[str] = None
    filter: Optional[GroupFilterModel] = None


class PdpTargetCreate(BaseModel):
    asin: str
    enabled: bool = True
    notes: Optional[str] = None


class SerpTargetCreate(BaseModel):
    search_url: str
    label: Optional[str] = None
    scrape_mode: Literal["newest_front", "featured_full"] = "newest_front"
    max_pages: int = 1
    enabled: bool = True


class SelectorProfileCreate(BaseModel):
    name: str
    marketplace: str = "amazon.com"
    locale: str = "en-IL"
    selectors: dict[str, Any]
    is_default: bool = False


class SelectorProfileUpdate(BaseModel):
    name: Optional[str] = None
    marketplace: Optional[str] = None
    locale: Optional[str] = None
    selectors: Optional[dict[str, Any]] = None
    is_default: Optional[bool] = None


class JobClaim(BaseModel):
    worker_id: str


class JobCreate(BaseModel):
    group_key: str
    kind: Literal["pdp", "serp"]
    payload: dict[str, Any]
    browser_profile: Optional[str] = None
    attempt: int = 1
    run_id: Optional[int] = None
    trigger: Optional[str] = None


class JobResult(BaseModel):
    rows: list[dict[str, Any]] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    captcha: bool = False
    error: Optional[str] = None
    scrape_quality: Optional[str] = None
    browser_profile: Optional[str] = None
    attempt: Optional[int] = None
    timing_ms: Optional[dict[str, Any]] = None


class ProductStateUpsert(BaseModel):
    group_key: str
    asin: str
    title: Optional[str] = None
    seller: Optional[str] = None
    price: Optional[float] = None
    in_stock: bool = False
    image_url: Optional[str] = None
    product_url: Optional[str] = None
    group_id: Optional[int] = None


class PriceHistoryCreate(BaseModel):
    group_key: str
    asin: str
    price: Optional[float] = None
    in_stock: Optional[bool] = None
    group_id: Optional[int] = None


class AlertCreate(BaseModel):
    group_key: str
    asin: str
    alert_type: str
    title: Optional[str] = None
    old_price: Optional[float] = None
    new_price: Optional[float] = None
    image_url: Optional[str] = None
    product_url: Optional[str] = None
    notify_channel: Optional[str] = None
    group_id: Optional[int] = None


class StateQuery(BaseModel):
    group_key: str
