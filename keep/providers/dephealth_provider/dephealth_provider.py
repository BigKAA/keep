"""
DephealthProvider is a class that pulls a service dependency topology from
app_dependency_* metrics (dephealth / topologymetrics SDKs) stored in any
Prometheus or VictoriaMetrics-compatible server.
"""

import dataclasses
import uuid

import pydantic
import requests
from requests.auth import HTTPBasicAuth

from keep.api.models.db.topology import TopologyServiceInDto
from keep.contextmanager.contextmanager import ContextManager
from keep.providers.base.base_provider import BaseTopologyProvider
from keep.providers.models.provider_config import ProviderConfig, ProviderScope

DEFAULT_TOPOLOGY_QUERY = (
    "group by (name, namespace, group, dependency, type, critical, isentry) "
    "(app_dependency_health)"
)


@pydantic.dataclasses.dataclass
class DephealthProviderAuthConfig:
    url: pydantic.AnyHttpUrl = dataclasses.field(
        metadata={
            "required": True,
            "description": "Prometheus or VictoriaMetrics server URL",
            "hint": "http://victoriametrics:8428",
            "validation": "any_http_url",
        }
    )
    username: str = dataclasses.field(
        metadata={
            "description": "Username for basic authentication",
            "sensitive": False,
        },
        default="",
    )
    password: str = dataclasses.field(
        metadata={
            "description": "Password for basic authentication",
            "sensitive": True,
        },
        default="",
    )
    verify: bool = dataclasses.field(
        metadata={
            "description": "Verify SSL certificates",
            "hint": "Set to false to allow self-signed certificates",
            "sensitive": False,
        },
        default=True,
    )
    query: str = dataclasses.field(
        metadata={
            "description": "PromQL query that returns one label group per topology edge",
            "hint": DEFAULT_TOPOLOGY_QUERY,
            "sensitive": False,
        },
        default=DEFAULT_TOPOLOGY_QUERY,
    )
    service_label: str = dataclasses.field(
        metadata={
            "description": "Label that names the reporting service (topology node)",
            "sensitive": False,
        },
        default="name",
    )
    namespace_label: str = dataclasses.field(
        metadata={
            "description": "Label that maps to the service namespace",
            "sensitive": False,
        },
        default="namespace",
    )
    group_label: str = dataclasses.field(
        metadata={
            "description": "Label that maps to the application (service group)",
            "sensitive": False,
        },
        default="group",
    )
    dependency_label: str = dataclasses.field(
        metadata={
            "description": "Label that names the dependency (edge target)",
            "sensitive": False,
        },
        default="dependency",
    )
    protocol_label: str = dataclasses.field(
        metadata={
            "description": "Label that maps to the edge protocol",
            "sensitive": False,
        },
        default="type",
    )
    critical_label: str = dataclasses.field(
        metadata={
            "description": "Label that marks the dependency as critical (yes/no)",
            "sensitive": False,
        },
        default="critical",
    )
    entry_label: str = dataclasses.field(
        metadata={
            "description": "Label that marks the service as an application entry point (yes/no)",
            "sensitive": False,
        },
        default="isentry",
    )


class DephealthProvider(BaseTopologyProvider):
    """Pull service dependency topology from app_dependency_* metrics."""

    PROVIDER_CATEGORY = ["Monitoring"]
    PROVIDER_TAGS = ["topology"]
    PROVIDER_SCOPES = [
        ProviderScope(
            name="connectivity", description="Connectivity Test", mandatory=True
        )
    ]

    def __init__(
        self, context_manager: ContextManager, provider_id: str, config: ProviderConfig
    ):
        super().__init__(context_manager, provider_id, config)

    def validate_config(self):
        """
        Validates required configuration for the dephealth provider.
        """
        self.authentication_config = DephealthProviderAuthConfig(
            **self.config.authentication
        )

    def validate_scopes(self) -> dict[str, bool | str]:
        scopes = {"connectivity": True}
        try:
            self._query("up")
        except Exception as e:
            scopes["connectivity"] = str(e)
        return scopes

    def _query(self, query: str) -> dict:
        """
        Executes an instant query against the configured server and returns
        the parsed JSON response.
        """
        auth = None
        if self.authentication_config.username and self.authentication_config.password:
            auth = HTTPBasicAuth(
                self.authentication_config.username,
                self.authentication_config.password,
            )
        response = requests.get(
            f"{self.authentication_config.url}/api/v1/query",
            params={"query": query},
            auth=auth,
            verify=self.authentication_config.verify,
        )
        if response.status_code != 200:
            raise Exception(f"dephealth query failed: {response.content}")
        return response.json()

    def dispose(self):
        """
        Disposes the dephealth provider.
        """
        return

    def pull_topology(self) -> tuple[list[TopologyServiceInDto], dict]:
        """
        Builds the topology from the configured PromQL query.

        Each returned series is one edge: service (service_label) depends on
        dependency (dependency_label) via protocol (protocol_label). Services
        appear as nodes, groups (group_label) become applications. Dependencies
        that do not export metrics of their own (databases, external APIs, ...)
        are still created as services so the edge has a target node, and they
        belong to every application that references them.
        """
        self.logger.info("Pulling topology from dephealth metrics...")
        config = self.authentication_config
        response = self._query(config.query)
        if response.get("status") != "success":
            raise Exception(f"dephealth query failed: {response}")

        services: dict[str, TopologyServiceInDto] = {}
        applications: dict[str, set[str]] = {}

        def _ensure_service(name: str) -> TopologyServiceInDto:
            if name not in services:
                services[name] = TopologyServiceInDto(
                    source_provider_id=self.provider_id,
                    service=name,
                    display_name=name,
                    tags=[],
                    dependencies={},
                    application_relations={},
                )
            return services[name]

        for series in response.get("data", {}).get("result", []):
            metric = series.get("metric", {})
            service_name = metric.get(config.service_label)
            if not service_name:
                self.logger.warning(
                    "Skipping series without a service label",
                    extra={"metric": metric},
                )
                continue
            service = _ensure_service(service_name)

            namespace = metric.get(config.namespace_label)
            if namespace:
                service.namespace = namespace

            group = metric.get(config.group_label)
            if group:
                applications.setdefault(group, set()).add(service_name)
                if group not in service.tags:
                    service.tags.append(group)

            # entry points and critical dependencies are surfaced as node tags
            if (metric.get(config.entry_label) or "").lower() == "yes":
                if "entry" not in service.tags:
                    service.tags.append("entry")

            dependency_name = metric.get(config.dependency_label)
            if not dependency_name:
                continue
            dependency = _ensure_service(dependency_name)
            if group:
                applications.setdefault(group, set()).add(dependency_name)
            if (metric.get(config.critical_label) or "").lower() == "yes":
                if "critical" not in dependency.tags:
                    dependency.tags.append("critical")
            service.dependencies[dependency_name] = (
                metric.get(config.protocol_label) or "unknown"
            )

        # application ids must be stable across pulls so that re-pulling
        # updates applications instead of duplicating them
        for group, members in applications.items():
            application_id = uuid.uuid5(uuid.NAMESPACE_DNS, group)
            for member in members:
                services[member].application_relations[application_id] = group

        for service in services.values():
            if not service.application_relations:
                service.application_relations = None

        self.logger.info(
            "Pulled topology from dephealth metrics",
            extra={"services": len(services), "applications": len(applications)},
        )
        return list(services.values()), {}
