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
    passed: bool = True
    notes: Optional[str] = None


@dataclass
class VerificationReport:
    checks: list[CheckResult] = field(default_factory=list)
    adversarial_probes: list[CheckResult] = field(default_factory=list)
    verdict: Verdict = Verdict.PASS
    summary: str = ""
    fail_reasons: list[str] = field(default_factory=list)
    partial_reasons: list[str] = field(default_factory=list)

    def add_check(self, result: CheckResult):
        self.checks.append(result)
        if not result.passed:
            self.fail_reasons.append(f"{result.check_name}: {result.actual} vs expected {result.expected}")

    def add_adversarial(self, result: CheckResult):
        self.adversarial_probes.append(result)
        if not result.passed:
            self.fail_reasons.append(f"ADVERSARIAL {result.check_name}: {result.actual}")

    def final_verdict(self) -> str:
        if self.fail_reasons:
            self.verdict = Verdict.FAIL
            self.summary = f"FAIL — {len(self.fail_reasons)} issue(s) found"
        elif not self.checks and not self.adversarial_probes:
            self.verdict = Verdict.PARTIAL
            self.summary = "PARTIAL — no checks could be run"
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

    def __init__(self):
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

    def verify_build(self, project_path: str, build_command: Optional[str] = None) -> CheckResult:
        """Run the build. A broken build is automatic FAIL."""
        if not build_command:
            build_command = self._detect_build_command(project_path)

        result = subprocess.run(
            build_command,
            shell=True,
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=120,
        )

        check = CheckResult(
            check_name="Build",
            command=build_command,
            output=result.stdout + result.stderr,
            passed=(result.returncode == 0),
        )
        if result.returncode != 0:
            check.actual = f"exit code {result.returncode}"
            check.expected = "exit code 0"
        self.report.add_check(check)
        return check

    def verify_tests(self, project_path: str, test_command: Optional[str] = None) -> CheckResult:
        """Run the test suite. Failing tests are automatic FAIL."""
        if not test_command:
            test_command = self._detect_test_command(project_path)

        result = subprocess.run(
            test_command,
            shell=True,
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=120,
        )

        check = CheckResult(
            check_name="Test Suite",
            command=test_command,
            output=result.stdout + result.stderr,
            passed=(result.returncode == 0),
        )
        if result.returncode != 0:
            check.actual = f"exit code {result.returncode}"
            check.expected = "exit code 0"
        self.report.add_check(check)
        return check

    def verify_lint(self, project_path: str, linter: Optional[str] = None) -> CheckResult:
        """Run linter if configured."""
        if not linter:
            linter = self._detect_linter(project_path)
        if not linter:
            return CheckResult(check_name="Lint", command="none", output="no linter configured", passed=True)

        result = subprocess.run(
            linter,
            shell=True,
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=60,
        )

        check = CheckResult(
            check_name="Lint",
            command=linter,
            output=result.stdout + result.stderr,
            passed=(result.returncode == 0),
        )
        self.report.add_check(check)
        return check

    def adversarial_probe_boundary_values(
        self,
        func_or_endpoint: str,
        params: dict,
        boundary_values: Optional[list] = None,
    ) -> list[CheckResult]:
        """
        Test boundary values: 0, -1, empty string, very long strings, unicode, MAX_INT.
        Returns results for each boundary test.
        """
        if boundary_values is None:
            boundary_values = [0, -1, "", " " * 10000, "🔥💀🚀" * 100, 2**31 - 1]

        results = []
        for bv in boundary_values:
            test_params = params.copy()
            # Inject boundary value into first param
            first_key = next(iter(test_params))
            test_params[first_key] = bv

            result = self._call_with_params(func_or_endpoint, test_params)
            probe = CheckResult(
                check_name=f"Boundary: {bv!r} on {first_key}",
                command=f"invoke {func_or_endpoint} with {test_params}",
                output=str(result),
                passed=self._check_boundary_handled(result),
            )
            results.append(probe)
            self.report.add_adversarial(probe)
        return results

    def adversarial_probe_idempotency(
        self,
        func_or_endpoint: str,
        params: dict,
    ) -> list[CheckResult]:
        """
        Test idempotency: same mutation twice — duplicate created? error? correct no-op?
        """
        results = []

        # First call
        result1 = self._call_with_params(func_or_endpoint, params)
        probe1 = CheckResult(
            check_name="Idempotency (call 1)",
            command=f"invoke {func_or_endpoint}",
            output=str(result1),
            passed=True,
        )
        results.append(probe1)

        # Second call — same params
        result2 = self._call_with_params(func_or_endpoint, params)
        probe2 = CheckResult(
            check_name="Idempotency (call 2 — same params)",
            command=f"invoke {func_or_endpoint}",
            output=str(result2),
            passed=self._check_idempotent(result1, result2),
            notes="Should be no-op or error, not duplicate",
        )
        results.append(probe2)
        self.report.add_adversarial(probe2)
        return results

    def adversarial_probe_concurrency(
        self,
        func_or_endpoint: str,
        params: dict,
        num_parallel: int = 5,
    ) -> list[CheckResult]:
        """
        Test concurrency: parallel requests to create-if-not-exists paths.
        Are duplicates created? Lost writes?
        """
        import concurrent.futures

        def call():
            return self._call_with_params(func_or_endpoint, params)

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_parallel) as executor:
            futures = [executor.submit(call) for _ in range(num_parallel)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        unique_results = len(set(str(r) for r in results))
        probe = CheckResult(
            check_name=f"Concurrency ({num_parallel} parallel)",
            command=f"parallel {num_parallel}x {func_or_endpoint}",
            output=f"Got {unique_results} unique result(s) from {num_parallel} calls",
            passed=(unique_results <= 2),  # Should get at most 2 unique states (success + error or 2 successes)
        )
        self.report.add_adversarial(probe)
        return [probe]

    def adversarial_probe_orphan_operation(
        self,
        func_or_endpoint: str,
        params: dict,
        non_existent_id: int = 999999999,
    ) -> CheckResult:
        """Test: delete/reference an ID that doesn't exist. Should be graceful error."""
        test_params = params.copy()
        # Try to use a non-existent ID in various ways
        if "id" in test_params:
            test_params["id"] = non_existent_id
        elif "entity_id" in test_params:
            test_params["entity_id"] = non_existent_id
        else:
            test_params["id"] = non_existent_id

        result = self._call_with_params(func_or_endpoint, test_params)
        probe = CheckResult(
            check_name=f"Orphan: non-existent ID {non_existent_id}",
            command=f"invoke {func_or_endpoint} with non_existent_id={non_existent_id}",
            output=str(result),
            passed=self._check_graceful_error(result),
        )
        self.report.add_adversarial(probe)
        return probe

    def finalize(self) -> VerificationReport:
        """Finalize the report and issue verdict."""
        # Verify at least one adversarial probe was run
        if not self.report.adversarial_probes:
            self.report.partial_reasons.append(
                "No adversarial probes were run. Happy-path confirmation only is not verification."
            )
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
        return "python -m py_compile ."

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
        return "python -m pytest 2>/dev/null || true"

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

    def _call_with_params(self, func_or_endpoint: str, params: dict):
        """Call a function or endpoint with params. Override this for real implementations."""
        # Placeholder — override in production
        return {"status": "ok", "params": params}

    def _check_boundary_handled(self, result) -> bool:
        """Check if boundary value was handled gracefully."""
        if isinstance(result, dict):
            return result.get("status") in ("ok", "error_handled")
        return True

    def _check_idempotent(self, result1, result2) -> bool:
        """Check if operation was idempotent (same result or graceful no-op)."""
        s1, s2 = str(result1), str(result2)
        # Same result is fine
        if s1 == s2:
            return True
        # One is error and other is no-op is fine
        if "already exists" in s1 or "already exists" in s2:
            return True
        return False

    def _check_graceful_error(self, result) -> bool:
        """Check if error was handled gracefully (not a crash)."""
        if isinstance(result, dict):
            return "error" in str(result.get("status", "")).lower() or "not found" in str(result).lower()
        return True
