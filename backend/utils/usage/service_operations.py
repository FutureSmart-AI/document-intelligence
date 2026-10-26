"""Static service-type/operation/label catalogue, ported from
fs-agent-usage-limits' utils/service_operations.py. Reads
utils/config/usage/service_operations.yaml. Not owner/knowledge scoped — this
is a global, static config, same as the source service.
"""

from typing import List, Optional, Set
import yaml
from pathlib import Path

from utils.logger import logger

_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "usage" / "service_operations.yaml"


class ServiceOperationsManager:
    _instance = None
    _config = None
    _response_cache = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if self._config is None:
            self._load_config()

    def _load_config(self) -> None:
        try:
            with open(_CONFIG_PATH, "r") as f:
                self._config = yaml.safe_load(f)
            logger.info(f"Loaded usage service_operations.yaml. Service types: {list(self._config['service_types'].keys())}")
        except Exception as e:
            logger.error(f"Failed to load service operations config: {e}", exc_info=True)
            raise RuntimeError(f"Cannot load service operations configuration: {e}")

    @property
    def config(self) -> dict:
        if self._config is None:
            self._load_config()
        return self._config

    def get_service_options_response(self) -> dict:
        if self._response_cache is not None:
            return self._response_cache

        service_types_list = []
        all_operations: Set[str] = set()
        all_labels: Set[str] = set()

        for service_type, service_data in self.config["service_types"].items():
            operations_list = []
            for operation, operation_data in service_data.get("operations", {}).items():
                all_operations.add(operation)
                labels_list = []
                for label_item in operation_data.get("labels", []):
                    all_labels.add(label_item["label"])
                    labels_list.append({"label": label_item["label"], "display_name": label_item["display_name"]})
                operations_list.append({"operation": operation, "display_name": operation_data["display_name"], "labels": labels_list})
            service_types_list.append(
                {"service_type": service_type, "display_name": service_data["display_name"], "description": service_data.get("description"), "operations": operations_list}
            )

        self._response_cache = {
            "service_types": service_types_list,
            "all_operations": sorted(all_operations),
            "all_labels": sorted(all_labels),
        }
        return self._response_cache

    def get_valid_service_types(self) -> List[str]:
        return list(self.config["service_types"].keys())


_service_operations_manager: Optional[ServiceOperationsManager] = None


def get_service_operations_manager() -> ServiceOperationsManager:
    global _service_operations_manager
    if _service_operations_manager is None:
        _service_operations_manager = ServiceOperationsManager()
    return _service_operations_manager
