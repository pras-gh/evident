"""Topic graph construction.

Turns resolved memory into the node/edge structure the Company Memory
visualisation renders: a company core, the topics that surfaced around it, the
documents that discuss them, and the co-occurrence between topics.

Edges are weighted by *shared documents*, not by text similarity. Two topics
are related here because the same filing discussed both — which is a fact about
the corpus rather than a guess about meaning, and it stays explainable: every
edge can name the documents that produced it.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Iterable, Literal, Sequence

NodeKind = Literal["company", "topic", "document", "product", "person"]


@dataclass(slots=True, frozen=True)
class Node:
    id: str
    kind: NodeKind
    label: str
    weight: int = 1
    first_seen_at: date | None = None
    last_seen_at: date | None = None


@dataclass(slots=True, frozen=True)
class Edge:
    source: str
    target: str
    kind: Literal["mentions", "co_occurs", "about"]
    weight: int = 1
    # the documents that justify this edge — an edge you cannot explain is a
    # decoration, and this graph is meant to be evidence-backed like everything
    # else in the product
    documents: tuple[str, ...] = ()


@dataclass(slots=True)
class TopicGraph:
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)

    def to_json(self) -> dict:
        return {
            "nodes": [{"id": n.id, "kind": n.kind, "label": n.label,
                       "weight": n.weight,
                       "firstSeen": n.first_seen_at.isoformat() if n.first_seen_at else None,
                       "lastSeen": n.last_seen_at.isoformat() if n.last_seen_at else None}
                      for n in self.nodes],
            "edges": [{"source": e.source, "target": e.target, "kind": e.kind,
                       "weight": e.weight, "documents": list(e.documents)}
                      for e in self.edges],
        }

    def neighbours(self, node_id: str) -> list[str]:
        out = []
        for e in self.edges:
            if e.source == node_id:
                out.append(e.target)
            elif e.target == node_id:
                out.append(e.source)
        return out


def build(memory, *, min_co_occurrence: int = 1,
          max_topics: int | None = None) -> TopicGraph:
    """Build the graph from a CompanyMemory.

    `min_co_occurrence` is the honest knob: at 1 every shared document makes an
    edge, which is noisy on a large corpus. Raising it keeps only topics that
    recur together, which is usually what a reader means by "related".
    """
    graph = TopicGraph()
    core = f"company:{memory.company_id}"
    graph.nodes.append(Node(id=core, kind="company",
                            label=memory.ticker or memory.company_id,
                            weight=len(memory.documents)))

    topics = sorted(memory.topics, key=lambda t: -t.mention_count)
    if max_topics:
        topics = topics[:max_topics]

    docs_by_topic: dict[str, set[str]] = defaultdict(set)
    for t in topics:
        tid = f"topic:{t.slug}"
        graph.nodes.append(Node(id=tid, kind="topic", label=t.label,
                                weight=t.mention_count,
                                first_seen_at=t.first_seen_at,
                                last_seen_at=t.last_seen_at))
        graph.edges.append(Edge(source=core, target=tid, kind="about",
                                weight=t.mention_count))
        for e in t.evidence:
            docs_by_topic[t.slug].add(e.document_id)

    seen_docs = {d for docs in docs_by_topic.values() for d in docs}
    for doc_id in sorted(seen_docs):
        ref = next((d for d in memory.documents if d.document_id == doc_id), None)
        graph.nodes.append(Node(
            id=f"document:{doc_id}", kind="document",
            label=ref.form_type if ref else doc_id,
            first_seen_at=ref.filed_date if ref else None))

    for slug, docs in docs_by_topic.items():
        for doc_id in sorted(docs):
            graph.edges.append(Edge(source=f"document:{doc_id}",
                                    target=f"topic:{slug}", kind="mentions",
                                    documents=(doc_id,)))

    graph.edges.extend(_co_occurrence(docs_by_topic, min_co_occurrence))
    return graph


def _co_occurrence(docs_by_topic: dict[str, set[str]], threshold: int) -> list[Edge]:
    slugs = sorted(docs_by_topic)
    out: list[Edge] = []
    for i, a in enumerate(slugs):
        for b in slugs[i + 1:]:
            shared = docs_by_topic[a] & docs_by_topic[b]
            if len(shared) >= threshold:
                out.append(Edge(source=f"topic:{a}", target=f"topic:{b}",
                                kind="co_occurs", weight=len(shared),
                                documents=tuple(sorted(shared))))
    return out


def slice_by_period(graph: TopicGraph, *, until: date) -> TopicGraph:
    """The graph as it stood on a date — what the replay animation scrubs.

    Nodes whose first mention is later than `until` did not exist yet, so the
    graph honestly has fewer of them.
    """
    keep = {n.id for n in graph.nodes
            if n.first_seen_at is None or n.first_seen_at <= until}
    return TopicGraph(
        nodes=[n for n in graph.nodes if n.id in keep],
        edges=[e for e in graph.edges if e.source in keep and e.target in keep],
    )
