"""CRUD for account-wide tier/platform-limit configuration, ported from
fs-agent-usage-limits' utils/owner_config_management.py.

Every function here takes owner_id (already resolved from knowledge_id by the
router — see routers/usage_owner_config.py and the exception documented in
utils/models/usage_owner_config.py) rather than knowledge_id directly.
"""

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from typing import Optional, Dict, Tuple
from datetime import datetime

from utils.models.usage_owner_config import OwnerConfiguration, OwnerPlatformLimits, OwnerDefaultModels, OwnerEnabledServices, ServiceFeatures
from utils.pydantic_models_usage_owner_config import (
    OwnerConfigResponse, OwnerConfigCreateRequest, OwnerConfigPatchRequest,
    ServiceResponse, ServicesListResponse, ServiceFeatures as PydanticServiceFeatures,
)
from utils.logger import logger


async def _ensure_owner_exists(session: AsyncSession, owner_id: str) -> OwnerConfiguration:
    """Create a complete default owner configuration row if one doesn't exist yet."""
    owner = await session.get(OwnerConfiguration, owner_id)
    if not owner:
        from utils.usage.owner_defaults import get_defaults_manager

        defaults_manager = get_defaults_manager()
        now = datetime.utcnow()
        default_tier = defaults_manager.defaults.get("default_tier", "trial")

        owner = OwnerConfiguration(owner_id=owner_id, tier_type=default_tier, base_tier=None, created_at=now, updated_at=now)
        session.add(owner)
        session.add(OwnerPlatformLimits(owner_id=owner_id, **defaults_manager.defaults["platform_limits"], updated_at=now))
        session.add(OwnerDefaultModels(owner_id=owner_id, **defaults_manager.defaults["default_models"], updated_at=now))

        for service_name, service_info in defaults_manager.defaults["services"].items():
            session.add(
                OwnerEnabledServices(
                    owner_id=owner_id, service_name=service_name, enabled=service_info["enabled"],
                    enabled_at=now if service_info["enabled"] else None, updated_at=now,
                )
            )
            for feature_name, feature_enabled in service_info.get("default_features", {}).items():
                session.add(ServiceFeatures(owner_id=owner_id, service_name=service_name, feature_name=feature_name, enabled=feature_enabled, updated_at=now))

        await session.flush()
        logger.info(f"Created default owner configuration for owner_id={owner_id} with tier={default_tier}")

    return owner


def _service_db_to_api(db_features: list) -> Dict[str, bool]:
    return {f.feature_name: f.enabled for f in db_features}


async def get_owner_config(session: AsyncSession, owner_id: str) -> Optional[OwnerConfigResponse]:
    owner = await session.get(OwnerConfiguration, owner_id)
    if not owner:
        return None

    from utils.usage.owner_defaults import get_defaults_manager

    defaults_manager = get_defaults_manager()

    limits = await session.get(OwnerPlatformLimits, owner_id)
    platform_limits_dict = (
        {
            "max_agents": limits.max_agents, "max_team_members": limits.max_team_members, "max_file_size_mb": limits.max_file_size_mb,
            "max_indexed_characters": limits.max_indexed_characters, "max_knowledge_items": limits.max_knowledge_items,
            "max_conversations_per_month": limits.max_conversations_per_month,
            "max_messages_per_month": limits.max_messages_per_month, "max_llm_tokens_per_month": limits.max_llm_tokens_per_month,
            "max_documents": limits.max_documents, "max_urls": limits.max_urls,
            "max_messages_per_day": limits.max_messages_per_day, "max_cost_usd_per_month": limits.max_cost_usd_per_month,
        }
        if limits
        else defaults_manager.defaults["platform_limits"]
    )

    models = await session.get(OwnerDefaultModels, owner_id)
    default_models_dict = (
        {"llm": models.llm, "embedding": models.embedding, "enrichment_llm": models.enrichment_llm} if models else defaults_manager.defaults["default_models"]
    )

    services_result = await session.execute(select(OwnerEnabledServices).where(OwnerEnabledServices.owner_id == owner_id))
    services_db = services_result.scalars().all()
    enabled_services = {}
    for service in services_db:
        feat_result = await session.execute(
            select(ServiceFeatures).where(ServiceFeatures.owner_id == owner_id, ServiceFeatures.service_name == service.service_name)
        )
        features = feat_result.scalars().all()
        enabled_services[service.service_name] = PydanticServiceFeatures(
            enabled=service.enabled,
            enabled_at=service.enabled_at.isoformat() if service.enabled_at else None,
            features=_service_db_to_api(features),
        )

    from utils.pydantic_models_usage_owner_config import PlatformLimits, DefaultModels

    return OwnerConfigResponse(
        owner_id=owner.owner_id, tier_type=owner.tier_type, base_tier=owner.base_tier,
        platform_limits=PlatformLimits(**platform_limits_dict), default_models=DefaultModels(**default_models_dict),
        enabled_services=enabled_services,
    )


