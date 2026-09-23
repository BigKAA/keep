# Changelog

All notable changes to this fork (BigKAA/keep) are documented in this file.
Upstream changes are documented at https://github.com/keephq/keep/releases.

## [0.54.3-bk-0.2] - 2026-09-23

Topology UI refinements from lab feedback; backend unchanged from 0.54.3-bk-0.1.

### Changed
- Topology UI: the node alert badge counts firing alerts only — resolved
  alerts no longer badge a node (consistent with border colors and cascade
  warnings; keephq/keep#6838, comment on keephq/keep#6836)
- Topology UI: legend moved to the top-left corner (bottom-left overlapped
  the React Flow controls) and made collapsible (keephq/keep#6838)

### Fixed
- N/A

### Security
- N/A

## [0.54.3-bk-0.1] - 2026-09-23

Based on upstream keep v0.54.3 (+4 commits on main at fork time, fe5c8964).

### Added
- dephealth topology provider: pulls a service dependency topology from
  `app_dependency_*` metrics (topologymetrics SDKs) via any Prometheus /
  VictoriaMetrics-compatible server; configurable PromQL and label mapping;
  deterministic application UUIDs; average check latency appended to edge
  protocols (keephq/keep#6840, issue keephq/keep#6835)
- Topology UI: service nodes colored by highest firing alert severity with a
  legend (keephq/keep#6838, issue keephq/keep#6839)
- Topology UI: cascade warnings — `⚠ N` badge on services affected by down
  critical dependencies, cycle-safe (keephq/keep#6842, issue keephq/keep#6841)

### Fixed
- Topology UI: alert badges never rendered on topology nodes because
  `useLastAlerts(undefined)` produced a null SWR key and `/alerts/query` was
  never executed (keephq/keep#6837, issue keephq/keep#6836)
- Topology UI: node data did not recompute when alerts changed (missing
  `allAlerts` effect dependency; included in keephq/keep#6838)

### Security
- N/A
