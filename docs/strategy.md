# Aggressive Expansion Strategy

The default profile converts resource income into a large combat force, then
uses that force to remove nearby opponents and contest the Champion Beacon.
Arena Hero does not expose a territory-ownership command, so expansion means
exploration, outward patrols, enemy removal, and durable map presence.

## Population Plan

| Stage | Worker | Vanguard | Ranger | Total | Intent |
| --- | ---: | ---: | ---: | ---: | --- |
| Establish | 6 | 2 | 2 | 10 | Keep harvesting while establishing the first attack screen. |
| Mobilize | 12 | 6 | 8 | 26 | Build enough damage to attack nearby Cores continuously. |
| Overwhelm | 18 | 14 | 16 | 48 | Maintain two Core guards and send the rest outward. |

The Core capacity at the final population is `max(10, 48 * 5) = 240`.
There is no upkeep in gameplay v0.14. Every spawn branch previews the current
price with the official SDK's `unit_cost()`; the settled spawn event remains
authoritative. Ordinary production keeps a ten-resource Core reserve, while an
immediate threat can spend it on emergency combat units.

## Core And Economy

- A Worker occupying the Core production cell moves away before a spawn. If
  both exits are occupied, the deterministic corridor handoff clears one.
- Resource cells are observations, not permanent terrain. Workers use stable
  one-to-one assignments and return loaded cargo to the Core.
- Production continues during an offensive mission unless survival logic must
  heal, repair, evacuate, or clear an occupied production cell.
- The Core remains stationary for ordinary expansion and Beacon pressure. Only
  verified survival threats start a four-Tick Core migration.

## Combat Priorities

1. Survive a direct Core threat.
2. Attack a visible hostile Core.
3. Attack visible hostile combat units and Workers.
4. Patrol unexplored or stale perimeter sectors.
5. Contest the Champion Beacon when the force is mature.

Visible enemy Cores are not required to be isolated, stationary, repeatedly
confirmed, or escort-free before becoming a mission target. A remote escort can
change local threat posture, but it does not erase the Core target. Rangers use
legal cell fire; Vanguards sweep adjacent targets or move toward the target.

One Vanguard and one Ranger remain as Core guards. Other combat units join the
strike group. When there is no visible target, non-guard units follow
deterministic outward patrol sectors whose radius grows with elapsed Ticks.

## Beacon Campaign

The Beacon campaign starts only when all of these are true:

- population is at least 40;
- resources are at least 30;
- the Core has full HP and shield;
- there is no current Core attack or threatening enemy;
- more than one Vanguard is available so the guard layer remains intact.

The selected Vanguard travels to a ground Beacon, picks it up, and returns to
the Core. The Core itself never moves just to pursue or retreat from a Beacon.

## Safety And Recovery

Lifecycle, threat, and mission layers remain independent. `RESPAWNING` queues
no invented actions, `COMPATIBILITY_HOLD` stops offensive production, and
`RECOVERY` rebuilds locally after a replacement Core. A hard survival threat
still overrides the aggressive mission plan and starts the existing multi-axis
evasion logic.

Every accepted Turn can be written to SQLite. The dashboard uses this history
to replay explored cells, resources, unit trails, events, and historical enemy
Core sightings without exposing credentials.
