"""Small, deterministic Scenario RAG retriever for the baitbot MVP.

The corpus is curated JSON rather than live transcript memory.  Candidate
sources stay out of retrieval until they are explicitly reviewed.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Mapping


TOKEN_RE = re.compile(r"[가-힣A-Za-z0-9]+")
DEFAULT_CORPUS_PATH = Path(__file__).with_name("scenario_corpus.json")
RETRIEVABLE_STATUS = "VERIFIED"
SUSPECTED_MIN_PERCENT = 40
PHISHING_CONFIRMED_MIN_PERCENT = 80


def _flatten(value: Any) -> str:
    if isinstance(value, Mapping):
        return " ".join(_flatten(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return " ".join(_flatten(item) for item in value)
    return str(value) if value is not None else ""


def _terms(value: Any) -> set[str]:
    """Keep exact words and short Korean character n-grams for spacing drift."""

    terms: set[str] = set()
    for token in TOKEN_RE.findall(_flatten(value).lower()):
        terms.add(token)
        if len(token) >= 2:
            for size in (2, 3, 4):
                terms.update(token[index : index + size] for index in range(len(token) - size + 1))
    return terms


class ScenarioRAG:
    """Read-only lexical retrieval over reviewed scenario documents."""

    _weighted_fields = (
        ("title", 3.0),
        ("scam_type", 2.0),
        ("phase", 2.0),
        ("signals", 3.0),
        ("pretext", 2.0),
        ("requested_actions", 2.0),
        ("pressure_cues", 2.0),
        ("artifacts", 1.0),
        ("summary", 1.0),
        ("roles", 1.0),
    )

    def __init__(
        self,
        corpus_path: str | Path | None = None,
        *,
        include_candidate: bool = False,
    ) -> None:
        self.corpus_path = Path(corpus_path or DEFAULT_CORPUS_PATH)
        self.include_candidate = include_candidate
        self._all_documents = self._load(self.corpus_path)
        self._documents = [
            document
            for document in self._all_documents
            if include_candidate or document["review_status"] == RETRIEVABLE_STATUS
        ]
        self._field_terms = {
            document["id"]: {
                field: _terms(document.get(field, ""))
                for field, _ in self._weighted_fields
            }
            for document in self._documents
        }

    @staticmethod
    def _load(path: Path) -> list[dict[str, Any]]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError(f"scenario corpus is unavailable: {path}") from error
        if not isinstance(payload, Mapping) or payload.get("schema_version") != "1.0":
            raise ValueError("scenario corpus schema_version must be 1.0")
        documents = payload.get("documents")
        if not isinstance(documents, list) or not documents:
            raise ValueError("scenario corpus documents must be a non-empty list")

        seen_ids: set[str] = set()
        normalized: list[dict[str, Any]] = []
        for document in documents:
            if not isinstance(document, Mapping):
                raise ValueError("scenario corpus document must be an object")
            required = {"id", "title", "review_status", "is_benign", "summary", "source_urls"}
            if not required <= set(document):
                raise ValueError("scenario corpus document is missing required fields")
            document_id = document["id"]
            if not isinstance(document_id, str) or not re.fullmatch(r"[a-z0-9_]+", document_id):
                raise ValueError("scenario corpus document id is invalid")
            if document_id in seen_ids:
                raise ValueError("scenario corpus document ids must be unique")
            if document["review_status"] not in {"VERIFIED", "CANDIDATE"}:
                raise ValueError("scenario corpus review_status is invalid")
            if not isinstance(document["is_benign"], bool):
                raise ValueError("scenario corpus is_benign is invalid")
            if not isinstance(document["summary"], str) or not document["summary"].strip():
                raise ValueError("scenario corpus summary is invalid")
            if not isinstance(document["source_urls"], list) or any(
                not isinstance(url, str) for url in document["source_urls"]
            ):
                raise ValueError("scenario corpus source_urls is invalid")
            seen_ids.add(document_id)
            normalized.append(dict(document))
        return normalized

    @staticmethod
    def _score(
        query_terms: set[str],
        field_terms: Mapping[str, set[str]],
    ) -> tuple[float, list[str]]:
        matched: set[str] = set()
        weighted_overlap = 0.0
        for field, weight in ScenarioRAG._weighted_fields:
            hits = query_terms & field_terms.get(field, set())
            if hits:
                weighted_overlap += weight * len(hits)
                matched.update(hits)
        if not matched:
            return 0.0, []
        # A bounded lexical score is sufficient for the small curated MVP corpus.
        score = weighted_overlap / math.sqrt(max(1, len(query_terms)))
        return round(score, 6), sorted(matched, key=lambda term: (-len(term), term))[:20]

    @staticmethod
    def _result(document: Mapping[str, Any], score: float, matched_terms: list[str], reason: str) -> dict[str, Any]:
        return {
            "id": document["id"],
            "title": document["title"],
            "score": score,
            "reason": reason,
            "matched_terms": matched_terms,
            "scam_type": document.get("scam_type"),
            "phase": document.get("phase"),
            "is_benign": document["is_benign"],
            "review_status": document["review_status"],
            "summary": document["summary"],
            "signals": list(document.get("signals", [])),
            "safe_actions": list(document.get("safe_actions", [])),
            "source_urls": list(document["source_urls"]),
        }

    def retrieve(self, query: str, *, top_k: int = 5, include_benign: bool = True) -> list[dict[str, Any]]:
        if not isinstance(query, str) or not query.strip() or top_k <= 0:
            return []
        query_terms = _terms(query)
        scored: list[tuple[float, list[str], dict[str, Any]]] = []
        for document in self._documents:
            if not include_benign and document["is_benign"]:
                continue
            score, matched_terms = self._score(query_terms, self._field_terms[document["id"]])
            scored.append((score, matched_terms, document))

        scored.sort(key=lambda item: (-item[0], item[2]["is_benign"], item[2]["id"]))
        positive = [item for item in scored if item[0] > 0]
        selected = positive[:top_k]

        if include_benign and top_k > 1:
            benign = next((item for item in scored if item[2]["is_benign"]), None)
            if benign is not None and not any(item[2]["is_benign"] for item in selected):
                if len(selected) >= top_k:
                    selected = selected[: top_k - 1]
                selected.append(benign)

        return [
            self._result(
                document,
                score,
                matched_terms,
                "match" if score > 0 else "contrast",
            )
            for score, matched_terms, document in selected[:top_k]
        ]

    def assess(self, query: str) -> dict[str, Any]:
        """Return a deterministic suspicion state from reviewed lexical evidence."""

        results = self.retrieve(query, top_k=5, include_benign=True)
        suspicion_percent = 0
        if isinstance(query, str) and query.strip():
            query_terms = _terms(query)
            query_word_count = len(TOKEN_RE.findall(query))
            scam_results = [
                result
                for result in results
                if not result["is_benign"]
                and result["review_status"] == RETRIEVABLE_STATUS
                and result["score"] > 0
            ]
            if scam_results:
                top_scam = scam_results[0]
                evidence_fields = (
                    ("signals", 6),
                    ("pretext", 4),
                    ("requested_actions", 12),
                    ("pressure_cues", 8),
                    ("artifacts", 10),
                    ("roles", 4),
                )
                matched_evidence = [
                    weight
                    for field, weight in evidence_fields
                    if query_terms & self._field_terms[top_scam["id"]][field]
                ]
                # Score = min(100, 3*similarity (45 cap) + matched evidence
                # (signal 6, pretext 4, action 12, pressure 8, artifact 10, role 4)
                # + 2*conversation terms (13 cap)).
                suspicion_percent = min(
                    100,
                    min(45, round(top_scam["score"] * 3))
                    + sum(matched_evidence)
                    + min(13, query_word_count * 2),
                )
                # A short repeated corpus phrase cannot confirm a call by itself.
                if len(matched_evidence) < 3 or query_word_count < 6:
                    suspicion_percent = min(suspicion_percent, PHISHING_CONFIRMED_MIN_PERCENT - 1)
                benign_score = max(
                    (result["score"] for result in results if result["is_benign"]),
                    default=0.0,
                )
                if benign_score >= top_scam["score"]:
                    suspicion_percent = min(suspicion_percent, 35)

        scam_state, handoff_available = self._state_for_suspicion(suspicion_percent)
        return {
            "suspicion_percent": suspicion_percent,
            "scam_state": scam_state,
            "handoff_available": handoff_available,
            "results": results,
        }

    @staticmethod
    def _state_for_suspicion(percent: int) -> tuple[str, bool]:
        if percent >= PHISHING_CONFIRMED_MIN_PERCENT:
            return "PHISHING_CONFIRMED", True
        if percent >= SUSPECTED_MIN_PERCENT:
            return "SUSPECTED", False
        return "NORMAL", False

    def health(self) -> dict[str, Any]:
        return {
            "corpus_path": str(self.corpus_path),
            "document_count": len(self._all_documents),
            "retrievable_count": len(self._documents),
            "verified_count": sum(document["review_status"] == RETRIEVABLE_STATUS for document in self._all_documents),
            "candidate_count": sum(document["review_status"] == "CANDIDATE" for document in self._all_documents),
            "benign_count": sum(document["is_benign"] for document in self._documents),
            "include_candidate": self.include_candidate,
        }
