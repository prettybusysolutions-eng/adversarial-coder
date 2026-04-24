"""
agent_parliament.py — Self-organizing adversarial agent parliament.

Five constitutional roles challenge each other's outputs. Consensus requires
surviving formal verification AND Red Team adversarial probing — not just
peer agreement.

The "frontier without hardware" insight: adversarial constitutional pressure +
Red Team exploitation probing + formal verification ground truth +
semantic collective memory = emergent reliability that exceeds any single
small model's output, improving over time without retraining.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from parliament_tools import AnthropicClient, FormalVerifier, ToolResult
from security_monitor import SecurityMonitor

# Optional elite components — graceful degradation if not installed
try:
    from red_team import RedTeamAgent, RedTeamReport
    _RED_TEAM_AVAILABLE = True
except ImportError:
    _RED_TEAM_AVAILABLE = False

try:
    from semantic_memory import SemanticMemory
    _SEMANTIC_MEMORY_AVAILABLE = True
except ImportError:
    _SEMANTIC_MEMORY_AVAILABLE = False


# ── Role system prompt constants ─────────────────────────────────────────────

IMPLEMENTER_SYSTEM_PROMPT = """You are the Implementer in an adversarial agent parliament.

Your constitutional role: produce concrete, working code. Not pseudocode. Not plans. Code.

You know a Critic will review your output. Make your first attempt as correct as possible.
Do not hedge. Do not say "this might work". Either it works or it doesn't.

Always respond with a JSON object matching this exact schema:
{
  "code": "the complete implementation as a string",
  "explanation": "one sentence describing what was done",
  "files_modified": ["list", "of", "file", "paths"],
  "commands_to_run": ["list", "of", "shell", "commands", "if", "any"]
}

If the task is a question or read-only, set code to "" and use explanation for the answer.
If no commands are needed, set commands_to_run to [].
If no files are modified, set files_modified to []."""

CRITIC_SYSTEM_PROMPT = """You are the Critic in an adversarial agent parliament.

Your constitutional role: find flaws. Assume the Implementer's proposal is wrong until proven otherwise.
You are looking for the THREE most serious problems. Not style preferences — real defects.

Common things to check:
- Logic errors: does this actually do what it claims?
- Edge cases: what happens with empty input, None, 0, very large values?
- Correctness: does the code match the task requirements?
- Completeness: is anything missing from the implementation?
- Assumptions: does this make assumptions that are not guaranteed?

If you find NO real flaws, say so explicitly. Do not invent flaws.
Do not rubber-stamp. "Looks good" with no reasoning is a failure of your role.

Always respond with a JSON object matching this exact schema:
{
  "flaws": [
    {
      "severity": "HIGH or MEDIUM or LOW",
      "description": "specific description of the flaw",
      "suggested_fix": "concrete suggestion"
    }
  ],
  "verdict": "ACCEPT or REVISE",
  "reasoning": "one sentence summary of your verdict"
}

verdict must be REVISE if any HIGH or MEDIUM severity flaws exist.
verdict may be ACCEPT if only LOW severity flaws or no flaws exist."""

SECURITY_AUDITOR_SYSTEM_PROMPT = """You are the Security Auditor in an adversarial agent parliament.

Your constitutional role: evaluate whether this action is safe to execute.
You have veto power. Use it when in doubt. It is harder to un-veto than to re-allow.

Evaluate against these BLOCK conditions:
- Force pushing or rewriting remote git history
- Deploying to production without verification
- Embedding secrets or credentials in code
- Sending credentials to external endpoints
- Irreversible deletion of files or data
- Downloading and executing code from external sources
- Granting elevated permissions
- Modifying shared infrastructure
- Adding persistent access mechanisms (cron, ssh keys)
- Modifying the agent's own configuration

If the action is clearly safe and produces no security risk, do NOT veto.
Only veto when there is a genuine security concern.

Always respond with a JSON object matching this exact schema:
{
  "veto": true or false,
  "veto_reason": "reason if veto is true, empty string if false",
  "security_concerns": [
    {
      "severity": "HIGH or MEDIUM or LOW",
      "description": "description of concern"
    }
  ]
}"""

ARCHITECT_SYSTEM_PROMPT = """You are the Architect in an adversarial agent parliament.

