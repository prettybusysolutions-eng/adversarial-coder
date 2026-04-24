"""
semantic_memory.py — Vector-based collective memory with semantic search.

Replaces DreamConsolidation's regex-extraction-to-markdown-files approach
with vector embeddings and semantic similarity search. The parliament's
accumulated knowledge becomes queryable — not just grep-able.

Uses ChromaDB for local persistent vector storage (no server required).
Falls back gracefully to keyword matching if ChromaDB is unavailable.

Categories:
  lesson       — rules learned from past failures
  error        — errors encountered and their context
  decision     — architectural or approach choices made
  pattern      — recurring patterns discovered
  blocker      — things that blocked progress and how they were resolved
  threat       — security vulnerabilities found by the Red Team
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass
class MemoryRecord:
    id: str
    content: str
    category: str       # lesson | error | decision | pattern | blocker | threat
    session_id: str
    timestamp: str
    metadata: dict
    relevance_score: float = 0.0


class SemanticMemory:
    """
    Vector-based semantic memory for the agent parliament collective.

    On first use, initializes a ChromaDB persistent store. If ChromaDB
    is not installed, falls back to in-memory keyword search with JSON
    persistence — lower recall quality but no hard dependency.

    Usage:
        mem = SemanticMemory("~/.openclaw/parliament/memory/semantic")
        mem.store("always validate auth before executing", "lesson", session_id)
        results = mem.search("authentication bypass", top_k=5)
        context = mem.get_context_for_task("implement login endpoint")
    """

    CATEGORIES = ["lesson", "error", "decision", "pattern", "blocker", "threat"]

    def __init__(self, persist_dir: str):
        self.persist_dir = Path(persist_dir).expanduser()
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self._chroma_client = None
        self._collections: dict = {}
        self._fallback_store: dict[str, list[dict]] = {c: [] for c in self.CATEGORIES}
        self._fallback_path = self.persist_dir / "fallback.json"
        self._use_chroma = self._init_chroma()
        if not self._use_chroma:
            self._load_fallback()

    def _init_chroma(self) -> bool:
        try:
            import chromadb
            self._chroma_client = chromadb.PersistentClient(
                path=str(self.persist_dir / "chroma")
            )
            for category in self.CATEGORIES:
                self._collections[category] = self._chroma_client.get_or_create_collection(
                    name=f"parliament_{category}",
                    metadata={"hnsw:space": "cosine"},
                )
            return True
        except ImportError:
            return False

    def _load_fallback(self):
        if self._fallback_path.exists():
            try:
                data = json.loads(self._fallback_path.read_text())
                for cat in self.CATEGORIES:
                    self._fallback_store[cat] = data.get(cat, [])
            except (json.JSONDecodeError, KeyError):
                pass

    def _save_fallback(self):
        self._fallback_path.write_text(
            json.dumps(self._fallback_store, indent=2, default=str)
        )

    def store(
        self,
        content: str,
        category: str,
        session_id: str,
        metadata: Optional[dict] = None,
    ) -> MemoryRecord:
        """Store a memory record. Deduplicates by content hash."""
        if category not in self.CATEGORIES:
            category = "pattern"

        record_id = hashlib.sha256(
            f"{session_id}:{category}:{content}".encode()
        ).hexdigest()[:16]
        timestamp = datetime.utcnow().isoformat() + "Z"
        meta = {
            "session_id": session_id,
            "timestamp": timestamp,
            "category": category,
            **(metadata or {}),
        }

        if self._use_chroma and category in self._collections:
            try:
                self._collections[category].upsert(
                    ids=[record_id],
                    documents=[content],
                    metadatas=[meta],
                )
            except Exception:
                pass
        else:
            existing_ids = {r["id"] for r in self._fallback_store[category]}
            if record_id not in existing_ids:
                self._fallback_store[category].append({
                    "id": record_id,
                    "content": content,
                    **meta,
                })
                self._save_fallback()

        return MemoryRecord(
            id=record_id,
            content=content,
            category=category,
            session_id=session_id,
            timestamp=timestamp,
            metadata=meta,
        )

    def search(
        self,
        query: str,
        category: Optional[str] = None,
        top_k: int = 5,
    ) -> list[MemoryRecord]:
        """
        Semantic search over stored memories.
        With ChromaDB: embedding similarity (cosine).
        Fallback: keyword overlap scoring.
        """
        categories = [category] if category else self.CATEGORIES
        results: list[MemoryRecord] = []

        if self._use_chroma:
            for cat in categories:
                if cat not in self._collections:
                    continue
                try:
                    col = self._collections[cat]
                    count = col.count()
                    if count == 0:
                        continue
                    qr = col.query(
                        query_texts=[query],
                        n_results=min(top_k, count),
                    )
                    for i, doc in enumerate(qr["documents"][0]):
                        meta = qr["metadatas"][0][i]
                        distance = (
                            qr["distances"][0][i]
                            if "distances" in qr
                            else 0.5
                        )
                        results.append(MemoryRecord(
                            id=qr["ids"][0][i],
                            content=doc,
                            category=cat,
                            session_id=meta.get("session_id", ""),
                            timestamp=meta.get("timestamp", ""),
                            metadata=meta,
                            relevance_score=max(0.0, 1.0 - distance),
                        ))
                except Exception:
                    pass
        else:
            # Fallback: word-overlap scoring (Jaccard-ish)
            query_words = set(query.lower().split())
            for cat in categories:
                for record in self._fallback_store.get(cat, []):
                    doc_words = set(record["content"].lower().split())
                    if not query_words or not doc_words:
                        score = 0.0
                    else:
                        intersection = query_words & doc_words
                        union = query_words | doc_words
                        score = len(intersection) / len(union)
                    if score > 0:
                        results.append(MemoryRecord(
                            id=record["id"],
                            content=record["content"],
                            category=cat,
                            session_id=record.get("session_id", ""),
                            timestamp=record.get("timestamp", ""),
                            metadata=record,
                            relevance_score=score,
                        ))

        results.sort(key=lambda r: r.relevance_score, reverse=True)
        return results[:top_k]

    def get_context_for_task(self, task: str, max_entries: int = 8) -> str:
        """
        Retrieve the most relevant past memories for a task.
        Returns a formatted string ready for injection into agent system prompts.
        """
        results = self.search(task, top_k=max_entries)
        if not results:
            return ""

        lines = ["## Collective Memory — Relevant Past Experience\n"]
        by_category: dict[str, list[str]] = {}
        for r in results:
            by_category.setdefault(r.category, []).append(r.content)

        for cat, entries in by_category.items():
            lines.append(f"**{cat.upper()}**")
            for entry in entries:
                lines.append(f"  - {entry}")

        return "\n".join(lines)

    def store_debate_outcome(self, result, session_id: str):
        """
        Extract and store structured learnings from a DebateResult.
        Called at the end of each deliberation.
        """
        # Store the outcome as a decision
        outcome_text = (
            f"Task '{result.task[:120]}' → {result.winning_position} "
            f"via {result.compute_tier} ({len(result.rounds)} rounds)"
        )
        self.store(outcome_text, "decision", session_id)

        # Store block reasons as lessons
        if result.block_reason:
            self.store(
                f"BLOCKED: {result.block_reason}. Task was: {result.task[:100]}",
                "lesson",
                session_id,
                metadata={"task_snippet": result.task[:100], "blocked": True},
            )

        # Store formal verification failures as errors
        for fr in getattr(result, "formal_results", []):
            if not fr.passed:
                self.store(
                    f"Formal check {fr.tool} failed on task '{result.task[:80]}'",
                    "error",
                    session_id,
                    metadata={"tool": fr.tool, "exit_code": fr.exit_code},
                )

        # Store Red Team findings as threats
        red_team = getattr(result, "red_team_report", None)
        if red_team and red_team.vulnerabilities:
            for v in red_team.vulnerabilities:
                self.store(
                    f"{v.category} [{v.severity}]: {v.description}. "
                    f"Mitigation: {v.mitigation}",
                    "threat",
                    session_id,
                    metadata={
                        "category": v.category,
                        "severity": v.severity,
                        "task_snippet": result.task[:80],
                    },
                )

    def stats(self) -> dict:
        """Return count of stored records per category."""
        counts = {}
        if self._use_chroma:
            for cat, col in self._collections.items():
                try:
                    counts[cat] = col.count()
                except Exception:
                    counts[cat] = 0
        else:
            for cat in self.CATEGORIES:
                counts[cat] = len(self._fallback_store.get(cat, []))
        return counts
