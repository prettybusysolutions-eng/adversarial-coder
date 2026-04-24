"""
parliament_harness.py — AdversarialCoder subclass that fills the TODO.

The TODO at agent_harness.py:221 reads:
    "Actually execute the task (would integrate with actual AI model)"

This subclass overrides run_interactive() and introduces execute_task(),
wiring AgentParliament into the existing security + verification loop.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from agent_harness import AdversarialCoder
from agent_parliament import AgentParliament, DebateResult


class ParliamentHarness(AdversarialCoder):
    """
    Extends AdversarialCoder by wiring AgentParliament at the TODO gap.

    Inherits:
      pre_action_check()        — SecurityMonitor first checkpoint
      post_action_verify()      — VerificationSpecialist adversarial probes
      load_session_memory()     — DreamConsolidation context
      log_action()              — audit trail
      run_dream_consolidation() — end-of-session memory merge
    """

    def __init__(
        self,
        memory_dir: str,
        project_path: Optional[str] = None,
        api_key: Optional[str] = None,
        max_debate_rounds: int = 3,
    ):
        super().__init__(memory_dir=memory_dir, project_path=project_path)

        resolved_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not resolved_key:
            raise ValueError(
                "ANTHROPIC_API_KEY environment variable is required.\n"
                "  export ANTHROPIC_API_KEY=your-key-here"
            )

        self.parliament = AgentParliament(
            api_key=resolved_key,
            memory_dir=str(self.memory_dir),
            project_path=str(self.project_path),
            max_debate_rounds=max_debate_rounds,
        )

    def execute_task(self, task: str) -> DebateResult:
        """
        Fills agent_harness.py:221.

        Flow:
          1. SecurityMonitor pre-check (inherited)
          2. AgentParliament.deliberate() — full adversarial debate
          3. VerificationSpecialist post-action probes (inherited)
          4. Log action for DreamConsolidation
        """
        # Step 1: SecurityMonitor first checkpoint
        decision = self.pre_action_check(task)
        if decision.should_block:
            return DebateResult(
                task=task,
                compute_tier="blocked_pre",
                rounds=[],
                final_output={},
                winning_position="blocked",
                block_reason=f"SecurityMonitor: {decision.reason}",
            )

        # Step 2: Parliament deliberation
        result = self.parliament.deliberate(task)

        # Step 3: Post-action verification when something was produced
        if result.winning_position != "blocked" and result.final_output:
            modified_files = result.final_output.get("files_modified", [])
            passed, _ = self.post_action_verify(
                action_text=task,
                output=result.final_output.get("explanation", ""),
                modified_files=modified_files,
            )
            if not passed:
                result.winning_position = "blocked"
                result.block_reason = "VerificationSpecialist post-action probes failed"

        # Step 4: Audit log
        self.log_action(task, decision, result.winning_position)

        return result

    def run_interactive(self):
        """
        Overrides agent_harness.py:run_interactive().
        Replaces the stub at line 221 with actual parliament execution.
        """
        print(f"AgentParliament v0.1 — session {self.session_id}")
        print(f"Project: {self.project_path}")
        print(f"Memory:  {self.memory_dir}")
        print()

        if self.load_session_memory():
            print("Memory context loaded.")
        print("Ready. Enter task (Ctrl+D to consolidate and exit, Ctrl+C to exit):\n")

        try:
            while True:
                try:
                    task = input("[parliament]> ").strip()
                except EOFError:
                    self._shutdown()
                    break

                if not task:
                    continue

                result = self.execute_task(task)
                self._print_result(result)

        except KeyboardInterrupt:
            self._shutdown()

    def _shutdown(self):
        print("\nRunning parliament dream consolidation...")
        results = self.parliament.end_session()
        merged = results.get("merged", [])
        print(f"Memory consolidated: {', '.join(merged)}" if merged else "No new signal to consolidate.")

    def _print_result(self, result: DebateResult):
        print()
        if result.winning_position == "blocked":
            print(f"BLOCKED — {result.block_reason}")
            return

        tier_label = {
            "single": "Single agent",
            "dual": f"Dual agent ({len(result.rounds)} round(s))",
            "parliament": f"Full parliament ({len(result.rounds)} round(s))",
        }.get(result.compute_tier, result.compute_tier)

        print(f"[{tier_label}] {result.winning_position.upper()}")

        if result.final_output:
            explanation = result.final_output.get("explanation", "")
            if explanation:
                print(f"  {explanation}")

            code = result.final_output.get("code", "")
            if code:
                print(f"\n{code}")

            commands = result.final_output.get("commands_to_run", [])
            if commands:
                print("\nCommands to run:")
                for cmd in commands:
                    print(f"  $ {cmd}")

        if result.formal_results:
            print()
            for fr in result.formal_results:
                status = "PASS" if fr.passed else "FAIL"
                print(f"  [{status}] {fr.tool} ({fr.duration_ms}ms)")

        tokens = result.total_input_tokens + result.total_output_tokens
        if tokens:
            print(f"\n  Tokens used: {tokens:,}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="AgentParliament — adversarial coding agent")
    parser.add_argument(
        "--memory-dir",
        default=str(Path.home() / ".openclaw" / "parliament" / "memory"),
    )
    parser.add_argument("--project", default=os.getcwd())
    parser.add_argument("--rounds", type=int, default=3, help="Max debate rounds per task")
    parser.add_argument("--task", help="Run a single task non-interactively")
    args = parser.parse_args()

    harness = ParliamentHarness(
        memory_dir=args.memory_dir,
        project_path=args.project,
        max_debate_rounds=args.rounds,
    )

    if args.task:
        result = harness.execute_task(args.task)
        harness._print_result(result)
        harness._shutdown()
    else:
        harness.run_interactive()
