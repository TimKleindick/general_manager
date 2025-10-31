# Roadmap

This roadmap highlights the major initiatives planned for GeneralManager. Targets are grouped by approximate release horizon; exact ordering may change as we incorporate community feedback and real-world learnings.

## Vision

Deliver a cohesive domain platform that lets Django teams define business logic once, connect to multiple data sources, observe runtime behaviour, and deploy reliably from local demos to production-grade clusters.

## Near-Term Priorities

- **ExistingModelInterface**: Provide an Interface to wrap pre-existing Django models inside GeneralManager, including:
  - Compatibility requirements (history tracking, `is_active`, validation hooks).
  - Strategies for read-only vs. read/write integration and error mapping.
  - Schema-sync safeguards and automated metadata discovery for GraphQL, permissions, and factories.
- **RequestInterface Foundations**: Provide a base HTTP interface with:
  - Pluggable authentication (API keys, OAuth token callbacks) and resilient retry/backoff options.
  - Mapping conventions for buckets (`filter`, `exclude`) and normalization of responses to Manager objects.
  - Guidance for extending into REST- and GraphQL-specific subclasses without duplicating logic.
- **Observability Quickstart**: Ship an open instrumentation layer (Prometheus/OpenTelemetry) plus Grafana-ready dashboards that detail:
  - Latency percentiles, call volume, error rate per Manager/Interface method.
  - Cache hit/miss ratios, query counts, and external service health (timeouts, retries).
  - Configuration tips for sampling, secure label usage, and integration with existing logging/tracing.
- **Testing Expansion Guides**: Extend documentation to cover:
  - End-to-end scenarios using the example project, deterministic fixtures, and CI setup.
  - Performance/benchmark recipes with tooling suggestions (`pytest-benchmark`, load generation).
  - Contract and chaos testing strategies for interfaces that reach external systems.
- **One-Click Demo Environment**: Publish a Docker Compose stack that bootstraps:
  - Example domain, seeded data, GraphQL endpoint, and observability exporters.
  - Optional admin/dashboard UI, plus troubleshooting for common local issues.

## Mid-Term Priorities

- **Specialised Request Interfaces**: Deeper guides for REST/GraphQL adapters, including:
  - Schema introspection, pagination/link handling, and mutation support.
  - Mock-server testing patterns and rate-limit management.
- **Observability Operations Playbook**: Best practices for:
  - Alerting rules (latency thresholds, cache degradation, error bursts).
  - Tracing correlation across Managers and downstream services.
  - Incident response workflows tied to GeneralManager metrics and logs.
- **Kubernetes Deployment Blueprint**: Provide a reference cluster setup:
  - App, database, cache, and background workers with secure defaults (Secrets, TLS, RBAC).
  - CI/CD examples for container build, migrations, health probes, and scaling policies.
  - Integration of observability stack (Prometheus, OTEL Collector, Grafana) aligned with the quickstart.
- **Expanded Test Pyramids**: Reference architectures for:
  - Layered suites (unit, integration, E2E, performance) with recommended runtimes and flake mitigation.
  - Benchmark baselines and regression detection in CI (warning thresholds, trend reporting).
- **Interface & Metric Cookbook**: Scenario-based recipes that combine new interfaces, observability, and testing guidance for common reliability challenges.

## Long-Term Initiatives

- **EventSourcing/CQRS Interface**: RFC-driven design documenting:
  - Event store requirements, projection mechanisms, hybrid read-model strategies.
  - Consistency guarantees, replay tooling, and integration with permissions & factories.
- **Extension Ecosystem**: Define plugin hooks, registration lifecycle, versioning guidelines, and example community extensions (custom interfaces, permission packs).
- **Domain Component Library**: Curated reusable modules (identity/roles, billing, audit trail) showing how to assemble larger solutions on GeneralManager.
- **Data & Analytics Bridges**: Interfaces or exporters for data warehouses, streaming platforms, and BI toolchains, with reliability and governance guidance.
- **Security & Compliance Toolkit**: Blueprint for secrets management, field-level encryption, data-retention policies, and audit reporting aligned with enterprise needs.
- **Lifecycle & Migration Playbooks**: Best practices for schema diffs, automated migrations, semantic release alignment, and multi-environment rollouts.

## Contributing & Feedback

- Track roadmap updates in release notes and changelog entries.
- Share proposals via GitHub Issues or discussions—especially for RFC-driven items (e.g., EventSourcing/CQRS).
- Pilot adopters for demos, observability, and new interfaces are encouraged to document findings so we can fold them back into official guides.

The roadmap will evolve as we validate assumptions with users. Expect adjustments as we balance reliability goals, community ideas, and maintainership capacity.
