"""Semantic Code Intelligence, Code Dependency Graph, and Change Impact Analysis.

Covers Sessions 21, 22, and 23:
1. Semantic Code Intelligence: Strongly-typed AST symbol representation (Class, Interface, Method, Endpoint, Test).
2. Code Dependency Graph: Lightweight graph representation for dependencies, callers, implementations, and test coverage.
3. Change Impact Analysis: Calculates blast radius before code edits, identifying directly affected symbols,
   affected endpoints, and required targeted test suites.
"""
from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Optional

from .redaction import redact


class NodeType(str, Enum):
    """Semantic code entity types."""
    FILE = "FILE"
    CLASS = "CLASS"
    INTERFACE = "INTERFACE"
    METHOD = "METHOD"
    FUNCTION = "FUNCTION"
    ENDPOINT = "ENDPOINT"
    TEST = "TEST"
    MODULE = "MODULE"


class EdgeType(str, Enum):
    """Semantic relationship types between code entities."""
    IMPLEMENTS = "IMPLEMENTS"
    EXTENDS = "EXTENDS"
    CALLS = "CALLS"
    IMPORTS = "IMPORTS"
    TESTS = "TESTS"
    EXPOSES = "EXPOSES"
    DEPENDS_ON = "DEPENDS_ON"


@dataclass
class GraphNode:
    """An individual entity in the code intelligence graph."""
    node_id: str
    name: str
    node_type: NodeType
    file_path: str
    line_start: int = 1
    line_end: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "name": self.name,
            "node_type": self.node_type.value,
            "file_path": redact(self.file_path),
            "line_start": self.line_start,
            "line_end": self.line_end,
            "metadata": self.metadata,
        }


@dataclass
class GraphEdge:
    """A directed semantic relationship connecting two code entities."""
    source_id: str
    target_id: str
    edge_type: EdgeType

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "target_id": self.target_id,
            "edge_type": self.edge_type.value,
        }


@dataclass
class BlastRadiusReport:
    """Predicted impact assessment before applying code modifications."""
    changed_nodes: list[str]
    directly_affected: list[str]
    transitively_affected: list[str]
    affected_endpoints: list[str]
    affected_tests: list[str]
    risk_level: str  # LOW, MEDIUM, HIGH
    summary: str = ""

    def to_markdown(self) -> str:
        lines = [
            f"# Change Blast Radius Report (Risk: {self.risk_level})",
            f"**Modified Entities:** {', '.join(self.changed_nodes) or 'None'}",
            "",
            "## Impact Breakdown",
            f"- **Directly Dependent Entities:** {len(self.directly_affected)} ({', '.join(self.directly_affected[:5]) or 'None'})",
            f"- **Transitive Dependents:** {len(self.transitively_affected)}",
            f"- **Exposed API Endpoints Affected:** {len(self.affected_endpoints)} ({', '.join(self.affected_endpoints) or 'None'})",
            f"- **Targeted Tests Required:** {len(self.affected_tests)} ({', '.join(self.affected_tests[:5]) or 'None'})",
        ]
        if self.summary:
            lines.extend(["", f"**Analysis Rationale:** {self.summary}"])
        return "\n".join(lines)


