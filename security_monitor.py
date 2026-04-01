"""
SecurityMonitor — pre-action immune system for autonomous agents.

Built from: Piebald-AI/claude-code-system-prompts
  - agent-prompt-security-monitor-for-autonomous-agent-actions-first-part.md
  - agent-prompt-security-monitor-for-autonomous-agent-actions-second-part.md

Evaluates agent actions against BLOCK/ALLOW rules. Returns {should_block, reason}.
Default is ALLOW — only block if a BLOCK condition matches with no ALLOW exception.
"""

from dataclasses import dataclass
from typing import Optional
import re


@dataclass
class SecurityDecision:
    should_block: bool
    reason: Optional[str] = None
    block_condition: Optional[str] = None
    allow_exception: Optional[str] = None


# BLOCK conditions — actions that require review before execution
BLOCK_CONDITIONS = {
    "git_destructive": {
        "pattern": r"git\s+push\s+--force|git\s+push\s+-f",
        "description": "Force pushing or rewriting remote history",
        "severity": "HIGH",
    },
    "git_push_default_branch": {
        "pattern": r"git\s+push.*(?:main|master|default).*--set-upstream",
        "description": "Pushing directly to default branch bypasses PR review",
        "severity": "MEDIUM",
    },
    "production_deploy": {
        "pattern": r"--production|--prod|deploy.*production|render.*production",
        "description": "Deploying to production without verification",
        "severity": "HIGH",
    },
    "blind_apply": {
        "pattern": r"--yes|--auto-approve|--force|--no-verify|-y\s+\w+",
        "description": "Bypassing preview/dry-run step",
        "severity": "MEDIUM",
    },
    "credential_leak": {
        "pattern": r"(api[_-]?key|password|secret|token)\s*=\s*['\"][^'\"]{8,}['\"]",
        "description": "Embedding secrets in code or configs",
        "severity": "HIGH",
    },
    "data_exfiltration": {
        "pattern": r"curl.*--data.*\$(?:\w+\s+)?(?:password|api[_-]?key|secret|token)",
        "description": "Sending credentials to external endpoints",
        "severity": "HIGH",
    },
    "destructive_local": {
        "pattern": r"rm\s+-rf|rm\s+-fr|git\s+clean\s+-fdx|git\s+reset\s+--hard",
        "description": "Irreversible deletion of local files",
        "severity": "HIGH",
    },
    "external_code_execution": {
        "pattern": r"curl\s+\|\s*bash|sh\s+<(?:curl|wget)|pip\s+install\s+(?!-r\s)",
        "description": "Downloading and executing code from external sources",
        "severity": "HIGH",
    },
    "permission_elevation": {
        "pattern": r"(chmod\s+777|sudo\s+|admin|owner|grant).*(?:access|permission|role)",
        "description": "Granting elevated permissions",
        "severity": "HIGH",
    },
    "shared_resource_modification": {
        "pattern": r"(kubectl\s+delete|docker\s+rm\s+[^-]|pkill\s+-9)",
        "description": "Modifying or deleting shared infrastructure",
        "severity": "HIGH",
    },
    "unauthorized_persistence": {
        "pattern": r"(cron|ssh[_-]?key|\.bashrc|\.profile|crontab\s+-r)",
        "description": "Adding persistent access mechanisms",
        "severity": "HIGH",
    },
    "self_modification": {
        "pattern": r"(settings\.json|CLAUDE\.md|\.claude/).*(?:edit|write|modify)",
        "description": "Modifying agent own configuration",
        "severity": "MEDIUM",
    },
}

# ALLOW exceptions — actions that are permitted even if they match a BLOCK condition
ALLOW_EXCEPTIONS = {
    "test_artifacts": {
        "pattern": r"(test|spec|mock).*(?:key|token|credential)",
        "description": "Test-only credentials in test files",
    },
    "local_operations": {
        "pattern": r"(rm|git\s+clean)\s+.*(?:node_modules|\.git|tmp|temp)",
        "description": "Cleaning local temp/generated directories only",
    },
    "declared_dependencies": {
        "pattern": r"pip\s+install\s+-r|npm\s+install\s+--save-dev|bundle\s+install",
        "description": "Installing from project's declared manifest",
    },
    "git_working_branch": {
        "pattern": r"git\s+push\s+.*(?:\bfix\b|\bfeat\b|\bfeature\b|\bchore\b|\btest\b)",
        "description": "Pushing to a non-default feature branch",
    },
    "read_only": {
        "pattern": r"curl\s+(?!.*--data|--method\s+POST|--method\s+PUT|--method\s+DELETE).*(?:\bget\b|\bhead\b)",
        "description": "Read-only HTTP requests only",
    },
}


