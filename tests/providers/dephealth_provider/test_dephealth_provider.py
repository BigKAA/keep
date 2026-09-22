"""
Unit tests for the dephealth topology provider.

vm_topology_response.json is a real instant-query response from a
VictoriaMetrics instance scraping the uniproxy test topology: 20 edges,
2 applications (proxy-cluster-1, proxy-cluster-2) with shared dependency
nodes (postgresql, uniproxy-04).
"""

import json
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from keep.contextmanager.contextmanager import ContextManager
from keep.providers.dephealth_provider.dephealth_provider import (
    DEFAULT_TOPOLOGY_QUERY,
    DephealthProvider,
)
from keep.providers.models.provider_config import ProviderConfig

FIXTURES_DIR = Path(__file__).parent / "fixtures"
VICTORIAMETRICS_URL = "http://victoriametrics.example.com:8428"
PROVIDER_ID = "dephealth-test"


def _build_provider(**authentication) -> DephealthProvider:
    config = ProviderConfig(
        description="Dephealth Provider",
        authentication={"url": VICTORIAMETRICS_URL, **authentication},
    )
    return DephealthProvider(ContextManager(tenant_id="test"), PROVIDER_ID, config)


def _vm_response(fixture: str = "vm_topology_response.json") -> MagicMock:
    response = MagicMock()
    response.status_code = 200
    with open(FIXTURES_DIR / fixture) as f:
        response.json.return_value = json.load(f)
    return response


class TestValidateConfig:
    def test_defaults(self):
        provider = _build_provider()
        assert str(provider.authentication_config.url).rstrip("/") == (
            VICTORIAMETRICS_URL
        )
        assert provider.authentication_config.query == DEFAULT_TOPOLOGY_QUERY
        assert provider.authentication_config.verify is True
        assert provider.authentication_config.service_label == "name"
        assert provider.authentication_config.namespace_label == "namespace"
        assert provider.authentication_config.group_label == "group"
        assert provider.authentication_config.dependency_label == "dependency"
        assert provider.authentication_config.protocol_label == "type"
        assert provider.authentication_config.critical_label == "critical"
        assert provider.authentication_config.entry_label == "isentry"

    def test_custom_configuration(self):
        provider = _build_provider(
            query="group by (svc, dep, kind) (custom_metric)",
            service_label="svc",
            dependency_label="dep",
            protocol_label="kind",
            verify=False,
        )
        assert provider.authentication_config.query == (
            "group by (svc, dep, kind) (custom_metric)"
        )
        assert provider.authentication_config.service_label == "svc"
        assert provider.authentication_config.dependency_label == "dep"
        assert provider.authentication_config.protocol_label == "kind"
        assert provider.authentication_config.verify is False


class TestPullTopology:
    @patch("keep.providers.dephealth_provider.dephealth_provider.requests.get")
    def test_builds_topology_from_real_metrics(self, mock_get):
        mock_get.return_value = _vm_response()
        services, extra = _build_provider().pull_topology()
        assert extra == {}
        by_name = {service.service: service for service in services}

        assert len(services) == 17
        assert sum(len(s.dependencies) for s in services) == 20

        # reporters carry namespace, group tag and provider ownership
        reporter = by_name["uniproxy-01"]
        assert reporter.namespace == "dephealth-uniproxy"
        assert reporter.display_name == "uniproxy-01"
        assert "proxy-cluster-1" in reporter.tags
        assert "entry" in reporter.tags  # isentry=yes
        assert reporter.source_provider_id == PROVIDER_ID

        # dependency-only nodes exist as services without metrics of their own
        postgres = by_name["postgresql"]
        assert postgres.namespace is None
        assert "critical" in postgres.tags  # critical=yes edge from uniproxy-03
        assert postgres.dependencies == {}

        # edges keep the metric type as protocol
        assert by_name["uniproxy-03"].dependencies["postgresql"] == "postgres"

    @patch("keep.providers.dephealth_provider.dephealth_provider.requests.get")
    def test_applications_are_stable_and_shared(self, mock_get):
        mock_get.return_value = _vm_response()
        services, _ = _build_provider().pull_topology()
        by_name = {service.service: service for service in services}

        app1_id = uuid.uuid5(uuid.NAMESPACE_DNS, "proxy-cluster-1")
        app2_id = uuid.uuid5(uuid.NAMESPACE_DNS, "proxy-cluster-2")
        # shared nodes belong to both applications
        for shared in ("postgresql", "uniproxy-04"):
            assert by_name[shared].application_relations == {
                app1_id: "proxy-cluster-1",
                app2_id: "proxy-cluster-2",
            }
        assert by_name["uniproxy-01"].application_relations == {
            app1_id: "proxy-cluster-1"
        }

    @patch("keep.providers.dephealth_provider.dephealth_provider.requests.get")
    def test_custom_label_mapping(self, mock_get):
        mock_get.return_value = _vm_response("vm_renamed_labels_response.json")
        provider = _build_provider(
            query="group by (svc, dep, kind) (custom_metric)",
            service_label="svc",
            dependency_label="dep",
            protocol_label="kind",
        )
        services, _ = provider.pull_topology()
        by_name = {service.service: service for service in services}
        assert len(services) == 4
        assert by_name["billing-api"].dependencies["postgres-main"] == "postgres"
        assert by_name["order-api"].dependencies["payment-api"] == "http"

    @patch("keep.providers.dephealth_provider.dephealth_provider.requests.get")
    def test_query_error_raises(self, mock_get):
        response = MagicMock()
        response.status_code = 500
        response.content = b"boom"
        mock_get.return_value = response
        provider = _build_provider()
        with pytest.raises(Exception, match="dephealth query failed"):
            provider.pull_topology()


class TestValidateScopes:
    @patch("keep.providers.dephealth_provider.dephealth_provider.requests.get")
    def test_connectivity_ok(self, mock_get):
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"status": "success", "data": {"result": []}}
        mock_get.return_value = response
        scopes = _build_provider().validate_scopes()
        assert scopes == {"connectivity": True}
        # the connectivity probe runs a lightweight `up` query
        assert mock_get.call_args.kwargs["params"] == {"query": "up"}

    @patch("keep.providers.dephealth_provider.dephealth_provider.requests.get")
    def test_connectivity_failure_reports_error(self, mock_get):
        response = MagicMock()
        response.status_code = 401
        response.content = b"unauthorized"
        mock_get.return_value = response
        scopes = _build_provider().validate_scopes()
        assert isinstance(scopes["connectivity"], str)
        assert "dephealth query failed" in scopes["connectivity"]
