"""Generic bootstrap policy. No company/entity exceptions are bundled."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .normalize import normalize_text


BOOTSTRAP_GOVERNANCE_RULE_ID = "PUBLIC_BOOTSTRAP_RULES_V1"


@dataclass(frozen=True)
class HistoricalChannelRule:
    channel_name: str
    channel_type: str
    status: str
    new_business_allowed: bool
    reason: str

    def as_dict(self) -> dict[str, object]:
        return {
            "channel_name": self.channel_name,
            "channel_type": self.channel_type,
            "status": self.status,
            "new_business_allowed": self.new_business_allowed,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class NonSalesExclusionRule:
    source_value: str
    classification: str
    exclusion_reason: str

    def as_dict(self) -> dict[str, str]:
        return {
            "source_value": self.source_value,
            "classification": self.classification,
            "exclusion_reason": self.exclusion_reason,
        }


@dataclass(frozen=True)
class BootstrapGovernanceRules:
    rule_id: str
    historical_channels: tuple[HistoricalChannelRule, ...]
    non_sales_exclusions: tuple[NonSalesExclusionRule, ...]
    confirmed_product_merge_codes: frozenset[str]

    def historical_channel(self, value: str | None) -> HistoricalChannelRule | None:
        normalized = normalize_text(value)
        return next(
            (rule for rule in self.historical_channels if normalize_text(rule.channel_name) == normalized),
            None,
        )

    def non_sales_exclusion(self, value: str | None) -> NonSalesExclusionRule | None:
        normalized = normalize_text(value)
        return next(
            (rule for rule in self.non_sales_exclusions if normalize_text(rule.source_value) == normalized),
            None,
        )

    def confirms_product_merge(self, source_product_code: str | None) -> bool:
        normalized = normalize_text(source_product_code)
        return normalized in self.confirmed_product_merge_codes

    @staticmethod
    def is_source_created_at_label(value: object) -> bool:
        # Public adapters accept actual dates, not internal business-label exceptions.
        return False

    def as_dict(self) -> dict[str, object]:
        return {
            "rule_id": self.rule_id,
            "scope": "BOOTSTRAP_ONLY",
            "historical_channels": [rule.as_dict() for rule in self.historical_channels],
            "non_sales_exclusions": [rule.as_dict() for rule in self.non_sales_exclusions],
            "confirmed_product_merge_codes": sorted(self.confirmed_product_merge_codes),
        }


BOOTSTRAP_GOVERNANCE_RULES = BootstrapGovernanceRules(
    rule_id=BOOTSTRAP_GOVERNANCE_RULE_ID,
    historical_channels=(),
    non_sales_exclusions=(),
    confirmed_product_merge_codes=frozenset(),
)
