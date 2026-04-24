"""
parliament_tools.py — Infrastructure layer for AgentParliament.

FormalVerifier: wraps static analysis tools (mypy, ruff, bandit, pytest)
                as ground truth verification. No LLM judgment — subprocess only.

AnthropicClient: thin wrapper around the Anthropic SDK with lazy import,
                 token tracking, and structured output support.
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from typing import Optional


@dataclass
class ToolResult:
    tool: str
    passed: bool
    output: str
    exit_code: int
    duration_ms: int


class FormalVerifier:
    """
    Wraps deterministic static analysis tools as ground truth.
    No LLM judgment — only subprocess output counts.
    """

    def detect_available_tools(self, project_path: str) -> dict[str, bool]:
        """Check which analysis tools are installed and runnable."""
        available = {}
        for tool in ["mypy", "ruff", "bandit", "pytest"]:
            result = subprocess.run(
                [tool, "--version"],
                capture_output=True,
                text=True,
            )
            available[tool] = result.returncode == 0
        return available

    def run_mypy(self, project_path: str, files: Optional[list[str]] = None) -> ToolResult:
        start = time.monotonic()
        target = " ".join(files) if files else "."
        result = subprocess.run(
            f"mypy {target} --ignore-missing-imports",
            shell=True,
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=60,
        )
        return ToolResult(
            tool="mypy",
            passed=result.returncode == 0,
            output=(result.stdout + result.stderr)[:2000],
            exit_code=result.returncode,
            duration_ms=int((time.monotonic() - start) * 1000),
        )

    def run_ruff(self, project_path: str) -> ToolResult:
        start = time.monotonic()
        result = subprocess.run(
            "ruff check .",
            shell=True,
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return ToolResult(
            tool="ruff",
            passed=result.returncode == 0,
            output=(result.stdout + result.stderr)[:2000],
            exit_code=result.returncode,
            duration_ms=int((time.monotonic() - start) * 1000),
        )

    def run_bandit(self, project_path: str) -> ToolResult:
        start = time.monotonic()
        result = subprocess.run(
            "bandit -r . -ll -q",
            shell=True,
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=60,
        )
        return ToolResult(
            tool="bandit",
            passed=result.returncode == 0,
            output=(result.stdout + result.stderr)[:2000],
            exit_code=result.returncode,
            duration_ms=int((time.monotonic() - start) * 1000),
        )

    def run_pytest(self, project_path: str) -> ToolResult:
        start = time.monotonic()
        result = subprocess.run(
            "pytest --tb=short -q",
            shell=True,
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=120,
        )
        return ToolResult(
            tool="pytest",
            passed=result.returncode == 0,
            output=(result.stdout + result.stderr)[:2000],
            exit_code=result.returncode,
            duration_ms=int((time.monotonic() - start) * 1000),
        )

    def verify_all(self, project_path: str) -> list[ToolResult]:
        """Run all available tools. Returns ground truth verdict list."""
        available = self.detect_available_tools(project_path)
        results = []
        if available.get("mypy"):
            results.append(self.run_mypy(project_path))
        if available.get("ruff"):
            results.append(self.run_ruff(project_path))
        if available.get("bandit"):
            results.append(self.run_bandit(project_path))
        if available.get("pytest"):
            results.append(self.run_pytest(project_path))
        return results


class AnthropicClient:
    """
    Thin wrapper around the Anthropic SDK.

    Uses lazy import so the module loads even without anthropic installed.
    Tracks token usage for cost awareness.
    """

    def __init__(self, api_key: str, default_model: str = "claude-haiku-4-5-20251001"):
        self._client = None
        self.api_key = api_key
        self.default_model = default_model
        self.total_input_tokens = 0
        self.total_output_tokens = 0

    def _get_client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic(api_key=self.api_key)
        return self._client

    def chat(
        self,
        messages: list[dict],
        system: str,
        model: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ) -> str:
        """Blocking call. Returns assistant content string."""
        client = self._get_client()
        response = client.messages.create(
            model=model or self.default_model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
            temperature=temperature,
        )
        self.total_input_tokens += response.usage.input_tokens
        self.total_output_tokens += response.usage.output_tokens
        return response.content[0].text

    def chat_with_structured_output(
        self,
        messages: list[dict],
        system: str,
        model: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ) -> dict:
        """
        Forces structured JSON output.
        Retries once at T=0 on parse failure.
        Falls back to {"raw": content} if both attempts fail.
        """
        json_system = (
            system
            + "\n\nIMPORTANT: Respond ONLY with valid JSON. "
            "No prose, no markdown fences, no explanation outside the JSON object."
        )

        content = self.chat(messages, json_system, model, max_tokens, temperature)

        try:
            return json.loads(content)
        except json.JSONDecodeError:
            stripped = (
                content.strip()
                .removeprefix("```json")
                .removeprefix("```")
                .removesuffix("```")
                .strip()
            )
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                pass

        # Retry at T=0
        content = self.chat(messages, json_system, model, max_tokens, temperature=0.0)
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return {"raw": content, "parse_failed": True}