async def create_or_update_owner_config(session: AsyncSession, owner_id: str, config: OwnerConfigCreateRequest) -> Tuple[OwnerConfigResponse, bool]:
    owner = await session.get(OwnerConfiguration, owner_id)
    is_new = owner is None
    now = datetime.utcnow()

    if not owner:
        owner = OwnerConfiguration(owner_id=owner_id, tier_type=config.tier_type, base_tier=config.base_tier, created_at=now, updated_at=now)
        session.add(owner)
    else:
        owner.tier_type = config.tier_type
        owner.base_tier = config.base_tier
        owner.updated_at = now

    pl = await session.get(OwnerPlatformLimits, owner_id)
    if pl:
        for f, v in config.platform_limits.dict(exclude_unset=True).items():
            setattr(pl, f, v)
        pl.updated_at = now
    else:
        session.add(OwnerPlatformLimits(owner_id=owner_id, **config.platform_limits.dict(), updated_at=now))

    dm = await session.get(OwnerDefaultModels, owner_id)
    if dm:
        for f, v in config.default_models.dict(exclude_unset=True).items():
            setattr(dm, f, v)
        dm.updated_at = now
    else:
        session.add(OwnerDefaultModels(owner_id=owner_id, **config.default_models.dict(), updated_at=now))

    from sqlalchemy import delete

    await session.execute(delete(ServiceFeatures).where(ServiceFeatures.owner_id == owner_id))
    await session.execute(delete(OwnerEnabledServices).where(OwnerEnabledServices.owner_id == owner_id))

    if config.enabled_services:
        for service_name, service_features in config.enabled_services.items():
            session.add(
                OwnerEnabledServices(
                    owner_id=owner_id, service_name=service_name, enabled=service_features.enabled,
                    enabled_at=now if service_features.enabled else None, updated_at=now,
                )
            )
            for feat_name, feat_enabled in service_features.features.items():
                session.add(ServiceFeatures(owner_id=owner_id, service_name=service_name, feature_name=feat_name, enabled=feat_enabled, updated_at=now))

    await session.commit()
    result = await get_owner_config(session, owner_id)
    return result, is_new


async def patch_owner_config(session: AsyncSession, owner_id: str, updates: OwnerConfigPatchRequest) -> OwnerConfigResponse:
    owner = await session.get(OwnerConfiguration, owner_id)
    if not owner:
        raise ValueError("Owner not found")
    now = datetime.utcnow()

    if updates.tier_type is not None:
        owner.tier_type = updates.tier_type
        owner.updated_at = now
    if updates.base_tier is not None:
        owner.base_tier = updates.base_tier
        owner.updated_at = now
    await session.commit()

    if updates.platform_limits is not None:
        pl = await session.get(OwnerPlatformLimits, owner_id)
        if pl:
            for f, v in updates.platform_limits.dict(exclude_unset=True).items():
                setattr(pl, f, v)
            pl.updated_at = now
        else:
            session.add(OwnerPlatformLimits(owner_id=owner_id, **updates.platform_limits.dict(), updated_at=now))
        await session.commit()

    if updates.default_models is not None:
        dm = await session.get(OwnerDefaultModels, owner_id)
        if dm:
            for f, v in updates.default_models.dict(exclude_unset=True).items():
                setattr(dm, f, v)
            dm.updated_at = now
        else:
            session.add(OwnerDefaultModels(owner_id=owner_id, **updates.default_models.dict(), updated_at=now))
        await session.commit()

    return await get_owner_config(session, owner_id)


