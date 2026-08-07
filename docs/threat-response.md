# Threat Response State Machine

This policy groups the infinite world-state space into a finite set of tactical
equivalence classes. It is deterministic and runs inside the normal Tick loop;
it does not call a language model or rewrite itself while the game is live.

## Safety Invariants

1. Core survival has priority over loot, production, raids, scouting, and cargo.
2. The Core is never intentionally self-destructed.
3. A hostile movement observation is an alert, not proof of pursuit.
4. A hostile position from an old Turn is memory, not current truth.
5. Movement and attack are mutually exclusive Unit actions. An enemy cannot move
   and attack in the same Tick.
6. Normal production stops at 30 Units: 23 Workers, 3 Vanguards, and 4 Rangers.
   Dynamic-price expansion is staged and never exceeds this tested cap.
7. No raid continues through local combat pressure merely because the original
   target still exists.

## Hierarchical Controller

The controller does not flatten every combination into one giant state enum.
It keeps three layers independent so, for example, recovery can coexist with an
active attack without either fact being lost:

| Layer | States | Purpose |
| --- | --- | --- |
| Lifecycle | `ACTIVE`, `RESPAWNING`, `COMPATIBILITY_HOLD`, `RECOVERY` | Applies fail-closed operational constraints. |
| Threat | `NORMAL`, `ALERT`, `PRE_EVADE`, `ENGAGED`, `BREAKOUT` | Selects preparation, defense, or escape behavior. |
| Mission | economy, guard, Scout, observer, raid, disengage, return, heal | Keeps local Unit work subordinate to the first two layers. |

`ThreatAssessment` stores the orthogonal evidence: recent attacks, active and
approaching enemy IDs, confirmed pursuit, current Core attackers, nearby combat
Units, local squad contact, disengage memory, and post-threat caution. Its
`global_posture` is only a compact operational summary. Consumers that make
safety decisions continue to read the underlying facts, so
`COMPATIBILITY_HOLD + ENGAGED` still permits an emergency retreat and
`RECOVERY + PRE_EVADE` still blocks unsafe production.

The structured log exposes `global_posture`, `threat_level`, and
`threat_reason`. The systemd status line exposes the same summary as `posture`,
`threat`, and `threat_reason`.

## Threat Levels

| State | Entry | Immediate behavior | Exit or escalation |
| --- | --- | --- | --- |
| `NORMAL` | No active combat threat. | Collect, produce within reserves, maintain layered guards, and run bounded missions. | Any combat Unit movement enters `ALERT`. |
| `ALERT` | A visible Vanguard or Ranger changed position between observations. | Release raids and stationary-clearance work, pause non-emergency production, and split guards across observed threat axes. | Expires after two complete hidden Ticks without renewed movement; approaching or attacking enemies escalate. |
| `PRE_EVADE` | Estimated time to enemy attack range is at most 16 Ticks, a pursuit is confirmed, or an enemy is within the 12-cell fallback radius. | Start or preserve the safest viable Core migration. Do not cancel it for routine cargo, repair, or healing. | Returns to `ALERT` after the approach memory expires, or escalates on attack. |
| `ENGAGED` | Core/fleet attack event, or a visible enemy has a current legal attack on the Core. | Counterfire with legal defenders, retain attacker positions for exactly six planning Ticks, and continue a non-worsening emergency migration. | Falls back only after attack memory expires and no other alert remains. |
| `BREAKOUT` | Multiple threat axes leave no direction that strictly increases distance from every enemy. | Choose the legal destination with the least current projected damage, then maximize the sorted enemy-distance vector. A lower-damage cell is allowed even when one enemy becomes closer. | Re-evaluate every Turn; cancel only for a hard block or a destination with worse projected damage/risk. |

`ALERT` deliberately does not move the Core for a distant lateral or retreating
enemy. The Core is four times slower than Units, so an approaching track uses a
time-to-range trigger instead of waiting only for the 12-cell fallback.
Merely seeing a distant stationary combat Unit, or retaining a conservative
post-threat caution timer, does not by itself create an activity alert.

## Lifecycle Overlays

- `RESPAWNING` queues no invented actions while the authoritative Turn lacks a
  Core.
- `COMPATIBILITY_HOLD` stops raids, ordinary migration, and production but
  retains legal harvesting, deposits, healing, shield repair, and emergency
  evasion.
- `RECOVERY` clears battlefield memory, rebuilds locally after a replacement
  Core, and remains compatible with simultaneous threat classification.

## Multi-Axis Breakout

Core and Unit escape candidates use a lexicographic survival key:

```text
(
  projected_current_tick_hits,
  negative_sorted_enemy_distances,
  Beacon-retreat preference,
  direction continuity,
  deterministic direction order,
)
```

Obstacles, resource terrain for the Core, enemy-occupied cells, and full cells
remain hard blocks. Current attack lanes are risks, not hard terrain. This
distinction prevents two failure modes:

- freezing when every direction approaches at least one enemy;
- cancelling a migration into one hostile lane while remaining in a cell hit by
  several hostile lanes.

