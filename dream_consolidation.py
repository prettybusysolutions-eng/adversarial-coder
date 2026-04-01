"""
DreamConsolidation — between-session memory merging.

Built from: Piebald-AI/claude-code-system-prompts
  - agent-prompt-dream-memory-consolidation.md

Multi-phase memory consolidation pass:
1. Orient on existing memories
2. Gather recent signal from logs and transcripts
3. Merge updates into topic files
4. Prune the index

This runs after each session or when triggered by the heartbeat.
"""

from dataclasses import dataclass
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional
import json
import re


@dataclass
class MemoryEntry:
    timestamp: datetime
    source: str  # session_id or "heartbeat"
    content: str
    topics: list[str]
    significance: str  # "high", "medium", "low"


class DreamConsolidation:
    """
    Dream consolidation: merges session signal into persistent topic memories.

    Usage:
        dream = DreamConsolidation(memory_dir="~/.openclaw/workspace-aurex/memory")
        dream.consolidate(session_transcript="/path/to/transcript.md")
    """

    def __init__(self, memory_dir: str):
        self.memory_dir = Path(memory_dir).expanduser()
        self.topic_files = {
            "security_patterns": self.memory_dir / "topics" / "security-patterns.md",
            "codebase_map": self.memory_dir / "topics" / "codebase-map.md",
            "mistakes_avoided": self.memory_dir / "topics" / "mistakes-avoided.md",
            "agent_behavior": self.memory_dir / "topics" / "agent-behavior.md",
            "products": self.memory_dir / "topics" / "products.md",
        }
        self._ensure_topic_dirs()

    def _ensure_topic_dirs(self):
        for path in self.topic_files.values():
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_text(f"# {path.stem.replace('-', ' ').title()}\n\n")

    def consolidate(
        self,
        session_transcript: Optional[str] = None,
        session_log_path: Optional[str] = None,
        dry_run: bool = False,
    ) -> dict:
        """
        Run the full consolidation pass.

        Args:
            session_transcript: Path to this session's transcript
            session_log_path: Path to this session's log file
            dry_run: If True, don't write anything

        Returns:
            Summary of what was consolidated
        """
        results = {
            "orient": [],
            "signal_gathered": [],
            "merged": [],
            "pruned": [],
        }

        # Phase 1: Orient on existing memories
        existing_topics = self._orient_on_existing_memories()
        results["orient"] = existing_topics

        # Phase 2: Gather signal from recent sessions
        signal = self._gather_recent_signal(session_transcript, session_log_path)
        results["signal_gathered"] = signal

        # Phase 3: Merge into topic files
        if not dry_run:
            self._merge_signal_into_topics(signal)
            results["merged"] = list(signal.keys())

        # Phase 4: Prune redundant entries
        if not dry_run:
            pruned = self._prune_index()
            results["pruned"] = pruned

        # Update last dream timestamp
        if not dry_run:
            self._update_dream_timestamp(results)

        return results

    def _orient_on_existing_memories(self) -> list[str]:
        """Read existing topic memories to understand current state."""
        existing = []
        for topic, path in self.topic_files.items():
            if path.exists():
                content = path.read_text()
                if len(content) > 50:  # Has actual content
                    existing.append(topic)
        return existing

    def _gather_recent_signal(
        self,
        session_transcript: Optional[str] = None,
        session_log_path: Optional[str] = None,
    ) -> dict:
        """
        Gather signal from recent session log and any transcript.
        Extract: errors, decisions, patterns, blockers, lessons.
        """
        signal = {
            "errors_encountered": [],
            "decisions_made": [],
            "blockers_hit": [],
            "lessons_learned": [],
            "patterns_discovered": [],
            "mistakes_made": [],
        }

        # From session transcript
        if session_transcript:
            content = Path(session_transcript).read_text()
            signal.update(self._extract_from_text(content))

        # From today's session log
        today = datetime.now().strftime("%Y-%m-%d")
        today_log = self.memory_dir / f"{today}.md"
        if today_log.exists():
            content = today_log.read_text()
            extracted = self._extract_from_text(content)
            for k, v in extracted.items():
                signal[k].extend(v)

        # From recent days
        for i in range(1, 7):
            date = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
            log = self.memory_dir / f"{date}.md"
            if log.exists():
                content = log.read_text()
                extracted = self._extract_from_text(content)
                for k, v in extracted.items():
                    signal[k].extend(v[:5])  # Cap at 5 from older days

        # Deduplicate
        for k in signal:
            signal[k] = list(dict.fromkeys(signal[k]))  # Preserve order, remove dupes

        return signal

    def _extract_from_text(self, content: str) -> dict:
        """Extract structured signal from raw text."""
        signal = {
            "errors_encountered": [],
            "decisions_made": [],
            "blockers_hit": [],
            "lessons_learned": [],
            "patterns_discovered": [],
            "mistakes_made": [],
        }

        # Extract errors
        error_patterns = [
            r"(?i)error[:\s]+([^\n]+)",
            r"(?i)failed[:\s]+([^\n]+)",
            r"(?i)Exception[:\s]+([^\n]+)",
            r"(?i)Traceback[^\n]*\n[^\n]*\n[^\n]*([^\n]+)",
        ]
        for pattern in error_patterns:
            for m in re.finditer(pattern, content):
                err = m.group(1).strip()[:200]
                if err and len(err) > 10:
                    signal["errors_encountered"].append(err)

        # Extract decisions
        decision_patterns = [
            r"(?i)decided[:\s]+([^\n]+)",
            r"(?i)chose[:\s]+([^\n]+)",
            r"(?i)using[:\s]+([^\n]+)",
            r"(?i)built[:\s]+([^\n]+)",
        ]
        for pattern in decision_patterns:
            for m in re.finditer(pattern, content):
                dec = m.group(1).strip()[:200]
                if dec and len(dec) > 10:
                    signal["decisions_made"].append(dec)

        # Extract blockers
        blocker_patterns = [
            r"(?i)blocked[:\s]+([^\n]+)",
            r"(?i)wait.*(?:for|on)[:\s]+([^\n]+)",
            r"(?i)stuck[:\s]+([^\n]+)",
            r"(?i)wedge[sd]?[:\s]+([^\n]+)",
        ]
        for pattern in blocker_patterns:
            for m in re.finditer(pattern, content):
                blk = m.group(1).strip()[:200]
                if blk and len(blk) > 10:
                    signal["blockers_hit"].append(blk)

        # Extract lessons
        lesson_patterns = [
            r"(?i)learned[:\s]+([^\n]+)",
            r"(?i)lesson[:\s]+([^\n]+)",
            r"(?i)always[:\s]+([^\n]+)",
            r"(?i)never[:\s]+([^\n]+)",
            r"(?i)if.*then.*blocker",  # Found blocker pattern
        ]
        for pattern in lesson_patterns:
            for m in re.finditer(pattern, content):
                lesson = m.group(1).strip()[:200]
                if lesson and len(lesson) > 10:
                    signal["lessons_learned"].append(lesson)

        return signal

    def _merge_signal_into_topics(self, signal: dict):
        """Merge gathered signal into appropriate topic files."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M")

        # Security patterns
        if signal["errors_encountered"]:
            entries = [f"- {now}: {e}" for e in signal["errors_encountered"][:10]]
            self._append_to_topic("security_patterns", entries)

        # Mistakes avoided (lessons learned become rules)
        if signal["lessons_learned"]:
            entries = [f"- {now}: {l}" for l in signal["lessons_learned"][:10]]
            self._append_to_topic("mistakes_avoided", entries)

        # Agent behavior
        if signal["patterns_discovered"]:
            entries = [f"- {now}: {p}" for p in signal["patterns_discovered"][:10]]
            self._append_to_topic("agent_behavior", entries)

        # Decisions made
        if signal["decisions_made"]:
            entries = [f"- {now}: {d}" for d in signal["decisions_made"][:10]]
            self._append_to_topic("codebase_map", entries)

    def _append_to_topic(self, topic: str, entries: list[str]):
        """Append entries to a topic file."""
        path = self.topic_files.get(topic)
        if not path:
            return
        with open(path, "a") as f:
            f.write("\n".join(entries))
            f.write("\n")

    def _prune_index(self) -> list[str]:
        """
        Prune the daily memory logs — keep only significant entries.
        Remove entries older than 7 days or entries that have been
        superseded by later entries.
        """
        pruned = []
        today = datetime.now()
        cutoff = today - timedelta(days=7)

        for log_file in self.memory_dir.glob("????-??-??*.md"):
            if log_file.stem in ["topics", "heartbeat-state"]:
                continue

            try:
                date_str = log_file.stem[:10]  # YYYY-MM-DD
                file_date = datetime.strptime(date_str, "%Y-%m-%d")
            except ValueError:
                continue

            # Delete logs older than 7 days
            if file_date < cutoff:
                log_file.unlink()
                pruned.append(str(log_file))

        return pruned

    def _update_dream_timestamp(self, results: dict):
        """Update the last dream summary."""
        summary_path = self.memory_dir / "consolidated" / "last-dream.md"
        summary_path.parent.mkdir(parents=True, exist_ok=True)

        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        summary = f"""# Last Dream — {now}

## Phases Run
- Orient: {results.get('orient', [])}
- Signal gathered: {list(results.get('signal_gathered', {}).keys())}
- Merged into: {results.get('merged', [])}
- Pruned: {len(results.get('pruned', []))} file(s)

## Signal Summary
{json.dumps({k: len(v) for k, v in results.get('signal_gathered', {}).items()}, indent=2)}
"""
        summary_path.write_text(summary)

    def get_memory_context(self, topics: Optional[list[str]] = None) -> str:
        """
        Get relevant memory context for a new session.
        Reads relevant topic files and formats for injection.
        """
        if topics is None:
            topics = list(self.topic_files.keys())

        context_parts = []
        for topic in topics:
            path = self.topic_files.get(topic)
            if path and path.exists():
                content = path.read_text().strip()
                if content:
                    context_parts.append(f"## {topic.replace('_', ' ').title()}\n{content[-2000:]}\n")

        if context_parts:
            return "\n---\n".join(context_parts)
        return ""
