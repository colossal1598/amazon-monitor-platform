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


class RunRequest(BaseModel):
    cadence: Optional[Literal["short", "long"]] = None
    group_id: Optional[int] = None
    trigger: str = "scheduled"


class JobClaim(BaseModel):
    worker_id: str


class JobResult(BaseModel):
    rows: list[dict[str, Any]] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    captcha: bool = False
    error: Optional[str] = None
