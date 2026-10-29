from __future__ import annotations

import inspect
import re
from pathlib import Path

import yaml

from pm_gateway.generated.apis.discovery_api_base import BaseDiscoveryApi
from pm_gateway.generated.apis.work_items_api_base import BaseWorkItemsApi
from pm_gateway.generated.apis.workflow_api_base import BaseWorkflowApi
from pm_gateway.impl.provider_api import _OPERATION_BASES, _UNSUPPORTED_OPERATIONS

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = PACKAGE_ROOT / "spec" / "pm-api-openapi-v0.1.yaml"


def _operation_id_to_method(operation_id: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", operation_id).lower()


def test_every_generated_server_operation_has_a_gateway_dispatch_entry() -> None:
    generated_operations = {
        name
        for api_base in (BaseDiscoveryApi, BaseWorkItemsApi, BaseWorkflowApi)
        for name, member in vars(api_base).items()
        if inspect.iscoroutinefunction(member)
    }

    assert generated_operations == set(_OPERATION_BASES)


def test_current_openapi_operations_are_present_in_generated_server_bases() -> None:
    document = yaml.safe_load(SPEC_PATH.read_text(encoding="utf-8"))
    operation_ids = {
        operation["operationId"]
        for path_item in document["paths"].values()
        for method, operation in path_item.items()
        if method.lower() in {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
    }
    generated_operations = set(_OPERATION_BASES)
    expected_methods = {_operation_id_to_method(operation_id) for operation_id in operation_ids}

    assert expected_methods == generated_operations


def test_unsupported_operations_are_explicitly_listed() -> None:
    assert _UNSUPPORTED_OPERATIONS <= set(_OPERATION_BASES)
