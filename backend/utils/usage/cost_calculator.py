"""Cost calculation for usage analytics, ported from fs-agent-usage-limits'
utils/cost_calculator.py. Reads pricing from usage_service_pricing (global,
unscoped — see utils/models/usage_pricing.py) and the extraction-method
config YAML for document-parsing / transcription / web-scraping cost lookups.
"""

from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, timezone as _tz
from pathlib import Path
import yaml

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from utils.models.usage_pricing import ServicePricing
from utils.models.usage_events import UsageEvent
from utils.logger import logger

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "config" / "usage"

_extraction_method_config: Optional[Dict] = None


def get_extraction_method_config() -> Dict:
    """Load (and cache) config/usage/extraction_method_config.yaml."""
    global _extraction_method_config
    if _extraction_method_config is None:
        config_path = _CONFIG_DIR / "extraction_method_config.yaml"
        with open(config_path, "r") as fh:
            _extraction_method_config = yaml.safe_load(fh) or {}
        logger.debug("Loaded usage extraction_method_config.yaml")
    return _extraction_method_config


class CostCalculator:
    """Handles cost calculations for different service types and pricing models."""

    def __init__(self, session: AsyncSession, pricing_cache: Optional[Dict] = None):
        self.session = session
        self.pricing_cache = pricing_cache or {}

    async def get_pricing(
        self, service_name: str, provider: str, service_type: str, effective_date: Optional[datetime] = None
    ) -> Optional[Dict[str, Any]]:
        """Pricing active at effective_date: effective_from <= date < effective_to (or open-ended)."""
        lookup_date = effective_date or datetime.now(tz=_tz.utc)
        date_bucket = lookup_date.strftime("%Y-%m-%d")
        cache_key = f"{provider}:{service_name}:{service_type}:{date_bucket}"
        if cache_key in self.pricing_cache:
            return self.pricing_cache[cache_key]

        try:
            query = (
                select(ServicePricing)
                .where(
                    and_(
                        ServicePricing.service_name == service_name,
                        ServicePricing.provider == provider,
                        ServicePricing.service_type == service_type,
                        ServicePricing.effective_from <= lookup_date,
                        (ServicePricing.effective_to == None) | (ServicePricing.effective_to > lookup_date),  # noqa: E711
                    )
                )
                .order_by(ServicePricing.effective_from.desc())
                .limit(1)
            )
            result = await self.session.execute(query)
            pricing_record = result.scalar_one_or_none()
            if pricing_record:
                pricing_data = {
                    "pricing_model": pricing_record.pricing_model,
                    "pricing_details": pricing_record.pricing_details,
                    "effective_from": pricing_record.effective_from,
                    "effective_to": pricing_record.effective_to,
                }
                self.pricing_cache[cache_key] = pricing_data
                return pricing_data
            self.pricing_cache[cache_key] = None
            logger.debug(f"Pricing not found for {service_name}/{provider}/{service_type} @ {lookup_date}")
            return None
        except Exception as e:
            logger.warning(f"Error fetching pricing for {service_name} ({provider}): {e}")
            self.pricing_cache[cache_key] = None
            return None

    def calculate_llm_cost(self, input_tokens: float, output_tokens: float, pricing_details: Dict[str, Any]) -> Tuple[float, float, float]:
        input_price_per_1m = pricing_details.get("input_token_price", 0)
        output_price_per_1m = pricing_details.get("output_token_price", 0)
        input_cost = (input_tokens * input_price_per_1m) / 1_000_000
        output_cost = (output_tokens * output_price_per_1m) / 1_000_000
        total_cost = input_cost + output_cost
        return round(total_cost, 6), round(input_cost, 6), round(output_cost, 6)

    def calculate_embedding_cost(self, total_tokens: float, pricing_details: Dict[str, Any]) -> float:
        price_per_1m = pricing_details.get("input_token_price", 0)
        return round((total_tokens * price_per_1m) / 1_000_000, 6)

    def calculate_document_processing_cost(self, page_count: float, pricing_details: Dict[str, Any]) -> float:
        if "credits_per_unit" in pricing_details and "credit_price_per_1000" in pricing_details:
            credits_per_page = pricing_details["credits_per_unit"]
            price_per_1000_credits = pricing_details["credit_price_per_1000"]
            price_per_page = (price_per_1000_credits / 1000) * credits_per_page
        else:
            price_per_page = pricing_details.get("price_per_page", 0)
        return round(page_count * price_per_page, 6)

    def calculate_transcription_cost(self, duration_seconds: float, pricing_details: Dict[str, Any]) -> float:
        billing_unit = pricing_details.get("billing_unit", "minute")
        if billing_unit == "minute":
            cost = (duration_seconds / 60) * pricing_details.get("price_per_minute", 0)
        elif billing_unit == "hour":
            cost = (duration_seconds / 3600) * pricing_details.get("price_per_hour", 0)
        else:
            cost = duration_seconds * pricing_details.get("price_per_second", 0)
        return round(cost, 6)

    def _record_period_cost(self, cost_breakdown: Dict, pricing: Optional[Dict], cost_key: str, cost_value: float, add_to_total: bool = True) -> None:
        if cost_value <= 0 or not pricing:
            return
        eff_from = pricing.get("effective_from")
        eff_to = pricing.get("effective_to")
        if eff_to is None:
            period_key = "current_active"
            from_str = "Current Active Prices"
            to_str = None
        else:
            from_str = eff_from.isoformat() if hasattr(eff_from, "isoformat") else str(eff_from or "")
            to_str = eff_to.isoformat() if hasattr(eff_to, "isoformat") else str(eff_to)
            period_key = f"{from_str}|{to_str}"
        periods = cost_breakdown.setdefault("_pricing_periods", {})
        if period_key not in periods:
            periods[period_key] = {"effective_from": from_str, "effective_to": to_str, "total_cost_usd": 0.0}
        period = periods[period_key]
        if add_to_total:
            period["total_cost_usd"] = round(period["total_cost_usd"] + cost_value, 6)
        period[cost_key] = round(period.get(cost_key, 0.0) + cost_value, 6)

    async def calculate_event_cost(self, event: UsageEvent, metrics: Dict[str, float]) -> Dict[str, float]:
        """Cost breakdown for a single usage event, dispatched by operation."""
        cost_breakdown: Dict[str, Any] = {"total_cost_usd": 0.0}

        if not event.event_metadata:
            return cost_breakdown

        operation = event.operation
        provider = event.event_metadata.get("provider")
        model = event.event_metadata.get("model") or event.event_metadata.get("model_name")

        is_missing_llm = not provider or not model or provider == "unknown" or model == "unknown"
        if is_missing_llm and operation != "data_ingestion":
            return cost_breakdown

        try:
            if operation in ("llm_call", "llm_generation"):
                pricing = await self.get_pricing(model, provider, "llm", event.timestamp)
                if pricing and pricing["pricing_model"] == "token_based":
                    input_tokens = metrics.get("input_tokens", 0)
                    output_tokens = metrics.get("output_tokens", 0)
                    llm_total, llm_input_cost, llm_output_cost = self.calculate_llm_cost(
                        input_tokens, output_tokens, pricing["pricing_details"]
                    )
                    if llm_total > 0:
                        cost_breakdown["llm_cost_usd"] = llm_total
                        cost_breakdown["total_cost_usd"] += llm_total
                        self._record_period_cost(cost_breakdown, pricing, "llm_cost_usd", llm_total)
                    if llm_input_cost > 0:
                        cost_breakdown["llm_input_tokens_cost_usd"] = llm_input_cost
                        self._record_period_cost(cost_breakdown, pricing, "llm_input_tokens_cost_usd", llm_input_cost, add_to_total=False)
                    if llm_output_cost > 0:
                        cost_breakdown["llm_output_tokens_cost_usd"] = llm_output_cost
                        self._record_period_cost(cost_breakdown, pricing, "llm_output_tokens_cost_usd", llm_output_cost, add_to_total=False)

            elif operation == "embedding_generation":
                pricing = await self.get_pricing(model, provider, "embedding", event.timestamp)
                if pricing and pricing["pricing_model"] == "token_based":
                    total_tokens = metrics.get("total_tokens", 0)
                    embedding_cost = self.calculate_embedding_cost(total_tokens, pricing["pricing_details"])
                    if embedding_cost > 0:
                        cost_breakdown["embedding_cost_usd"] = embedding_cost
                        cost_breakdown["total_cost_usd"] += embedding_cost
                        self._record_period_cost(cost_breakdown, pricing, "embedding_cost_usd", embedding_cost)

            elif operation == "data_ingestion":
                extraction_method = event.event_metadata.get("extraction_method")
                scrape_method = event.event_metadata.get("scrape_method")
                em_cfg = get_extraction_method_config()

                if extraction_method:
                    em = em_cfg.get("extraction_methods", {}).get(extraction_method)
                    if em:
                        svc_type, metric_key = em.get("service_type"), em.get("metric_key")
                        em_model, em_provider = em.get("model"), em.get("provider")

                        if svc_type == "document_parser" and metric_key:
                            page_count = metrics.get(metric_key, 0)
                            if page_count > 0:
                                pricing = await self.get_pricing(em_model, em_provider, svc_type, event.timestamp)
                                if pricing and pricing["pricing_model"] == "credit_based":
                                    doc_cost = self.calculate_document_processing_cost(page_count, pricing["pricing_details"])
                                    if doc_cost > 0:
                                        current = cost_breakdown.get("document_processing_cost_usd", 0)
                                        cost_breakdown["document_processing_cost_usd"] = current + doc_cost
                                        cost_breakdown["total_cost_usd"] += doc_cost
                                        self._record_period_cost(cost_breakdown, pricing, "document_processing_cost_usd", doc_cost)

                        elif svc_type == "transcription" and metric_key:
                            audio_seconds = metrics.get(metric_key, 0)
                            if audio_seconds > 0:
                                svc_name = (model if model and model != "unknown" else None) or em_model
                                prov_name = (provider if provider and provider != "unknown" else None) or em_provider
                                pricing = await self.get_pricing(svc_name, prov_name, svc_type, event.timestamp)
                                if pricing and pricing["pricing_model"] in ["per_minute", "per_hour", "per_second"]:
                                    trans_cost = self.calculate_transcription_cost(audio_seconds, pricing["pricing_details"])
                                    if trans_cost > 0:
                                        cost_breakdown["transcription_cost_usd"] = trans_cost
                                        cost_breakdown["total_cost_usd"] += trans_cost
                                        self._record_period_cost(cost_breakdown, pricing, "transcription_cost_usd", trans_cost)

                if scrape_method:
                    sm = em_cfg.get("scrape_methods", {}).get(scrape_method)
                    if sm:
                        svc_type, metric_key = sm.get("service_type"), sm.get("metric_key")
                        sm_model, sm_provider = sm.get("model"), sm.get("provider")
                        if svc_type == "web_scraping" and metric_key:
                            page_count = metrics.get(metric_key, 0)
                            if page_count > 0:
                                pricing = await self.get_pricing(sm_model, sm_provider, svc_type, event.timestamp)
                                if pricing and pricing["pricing_model"] == "per_page":
                                    doc_cost = self.calculate_document_processing_cost(page_count, pricing["pricing_details"])
                                    if doc_cost > 0:
                                        current = cost_breakdown.get("document_processing_cost_usd", 0)
                                        cost_breakdown["document_processing_cost_usd"] = current + doc_cost
                                        cost_breakdown["total_cost_usd"] += doc_cost
                                        self._record_period_cost(cost_breakdown, pricing, "document_processing_cost_usd", doc_cost)

            # Transcription by model+provider even when operation isn't data_ingestion.
            if cost_breakdown.get("transcription_cost_usd", 0) == 0:
                is_whisper_openai = model == "whisper-1" and provider == "openai"
                is_whisper_groq = model == "whisper-large-v3-turbo" and provider == "groq"
                if is_whisper_openai or is_whisper_groq:
                    audio_seconds = metrics.get("audio_seconds", 0)
                    if audio_seconds > 0:
                        service_name = model or ("whisper-1" if is_whisper_openai else "whisper-large-v3-turbo")
                        provider_name = provider or ("openai" if is_whisper_openai else "groq")
                        pricing = await self.get_pricing(service_name, provider_name, "transcription", event.timestamp)
                        if pricing and pricing["pricing_model"] in ["per_minute", "per_hour", "per_second"]:
                            trans_cost = self.calculate_transcription_cost(audio_seconds, pricing["pricing_details"])
                            if trans_cost > 0:
                                cost_breakdown["transcription_cost_usd"] = trans_cost
                                cost_breakdown["total_cost_usd"] += trans_cost
                                self._record_period_cost(cost_breakdown, pricing, "transcription_cost_usd", trans_cost)

        except Exception as e:
            logger.debug(f"Could not calculate cost for event {event.event_id}: {e}")

        return cost_breakdown


def merge_pricing_periods(dest: Dict[str, Any], event_cost: Dict) -> None:
    """Pop `_pricing_periods` from event_cost and merge into dest ({period_key: period_data})."""
    for period_key, period_data in event_cost.pop("_pricing_periods", {}).items():
        if period_key not in dest:
            dest[period_key] = {
                "effective_from": period_data["effective_from"],
                "effective_to": period_data.get("effective_to"),
                "total_cost_usd": 0.0,
            }
        target = dest[period_key]
        for k, v in period_data.items():
            if k not in ("effective_from", "effective_to") and isinstance(v, (int, float)) and v > 0:
                target[k] = round(target.get(k, 0.0) + v, 6)


def build_pricing_periods_list(all_periods: Dict[str, Any]) -> Optional[List[Dict]]:
    """Time-sorted list of periods, or None when there's <=1 (no price change in range)."""
    if len(all_periods) <= 1:
        return None
    return sorted(all_periods.values(), key=lambda x: str(x["effective_from"]))
