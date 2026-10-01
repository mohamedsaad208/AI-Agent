"""Tests for RepoScanner and project-index.json generation."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_code_engineer.repo_scanner import (
    ProjectIndex,
    RepoScanner,
    _classify_source,
    _walk_files,
)
from ai_code_engineer import symbols


# ---------------------------------------------------------------------------
# Helpers to build a fake Spring project on disk
# ---------------------------------------------------------------------------

LOGIN_CONTROLLER = """\
package com.example.auth.web;

import com.example.auth.service.LoginService;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.bind.annotation.PostMapping;

@RestController
public class LoginController {
    private final LoginService loginService;
    public LoginController(LoginService loginService) {
        this.loginService = loginService;
    }
    @PostMapping("/login")
    public String login(String email, String password) {
        return loginService.login(email, password);
    }
}
"""

LOGIN_SERVICE = """\
package com.example.auth.service;

import com.example.auth.repository.CustomerRepository;
import org.springframework.stereotype.Service;

@Service
public class LoginService {
    private final CustomerRepository repo;
    public LoginService(CustomerRepository repo) { this.repo = repo; }
    public String login(String email, String password) {
        return repo.findByEmail(email) != null ? "ok" : "fail";
    }
}
"""

CUSTOMER_REPOSITORY = """\
package com.example.auth.repository;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;
import com.example.auth.entity.Customer;

@Repository
public interface CustomerRepository extends JpaRepository<Customer, Long> {
    Customer findByEmail(String email);
}
"""

CUSTOMER_ENTITY = """\
package com.example.auth.entity;

import javax.persistence.Entity;
import javax.persistence.Table;

@Entity
@Table(name = "customers")
public class Customer {
    private Long id;
    private String email;
    private String passwordHash;
}
"""

SECURITY_CONFIG = """\
package com.example.auth.config;

import org.springframework.context.annotation.Configuration;

@Configuration
public class SecurityConfig {
    public SecurityConfig() {}
}
"""

LOGIN_SERVICE_TEST = """\
package com.example.auth.service;

import org.junit.jupiter.api.Test;

public class LoginServiceTest {
    @Test
    public void testLogin() {}
}
"""

POM_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<project>
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.example</groupId>
  <artifactId>auth-service</artifactId>
  <version>1.0.0</version>
  <dependencies>
    <dependency>
      <groupId>org.springframework.boot</groupId>
      <artifactId>spring-boot-starter-web</artifactId>
      <version>3.1.0</version>
    </dependency>
  </dependencies>
</project>
"""


def _make_spring_project(tmp: Path) -> None:
    """Create a minimal Spring project layout for scanner tests."""
    src = tmp / "src" / "main" / "java" / "com" / "example" / "auth"
    test_dir = tmp / "src" / "test" / "java" / "com" / "example" / "auth" / "service"
    src.mkdir(parents=True)
    test_dir.mkdir(parents=True)

    (src / "web").mkdir()
    (src / "service").mkdir()
    (src / "repository").mkdir()
    (src / "entity").mkdir()
    (src / "config").mkdir()

    (src / "web" / "LoginController.java").write_text(LOGIN_CONTROLLER, encoding="utf-8")
    (src / "service" / "LoginService.java").write_text(LOGIN_SERVICE, encoding="utf-8")
    (src / "repository" / "CustomerRepository.java").write_text(CUSTOMER_REPOSITORY, encoding="utf-8")
    (src / "entity" / "Customer.java").write_text(CUSTOMER_ENTITY, encoding="utf-8")
    (src / "config" / "SecurityConfig.java").write_text(SECURITY_CONFIG, encoding="utf-8")
    (test_dir / "LoginServiceTest.java").write_text(LOGIN_SERVICE_TEST, encoding="utf-8")
    (tmp / "pom.xml").write_text(POM_XML, encoding="utf-8")


# ---------------------------------------------------------------------------
# Classification unit tests (no file system needed)
# ---------------------------------------------------------------------------

