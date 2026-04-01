# AdversarialCoder — SPEC.md

## What It Is

An autonomous coding agent built on three leaked Claude Code patterns:
1. **SecurityMonitor** — pre-action immune system (blocks destructive/harmful actions before execution)
2. **VerificationSpecialist** — post-action adversarial testing (catches what the implementer missed)
3. **DreamConsolidation** — between-session memory merging (learns from every session, never forgets)

Combined with Context Nexus as the persistent memory layer, this is a self-improving agent that:
- Catches its own mistakes before they ship
- Learns from every session
- Never makes the same mistake twice
- Has domain knowledge from real production codebases

## Architecture

```
User Input
    ↓
SecurityMonitor (pre-action guard)
    ↓ [ALLOW] → Agent Action
    ↓ [BLOCK] → Stop + explain why
    ↓
VerificationSpecialist (adversarial testing)
    ↓ [PASS]  → Commit + DreamConsolidation
    ↓ [FAIL]  → Fix + retry
    ↓
DreamConsolidation (memory merge)
    ↓
Context Nexus (persistent memory)
```

## Components

### SecurityMonitor
Reads: current action transcript
Evaluates: BLOCK conditions (destructive ops, credential access, scope creep, external posting)
Outputs: `{shouldBlock: bool, reason: string}`
Built from: `agent-prompt-security-monitor-for-autonomous-agent-actions-*.md` (5876 tokens)

### VerificationSpecialist
Reads: parent's current-turn conversation, modified files
Runs: builds, tests, linters, adversarial probes (boundary values, concurrency, idempotency)
Outputs: `VERDICT: PASS | FAIL | PARTIAL`
Built from: `agent-prompt-verification-specialist.md` (2866 tokens)

### DreamConsolidation
Triggered: after each session or on idle
Process:
1. Orient on existing memory files
2. Gather recent session signal
3. Merge into topic memory files
4. Prune redundant entries
Built from: `agent-prompt-dream-memory-consolidation.md`

### HookSystem
Event-driven automation:
- PostToolUse(Write|Edit) → auto-format + lint
- PostToolUse(Bash) → log command to audit
- PreToolUse(Bash) → security pre-check
- SessionStart → load relevant memory files
Built from: `system-prompt-hooks-configuration.md`

### AttributionLedger
Tracks: contributions per codebase, corrections caught, errors fixed
Pays: 70/30 split on attribution claims
Pattern: from DenialNet's attribution system

## Memory Schema

```
memory/
  YYYY-MM-DD.md           — daily session log
  topics/
    security-patterns.md   — security rules learned
    codebase-map.md       — repo structure + conventions
    mistakes-avoided.md   — errors caught + how to avoid
    agent-behavior.md     — what works / what doesn't
  consolidated/
    last-dream.md         — most recent consolidation summary
    active-projects.md    — current project states
```

## Products Built With It

- DenialNet — insurance claim denial patterns
- CPIN — child welfare screening patterns
- LeakLock — CSV data leak scanning
- Context Nexus — OpenClaw memory/observability

## Revenue Model

- Hosted AdversarialCoder agent (API access)
- Per-seat subscription for teams
- Enterprise: on-prem deployment with custom security rules

## Status

v0.1 — spec only, not yet built
