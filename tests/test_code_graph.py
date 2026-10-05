"""Unit tests for CodeDependencyGraph, BlastRadiusReport, and Semantic Code Intelligence."""
import unittest

from ai_code_engineer.code_graph import (
    BlastRadiusReport,
    CodeDependencyGraph,
    EdgeType,
    GraphNode,
    NodeType,
)


class CodeGraphTests(unittest.TestCase):
    def setUp(self):
        self.graph = CodeDependencyGraph()

        # Architecture:
        # UserController -> UserService (implements IUserService) -> UserRepository
        # UserController exposes /api/users
        # TestUser -> tests UserService
        self.nodes = [
            GraphNode("repo", "UserRepository", NodeType.CLASS, "src/repo.py"),
            GraphNode("i_service", "IUserService", NodeType.INTERFACE, "src/service.py"),
            GraphNode("service", "UserService", NodeType.CLASS, "src/service.py"),
            GraphNode("ctrl", "UserController", NodeType.CLASS, "src/controller.py"),
            GraphNode("ep_users", "GET /api/users", NodeType.ENDPOINT, "src/controller.py"),
            GraphNode("test_user", "test_user_service", NodeType.TEST, "tests/test_service.py"),
        ]
        for n in self.nodes:
            self.graph.add_node(n)

        # Edges
        self.graph.add_edge("service", "i_service", EdgeType.IMPLEMENTS)
        self.graph.add_edge("service", "repo", EdgeType.CALLS)
        self.graph.add_edge("ctrl", "service", EdgeType.CALLS)
        self.graph.add_edge("ctrl", "ep_users", EdgeType.EXPOSES)
        self.graph.add_edge("test_user", "service", EdgeType.TESTS)

    def test_dependencies_and_dependents(self):
        # UserService calls UserRepository and implements IUserService
        deps = self.graph.dependencies_of("service")
        self.assertEqual(sorted(deps), ["i_service", "repo"])

        # Dependents of UserService: UserController and test_user
        dependents = self.graph.dependents_of("service")
        self.assertEqual(sorted(dependents), ["ctrl", "test_user"])

    def test_implementations_of_interface(self):
        impls = self.graph.implementations_of("i_service")
        self.assertEqual(impls, ["service"])

    def test_callers_of_method(self):
        callers = self.graph.callers_of("repo")
        self.assertEqual(callers, ["service"])

    def test_tests_covering_target(self):
        tests_for_repo = self.graph.tests_covering("repo")
        # test_user tests service, which calls repo -> test_user covers repo transitively
        self.assertIn("test_user", tests_for_repo)

    def test_shortest_path(self):
        path = self.graph.shortest_path("ctrl", "repo")
        self.assertEqual(path, ["ctrl", "service", "repo"])

    def test_blast_radius_computation(self):
        # If UserRepository changes:
        # Directly affected: UserService
        # Transitive: UserController, GET /api/users, test_user
        report = self.graph.compute_blast_radius(["repo"])
        self.assertIn("service", report.directly_affected)
        self.assertIn("ctrl", report.transitively_affected)
        self.assertIn("ep_users", report.affected_endpoints)
        self.assertIn("test_user", report.affected_tests)
        self.assertEqual(report.risk_level, "HIGH")  # Touches exposed endpoint

        md = report.to_markdown()
        self.assertIn("# Change Blast Radius Report", md)
        self.assertIn("Targeted Tests Required", md)