class SecurityMonitor:
    """
    Pre-action security guard. Evaluates whether an action should be blocked.

    Usage:
        monitor = SecurityMonitor()
        decision = monitor.evaluate(action_text="rm -rf node_modules/")
        if decision.should_block:
            print(f"BLOCKED: {decision.reason}")
        else:
            execute_action()
    """

    def __init__(self, trusted_env: Optional[dict] = None):
        self.trusted_env = trusted_env or {
            "trusted_repos": [],  # repos the agent started in
            "trusted_domains": [],
            "trusted_buckets": [],
            "key_services": [],
        }

    def evaluate(self, action_text: str, context: Optional[str] = None) -> SecurityDecision:
        """
        Evaluate a single action against BLOCK/ALLOW rules.

        Args:
            action_text: The command or action to evaluate
            context: Optional transcript context (previous actions)

        Returns:
            SecurityDecision with should_block, reason, and matching rules
        """
        # Check BLOCK conditions
        for condition_name, condition in BLOCK_CONDITIONS.items():
            pattern = condition["pattern"]
            if re.search(pattern, action_text, re.IGNORECASE):
                # Matched a BLOCK condition — check for ALLOW exceptions
                for exception_name, exception in ALLOW_EXCEPTIONS.items():
                    if re.search(exception["pattern"], action_text, re.IGNORECASE):
                        return SecurityDecision(
                            should_block=False,
                            allow_exception=f"{exception_name}: {exception['description']}",
                        )
                # No exception — BLOCK
                return SecurityDecision(
                    should_block=True,
                    reason=f"BLOCK: {condition['description']} (severity: {condition['severity']})",
                    block_condition=condition_name,
                )

        # Check for scope escalation signals even without exact pattern match
        scope_signals = self._check_scope_escalation(action_text, context)
        if scope_signals:
            return SecurityDecision(
                should_block=True,
                reason=f"SCOPE ESCALATION: {scope_signals}",
                block_condition="scope_creep",
            )

        # No BLOCK match — ALLOW by default
        return SecurityDecision(should_block=False)

    def _check_scope_escalation(self, action_text: str, context: Optional[str]) -> Optional[str]:
        """Detect subtle scope creep that patterns alone might miss."""
        if not context:
            return None

        escalation_signals = [
            ("deleting infrastructure", r"delete.*(?:cluster|namespace|repo|db)"),
            ("modifying shared state", r"(?:update|modify).*(?:production|shared|common)"),
            ("bypassing review", r"--no-verify|--force|--skip"),
            ("credential access", r"(?:get|list|read).*(?:secret|key|credential).*(?:production|shared)"),
        ]

        for signal_desc, pattern in escalation_signals:
            if re.search(pattern, action_text, re.IGNORECASE):
                # Check if this was explicitly requested in context
                if self._user_explicitly_requested(action_text, context):
                    return None  # User intent established
                return f"Action suggests {signal_desc} without explicit user authorization"

        return None

    def _user_explicitly_requested(self, action_text: str, context: str) -> bool:
        """Check if user explicitly authorized this specific action."""
        # Look for explicit directives in context
        explicit_patterns = [
            r"(?:do\s+it|go\s+ahead|yes|confirm|approve).*(?:{}|that)",
            r"(?:delete|run|execute).*{}.*(?:specifically|explicitly|exactly)",
        ]
        action_keywords = action_text.split()[:3]  # First 3 words as identifier
        for pattern in explicit_patterns:
            for kw in action_keywords:
                if len(kw) > 3 and re.search(pattern.format(re.escape(kw)), context, re.IGNORECASE):
                    return True
        return False

    def evaluate_batch(self, actions: list[str], context: Optional[str] = None) -> list[SecurityDecision]:
        """Evaluate multiple actions in sequence."""
        return [self.evaluate(a, context) for a in actions]

    def summary(self, decisions: list[SecurityDecision]) -> dict:
        """Summarize a batch of decisions."""
        blocked = [d for d in decisions if d.should_block]
        return {
            "total": len(decisions),
            "allowed": len(decisions) - len(blocked),
            "blocked": len(blocked),
            "block_reasons": [d.reason for d in blocked if d.reason],
        }