class TestClassifySource(unittest.TestCase):

    def _row(self, types=None, pkg="com.example"):
        return {"types": types or [], "package": pkg, "path": "SomeFile.java"}

    def test_rest_controller_annotation(self):
        source = "@RestController\npublic class LoginController {}"
        row = self._row([{"name": "LoginController", "kind": "class", "members": [], "extends": []}])
        self.assertEqual(_classify_source(row, source), "controller")

    def test_service_annotation(self):
        source = "@Service\npublic class LoginService {}"
        row = self._row([{"name": "LoginService", "kind": "class", "members": [], "extends": []}])
        self.assertEqual(_classify_source(row, source), "service")

    def test_repository_annotation(self):
        source = "@Repository\npublic interface CustomerRepository {}"
        row = self._row([{"name": "CustomerRepository", "kind": "interface", "members": [], "extends": []}])
        self.assertEqual(_classify_source(row, source), "repository")

    def test_entity_annotation(self):
        source = "@Entity\n@Table(name=\"customers\")\npublic class Customer {}"
        row = self._row([{"name": "Customer", "kind": "class", "members": [], "extends": []}])
        self.assertEqual(_classify_source(row, source), "entity")

    def test_configuration_annotation(self):
        source = "@Configuration\npublic class SecurityConfig {}"
        row = self._row([{"name": "SecurityConfig", "kind": "class", "members": [], "extends": []}])
        self.assertEqual(_classify_source(row, source), "config")

    def test_name_based_test_fallback(self):
        source = "public class LoginServiceTest {}"
        row = self._row(
            [{"name": "LoginServiceTest", "kind": "class", "members": [], "extends": []}],
            pkg="com.example.service"
        )
        self.assertEqual(_classify_source(row, source), "test")

    def test_name_based_controller_fallback(self):
        source = "public class AuthController {}"
        row = self._row([{"name": "AuthController", "kind": "class", "members": [], "extends": []}])
        self.assertEqual(_classify_source(row, source), "controller")

    def test_path_based_test_fallback(self):
        source = "public class AuthTest {}"
        row = {"types": [], "package": "com.example", "path": "src/test/java/AuthTest.java"}
        self.assertEqual(_classify_source(row, source), "test")

    def test_annotation_wins_over_name(self):
        """A class named FooConfig annotated @Service should be classified as service."""
        source = "@Service\npublic class FooConfig {}"
        row = self._row([{"name": "FooConfig", "kind": "class", "members": [], "extends": []}])
        self.assertEqual(_classify_source(row, source), "service")

    def test_annotation_in_comment_ignored(self):
        """An annotation in a comment must not affect classification."""
        source = "// @RestController — do not use\npublic class Foo {}"
        row = self._row([{"name": "Foo", "kind": "class", "members": [], "extends": []}])
        result = _classify_source(row, source)
        self.assertNotEqual(result, "controller")

    # ── New layers: messaging, security, aspect, dto, util ──────────────────

    def test_kafka_listener_annotation(self):
        source = "@KafkaListener(topics=\"orders\")\npublic class OrderConsumer {}"
        row = self._row([{"name": "OrderConsumer", "kind": "class", "members": [], "extends": []}])
        self.assertEqual(_classify_source(row, source), "messaging")

    def test_rabbit_listener_annotation(self):
        source = "@RabbitListener(queues=\"payments\")\npublic class PaymentConsumer {}"
        row = self._row([{"name": "PaymentConsumer", "kind": "class", "members": [], "extends": []}])
        self.assertEqual(_classify_source(row, source), "messaging")

    def test_jms_listener_annotation(self):
        source = "@JmsListener(destination=\"queue.orders\")\npublic class OrderHandler {}"
        row = self._row([{"name": "OrderHandler", "kind": "class", "members": [], "extends": []}])
        self.assertEqual(_classify_source(row, source), "messaging")

    def test_event_listener_annotation(self):
        source = "@EventListener\npublic class DomainEventHandler {}"
        row = self._row([{"name": "DomainEventHandler", "kind": "class", "members": [], "extends": []}])
        self.assertEqual(_classify_source(row, source), "messaging")

    def test_enable_web_security_annotation(self):
        source = "@EnableWebSecurity\npublic class WebSecurityConfig extends WebSecurityConfigurerAdapter {}"
        row = self._row([{"name": "WebSecurityConfig", "kind": "class", "members": [], "extends": []}])
        self.assertEqual(_classify_source(row, source), "security")

    def test_enable_method_security_annotation(self):
        source = "@EnableMethodSecurity\npublic class MethodSecurityConfig {}"
        row = self._row([{"name": "MethodSecurityConfig", "kind": "class", "members": [], "extends": []}])
        self.assertEqual(_classify_source(row, source), "security")

    def test_aspect_annotation(self):
        source = "@Aspect\npublic class LoggingAspect {}"
        row = self._row([{"name": "LoggingAspect", "kind": "class", "members": [], "extends": []}])
        self.assertEqual(_classify_source(row, source), "aspect")

    def test_feign_client_annotation(self):
        source = "@FeignClient(name=\"user-service\")\npublic interface UserClient {}"
        row = self._row([{"name": "UserClient", "kind": "interface", "members": [], "extends": []}])
        self.assertEqual(_classify_source(row, source), "controller")

    def test_grpc_service_annotation(self):
        source = "@GrpcService\npublic class AuthGrpcService extends AuthServiceGrpc.AuthServiceImplBase {}"
        row = self._row([{"name": "AuthGrpcService", "kind": "class", "members": [], "extends": []}])
        self.assertEqual(_classify_source(row, source), "controller")

    def test_name_based_dto(self):
        source = "public class LoginRequest {}"
        row = self._row([{"name": "LoginRequest", "kind": "class", "members": [], "extends": []}])
        self.assertEqual(_classify_source(row, source), "dto")

    def test_name_based_response_dto(self):
        source = "public class LoginResponse {}"
        row = self._row([{"name": "LoginResponse", "kind": "class", "members": [], "extends": []}])
        self.assertEqual(_classify_source(row, source), "dto")

    def test_name_based_util(self):
        source = "public class JwtUtil {}"
        row = self._row([{"name": "JwtUtil", "kind": "class", "members": [], "extends": []}])
        self.assertEqual(_classify_source(row, source), "util")

    def test_name_based_helper(self):
        source = "public class DateHelper {}"
        row = self._row([{"name": "DateHelper", "kind": "class", "members": [], "extends": []}])
        self.assertEqual(_classify_source(row, source), "util")

    def test_name_based_converter(self):
        source = "public class UserConverter {}"
        row = self._row([{"name": "UserConverter", "kind": "class", "members": [], "extends": []}])
        self.assertEqual(_classify_source(row, source), "util")

    def test_name_based_interceptor(self):
        source = "public class AuthInterceptor implements HandlerInterceptor {}"
        row = self._row([{"name": "AuthInterceptor", "kind": "class", "members": [], "extends": []}])
        self.assertEqual(_classify_source(row, source), "aspect")

    def test_name_based_filter(self):
        source = "public class JwtFilter extends OncePerRequestFilter {}"
        row = self._row([{"name": "JwtFilter", "kind": "class", "members": [], "extends": []}])
        self.assertEqual(_classify_source(row, source), "aspect")

    def test_mybatis_mapper_annotation(self):
        source = "@Mapper\npublic interface UserMapper {}"
        row = self._row([{"name": "UserMapper", "kind": "interface", "members": [], "extends": []}])
        self.assertEqual(_classify_source(row, source), "repository")

    def test_enable_jpa_repositories(self):
        source = "@Configuration\n@EnableJpaRepositories\npublic class DatabaseConfig {}"
        row = self._row([{"name": "DatabaseConfig", "kind": "class", "members": [], "extends": []}])
        # @Configuration comes before @EnableJpaRepositories in priority check but both are "config"
        self.assertEqual(_classify_source(row, source), "config")

    def test_quarkus_application_scoped(self):
        source = "@ApplicationScoped\npublic class OrderService {}"
        row = self._row([{"name": "OrderService", "kind": "class", "members": [], "extends": []}])
        self.assertEqual(_classify_source(row, source), "service")

    def test_micronaut_singleton(self):
        source = "@Singleton\npublic class CacheService {}"
        row = self._row([{"name": "CacheService", "kind": "class", "members": [], "extends": []}])
        self.assertEqual(_classify_source(row, source), "service")

    def test_spring_cloud_feign_clients(self):
        source = "@Configuration\n@EnableFeignClients\npublic class FeignConfig {}"
        row = self._row([{"name": "FeignConfig", "kind": "class", "members": [], "extends": []}])
        self.assertEqual(_classify_source(row, source), "config")

    def test_resilience4j_circuit_breaker(self):
        source = "@CircuitBreaker(name=\"backend\", fallbackMethod=\"fallback\")\npublic class PaymentService {}"
        row = self._row([{"name": "PaymentService", "kind": "class", "members": [], "extends": []}])
        self.assertEqual(_classify_source(row, source), "service")