The model is conservative but rule-correct. It scores attacks available from an
enemy's current cell. It does not pretend that an enemy can move and attack in
one Tick.

## Defense Distribution

Visible combat enemies are bucketed by their primary direction from the Core.
Guards rotate across those axes instead of all selecting the single nearest
enemy:

- Vanguards hold the outer three-cell screen;
- Rangers hold the inner two-cell screen;
- active and confirmed-pursuit IDs receive axis priority;
- a legal immediate attack takes precedence over repositioning;
- defenders do not chase beyond the protective posture.

When every zero-risk route is gone, a defender counterattacks if it has a legal
shot or sweep. Otherwise it takes the lowest-risk move or explicitly waits.

## Detached Squad Response

Core raids and stationary-clearance missions are subordinate to local survival.
If a non-target Vanguard or Ranger comes within the strike group's local
five-cell response radius:

1. release the old target and observer assignment;
2. mark the strike members for return;
3. allow an immediately legal counterattack;
4. recall survivors toward Core guard posts for at least eight Ticks;
5. keep the Core stationary unless the enemy also satisfies a Core-specific
   pre-evade or attack condition.

This separates "the squad is being intercepted" from "the Core is being
chased." A remote skirmish pauses expansion and cancels offensive work, but it
does not drag the slow Core across the map.

## Scout And Observer Response

An empty remote Worker that evades a combat Unit enters a persistent return
flow:

```text
SCOUT -> EVADE -> RETURN -> COOLDOWN -> SCOUT
```

- `EVADE` picks the lowest-risk available step.
- `RETURN` continues toward the Core after contact is lost instead of resuming
  the old exploration ray.
- arrival within three cells starts a three-Tick cooldown;
- old route progress is not treated as a reason to re-enter the threat area;
- an observer threatened during a raid is recalled with the strike group.

## Fight Or Withdraw

Fight when all of the following are true:

- the attack is legal from the authoritative current Turn;
- firing does not replace a required Core-survival move for that Unit;
- the target is an immediate threat, confirmed pursuer, or current mission
  target outside a disengage state;
- at least the minimum Core guard layer remains.

Withdraw when any of the following is true:

- the Core reaches `PRE_EVADE`, `ENGAGED`, or `BREAKOUT`;
- a detached squad is intercepted by a non-target combat Unit;
- a Scout or observer is exposed inside the local response radius;
- an offensive Core becomes protected, moves, contradicts memory, or exceeds the
  bounded mission distance;
- the only available combat would turn defense into an unbounded chase.

Rangers use target-free cell fire for a legal current cell. A shot at a moving
Unit's remembered cell is not treated as certain. Stationary Core memory remains
usable only under its separate repeated-observation contract.

## Visibility And Cover

Permanent obstacles are retained as map truth. They block vision and Ranger
fire under their respective rules. Evasion prefers cells where current Ranger
lines are blocked, but old enemy positions are only short-lived risk hints.

The controller cannot know an enemy's submitted action. Therefore it does not
claim guaranteed dodges or guaranteed shots against a mobile target. Each new
Turn replaces the prior position truth, and the tactic re-scores cover, attack,
and escape candidates immediately.

## Scenario Matrix

| Situation | Classification | Required result |
| --- | --- | --- |
| Distant stationary combat Unit | Observation only | No activity alert and no production pause. |
| Distant lateral movement | `ALERT` | Recall/prepare, but do not move the Core. |
| Closing movement with time-to-range at most 16 | `PRE_EVADE` | Start early migration. |
| Confirmed distant pursuit | `PRE_EVADE` | Preserve migration through short visibility loss. |
| Core attacked, attacker hidden | `ENGAGED` | Retain only geometrically possible or explicitly attributed attackers. |
| Four-way Ranger crossfire | `BREAKOUT` | Move to the legal cell with fewer projected hits. |
| Enemies on opposite axes | `ALERT`/`ENGAGED` | Split guard posts across axes. |
| Remote raid squad intercepted | Detached disengage | Counterattack if legal, release target, and return without moving Core. |
| Scout enemy briefly disappears | Scout return | Continue returning; do not resume the old ray. |
| Core respawns elsewhere | `RECOVERY` | Clear old threat positions and rebuild locally. |

## Controlled Optimization

Online decisions may adapt to observations, but live code and thresholds do not
mutate. Optimization is an offline release process:

1. capture structured Tick diagnostics and the unhealthy window;
2. classify the incident by the scenario matrix;
3. replay candidate thresholds and rules against saved fixtures;
4. require all safety invariants and regression tests to pass;
5. deploy a versioned commit and compare survival, damage, resource throughput,
   missed attacks, and mission-return metrics.

A model can summarize logs or propose candidate changes outside the Tick loop.
It is optional and never has authority to publish an untested live strategy.
The current runtime optimizer intentionally changes only an allow-listed
economic profile; expansion into combat parameters requires replay and shadow
evaluation first.
