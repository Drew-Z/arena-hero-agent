# Strategy Profile

The default profile optimizes for long-term Core survival and resource accumulation rather than Beacon progress or indiscriminate combat.

## Population Plan

The mature target is:

| Unit | Target | Role |
| --- | ---: | --- |
| Worker | 23 | Harvest, deposit, scout, observe, and recover dropped cargo. |
| Vanguard | 3 | Outer screen, route screening, durable Core defense, and bounded assault reinforcement. |
| Ranger | 4 | Inner Core defense and ranged stationary-target clearing. |
| Total | 30 | Provides the 150-resource Core capacity target. |

Gameplay v0.14 has no per-Tick upkeep or maintenance damage. Production still
reserves resources for healing, shield repair, and emergency replacement. Every
spawn branch previews the current price with the official SDK's `unit_cost()`;
the settled `CORE_SPAWN_SUCCEEDED.values.cost` remains authoritative.

The tactic first grows to 12 Workers, completes the 3-Vanguard and 4-Ranger
defense, then resumes Worker expansion. Later growth is staged at populations
20, 24, 29, and 30 so each dynamic-price band is funded before another spawn.
Worker prices are 7 at population 20-24 and 8 at 25-29.

## Core Safety

- The Core is the highest-value object and is never intentionally self-destructed.
- Runtime decisions use independent lifecycle, threat, and mission layers.
  `global_posture` is a diagnostic summary rather than a replacement for the
  underlying attack, pursuit, recovery, and compatibility facts.
- With the default `retreat` policy, migration candidates favor directions away from the Beacon and visible threats.
- Guards are distributed around the Core instead of stacking on its cell or blocking Worker routes.
- Any observed Vanguard/Ranger movement enters a short alert that recalls missions, pauses expansion production, and reorients defenders. Lateral activity does not move the Core by itself.
- An approaching enemy whose estimated time to attack range is at most 16 Ticks starts pre-emptive evasion. A confirmed distant pursuit also starts evasion before the normal 12-cell fallback trigger.
- Recent attack positions remain actionable for exactly six planning Ticks when visibility is lost; event geometry and explicit actor IDs exclude unrelated enemies when possible.
- Multi-axis breakout minimizes projected damage before comparing the complete sorted enemy-distance vector, so the Core can leave crossfire even when no step increases distance from every enemy.
- An emergency migration whose destination does not worsen projected damage or aggregate enemy risk is allowed to finish instead of being cancelled for an immediate heal or cargo deposit. A hard-blocked or riskier destination can still cancel.
- Core and fleet attack memory are separate: a remote Worker taking damage recalls the defense posture but does not by itself move the Core.
- A compatibility marker forces conservative behavior when published rules, the server contract, or the SDK no longer match the tested profile.

## Economy and Scouting

- Resource cells are treated as dynamic observations, not permanent terrain.
- Empty Workers and remembered resource cells are paired with deterministic minimum-cost matching. A small intent bonus prevents churn, but a materially closer Worker can take over a target; each resource remains assigned to at most one Worker.
- Resource routes are released after six non-improving Ticks. Scout routes change direction after three non-improving Ticks, prioritize the least recently observed chunks, and avoid sending every Worker through the same corridor.
- Loaded Workers prioritize a legal return route and account for Core movement.
- When a Worker occupies the Core production cell, it moves out before a spawn.
  If both exits are occupied, the tactic queues a deterministic corridor
  handoff; a full Core also keeps loaded Workers outside until storage reopens.
- A loaded Worker may use the second legal slot of a cell occupied by one
  friendly unit. Core egress uses the same narrow exception, while normal
  movement still reserves one destination per Tick to prevent uncontrolled
  stacking.
- Recovery mode protects the replacement Worker and dropped cargo after a Core loss.

## Combat Policy

- Active enemy fleets release raids and stationary-clearance targets, pause non-emergency production, then distribute defenders across distinct threat-facing axes around the Core.
- A detached strike group intercepted by a non-target combat Unit releases its mission, counterattacks when immediately legal, and returns to the Core without forcing a remote Core migration.
- A remote Scout that evades a combat Unit keeps returning after contact is lost, then observes a short cooldown near the Core before it can resume scouting.
- A defender with a legal attack during combat pressure counterattacks before generic retreat. Rangers prioritize hostile Rangers, then Vanguards, and continue firing on every legal Tick instead of alternating by Tick parity. This does not authorize a chase.
- During safe, uncongested windows, one wounded non-assault defender at a time
  returns to a stationary, healthy Core for healing. The reserve covers the
  exact missing HP, another same-type guard remains outside, and combat pressure
  or imminent cargo delivery pauses the return.
- Confirmed stationary units can be cleared by a small bounded strike group while guards remain with the Core, but only outside combat pressure.
- A stationary Core is considered for a raid only after repeated observations and isolation checks. The Worker that exposed it may remain as the designated observer. The default strike group can engage from at most 48 path-independent Manhattan cells and releases a target if pulled beyond 56, while one Vanguard and one Ranger remain as Core guards.
- Under gameplay v0.14, Rangers use target-free cell fire for legal defensive shots so another hostile remaining in the submitted cell can still be hit. A strike Ranger may also fire at the remembered cell of a confirmed stationary Core during a short visibility gap.
- Loss of visibility does not immediately invalidate stationary-target memory, but moving escorts, contradictory observations, age, and risk reduce confidence.
- Loot events, storage capacity, same-Tick Core survival, and return-path cost determine whether a kill was economically useful.

## Current Optimization Priority

The strategy currently has focused unit tests and structured diagnostics for economy stalls, blocking, Core survival, scouting coverage, combat pressure, lifecycle events, dynamic spawn prices, repeated affordability failures, and unexplained resource loss. New tuning should be driven by a captured unhealthy window rather than by increasing fleet size or adding a model to the Tick loop.

The first hierarchical-controller stage is implemented: every planned Turn now
records `global_posture`, `threat_level`, and a deterministic `threat_reason`.
The next stages are a shared two-horizon action-risk evaluator, persistent squad
missions, visibility-aware cover postures, generated scenario tests, and only
then shadow evaluation of additional allow-listed parameters.

The complete threat states, engagement boundaries, multi-axis breakout scoring,
visibility assumptions, and offline optimization contract are documented in
[Threat Response State Machine](threat-response.md).
