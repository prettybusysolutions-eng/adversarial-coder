"""
AdversarialCoder — main agent harness.

Ties together:
- SecurityMonitor: pre-action immune system
- VerificationSpecialist: post-action adversarial testing
- DreamConsolidation: between-session memory merging
- ContextNexusInterface: memory persistence layer

Usage:
    agent = AdversarialCoder(
        memory_dir="~/.openclaw/workspace-aurex/memory",
        project_path="~/code/myproject",
    )
    agent.run(interactive=True)
"""

import os
import sys
from pathlib import Path
from typing import Optional

from security_monitor import SecurityMonitor, SecurityDecision
from verification_specialist import VerificationSpecialist, Verdict
from dream_consolidation import DreamConsolidation


class AdversarialCoder:
    """
    Autonomous coding agent with adversarial guardrails.

    The execution loop:
        1. Read session memory context (DreamConsolidation)
        2. Get user task
        3. For each action:
            a. SecurityMonitor pre-check
            b. Execute action
            c. VerificationSpecialist adversarial test
            d. If FAIL → fix and retry
        4. On session end → DreamConsolidation merge
    """

    def __init__(
        self,
        memory_dir: str,
        project_path: Optional[str] = None,
        claude_md_path: Optional[str] = None,
    ):
        self.memory_dir = Path(memory_dir)
        self.project_path = Path(project_path or os.getcwd())
        self.claude_md_path = Path(claude_md_path) if claude_md_path else self.project_path / "CLAUDE.md"

        self.security_monitor = SecurityMonitor()
        self.verification_specialist = VerificationSpecialist()
        self.dream_consolidation = DreamConsolidation(str(self.memory_dir))

        self.session_id = self._generate_session_id()
        self.actions_log: list[dict] = []

    def _generate_session_id(self) -> str:
        from datetime import datetime
        return f"ac-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

    def pre_action_check(self, action_text: str, context: Optional[str] = None) -> SecurityDecision:
        """
        Phase 1: Security pre-check before any action.
        Returns decision — BLOCK means stop and ask user.
        """
        decision = self.security_monitor.evaluate(action_text, context)
        if decision.should_block:
            print(f"\n🛡️  SECURITY BLOCK: {decision.reason}")
            if decision.block_condition:
                print(f"   Condition: {decision.block_condition}")
            if decision.allow_exception:
                print(f"   Exception applied: {decision.allow_exception}")
        return decision

    def post_action_verify(
        self,
        action_text: str,
        output: str,
        modified_files: list[str],
    ) -> tuple[bool, VerificationSpecialist]:
        """
        Phase 2: Adversarial verification after action.
        Returns (passed, specialist).
        """
        vs = VerificationSpecialist()
        vs.scan_transcript(f"Action: {action_text}\nOutput: {output}")

        # Run build/test/lint if project has them
        if modified_files:
            project_path = str(self.project_path)
            vs.verify_build(project_path)
            vs.verify_tests(project_path)
            vs.verify_lint(project_path)

        report = vs.finalize()
        print(f"\n🔍 Verification: {report.summary}")
        for fail in report.fail_reasons:
            print(f"   ❌ {fail}")
        return report.verdict == Verdict.PASS, vs

    def load_session_memory(self) -> str:
        """Load relevant memory context for this session."""
        return self.dream_consolidation.get_memory_context()

    def run_dream_consolidation(
        self,
        session_transcript: Optional[str] = None,
    ):
        """Run between-session memory consolidation."""
        results = self.dream_consolidation.consolidate(
            session_transcript=session_transcript,
            dry_run=False,
        )
        return results

    def log_action(self, action: str, decision: SecurityDecision, result: str):
        """Log an action for later consolidation."""
        self.actions_log.append({
            "session_id": self.session_id,
            "timestamp": str(Path().stat().st_mtime),
            "action": action,
            "blocked": decision.should_block,
            "block_reason": decision.reason,
            "result": result[:500],  # Truncate
        })

    def create_claude_md(self, target_path: Optional[str] = None) -> str:
        """
        Create a CLAUDE.md file for a project.
        Uses the agent creation architect pattern.
        """
        path = Path(target_path) if target_path else self.claude_md_path

        content = f"""# CLAUDE.md — Project Context

**Project:** {path.parent.name}
**Generated:** {__import__('datetime').datetime.now().strftime('%Y-%m-%d')}
**Agent:** AdversarialCoder

## Project Overview

[Describe what this project does in 2-3 sentences]

## Project Structure

```
[list key files and directories]
```

## Build & Test Commands

- **Build:** [build command]
- **Test:** [test command]
- **Lint:** [lint command]

## Security Rules

This project follows these security rules (from SecurityMonitor):

### BLOCK Conditions
- No force-push to default branch
- No credentials in code or commits
- No production deploys without explicit user authorization
- No external code execution without declared dependencies
- No destructive operations without explicit target naming

### ALLOW Exceptions
- Test artifacts with placeholder credentials
- Local operations within project scope
- Installing from declared dependency files
- Git push to non-default branches

## Development Conventions

[Describe coding standards, patterns used, etc.]

## Known Issues

[Current blockers, known limitations]
"""
        return content

    def run_interactive(self):
        """Run the agent interactively."""
        print(f"AdversarialCoder v0.1 — session {self.session_id}")
        print(f"Project: {self.project_path}")
        print(f"Memory: {self.memory_dir}")
        print()

        # Load memory context
        memory_context = self.load_session_memory()
        if memory_context:
            print("📚 Memory context loaded")
            print(memory_context[:500])
            print()

        print("Ready. Enter task (Ctrl+C to exit, Ctrl+D to run consolidation):")

        try:
            while True:
                try:
                    task = input("\n> ")
                except EOFError:
                    print("\nRunning dream consolidation...")
                    results = self.run_dream_consolidation()
                    print(f"Done: {results}")
                    break

                if not task.strip():
                    continue

                # Pre-action security check
                decision = self.pre_action_check(task)
                if decision.should_block:
                    print("⚠️  Action blocked. Refine and retry, or ask user for authorization.")
                    continue

                # TODO: Actually execute the task (would integrate with actual AI model)
                print(f"✅ Security check passed — would execute: {task[:80]}...")

        except KeyboardInterrupt:
            print("\nRunning dream consolidation before exit...")
            results = self.run_dream_consolidation()
            print(f"Done: {results}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="AdversarialCoder")
    parser.add_argument("--memory-dir", default="~/.openclaw/workspace-aurex/memory")
    parser.add_argument("--project", default=os.getcwd())
    parser.add_argument("--interactive", action="store_true")
    args = parser.parse_args()

    agent = AdversarialCoder(
        memory_dir=args.memory_dir,
        project_path=args.project,
    )
    agent.run_interactive()
