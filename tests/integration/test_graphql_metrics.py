import unittest

from django.contrib.auth import get_user_model
from django.db.models import CharField
from django.test import override_settings
from django.utils.crypto import get_random_string

from general_manager.interface import DatabaseInterface
from general_manager.manager.general_manager import GeneralManager
from general_manager.metrics.graphql import (
    normalize_field_name,
    reset_graphql_metrics_backend_for_tests,
)
from general_manager.utils.testing import GeneralManagerTransactionTestCase

try:  # pragma: no cover - optional dependency
    from prometheus_client import REGISTRY

    PROMETHEUS_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency
    PROMETHEUS_AVAILABLE = False
    REGISTRY = None


@unittest.skipUnless(PROMETHEUS_AVAILABLE, "prometheus_client not installed")
@override_settings(
    GENERAL_MANAGER_GRAPHQL_METRICS_ENABLED=True,
    GENERAL_MANAGER_GRAPHQL_METRICS_BACKEND="prometheus",
    GENERAL_MANAGER_GRAPHQL_METRICS_OPERATION_ALLOWLIST=["MetricsQuery", "BadQuery"],
)
class TestGraphQLMetrics(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        class MetricProject(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=100)

        cls.general_manager_classes = [MetricProject]
        cls.metric_project = MetricProject

    def setUp(self):
        super().setUp()
        reset_graphql_metrics_backend_for_tests()
        password = get_random_string(12)
        self.user = get_user_model().objects.create_user(
            username="metricsuser", password=password
        )
        self.client.login(username="metricsuser", password=password)
        self.metric_project.Factory.create_batch(1)

    def _get_sample_value(self, name, labels):
        value = REGISTRY.get_sample_value(name, labels)  # type: ignore[union-attr]
        return value or 0.0

    def test_graphql_request_metrics_success(self):
        query = """
        query MetricsQuery {
            metricprojectList {
                items {
                    id
                    name
                }
            }
        }
        """
        labels = {
            "operation_name": "MetricsQuery",
            "operation_type": "query",
            "status": "success",
        }
        duration_labels = {
            "operation_name": "MetricsQuery",
            "operation_type": "query",
        }
        before_requests = self._get_sample_value("graphql_requests_total", labels)
        before_duration = self._get_sample_value(
            "graphql_request_duration_seconds_count", duration_labels
        )

        response = self.query(query, operation_name="MetricsQuery")
        self.assertResponseNoErrors(response)

        after_requests = self._get_sample_value("graphql_requests_total", labels)
        after_duration = self._get_sample_value(
            "graphql_request_duration_seconds_count", duration_labels
        )
        self.assertEqual(after_requests, before_requests + 1)
        self.assertEqual(after_duration, before_duration + 1)

    def test_graphql_error_metrics(self):
        query = """
        query BadQuery {
            metricprojectList {
                items {
                    doesNotExist
                }
            }
        }
        """
        request_labels = {
            "operation_name": "BadQuery",
            "operation_type": "query",
            "status": "error",
        }
        error_labels = {"operation_name": "BadQuery", "code": "unknown"}
        before_requests = self._get_sample_value(
            "graphql_requests_total", request_labels
        )
        before_errors = self._get_sample_value("graphql_errors_total", error_labels)

        response = self.query(query, operation_name="BadQuery")
        self.assertResponseHasErrors(response)

        after_requests = self._get_sample_value(
            "graphql_requests_total", request_labels
        )
        after_errors = self._get_sample_value("graphql_errors_total", error_labels)
        self.assertEqual(after_requests, before_requests + 1)
        self.assertGreaterEqual(after_errors, before_errors + 1)

    def test_graphql_unknown_operation_name(self):
        query = """
        query {
            metricprojectList {
                items {
                    id
                }
            }
        }
        """
        labels = {
            "operation_name": "unknown",
            "operation_type": "query",
            "status": "success",
        }
        before_requests = self._get_sample_value("graphql_requests_total", labels)

        response = self.query(query)
        self.assertResponseNoErrors(response)

        after_requests = self._get_sample_value("graphql_requests_total", labels)
        self.assertEqual(after_requests, before_requests + 1)


@unittest.skipUnless(PROMETHEUS_AVAILABLE, "prometheus_client not installed")
@override_settings(
    GENERAL_MANAGER_GRAPHQL_METRICS_ENABLED=True,
    GENERAL_MANAGER_GRAPHQL_METRICS_BACKEND="prometheus",
    GENERAL_MANAGER_GRAPHQL_METRICS_RESOLVER_TIMING=True,
)
class TestGraphQLResolverTimingMetrics(GeneralManagerTransactionTestCase):
    @classmethod
    def setUpClass(cls):
        class MetricProject(GeneralManager):
            class Interface(DatabaseInterface):
                name = CharField(max_length=100)

        cls.general_manager_classes = [MetricProject]
        cls.metric_project = MetricProject

    def setUp(self):
        super().setUp()
        reset_graphql_metrics_backend_for_tests()
        password = get_random_string(12)
        self.user = get_user_model().objects.create_user(
            username="metricsresolver", password=password
        )
        self.client.login(username="metricsresolver", password=password)
        self.metric_project.Factory.create_batch(1)

    def _get_sample_value(self, name, labels):
        value = REGISTRY.get_sample_value(name, labels)  # type: ignore[union-attr]
        return value or 0.0

    def test_resolver_timing_metrics(self):
        query = """
        query MetricsQuery {
            metricprojectList {
                items { id name }
            }
        }
        """
        field_name = normalize_field_name("Query.metricprojectList")
        labels = {"field_name": field_name}
        before = self._get_sample_value(
            "graphql_resolver_duration_seconds_count", labels
        )

        response = self.query(query, operation_name="MetricsQuery")
        self.assertResponseNoErrors(response)

        after = self._get_sample_value(
            "graphql_resolver_duration_seconds_count", labels
        )
        self.assertEqual(after, before + 1)