class CodeDependencyGraph:
    """In-memory directed dependency graph for semantic code intelligence."""

    def __init__(self) -> None:
        self.nodes: dict[str, GraphNode] = {}
        # adjacency: source_id -> list of (target_id, edge_type)
        self.outgoing: dict[str, list[GraphEdge]] = {}
        # reverse: target_id -> list of (source_id, edge_type)
        self.incoming: dict[str, list[GraphEdge]] = {}

    def add_node(self, node: GraphNode) -> None:
        self.nodes[node.node_id] = node
        self.outgoing.setdefault(node.node_id, [])
        self.incoming.setdefault(node.node_id, [])

    def add_edge(self, source_id: str, target_id: str, edge_type: EdgeType) -> None:
        if source_id not in self.nodes or target_id not in self.nodes:
            return
        edge = GraphEdge(source_id, target_id, edge_type)
        self.outgoing.setdefault(source_id, []).append(edge)
        self.incoming.setdefault(target_id, []).append(edge)

    def dependencies_of(self, node_id: str) -> list[str]:
        """Entities that node_id depends on (outgoing edges)."""
        edges = self.outgoing.get(node_id, [])
        return [e.target_id for e in edges]

    def dependents_of(self, node_id: str) -> list[str]:
        """Entities that depend on node_id (incoming edges)."""
        edges = self.incoming.get(node_id, [])
        return [e.source_id for e in edges]

    def implementations_of(self, interface_id: str) -> list[str]:
        """Find all classes that implement or extend interface_id."""
        return [
            e.source_id
            for e in self.incoming.get(interface_id, [])
            if e.edge_type in (EdgeType.IMPLEMENTS, EdgeType.EXTENDS)
        ]

    def callers_of(self, callable_id: str) -> list[str]:
        """Find all functions/methods that invoke callable_id."""
        return [
            e.source_id
            for e in self.incoming.get(callable_id, [])
            if e.edge_type == EdgeType.CALLS
        ]

    def tests_covering(self, target_id: str) -> list[str]:
        """Find all test nodes directly or transitively covering target_id."""
        visited: set[str] = set()
        queue = deque([target_id])
        tests_found: set[str] = set()

        while queue:
            curr = queue.popleft()
            if curr in visited:
                continue
            visited.add(curr)

            node = self.nodes.get(curr)
            if node and node.node_type == NodeType.TEST:
                tests_found.add(curr)

            # Follow reverse edges to see who tests or calls this
            for edge in self.incoming.get(curr, []):
                if edge.edge_type in (EdgeType.TESTS, EdgeType.CALLS, EdgeType.DEPENDS_ON):
                    if edge.source_id not in visited:
                        queue.append(edge.source_id)

        return sorted(list(tests_found))

    def shortest_path(self, source_id: str, target_id: str) -> Optional[list[str]]:
        """Compute shortest path between two entities using breadth-first search."""
        if source_id == target_id:
            return [source_id]
        if source_id not in self.nodes or target_id not in self.nodes:
            return None

        queue = deque([[source_id]])
        visited = {source_id}

        while queue:
            path = queue.popleft()
            last = path[-1]
            if last == target_id:
                return path

            for edge in self.outgoing.get(last, []):
                if edge.target_id not in visited:
                    visited.add(edge.target_id)
                    queue.append(path + [edge.target_id])

        return None

    def compute_blast_radius(self, changed_node_ids: list[str]) -> BlastRadiusReport:
        """Calculates direct and transitive blast radius of proposed changes."""
        direct: set[str] = set()
        transitive: set[str] = set()
        endpoints: set[str] = set()
        tests: set[str] = set()

        for c_id in changed_node_ids:
            # Direct incoming dependents
            for dep in self.dependents_of(c_id):
                if dep not in changed_node_ids:
                    direct.add(dep)

        # Transitive dependents via BFS
        visited = set(changed_node_ids).union(direct)
        queue = deque(list(direct))
        while queue:
            curr = queue.popleft()
            node = self.nodes.get(curr)
            if node:
                if node.node_type == NodeType.ENDPOINT:
                    endpoints.add(curr)
                elif node.node_type == NodeType.TEST:
                    tests.add(curr)

            # Check if this node exposes any API endpoints
            for edge in self.outgoing.get(curr, []):
                if edge.edge_type == EdgeType.EXPOSES:
                    endpoints.add(edge.target_id)

            for nxt in self.dependents_of(curr):
                if nxt not in visited:
                    visited.add(nxt)
                    transitive.add(nxt)
                    queue.append(nxt)

        # Also collect tests directly covering changed nodes
        for c_id in changed_node_ids:
            for t in self.tests_covering(c_id):
                tests.add(t)

        # Risk assessment
        risk = "LOW"
        if endpoints or len(direct) > 5 or len(transitive) > 10:
            risk = "HIGH"
        elif direct or tests:
            risk = "MEDIUM"

        summary = (
            f"Modifying {len(changed_node_ids)} nodes impacts {len(direct)} direct dependents, "
            f"{len(transitive)} transitive dependents, {len(endpoints)} API endpoints, and requires {len(tests)} tests."
        )

        return BlastRadiusReport(
            changed_nodes=changed_node_ids,
            directly_affected=sorted(list(direct)),
            transitively_affected=sorted(list(transitive)),
            affected_endpoints=sorted(list(endpoints)),
            affected_tests=sorted(list(tests)),
            risk_level=risk,
            summary=summary,
        )