async def enable_service(session: AsyncSession, owner_id: str, service_name: str, features: Optional[Dict[str, bool]] = None) -> ServiceResponse:
    now = datetime.utcnow()
    await _ensure_owner_exists(session, owner_id)

    svc = await session.get(OwnerEnabledServices, (owner_id, service_name))
    if not svc:
        svc = OwnerEnabledServices(owner_id=owner_id, service_name=service_name, enabled=True, enabled_at=now, updated_at=now)
        session.add(svc)
    else:
        svc.enabled = True
        svc.enabled_at = svc.enabled_at or now
        svc.updated_at = now

    if features:
        for fname, fenabled in features.items():
            f = await session.get(ServiceFeatures, (owner_id, service_name, fname))
            if not f:
                session.add(ServiceFeatures(owner_id=owner_id, service_name=service_name, feature_name=fname, enabled=fenabled, updated_at=now))
            else:
                f.enabled = fenabled
                f.updated_at = now
    await session.commit()

    feat_result = await session.execute(select(ServiceFeatures).where(ServiceFeatures.owner_id == owner_id, ServiceFeatures.service_name == service_name))
    return ServiceResponse(
        owner_id=owner_id, service_name=service_name, enabled=True,
        enabled_at=svc.enabled_at.isoformat() if svc.enabled_at else None, features=_service_db_to_api(feat_result.scalars().all()),
    )


async def disable_service(session: AsyncSession, owner_id: str, service_name: str) -> ServiceResponse:
    now = datetime.utcnow()
    await _ensure_owner_exists(session, owner_id)

    svc = await session.get(OwnerEnabledServices, (owner_id, service_name))
    if not svc:
        svc = OwnerEnabledServices(owner_id=owner_id, service_name=service_name, enabled=False, enabled_at=None, updated_at=now)
        session.add(svc)
    else:
        svc.enabled = False
        svc.updated_at = now
    await session.commit()

    feat_result = await session.execute(select(ServiceFeatures).where(ServiceFeatures.owner_id == owner_id, ServiceFeatures.service_name == service_name))
    return ServiceResponse(
        owner_id=owner_id, service_name=service_name, enabled=False,
        enabled_at=svc.enabled_at.isoformat() if svc.enabled_at else None, features=_service_db_to_api(feat_result.scalars().all()),
    )


async def get_enabled_services(session: AsyncSession, owner_id: str, enabled_only: bool = True) -> ServicesListResponse:
    from utils.usage.owner_defaults import get_defaults_manager

    defaults_manager = get_defaults_manager()
    services_result = await session.execute(select(OwnerEnabledServices).where(OwnerEnabledServices.owner_id == owner_id))
    services_db = services_result.scalars().all()

    owner_services_dict = {}
    for svc in services_db:
        feat_result = await session.execute(select(ServiceFeatures).where(ServiceFeatures.owner_id == owner_id, ServiceFeatures.service_name == svc.service_name))
        owner_services_dict[svc.service_name] = PydanticServiceFeatures(
            enabled=svc.enabled, enabled_at=svc.enabled_at.isoformat() if svc.enabled_at else None, features=_service_db_to_api(feat_result.scalars().all())
        )

    complete_services = defaults_manager.merge_services_with_defaults(owner_services=owner_services_dict, include_disabled=not enabled_only)
    return ServicesListResponse(owner_id=owner_id, services=complete_services)
