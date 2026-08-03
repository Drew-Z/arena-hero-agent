# Changelog

All notable changes to this project will be documented in this file. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses semantic versioning.

## [Unreleased]

### Added

- Public documentation navigation, executable clone-first quick starts, compatibility fields in bug reports, and clearer community reporting guidance.
- Release tags now pass the complete reusable CI workflow before publishing, with version validation, SBOM, provenance, and image-digest reporting.
- Tolerant stationary-Core confirmation across short visibility gaps, while still requiring three real same-position observations before a raid.
- Structured v0.11 upkeep due/paid/deficit and excess-Unit damage diagnostics with deterministic supervisor and optional model-review triggers.
- Bounded long-range raids against confirmed stationary, unprotected Cores, with strike-distance hysteresis and immediate combat-pressure recall.
- Gameplay v0.13 and official SDK 0.2.8 compatibility, including conservative Ranger cell fire against a confirmed stationary Core during short visibility gaps.

### Changed

- systemd upgrades now preflight host requirements, restart the Agent after compatibility validation, and support explicit supervisor, AI, and optimizer disable paths.
- Docker Compose now uses the same graceful `SIGINT` shutdown contract as systemd.
- Resource targets now use deterministic minimum-cost Worker matching with limited intent stickiness instead of preserving a worse assignment indefinitely.
- Scout routes prefer less recently covered chunks and rotate after three consecutive non-improving Ticks.

## [0.1.0] - 2026-08-03

### Added

- Cross-platform local bootstrap and launch scripts.
- Docker and Docker Compose deployment with runtime secret mounting.
- Hardened systemd installer with optional supervisor, AI review, and optimizer tiers.
- GitHub CI, community health files, and release documentation.
- Accepted-Turn heartbeat and deterministic unattended health checks for systemd and Compose.
- Deterministic resource-first tactic, structured diagnostics, compatibility monitor, read-only supervisor, and bounded runtime optimizer.
- Tag-driven GHCR release images for build-free Compose deployment.

### Changed

- AI supervisor review now requires explicit `ARENA_SUPERVISOR_AI_ENABLED=true` opt-in.
- Model IDs and model credentials are no longer embedded in systemd units.
- The main systemd service no longer depends on a supervisor refresh timer.
- systemd installation now requires an immediate compatibility check before starting the Agent.
