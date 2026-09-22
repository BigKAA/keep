"""
DephealthProvider is a class that pulls a service dependency topology from
app_dependency_* metrics (dephealth / topologymetrics SDKs) stored in any
Prometheus or VictoriaMetrics-compatible server.
"""

import dataclasses

import pydantic
import requests
from requests.auth import HTTPBasicAuth

from keep.contextmanager.contextmanager import ContextManager
from keep.providers.base.base_provider import BaseTopologyProvider
from keep.providers.models.provider_config import ProviderConfig, ProviderScope


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