# ---------------------------------------------------------------------------
# Full scanner integration tests (real Spring project on disk)
# ---------------------------------------------------------------------------

class TestRepoScanner(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        _make_spring_project(self.root)
        self.scanner = RepoScanner(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_scan_returns_project_index(self):
        idx = self.scanner.scan()
        self.assertIsInstance(idx, ProjectIndex)
        self.assertEqual(Path(idx.root), self.root.resolve())

    def test_controllers_classified(self):
        idx = self.scanner.scan()
        controller_types = [
            t["name"]
            for entry in idx.controllers
            for t in entry.get("types", [])
        ]
        self.assertIn("LoginController", controller_types,
                      f"controllers: {idx.controllers}")

    def test_services_classified(self):
        idx = self.scanner.scan()
        service_types = [
            t["name"]
            for entry in idx.services
            for t in entry.get("types", [])
        ]
        self.assertIn("LoginService", service_types,
                      f"services: {idx.services}")

    def test_repositories_classified(self):
        idx = self.scanner.scan()
        repo_types = [
            t["name"]
            for entry in idx.repositories
            for t in entry.get("types", [])
        ]
        self.assertIn("CustomerRepository", repo_types,
                      f"repositories: {idx.repositories}")

    def test_entities_classified(self):
        idx = self.scanner.scan()
        entity_types = [
            t["name"]
            for entry in idx.entities
            for t in entry.get("types", [])
        ]
        self.assertIn("Customer", entity_types,
                      f"entities: {idx.entities}")

    def test_configs_classified(self):
        idx = self.scanner.scan()
        config_types = [
            t["name"]
            for entry in idx.configs
            for t in entry.get("types", [])
        ]
        self.assertIn("SecurityConfig", config_types,
                      f"configs: {idx.configs}")

    def test_tests_classified(self):
        idx = self.scanner.scan()
        test_types = [
            t["name"]
            for entry in idx.tests
            for t in entry.get("types", [])
        ]
        self.assertIn("LoginServiceTest", test_types,
                      f"tests: {idx.tests}")

    def test_build_files_detected(self):
        idx = self.scanner.scan()
        paths = [b["path"] for b in idx.build_files]
        self.assertTrue(
            any("pom.xml" in p for p in paths),
            f"build_files paths: {paths}"
        )

    def test_dependency_graph_not_empty(self):
        idx = self.scanner.scan()
        self.assertIsInstance(idx.dependency_graph, dict)

    def test_every_route_is_indexed_with_the_method_that_answers_it(self):
        # The map already prints a controller's methods; what a change-impact question needs from the
        # index is the URL and the handler under it, held as data rather than as a line of prose.
        idx = self.scanner.scan()
        routes = [route for entry in idx.controllers for route in entry["endpoints"]]
        self.assertEqual([(r["verb"], r["path"], r["handler"]) for r in routes],
                         [("POST", "/login", "LoginController.login")])
        self.assertEqual(idx.stats["endpoints"], len(routes))

    def test_a_type_that_extends_something_says_what(self):
        idx = self.scanner.scan()
        extends = {t["name"]: t["extends"] for entry in idx.repositories
                   for t in entry["types"]}
        self.assertEqual(extends["CustomerRepository"], ["JpaRepository"])

    def test_the_build_files_answer_what_the_project_is(self):
        from ai_code_engineer.repo_scanner import project_facts
        self.assertEqual(project_facts(self.scanner.scan()),
                         {"artifact": "auth-service", "build": "maven",
                          "needs": "spring-boot-starter-web"})

    def test_a_route_list_that_was_cut_is_indexed_as_cut(self):
        # #8 asks "does anything answer this URL", and a list stopped at the ceiling cannot answer that
        # on its length alone. The flag is carried rather than re-derived so the answer stays honest.
        body = ("package com.example.web;\n"
                "import org.springframework.web.bind.annotation.*;\n"
                "@RestController\npublic class BigController {\n"
                + "".join('    @GetMapping("/x%d") public String h%d() { return ""; }\n' % (n, n)
                          for n in range(symbols.MAX_ENDPOINTS + 1)) + "}\n")
        (self.root / "src/main/java/com/example/auth/web/BigController.java").write_text(
            body, encoding="utf-8")
        entry = next(e for e in self.scanner.scan().controllers
                     if e["path"].endswith("BigController.java"))
        self.assertEqual(len(entry["endpoints"]), symbols.MAX_ENDPOINTS)
        self.assertTrue(entry["routes_capped"])

    def test_stats_correct(self):
        idx = self.scanner.scan()
        s = idx.stats
        self.assertGreaterEqual(s["controllers"], 1)
        self.assertGreaterEqual(s["services"], 1)
        self.assertGreaterEqual(s["repositories"], 1)
        self.assertGreaterEqual(s["entities"], 1)
        self.assertGreaterEqual(s["tests"], 1)
        self.assertGreaterEqual(s["total_files"], 6)

    def test_save_writes_valid_json(self):
        idx = self.scanner.scan()
        dest = self.scanner.save(idx)
        self.assertTrue(dest.exists())
        parsed = json.loads(dest.read_text(encoding="utf-8"))
        required_keys = [
            "controllers", "services", "repositories", "entities",
            "dtos", "security", "configs", "messaging", "aspects",
            "tests", "utils", "components",
            "dependency_graph", "stats", "generated_at", "fingerprint",
        ]
        for key in required_keys:
            self.assertIn(key, parsed, f"Missing key: {key}")

    def test_summary_contains_key_info(self):
        idx = self.scanner.scan()
        summary = self.scanner.summary(idx)
        self.assertIn("Controllers", summary)
        self.assertIn("Services", summary)
        self.assertIn("Repositories", summary)

    def test_chain_detection(self):
        """Controller → Service → Repository chain must be found."""
        idx = self.scanner.scan()
        chains = self.scanner._find_chains(idx)
        # At least one chain should contain LoginController, LoginService, or CustomerRepository
        all_names = [name for chain in chains for name in chain]
        spring_types = {"LoginController", "LoginService", "CustomerRepository"}
        self.assertTrue(
            spring_types & set(all_names),
            f"No Spring types found in chains: {chains}"
        )

    def test_serialization_roundtrip(self):
        idx = self.scanner.scan()
        data = idx.to_dict()
        self.assertIsInstance(json.dumps(data), str)
        required_keys = [
            "root", "generated_at", "modules",
            "controllers", "services", "repositories", "entities",
            "dtos", "security", "configs", "messaging", "aspects",
            "tests", "utils", "components",
            "build_files", "dependency_graph", "stats", "fingerprint",
        ]
        for key in required_keys:
            self.assertIn(key, data, f"Missing key: {key}")

    def test_empty_project_does_not_crash(self):
        with tempfile.TemporaryDirectory() as empty:
            scanner = RepoScanner(empty)
            idx = scanner.scan()
            self.assertIsInstance(idx, ProjectIndex)
            self.assertEqual(idx.stats["total_files"], 0)

    def test_invalid_root_raises(self):
        from ai_code_engineer.errors import PolicyError
        with self.assertRaises(PolicyError):
            RepoScanner("/nonexistent/path/that/does/not/exist")

    def test_load_and_get_or_create_index(self):
        from ai_code_engineer.repo_scanner import get_or_create_index, load_index
        # Initially no index file
        self.assertIsNone(load_index(self.root))

        # get_or_create_index creates it
        idx = get_or_create_index(self.root)
        self.assertIsNotNone(idx)
        self.assertTrue((self.root / "project-index.json").exists())

        # load_index now returns it
        loaded = load_index(self.root)
        self.assertIsNotNone(loaded)
        self.assertEqual(len(loaded.controllers), len(idx.controllers))

    def test_index_boost_elevates_relevant_layer(self):
        from ai_code_engineer.repo_scanner import index_boost
        from ai_code_engineer.workspace import Workspace

        idx = self.scanner.scan()
        ws = Workspace(self.root)
        _, rows = ws.index()

        # Task mentioning login / endpoint should prioritize LoginController
        results = index_boost(rows, "Add input validation to login endpoint", idx, limit=3)
        self.assertTrue(len(results) > 0)
        top_paths = [r["path"] for r in results]
        self.assertTrue(any("LoginController" in p for p in top_paths))
        # Top entry should have layer metadata
        self.assertIn("layer", results[0])


class IndexFreshnessTests(unittest.TestCase):
    """An index is a picture of a tree, so it stays true only while that tree is the one on disk.

    `get_or_create_index` used to answer from `project-index.json` forever once it existed, so a
    controller renamed or deleted after the first scan kept being offered as the place to put a
    change, with the confidence of an index. The stamp is path + size + `st_mtime_ns` over the files a
    scan would read, and reading them is what the check avoids: a guard that costs as much as the work
    it skips is a guard that gets skipped.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        _make_spring_project(self.root)
        self.addCleanup(self._tmp.cleanup)
        self.index_file = self.root / "project-index.json"

    def index(self):
        from ai_code_engineer.repo_scanner import get_or_create_index
        return get_or_create_index(self.root)

    def test_an_unchanged_tree_answers_from_the_file_it_already_wrote(self):
        first = self.index()
        self.assertTrue(self.index_file.is_file())
        self.assertEqual(self.index().generated_at, first.generated_at)

    def test_the_index_is_not_one_of_the_files_it_stamps(self):
        # `save()` writes inside the folder it describes. Counted, every saved index would look stale
        # the moment it was written, and the scan would run again on every turn for ever.
        from ai_code_engineer.repo_scanner import fingerprint
        index = self.index()
        self.assertNotIn("project-index.json", _walk_files(self.root))
        self.assertEqual(fingerprint(self.root), index.fingerprint)

    def test_a_file_added_after_the_scan_forces_a_new_one(self):
        first = self.index()
        (self.root / "src/main/java/com/example/auth/web/PaymentsController.java").write_text(
            "package com.example.web;\nimport org.springframework.web.bind.annotation.*;\n"
            '@RestController\n@RequestMapping("/api")\npublic class PaymentsController {\n'
            '    @PostMapping("/charge") public String charge() { return ""; }\n}\n',
            encoding="utf-8")
        fresh = self.index()
        self.assertNotEqual(fresh.generated_at, first.generated_at)
        routes = [route["path"] for entry in fresh.controllers for route in entry["endpoints"]]
        self.assertIn("/api/charge", routes)

    def test_an_edit_that_only_changes_the_routes_is_seen(self):
        first = self.index()
        controller = self.root / "src/main/java/com/example/auth/web/LoginController.java"
        controller.write_text(controller.read_text(encoding="utf-8")
                              .replace('"/login"', '"/signin"'), encoding="utf-8")
        self.assertEqual([r["path"] for e in self.index().controllers
                          for r in e["endpoints"]], ["/signin"])

    def test_an_edit_that_keeps_the_length_is_still_seen(self):
        # `@PostMapping` and `@DeleteMapping` are the same length, so a size-only stamp would call this
        # tree unchanged and keep answering with the route that is no longer there.
        first = self.index()
        controller = self.root / "src/main/java/com/example/auth/web/LoginController.java"
        controller.write_text(controller.read_text(encoding="utf-8")
                             .replace("@PostMapping", "@DeleteMapping"), encoding="utf-8")
        self.assertEqual([r["verb"] for e in self.index().controllers
                          for r in e["endpoints"]], ["DELETE"])

    def test_a_file_removed_after_the_scan_is_gone_from_the_index(self):
        self.index()
        (self.root / "src/main/java/com/example/auth/web/LoginController.java").unlink()
        paths = [entry["path"] for entry in self.index().controllers]
        self.assertFalse(any("LoginController" in p for p in paths), paths)

    def test_an_index_written_by_an_older_build_is_scanned_once(self):
        # An upgrade has no stamp to compare against, so the first call rebuilds and the file it writes
        # carries one: after that the tree is believed again, which is the whole cost of upgrading.
        from ai_code_engineer.repo_scanner import fingerprint, load_index
        self.index()
        data = json.loads(self.index_file.read_text(encoding="utf-8"))
        data.pop("fingerprint")
        self.index_file.write_text(json.dumps(data), encoding="utf-8")
        self.assertEqual(load_index(self.root).fingerprint, "")
        self.assertEqual(self.index().fingerprint, fingerprint(self.root))

    def test_a_scan_that_fails_keeps_the_stale_index_rather_than_nothing(self):
        # The tree has changed, so a rebuild is due, and the rebuild is what fails. An out-of-date map
        # still beats no map: it says when it was written, and the loop keeps its file selection.
        from unittest.mock import patch
        from ai_code_engineer.repo_scanner import get_or_create_index
        first = self.index()
        (self.root / "note.md").write_text("# changed after the scan\n", encoding="utf-8")
        with patch("ai_code_engineer.repo_scanner.RepoScanner.scan",
                   side_effect=OSError("disk unreadable")):
            kept = get_or_create_index(self.root)
        self.assertIsNotNone(kept)
        self.assertEqual(kept.generated_at, first.generated_at)


if __name__ == "__main__":
    unittest.main()
