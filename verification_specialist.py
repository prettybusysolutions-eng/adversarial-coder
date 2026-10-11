"""
VerificationSpecialist — adversarial testing agent.

Built from: Piebald-AI/claude-code-system-prompts
  - agent-prompt-verification-specialist.md

Adversarial testing framework. Receives the parent's current-turn conversation,
scans for claims, runs independent verification, issues PASS/FAIL/PARTIAL verdict.

Key principle: "You are Claude, and you are bad at verification."
The implementer is an LLM too — tests may be circular, heavy on mocks, or
assert what the code does instead of what it should do.
"""

from dataclasses import dataclass, field
from typing import Optional
from enum import Enum
import subprocess
import re


class Verdict(Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    PARTIAL = "PARTIAL"


@dataclass
class CheckResult:
    check_name: str
    command: str
    output: str
    expected: Optional[str] = None
    actual: Optional[str] = None
    passed: Optional[bool] = None
    notes: Optional[str] = None


@dataclass
class VerificationReport:
    checks: list[CheckResult] = field(default_factory=list)
    adversarial_probes: list[CheckResult] = field(default_factory=list)
    verdict: Verdict = Verdict.PARTIAL
    summary: str = ""
    fail_reasons: list[str] = field(default_factory=list)
    partial_reasons: list[str] = field(default_factory=list)

    def add_check(self, result: CheckResult):
        self.checks.append(result)
        if result.passed is False:
            self.fail_reasons.append(f"{result.check_name}: {result.actual} vs expected {result.expected}")

    def add_adversarial(self, result: CheckResult):
        self.adversarial_probes.append(result)
        if result.passed is False:
            self.fail_reasons.append(f"ADVERSARIAL {result.check_name}: {result.actual}")

    def final_verdict(self) -> str:
        if self.fail_reasons or any(c.passed is False for c in self.checks + self.adversarial_probes):
            self.verdict = Verdict.FAIL
            self.summary = f"FAIL — {len(self.fail_reasons)} issue(s) found"
        elif self.partial_reasons or any(c.passed is None for c in self.checks + self.adversarial_probes) or not (self.checks or self.adversarial_probes):
            self.verdict = Verdict.PARTIAL
            self.summary = "PARTIAL — incomplete or unavailable verification evidence"
        else:
            self.verdict = Verdict.PASS
            self.summary = f"PASS — {len(self.checks)} checks, {len(self.adversarial_probes)} adversarial probes"
        return f"VERDICT: {self.verdict.value}"


class VerificationSpecialist:
    """
    Adversarial testing framework for autonomous coding agents.

    Usage:
        vs = VerificationSpecialist()
        vs.scan_transcript(parent_turn_text)
        vs.verify_build(project_path)
        vs.verify_tests(project_path)
        vs.adversarial_probe_boundary_values(endpoint, params)
        vs.adversarial_probe_idempotency(endpoint, params)
        report = vs.finalize()
        print(report.final_verdict())
    """

    def __init__(self, target_adapter=None, state_observer=None, probe_oracle=None, command_runner=None):
        self.target_adapter = target_adapter
        self.state_observer = state_observer
        self.probe_oracle = probe_oracle
        self.command_runner = command_runner or self._trusted_local_command
        self.report = VerificationReport()
        self.claims_found: list[str] = []
        self.shortcuts_found: list[str] = []

    def scan_transcript(self, transcript: str):
        """
        Scan the parent's current-turn conversation for claims and shortcuts
        that need independent verification.
        """
        # Find claims
        claim_patterns = [
            r"(?i)(?:verified?|tested?|confirmed?|checked?).*(?:works?|passes?|correct)",
            r"(?i)(?:all\s+)?(?:tests?\s+)?pass",
            r"(?i)no\s+errors?",
            r"(?i)(?:success|fine|good)\s+(?:to\s+)?go",
        ]
        for pattern in claim_patterns:
            for match in re.finditer(pattern, transcript):
                self.claims_found.append(match.group())

        # Find shortcuts
        shortcut_patterns = [
            r"(?i)(?:should\s+be\s+fine|probably|likely|maybe|i\s+think)",
            r"(?i)(?:this\s+is\s+?:probably\s+fine|just|trivial|simple)",
            r"(?i)(?:not\s+necessary|skip|skipping)",
        ]
        for pattern in shortcut_patterns:
            for match in re.finditer(pattern, transcript):
                self.shortcuts_found.append(match.group())

    @staticmethod
    def _trusted_local_command(command, project_path):
        # Library use only: operator-selected trusted repositories. Not a sandbox.
        return subprocess.run(command, shell=True, cwd=project_path, capture_output=True, text=True, timeout=120)

    def _result(self, name, command, output, passed, probe=False):
        return CheckResult(check_name=name, command=command, output=output, passed=passed)

    def _record(self, result, probe=False):
        (self.report.add_adversarial if probe else self.report.add_check)(result)

    def verify_build(self, project_path, build_command=None):
        return self._run_command("Build", build_command or self._detect_build_command(project_path), project_path)

    def verify_tests(self, project_path, test_command=None):
        return self._run_command("Test Suite", test_command or self._detect_test_command(project_path), project_path)

    def verify_lint(self, project_path, linter=None):
        return self._run_command("Lint", linter or self._detect_linter(project_path), project_path)


    def _unknown(self, name, command, reason, probe=False):
        self.report.partial_reasons.append(reason)
        result = self._result(name, command, reason, None, probe)
        self._record(result, probe)
        return result

    def _run_command(self, name, command, project_path):
        if not command:
            return self._unknown(name, "none detected", f"{name}: no configured command")
        try:
            result = self.command_runner(command, project_path)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return self._unknown(name, command, project_path + ": " + type(exc).__name__)
        if result is None:
            return self._unknown(name, command, "No isolated command runner configured")
        check = self._result(name, command, result.stdout + result.stderr, result.returncode == 0)
        self._record(check)
        return check

    def _invoke(self, target, params):
        if self.target_adapter is None:
            raise RuntimeError("No real target adapter configured")
        return self.target_adapter(target, params)

    def _probe(self, name, target, params, kind, calls=1, parallel=False):
        # Correctness belongs to a target-specific oracle, never response strings.
        if self.target_adapter is None or self.state_observer is None or self.probe_oracle is None:
            return self._unknown(name, target, "Probe requires a real adapter, independent state observer and oracle", True)
        try:
            before = self.state_observer()
            if parallel:
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=calls) as pool:
                    results = list(pool.map(lambda _: self._invoke(target, params.copy()), range(calls)))
            else:
                results = [self._invoke(target, params.copy()) for _ in range(calls)]
            after = self.state_observer()
            passed = self.probe_oracle(kind, params, before, results, after)
            if passed is not True and passed is not False:
                return self._unknown(name, target, "Oracle did not provide a boolean verdict", True)
        except Exception as exc:
            return self._unknown(name, target, "Probe could not obtain evidence: " + type(exc).__name__, True)
        result = self._result(name, target, repr({"before": before, "results": results, "after": after}), passed, True)
        self._record(result, True)
        return result

    def adversarial_probe_boundary_values(self, func_or_endpoint, params, boundary_values=None):
        if not params:
            return [self._unknown("Boundary", func_or_endpoint, "No boundary parameter specified", True)]
        values = boundary_values if boundary_values is not None else [0, -1, "", "x" * 10000, 2**31 - 1]
        key = next(iter(params))
        return [self._probe("Boundary " + repr(value), func_or_endpoint, {**params, key: value}, "boundary") for value in values]

    def adversarial_probe_idempotency(self, func_or_endpoint, params):
        return [self._probe("Idempotency", func_or_endpoint, params, "idempotency", calls=2)]

    def adversarial_probe_concurrency(self, func_or_endpoint, params, num_parallel=5):
        if not 1 <= num_parallel <= 32:
            raise ValueError("Concurrency must be in [1, 32]")
        return [self._probe("Concurrency", func_or_endpoint, params, "concurrency", calls=num_parallel, parallel=True)]

    def adversarial_probe_orphan_operation(self, func_or_endpoint, params, non_existent_id=999999999):
        key = next((key for key in ("id", "entity_id", "resource_id", "item_id", "user_id") if key in params), "id")
        return self._probe("Orphan", func_or_endpoint, {**params, key: non_existent_id}, "orphan")

    def finalize(self):
        if not self.report.adversarial_probes:
            self.report.partial_reasons.append("No target-specific adversarial probes were run")
        self.report.final_verdict()
        return self.report
    def _detect_build_command(self, project_path: str) -> str:
        """Detect the project's build command."""
        import os
        for file, cmd in [
            ("Makefile", "make"),
            ("CMakeLists.txt", "cmake . && make"),
            ("Cargo.toml", "cargo build"),
            ("pyproject.toml", "python -m build"),
            ("package.json", "npm run build"),
            ("setup.py", "python -m build"),
        ]:
            if os.path.exists(os.path.join(project_path, file)):
                return cmd
        return None

    def _detect_test_command(self, project_path: str) -> str:
        """Detect the project's test command."""
        import os
        for file, cmd in [
            ("pytest.ini", "pytest"),
            ("pyproject.toml", "pytest"),
            ("package.json", "npm test"),
            ("Cargo.toml", "cargo test"),
            ("go.mod", "go test ./..."),
        ]:
            if os.path.exists(os.path.join(project_path, file)):
                return cmd
        return None

    def _detect_linter(self, project_path: str) -> Optional[str]:
        """Detect the project's linter."""
        import os
        for file, cmd in [
            (".eslintrc", "npx eslint ."),
            ("mypy.ini", "mypy ."),
            ("pylintrc", "pylint ."),
            (".prettierrc", "prettier --check ."),
        ]:
            if os.path.exists(os.path.join(project_path, file)):
                return cmd
        return None

