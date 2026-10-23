"""Owner default configuration manager, ported from fs-agent-usage-limits'
utils/owner_defaults.py. Reads utils/config/usage/owner_defaults.yaml. Values
here are account-wide (see the owner_id exception documented in
utils/models/usage_owner_config.py).
"""

from typing import Dict, List, Optional
import yaml
from pathlib import Path

from utils.pydantic_models_usage_owner_config import OwnerConfigResponse, ServiceFeatures, PlatformLimits, DefaultModels
from utils.logger import logger

_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "usage" / "owner_defaults.yaml"


class OwnerDefaultsManager:
    _instance = None
    _defaults = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if self._defaults is None:
            self._load_defaults()

    def _load_defaults(self) -> None:
        try:
            with open(_CONFIG_PATH, "r") as f:
                self._defaults = yaml.safe_load(f)
            logger.info(f"Loaded owner defaults. Valid services: {list(self._defaults['services'].keys())}")
        except Exception as e:
            logger.error(f"Failed to load owner defaults: {e}", exc_info=True)
            raise RuntimeError(f"Cannot load owner defaults configuration: {e}")

    @property
    def defaults(self) -> dict:
        if self._defaults is None:
            self._load_defaults()
        return self._defaults

    @property
    def valid_services(self) -> List[str]:
        return list(self.defaults["services"].keys())

    def validate_service_name(self, service_name: str) -> bool:
        is_valid = service_name in self.valid_services
        if not is_valid:
            logger.warning(f"Invalid service name: '{service_name}'. Valid services: {self.valid_services}")
        return is_valid

    def get_default_config(self, owner_id: str) -> OwnerConfigResponse:
        platform_limits = PlatformLimits(**self.defaults["platform_limits"])
        default_models = DefaultModels(**self.defaults["default_models"])
        enabled_services = {
            name: ServiceFeatures(enabled=info["enabled"], enabled_at=None, features=info.get("default_features", {}))
            for name, info in self.defaults["services"].items()
        }
        return OwnerConfigResponse(
            owner_id=owner_id,
            tier_type=self.defaults["default_tier"],
            base_tier=self.defaults.get("default_base_tier"),
            platform_limits=platform_limits,
            default_models=default_models,
            enabled_services=enabled_services,
        )

    def merge_services_with_defaults(self, owner_services: Dict[str, ServiceFeatures], include_disabled: bool = True) -> Dict[str, ServiceFeatures]:
        all_services = {}
        for service_name, service_info in self.defaults["services"].items():
            if service_name in owner_services:
                all_services[service_name] = owner_services[service_name]
            else:
                all_services[service_name] = ServiceFeatures(
                    enabled=service_info["enabled"], enabled_at=None, features=service_info.get("default_features", {})
                )
        if not include_disabled:
            all_services = {name: svc for name, svc in all_services.items() if svc.enabled}
        return all_services

    def get_all_services_metadata(self) -> List[dict]:
        return [
            {"name": name, "description": info.get("description", ""), "default_enabled": info.get("enabled", False)}
            for name, info in self.defaults["services"].items()
        ]


_defaults_manager: Optional[OwnerDefaultsManager] = None


def get_defaults_manager() -> OwnerDefaultsManager:
    global _defaults_manager
    if _defaults_manager is None:
        _defaults_manager = OwnerDefaultsManager()
    return _defaults_manager