Your constitutional role: evaluate the structural quality of the proposed implementation.
You are NOT looking for bugs (that is the Critic's job). You are looking at design:

- Abstractions: are they appropriate for the problem?
- Coupling: does this create unnecessary dependencies?
- Naming: do names clearly communicate intent?
- Extension points: if this needs to grow, can it?
- Consistency: does this fit the patterns of the existing codebase?
- Simplicity: is this as simple as it can be while meeting requirements?

If the architecture is sound, say so. Do not invent concerns.

Always respond with a JSON object matching this exact schema:
{
  "architectural_verdict": "SOUND or QUESTIONABLE or UNSOUND",
  "concerns": [
    {
      "area": "e.g. coupling, naming, etc.",
      "description": "specific concern"
    }
  ],
  "suggested_refactors": ["list of optional improvement suggestions"]
}

SOUND: no architectural concerns
QUESTIONABLE: minor concerns, acceptable to ship with caveats
UNSOUND: fundamental design problems that should be addressed before shipping"""


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class Challenge:
    role: str
    severity: str   # HIGH | MEDIUM | LOW | NONE
    description: str
    verdict: str    # ACCEPT | REVISE | VETO | SOUND | QUESTIONABLE | UNSOUND


@dataclass
class DebateRound:
    round_number: int
    implementer_output: dict
    challenges: list[Challenge] = field(default_factory=list)
    consensus: bool = False
    veto: bool = False
    veto_reason: str = ""


@dataclass
class DebateResult:
    task: str
    compute_tier: str       # "single" | "dual" | "parliament" | "blocked_pre"
    rounds: list[DebateRound]
    final_output: dict      # Winning Implementer output, or {} if blocked
    winning_position: str   # "implementer" | "critic_revision" | "blocked"
    block_reason: str = ""
    formal_results: list[ToolResult] = field(default_factory=list)
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    red_team_report: Optional[object] = field(default=None, repr=False)  # RedTeamReport | None


# ── AgentParliament ───────────────────────────────────────────────────────────

class AgentParliament:
    """
    Self-organizing adversarial agent parliament.

    Compute tiers:
      single     — Implementer only. Read-only tasks, simple queries.
      dual       — Implementer + Critic. Code changes.
      parliament — All 5 agents + formal verification. Features, security, architecture.

    Elite additions (opt-in, gracefully degrade if unavailable):
      enable_red_team       — 5th agent probes for exploitable vulnerabilities
      enable_semantic_memory — vector memory replaces regex+markdown memory
    """

    def __init__(
        self,
        api_key: str,
        memory_dir: str,
        project_path: str,
        max_debate_rounds: int = 3,
        enable_red_team: bool = True,
        enable_semantic_memory: bool = True,
    ):
        self.client = AnthropicClient(api_key=api_key)
        self.formal_verifier = FormalVerifier()
        self.security_monitor = SecurityMonitor()
        self.project_path = project_path
        self.max_debate_rounds = max_debate_rounds
        self._session_transcript: list[str] = []

        # Load memory context once at init
        from dream_consolidation import DreamConsolidation
        self._dream = DreamConsolidation(memory_dir)
        self._memory_context = self._dream.get_memory_context()

        # Red Team — 5th constitutional adversarial agent
        self._red_team: Optional[RedTeamAgent] = None
        if enable_red_team and _RED_TEAM_AVAILABLE:
            self._red_team = RedTeamAgent(self.client)

        # Semantic memory — vector-based collective knowledge store
        self._semantic_memory: Optional[SemanticMemory] = None
        if enable_semantic_memory and _SEMANTIC_MEMORY_AVAILABLE:
            self._semantic_memory = SemanticMemory(
                persist_dir=str(Path(memory_dir) / "semantic")
            )

    def deliberate(self, task: str) -> DebateResult:
        """Main entry point. Routes to the appropriate compute tier."""
        tier = self._classify_compute_tier(task)
        print(f"  [parliament] tier={tier}")

        if tier == "single":
            return self._single_agent_pass(task)
        elif tier == "dual":
            return self._dual_agent_debate(task)
        else:
            return self._full_parliament_debate(task)

    def end_session(self) -> dict:
        """Write session transcript to the daily log, then run DreamConsolidation."""
        if not self._session_transcript:
            return {}

        log_dir = Path(self._dream.memory_dir)
        today = datetime.now().strftime("%Y-%m-%d")
        log_path = log_dir / f"{today}.md"

        with open(log_path, "a") as f:
            f.write(f"\n\n## Parliament Session — {datetime.now().strftime('%H:%M')}\n\n")
            f.write("\n\n---\n\n".join(self._session_transcript))

        return self._dream.consolidate(dry_run=False)

    # ── Compute tier classification ───────────────────────────────────────────

    def _classify_compute_tier(self, task: str) -> str:
        task_lower = task.lower()

        # Read-only signals → SINGLE (use word-boundary match for single-word keywords)
        readonly_phrases = ["what is", "show me", "how does", "why does", "what does", "tell me"]
        readonly_words = {"explain", "list", "read", "describe", "summarize"}
        task_words = set(task_lower.split())
        if any(ph in task_lower for ph in readonly_phrases) or (task_words & readonly_words):
            return "single"

        # High-complexity signals → PARLIAMENT
        parliament_phrases = ["add feature", "api endpoint", "create new"]
        parliament_words = {
            "implement", "refactor", "security", "auth", "deploy", "database",
            "schema", "permission", "migrate", "architecture", "redesign", "build",
        }
        if any(ph in task_lower for ph in parliament_phrases) or (task_words & parliament_words):
            return "parliament"

        # Mutation signals → at least DUAL
        dual_keywords = ["fix", "change", "update", "modify", "rename", "move", "delete", "remove", "edit"]
        if any(kw in task_lower.split() for kw in dual_keywords):
            return "dual"

        # File extension mentions → at least DUAL
        file_extensions = [".py", ".js", ".ts", ".go", ".rs", ".sql", ".yaml", ".yml", ".json", ".toml"]
        if any(ext in task_lower for ext in file_extensions):
            return "dual"

        # Token length fallback
        word_count = len(task.split())
        if word_count < 10:
            return "single"
        elif word_count > 50:
            return "parliament"
        return "dual"

    # ── Single agent path ─────────────────────────────────────────────────────

    def _single_agent_pass(self, task: str) -> DebateResult:
        implementer_out = self._run_implementer(task, round_number=1)

        round_ = DebateRound(
            round_number=1,
            implementer_output=implementer_out,
            consensus=True,
        )

        result = DebateResult(
            task=task,
            compute_tier="single",
            rounds=[round_],
            final_output=implementer_out,
            winning_position="implementer",
            total_input_tokens=self.client.total_input_tokens,
            total_output_tokens=self.client.total_output_tokens,
        )
        self._session_transcript.append(self._build_summary(result))
        return result

    # ── Dual agent path ───────────────────────────────────────────────────────

    def _dual_agent_debate(self, task: str) -> DebateResult:
        rounds = []
        previous_critique: Optional[dict] = None
        winning_position = "implementer"

        for round_num in range(1, self.max_debate_rounds + 1):
            implementer_out = self._run_implementer(
                task, round_number=round_num, previous_critique=previous_critique
            )
            critic_out = self._run_critic(task, implementer_out)

            challenges = self._extract_challenges_from_critic(critic_out)
            consensus = critic_out.get("verdict", "REVISE") == "ACCEPT"

            rounds.append(DebateRound(
                round_number=round_num,
                implementer_output=implementer_out,
                challenges=challenges,
                consensus=consensus,
            ))

            if consensus:
                break

            previous_critique = critic_out
            winning_position = "critic_revision"

        result = DebateResult(
            task=task,
            compute_tier="dual",
            rounds=rounds,
            final_output=rounds[-1].implementer_output,
            winning_position=winning_position,
            total_input_tokens=self.client.total_input_tokens,
            total_output_tokens=self.client.total_output_tokens,
        )
        self._session_transcript.append(self._build_summary(result))
        return result

    # ── Full parliament path ──────────────────────────────────────────────────

    def _full_parliament_debate(self, task: str) -> DebateResult:
        # SecurityAuditor pre-check on the task itself before any generation
        auditor_pre = self._run_security_auditor(task, {})
        if auditor_pre.get("veto"):
            return self._make_blocked(
                task, "parliament",
                f"SecurityAuditor pre-veto: {auditor_pre.get('veto_reason', '')}",
            )

        rounds = []
        previous_critique: Optional[dict] = None
        winning_position = "implementer"

        for round_num in range(1, self.max_debate_rounds + 1):
            implementer_out = self._run_implementer(
                task, round_number=round_num, previous_critique=previous_critique
            )

            critic_out = self._run_critic(task, implementer_out)

            # SecurityAuditor veto check on the produced proposal
            auditor_out = self._run_security_auditor(task, implementer_out)
            if auditor_out.get("veto"):
                return self._make_blocked(
                    task, "parliament",
                    f"SecurityAuditor veto (round {round_num}): {auditor_out.get('veto_reason', '')}",
                )

            challenges = (
                self._extract_challenges_from_critic(critic_out)
                + self._extract_challenges_from_auditor(auditor_out)
            )
            has_high = any(c.severity == "HIGH" for c in challenges)
            consensus = critic_out.get("verdict", "REVISE") == "ACCEPT" and not has_high

            rounds.append(DebateRound(
                round_number=round_num,
                implementer_output=implementer_out,
                challenges=challenges,
                consensus=consensus,
                veto=auditor_out.get("veto", False),
                veto_reason=auditor_out.get("veto_reason", ""),
            ))

            if consensus:
                break

            previous_critique = critic_out
            winning_position = "critic_revision"

        final_out = rounds[-1].implementer_output

        # Architect review — structural soundness (runs once after debate)
        architect_out = self._run_architect(task, final_out)

        # Red Team — 5th constitutional agent: adversarial exploitation probing
        red_team_report: Optional[RedTeamReport] = None
        if self._red_team:
            red_team_report = self._red_team.probe(task, final_out)
            if red_team_report.is_critical:
                result = self._make_blocked(
                    task, "parliament",
                    f"Red Team CRITICAL: {red_team_report.summary}",
                )
                result.red_team_report = red_team_report
                if self._semantic_memory:
                    self._semantic_memory.store_debate_outcome(result, self._dream.memory_dir.name)
                return result

        # Formal verification — deterministic ground truth
        formal_results = self.formal_verifier.verify_all(self.project_path)
        formal_failures = [r for r in formal_results if not r.passed]

        # SecurityMonitor post-check on every produced shell command
        for cmd in final_out.get("commands_to_run", []):
            sec = self.security_monitor.evaluate(cmd)
            if sec.should_block:
                return self._make_blocked(
                    task, "parliament",
                    f"SecurityMonitor blocked produced command '{cmd}': {sec.reason}",
                    formal_results=formal_results,
                )

        # Formal failures are a hard block
        if formal_failures:
            winning_position = "blocked"
            block_reason = (
                f"Formal verification failed: {', '.join(r.tool for r in formal_failures)}"
            )
        else:
            block_reason = ""

        result = DebateResult(
            task=task,
            compute_tier="parliament",
            rounds=rounds,
            final_output=final_out if winning_position != "blocked" else {},
            winning_position=winning_position,
            block_reason=block_reason,
            formal_results=formal_results,
            total_input_tokens=self.client.total_input_tokens,
            total_output_tokens=self.client.total_output_tokens,
        )
        result.red_team_report = red_team_report

        if self._semantic_memory:
            self._semantic_memory.store_debate_outcome(result, self._dream.memory_dir.name)

        self._session_transcript.append(self._build_summary(result))
        return result

    # ── Individual role runners ───────────────────────────────────────────────

    def _run_implementer(
        self,
        task: str,
        round_number: int = 1,
        previous_critique: Optional[dict] = None,
    ) -> dict:
        system = self._inject_memory(IMPLEMENTER_SYSTEM_PROMPT, task=task)

        messages: list[dict] = [{"role": "user", "content": f"Task: {task}"}]
        if previous_critique:
            messages += [
                {"role": "assistant", "content": json.dumps({"note": "previous attempt"})},
                {
                    "role": "user",
                    "content": (
                        f"The Critic rejected your proposal:\n"
                        f"{json.dumps(previous_critique, indent=2)}\n\n"
                        f"Revise your implementation to address these concerns."
                    ),
                },
            ]

        return self.client.chat_with_structured_output(
            messages=messages,
            system=system,
            model="claude-sonnet-4-6",
            max_tokens=8192,
            temperature=0.7,
        )

    def _run_critic(self, task: str, implementer_output: dict) -> dict:
        messages = [{
            "role": "user",
            "content": (
                f"Task: {task}\n\n"
                f"Implementer's proposal:\n{json.dumps(implementer_output, indent=2)}"
            ),
        }]
        return self.client.chat_with_structured_output(
            messages=messages,
            system=CRITIC_SYSTEM_PROMPT,
            model="claude-haiku-4-5-20251001",
            max_tokens=2048,
            temperature=0.2,
        )

    def _run_security_auditor(self, task: str, implementer_output: dict) -> dict:
        content = f"Task: {task}"
        if implementer_output:
            content += f"\n\nProposed implementation:\n{json.dumps(implementer_output, indent=2)}"
        messages = [{"role": "user", "content": content}]
        return self.client.chat_with_structured_output(
            messages=messages,
            system=SECURITY_AUDITOR_SYSTEM_PROMPT,
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            temperature=0.1,
        )

    def _run_architect(self, task: str, implementer_output: dict) -> dict:
        messages = [{
            "role": "user",
            "content": (
                f"Task: {task}\n\n"
                f"Implementation:\n{json.dumps(implementer_output, indent=2)}"
            ),
        }]
        return self.client.chat_with_structured_output(
            messages=messages,
            system=ARCHITECT_SYSTEM_PROMPT,
            model="claude-sonnet-4-6",
            max_tokens=2048,
            temperature=0.4,
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _inject_memory(self, system_prompt: str, task: Optional[str] = None) -> str:
        parts = [system_prompt]
        if self._memory_context:
            parts.append(f"## Collective Memory (from past sessions)\n{self._memory_context}")
        if self._semantic_memory and task:
            semantic_ctx = self._semantic_memory.get_context_for_task(task)
            if semantic_ctx:
                parts.append(semantic_ctx)
        return "\n\n".join(parts)

    def _extract_challenges_from_critic(self, critic_out: dict) -> list[Challenge]:
        return [
            Challenge(
                role="Critic",
                severity=flaw.get("severity", "LOW"),
                description=flaw.get("description", ""),
                verdict=critic_out.get("verdict", "REVISE"),
            )
            for flaw in critic_out.get("flaws", [])
        ]

    def _extract_challenges_from_auditor(self, auditor_out: dict) -> list[Challenge]:
        return [
            Challenge(
                role="SecurityAuditor",
                severity=concern.get("severity", "MEDIUM"),
                description=concern.get("description", ""),
                verdict="VETO" if auditor_out.get("veto") else "ACCEPT",
            )
            for concern in auditor_out.get("security_concerns", [])
        ]

    def _make_blocked(
        self,
        task: str,
        tier: str,
        reason: str,
        formal_results: Optional[list[ToolResult]] = None,
    ) -> DebateResult:
        result = DebateResult(
            task=task,
            compute_tier=tier,
            rounds=[],
            final_output={},
            winning_position="blocked",
            block_reason=reason,
            formal_results=formal_results or [],
        )
        self._session_transcript.append(self._build_summary(result))
        return result

    def _build_summary(self, result: DebateResult) -> str:
        lines = [
            f"Task: {result.task}",
            f"Tier: {result.compute_tier}",
            f"Rounds: {len(result.rounds)}",
            f"Outcome: {result.winning_position}",
        ]
        if result.block_reason:
            lines.append(f"Block reason: {result.block_reason}")
        if result.formal_results:
            passed = [r.tool for r in result.formal_results if r.passed]
            failed = [r.tool for r in result.formal_results if not r.passed]
            if passed:
                lines.append(f"Formal passed: {', '.join(passed)}")
            if failed:
                lines.append(f"Formal failed: {', '.join(failed)}")
        for round_ in result.rounds:
            high = [c for c in round_.challenges if c.severity == "HIGH"]
            if high:
                lines.append(f"Round {round_.round_number} HIGH challenges: {len(high)}")
        red_team = getattr(result, "red_team_report", None)
        if red_team:
            lines.append(f"Red Team: {red_team.threat_level} — {red_team.summary}")
        return "\n".join(lines)
