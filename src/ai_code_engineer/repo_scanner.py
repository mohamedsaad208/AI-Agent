"""Repository Scanner: build a structured project index from source code.

Reads the workspace, parses every indexable file through the existing symbol
engine, then classifies each type by its Spring / JVM annotation layer so the
Agent can say "LoginController calls LoginService which calls CustomerRepository"
before it reads a single file.

Output — project-index.json — is a snapshot saved beside the workspace's own
.agent-runs folder.  It is advisory: the bytes on disk are always the authority,
and the index is regenerated on demand.  Nothing here writes source files.

Layered taxonomy — Spring / JVM / Jakarta EE / Quarkus / Micronaut:

  Controllers  — @RestController, @Controller, @FeignClient, @GrpcService,
                 @GraphQlController, JAX-RS @Path, *Controller, *Resource …
  Services     — @Service, @Transactional, @Singleton (Micronaut/Jakarta),
                 @ApplicationScoped (Quarkus), *Service, *Manager, *UseCase …
  Repositories — @Repository, @Mapper (MyBatis), @Dao,
                 *Repository, *Dao, *Store …
  Entities     — @Entity (JPA), @Document (MongoDB), @RedisHash, @Node (Neo4j),
                 @MappedSuperclass, @Embeddable …
  DTOs         — @JsonIgnoreProperties, @Schema, @Value (Lombok),
                 *Request, *Response, *Dto, *Payload, *ViewModel …
  Security     — @EnableWebSecurity, @EnableMethodSecurity, @KeycloakConfiguration,
                 JwtProvider, UserDetailsService … classes …
  Configs      — @Configuration, @SpringBootApplication, @Bean,
                 @EnableJpaRepositories, @EnableFeignClients, Spring Cloud … …
  Messaging    — @KafkaListener, @RabbitListener, @JmsListener, @SqsListener,
                 @EventListener, @ServiceActivator (Spring Integration) …
  Aspects      — @Aspect, @Around, @Before, @After, *Interceptor, *Filter …
  Tests        — @SpringBootTest, @WebMvcTest, @DataJpaTest, *Test, *Spec …
  Utils        — *Util, *Helper, *Converter, *Validator, *Mapper (non-DAO) …
  Components   — @Component, @Scheduled, @Async, @Cacheable … (catch-all)
  Build files  — pom.xml, build.gradle, build.gradle.kts …
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import ignore, symbols
from .errors import PolicyError


# ---------------------------------------------------------------------------
# Annotation taxonomy
#
# Keys are architectural layer names.  Values are every Spring / JVM / Jakarta /
# Quarkus / Micronaut / third-party annotation *simple name* (no package prefix)
# that reliably signals that layer.  Matched against blanked source so comments
# and string literals never produce false positives.
# ---------------------------------------------------------------------------

_ANNOTATION = re.compile(r"@([\w.]+)")

_LAYER_ANNOTATIONS: dict[str, list[str]] = {

    # ── Web / REST / RPC layer ──────────────────────────────────────────────
    "controller": [
        # Spring MVC / WebFlux
        "RestController", "Controller",
        "RestControllerAdvice", "ControllerAdvice",
        # Spring HATEOAS
        "RepositoryRestController",
        # Feign (microservice HTTP clients)
        "FeignClient",
        # JAX-RS — Jakarta EE / Quarkus / Micronaut
        "Path",                         # class-level @Path marks a resource
        # GraphQL — Spring for GraphQL
        "GraphQlController", "MutationMapping", "QueryMapping",
        "SchemaMapping", "BatchMapping",
        # gRPC — grpc-spring-boot-starter / quarkus-grpc
        "GrpcService", "GrpcClient",
        # Swagger / OpenAPI (some teams annotate only with these)
        "Api", "Tag",
        # Micronaut HTTP server (explicit annotation, not CDI scope)
        "Controller",                   # duplicate intentional: same name
    ],

    # ── Service / Business-logic layer ─────────────────────────────────────
    "service": [
        # Spring core
        "Service",
        # A class annotated only @Transactional at class level = business logic
        "Transactional",
        # Spring Retry / Resilience4J fault-tolerance
        "CircuitBreaker", "Retry", "RateLimiter", "Bulkhead", "TimeLimiter",
        # Spring Batch scopes
        "JobScope", "StepScope",
        # Micronaut CDI
        "Singleton",
        # Quarkus / Jakarta CDI
        "ApplicationScoped", "RequestScoped", "SessionScoped",
        # Jakarta EJB (legacy enterprise Java)
        "Stateless", "Stateful",
        # DDD markers used by some teams
        "UseCase", "Interactor",
    ],

    # ── Repository / Data-access layer ─────────────────────────────────────
    "repository": [
        # Spring Data
        "Repository", "RepositoryRestResource", "NoRepositoryBean",
        # MyBatis mapper interfaces
        "Mapper",
        # Legacy DAO marker
        "Dao",
    ],

    # ── Entity / Domain-model layer ────────────────────────────────────────
    "entity": [
        # JPA / Jakarta Persistence
        "Entity", "MappedSuperclass", "Embeddable", "IdClass",
        # Spring Data MongoDB
        "Document",
        # Spring Data Redis
        "RedisHash",
        # Spring Data Elasticsearch
        "Indexed",
        # Spring Data Neo4j 6+
        "Node", "RelationshipProperties",
        # Spring Data Cassandra
        "PrimaryKeyClass",
        # Hibernate-specific extras
        "NaturalId", "Formula",
        # Spring Data JDBC (aggregate root — no special annotation, caught by name)
    ],

    # ── DTO / Payload / Transfer-object layer ──────────────────────────────
    # Detected primarily by name patterns.  Annotations here are soft hints
    # (they alone are insufficient; a service class can also have @Schema).
    "dto": [
        # Jackson annotations (very common on DTO classes)
        "JsonIgnoreProperties", "JsonInclude", "JsonNaming",
        "JsonRootName", "JsonSubTypes", "JsonTypeInfo",
        # Swagger / OpenAPI schema
        "Schema",
        # Lombok immutable / builder DTOs
        "Value",        # @Value → immutable DTO
        "Builder",      # @Builder → often used on DTO/command classes
        # Jakarta validation (class-level — rare but used on record DTOs)
        "Valid", "Validated",
    ],

    # ── Security / Auth layer ───────────────────────────────────────────────
    "security": [
        # Spring Security
        "EnableWebSecurity", "EnableMethodSecurity",
        "EnableGlobalMethodSecurity", "EnableWebFluxSecurity",
        # Spring OAuth2 / Authorization Server
        "EnableOAuth2Sso", "EnableResourceServer", "EnableAuthorizationServer",
        "EnableOAuth2Client",
        # Spring Authorization Server 1.x
        "EnableAuthorizationServer",
        # Keycloak Spring adapter
        "KeycloakConfiguration", "KeycloakSecurityComponents",
        # AWS Cognito / Spring Security OAuth extensions
        "EnableJwtDecoder",
    ],

    # ── Configuration / Infrastructure-wiring layer ─────────────────────────
    "config": [
        # Spring core
        "Configuration", "ConfigurationProperties", "SpringBootApplication",
        "EnableAutoConfiguration", "ComponentScan",
        # Individual bean factory methods live inside a @Configuration class,
        # so @Bean alone on a class is a soft signal.
        "Bean",
        # Spring Cloud Service Discovery
        "EnableDiscoveryClient", "EnableEurekaClient", "EnableConsulClient",
        "EnableNacosDiscovery",
        # Spring Cloud OpenFeign
        "EnableFeignClients",
        # Spring Cloud Gateway / Zuul
        "EnableGateway", "EnableZuulProxy",
        # Spring Cloud Config Server
        "EnableConfigServer",
        # Circuit-breaker activation
        "EnableCircuitBreaker", "EnableHystrix",
        # Spring Scheduling / Caching / Async activation
        "EnableScheduling", "EnableCaching", "EnableAsync",
        # Spring Data repository activation
        "EnableJpaRepositories", "EnableMongoRepositories",
        "EnableCassandraRepositories", "EnableRedisRepositories",
        "EnableElasticsearchRepositories", "EnableNeo4jRepositories",
        "EnableLdapRepositories",
        # Spring AMQP / Kafka activation
        "EnableRabbit", "EnableKafka",
        # Spring WebSocket
        "EnableWebSocket", "EnableWebSocketMessageBroker",
        # Spring LDAP / OAuth
        "EnableWebSecurity",        # also security, but @Configuration wins scope
        # Conditional Spring Boot autoconfiguration
        "ConditionalOnProperty", "ConditionalOnClass",
        "ConditionalOnMissingBean", "ConditionalOnBean",
        "AutoConfiguration",
        # Jakarta / Servlet context config
        "ApplicationPath", "WebServlet", "WebFilter", "WebListener",
    ],

    # ── Messaging / Event-driven / Integration layer ────────────────────────
    "messaging": [
        # Spring AMQP / RabbitMQ
        "RabbitListener", "RabbitHandler",
        # Spring Kafka
        "KafkaListener", "KafkaHandler", "RetryableTopic",
        # Spring JMS / ActiveMQ / Artemis
        "JmsListener",
        # Spring Events (application events)
        "EventListener", "TransactionalEventListener",
        # Spring Integration
        "ServiceActivator", "MessageEndpoint", "InboundChannelAdapter",
        "Transformer", "Filter", "Router", "Splitter", "Aggregator",
        # Spring WebSocket messaging
        "MessageMapping", "SubscribeMapping",
        # Spring Cloud Stream (legacy)
        "StreamListener", "Input", "Output",
        # Spring Cloud Function
        "FunctionConfiguration",
        # Jakarta MDB (legacy enterprise messaging)
        "MessageDriven",
        # AWS SQS / SNS — Spring Cloud AWS 3.x
        "SqsListener", "SnsTopicListenerScanner",
        # Azure Service Bus — Spring Cloud Azure
        "ServiceBusListener",
        # Google Cloud Pub/Sub
        "GcpPubSubSubscriber",
        # Apache Pulsar — Spring for Apache Pulsar
        "PulsarListener",
    ],

    # ── AOP / Cross-cutting-concerns layer ─────────────────────────────────
    "aspect": [
        # Spring AOP / AspectJ
        "Aspect", "EnableAspectJAutoProxy",
        # Advice types (class-level is unusual but possible with compound aspects)
        "Around", "Before", "After", "AfterReturning", "AfterThrowing",
        "Pointcut",
        # Jakarta / CDI interceptor
        "Interceptor", "InterceptorBinding",
    ],

    # ── General component (catch-all) ───────────────────────────────────────
    "component": [
        # Spring catch-all stereotype
        "Component",
        # Background / reactive tasks
        "Scheduled", "Async",
        # Validation
        "Validated",
        # Caching
        "Cacheable", "CacheEvict", "CachePut",
        # Spring Shell CLI commands
        "ShellComponent",
        # Jakarta CDI
        "Dependent", "ConversationScoped",
    ],
}

# ── Annotation-classification priority ─────────────────────────────────────
# More specific / higher-risk layers come first.
# "component" is last-resort before name patterns.
_LAYER_PRIORITY = (
    "security",     # security config is never accidentally a service
    "messaging",    # listener classes have a distinct role
    "aspect",       # AOP classes are clearly cross-cutting
    "controller",   # REST / RPC endpoint
    "service",      # business logic
    "repository",   # data access
    "entity",       # persistence model
    "dto",          # data transfer (soft signals only)
    "config",       # infrastructure wiring
    "component",    # general Spring-managed bean
)

# Build the reverse lookup once: annotation name (lowercase) → layer
_ANNOTATION_TO_LAYER: dict[str, str] = {}
for _layer in _LAYER_PRIORITY:
    for _name in _LAYER_ANNOTATIONS.get(_layer, []):
        _ANNOTATION_TO_LAYER.setdefault(_name.casefold(), _layer)


# ---------------------------------------------------------------------------
# Name-pattern taxonomy
#
# Applied when no annotation gives an unambiguous classification.
# Each tuple: (compiled regex matched against lower-cased simple class name,
#              layer string).
# Order matters — first match wins.
# ---------------------------------------------------------------------------

_NAME_PATTERNS: list[tuple[re.Pattern[str], str]] = [

    # ── Tests ───────────────────────────────────────────────────────────────
    (re.compile(
        r"(test|tests|testcase|spec|specification|it|integrationtest|integrationspec"
        r"|fixture|stub|mock|fake|double|dummydata)$"
    ), "test"),

    # ── Security ───────────────────────────────────────────────────────────
    # Match FULL class names that are known security infrastructure types.
    # JwtFilter → aspect (it's a Servlet filter, not a security config class)
    # JwtUtil   → util   (it's a utility helper)
    (re.compile(
        r"^(securityconfig|securityconfiguration|securityfilterchain"
        r"|securitywebfilterchain|securityfilter"
        r"|authenticationprovider|authenticationmanager|authorizationmanager"
        r"|accessdecisionmanager"
        r"|jwtprovider|jwtservice"
        r"|jwtauthenticationfilter|jwtauthorizationfilter"
        r"|tokenprovider|tokenservice|tokenvalidator"
        r"|bearertokenfilter|oauthfilter|oauth2filter"
        r"|userdetailsservice|userdetailsserviceimpl"
        r"|userauthentication|customuserdetails"
        r"|resourceserverconfig|authorizationserverconfig"
        r"|passwordencoder|corsconfig|corsfilter)$"
    ), "security"),

    # ── Controllers / REST endpoints ────────────────────────────────────────
    (re.compile(
        r"(controller|restcontroller|resource|restresource"
        r"|endpoint|restapi|apiv1|apiv2|apiv3"
        r"|handler|requesthandler|httphandler|webhandler"
        r"|facade|gateway|graphqlcontroller|grpcservice"
        r"|servlet|httpservlet)$"
    ), "controller"),

    # ── Services / Business logic ───────────────────────────────────────────
    (re.compile(
        r"(service|serviceimpl|servicefacade"
        r"|manager|managerfacade|managerimpl"
        r"|processor|processorimpl"
        r"|orchestrator|coordinator|mediator"
        r"|usecase|interactor|commandhandler|queryhandler|eventhandler"
        r"|applicationservice|domainservice|businesslogic"
        r"|workflow|workflowservice|pipelineservice"
        r"|strategy|strategyimpl|policy|policyimpl"
        r"|saga|sagahandler|sagamanager)$"
    ), "service"),

    # ── Repositories / DAOs ─────────────────────────────────────────────────
    (re.compile(
        r"(repository|repositoryimpl|jparepository|mongorepository"
        r"|redisrepository|elasticsearchrepository|neo4jrepository"
        r"|dao|daoimpl|dataaccessobject"
        r"|store|datastore|storageadapter|persistenceadapter"
        r"|rowmapper|resultsetextractor|jdbctemplate)$"
    ), "repository"),

    # ── DTOs / Value objects / Payloads ─────────────────────────────────────
    (re.compile(
        r"(dto|requestdto|responsedto|requestbody|responsebody"
        r"|request|response|payload|body|form|formdata"
        r"|command|commandresult|commandresponse"
        r"|query|queryresult|queryresponse"
        r"|event|eventsourcing"
        r"|message|messagedata"
        r"|transferobject|valueobject"
        r"|viewmodel|vm|view"
        r"|projection|summary|detail|info"
        r"|representation|resource|record"
        r"|input|output|result|error|fault)$"
    ), "dto"),

    # ── Entities / Persistence models ───────────────────────────────────────
    (re.compile(
        r"(entity|persistententity|persistenceentity"
        r"|model|domainmodel|domainentity|domainobject"
        r"|document|mongodocument"
        r"|aggregate|aggregateroot"
        r"|embeddable|embedddable|valueobjectembeddable"
        r"|node|graphnode)$"
    ), "entity"),

    # ── Messaging / Event-driven ────────────────────────────────────────────
    (re.compile(
        r"(listener|eventlistener|messaginglistener"
        r"|consumer|kafkaconsumer|rabbitconsumer|jmsconsumer|sqsconsumer"
        r"|producer|kafkaproducer|rabbitproducer|messagingproducer"
        r"|publisher|eventpublisher|messagepublisher"
        r"|subscriber|eventsubscriber"
        r"|handler|messagehandler|eventhandlerbean"
        r"|dispatcher|eventdispatcher|messagedispatcher"
        r"|broker|messagebroker|eventbroker)$"
    ), "messaging"),

    # ── Configs / Infrastructure ─────────────────────────────────────────────
    (re.compile(
        r"(config|configuration|appconfig|applicationconfig"
        r"|properties|appproperties|applicationproperties"
        r"|settings|appsettings|systemsettings"
        r"|beanconfig|beanfactory"
        r"|datasourceconfig|databaseconfig|dbconfig"
        r"|swaggerconfig|openapiconfig|apiconfig"
        r"|kafkaconfig|rabbitconfig|activemqconfig"
        r"|redisconfig|mongoconfig|elasticsearchconfig|neo4jconfig"
        r"|cacheconfig|cachingconfig"
        r"|schedulerconfig|asyncconfig"
        r"|corsconfig|webconfigurer"
        r"|jpaconfig|hibernateconfig"
        r"|securityconfig)$"          # also caught by security, priority resolves it
    ), "config"),

    # ── AOP / Filters / Interceptors ────────────────────────────────────────
    (re.compile(
        r"(aspect|aopaspect"
        r"|interceptor|httpinterceptor|requestinterceptor|responseinterceptor"
        r"|filter|httpfilter|requestfilter|responsefilter|onceperrequestfilter"
        r"|middleware|decorator"
        r"|exceptionhandler|globalexceptionhandler|errorhandler)$"
    ), "aspect"),

    # ── Utilities / Helpers ─────────────────────────────────────────────────
    (re.compile(
        r"(util|utils|utility|utilities"
        r"|helper|helpers|support|supports"
        r"|converter|typeconverter|modelconverter"
        r"|transformer|datatransformer"
        r"|mapper|modelmapper|beenmapper|objectmapper"
        r"|validator|inputvalidator|fieldvalidator|validatorimpl"
        r"|serializer|deserializer"
        r"|formatter|dateformatter|numberformatter"
        r"|parser|requestparser|responseparser"
        r"|builder|objectbuilder|querybuilder"
        r"|factory|beanfactory|objectfactory"
        r"|provider|valueprovider|dataprovider"
        r"|resolver|pathresolver|uriresolver"
        r"|reader|filewriter|filereader|streamreader"
        r"|codec|encoder|decoder"
        r"|calculator|aggregator)$"
    ), "util"),
]


def _classify_source(row: dict, source: str) -> str:
    """Return the architectural layer for this file.

    Resolution order (first match wins):
    1. Annotation scan on *blanked* source  (comments / strings stripped)
    2. Class / interface / record name suffix patterns
    3. File path heuristic  (src/test/ → test)
    4. Package presence     (has JVM package → component)
    5. "other"              (no recognisable structure)
    """
    # ── 1. Annotation scan ──────────────────────────────────────────────────
    blanked = symbols.blank(source)
    found_annotations = {m.group(1).casefold() for m in _ANNOTATION.finditer(blanked)}

    for layer in _LAYER_PRIORITY:
        expected = {name.casefold() for name in _LAYER_ANNOTATIONS.get(layer, [])}
        if found_annotations & expected:
            return layer

    # ── 2. Class name suffix patterns ───────────────────────────────────────
    for item in row.get("types", []):
        simple = item["name"].rsplit(".", 1)[-1]
        lower = simple.casefold()
        for pattern, layer in _NAME_PATTERNS:
            if pattern.search(lower):
                return layer

    # ── 3. File path heuristic ───────────────────────────────────────────────
    path = row.get("path", "").replace("\\", "/")
    if "/test/" in path or "/tests/" in path \
            or path.endswith("Test.java") or path.endswith("Tests.java") \
            or path.endswith("Spec.kt") or path.endswith("IT.java"):
        return "test"

    # ── 4. Package presence ──────────────────────────────────────────────────
    if row.get("package"):
        return "component"

    return "other"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class TypeEntry:
    name: str
    kind: str          # class | interface | enum | record | object | annotation
    package: str
    file: str
    members: list[str] = field(default_factory=list)
    extends: list[str] = field(default_factory=list)
    line: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FileEntry:
    path: str
    layer: str         # controller | service | repository | entity | dto |
                       # security | config | messaging | aspect | test |
                       # util | component | other
    package: str
    module: str        # top-level directory (e.g. auth-service), or "."
    types: list[TypeEntry] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    endpoints: list[dict[str, Any]] = field(default_factory=list)
    # True when the parser refused a route at its per-file ceiling. An impact answer built on a route
    # list that stopped has to be able to say "I did not see every URL", so the cap travels with the
    # data rather than being re-derived from its length.
    routes_capped: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "layer": self.layer,
            "package": self.package,
            "module": self.module,
            "types": [t.to_dict() for t in self.types],
            "depends_on": self.depends_on,
            "endpoints": self.endpoints,
            "routes_capped": self.routes_capped,
        }


# All layer keys that appear as buckets in ProjectIndex
_ALL_LAYERS = (
    "controller", "service", "repository", "entity", "dto",
    "security", "config", "messaging", "aspect", "test",
    "util", "component",
)


@dataclass
class ProjectIndex:
    root: str
    generated_at: str
    modules: list[str]
    controllers: list[dict[str, Any]]
    services: list[dict[str, Any]]
    repositories: list[dict[str, Any]]
    entities: list[dict[str, Any]]
    dtos: list[dict[str, Any]]
    security: list[dict[str, Any]]
    configs: list[dict[str, Any]]
    messaging: list[dict[str, Any]]
    aspects: list[dict[str, Any]]
    tests: list[dict[str, Any]]
    utils: list[dict[str, Any]]
    components: list[dict[str, Any]]
    build_files: list[dict[str, Any]]
    dependency_graph: dict[str, list[str]]
    stats: dict[str, int]
    # Which tree this index describes. An index is only a picture of the files that were there when
    # it was written, so it is stored with the stamp those files made and re-checked before it is
    # trusted again -- see `get_or_create_index`.
    fingerprint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "generated_at": self.generated_at,
            "modules": self.modules,
            "controllers": self.controllers,
            "services": self.services,
            "repositories": self.repositories,
            "entities": self.entities,
            "dtos": self.dtos,
            "security": self.security,
            "configs": self.configs,
            "messaging": self.messaging,
            "aspects": self.aspects,
            "tests": self.tests,
            "utils": self.utils,
            "components": self.components,
            "build_files": self.build_files,
            "dependency_graph": self.dependency_graph,
            "stats": self.stats,
            "fingerprint": self.fingerprint,
        }


# ---------------------------------------------------------------------------
# File walking helpers
# ---------------------------------------------------------------------------

MAX_FILE_BYTES = 128 * 1024
# The scanner's own output, written inside the folder it describes. It is never indexed, and never
# stamped: an index that counted itself would look stale the moment it was saved and re-scan on every
# turn, which is the one loop this file must not create.
INDEX_NAME = "project-index.json"
_TEXT_SUFFIXES = {
    ".py", ".java", ".kt", ".kts", ".xml", ".gradle", ".md",
    ".json", ".yaml", ".yml", ".toml", ".properties", ".sql",
    ".js", ".ts", ".tsx", ".jsx", ".mjs", ".cjs", ".go", ".rs",
}


def _walk_files(root: Path) -> list[str]:
    """Walk the project root and return relative POSIX paths of readable files.

    Re-uses the same ignore rules as the Workspace so the scanner sees exactly
    the same set of files as the planning loop.
    """
    found: list[str] = []
    root_rules = ignore.Rules.load(root)
    in_force: dict[str, Any] = {"": root_rules}

    for base, dirs, names in os.walk(root, followlinks=False):
        walked = Path(base).relative_to(root).as_posix()
        own = "" if walked == "." else walked
        parent_key = own.rsplit("/", 1)[0] if "/" in own else ""
        rules = in_force.get(parent_key, root_rules)
        prefix = own + "/" if own else ""

        if ".gitignore" in names and own:
            try:
                child_rules = rules.child(
                    (Path(base) / ".gitignore").read_text(encoding="utf-8", errors="replace")
                )
                rules = child_rules
            except OSError:
                pass
        in_force[own] = rules

        kept, _hidden, _skipped = ignore.walk_prune(
            [d for d in dirs if not _is_link(Path(base) / d)], rules
        )
        dirs[:] = kept

        for name in sorted(names):
            relative = prefix + name
            if relative == INDEX_NAME:
                continue
            suffix = Path(name).suffix.lower()
            if suffix not in _TEXT_SUFFIXES and name.casefold() not in {
                ".gitignore", "dockerfile", "makefile", "license", "readme"
            }:
                continue
            if rules and rules.ignores(relative, False):
                continue
            if ignore.generated_file(name):
                continue
            if ignore.refused_dir(name):
                continue
            full = Path(base) / name
            try:
                info = full.stat()
            except OSError:
                continue
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
                continue
            found.append(relative)
            if len(found) >= symbols.MAX_FILES:
                return found
    return found


def _is_link(path: Path) -> bool:
    try:
        info = path.lstat()
        return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)
    except OSError:
        return False


def _read_safe(root: Path, relative: str) -> str | None:
    try:
        raw = (root / relative.replace("/", os.sep)).read_bytes()
        if len(raw) > MAX_FILE_BYTES or b"\x00" in raw:
            return None
        return raw.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


# ---------------------------------------------------------------------------
# RepoScanner
# ---------------------------------------------------------------------------

def _stamp(root: Path, files: list[str]) -> str:
    """Path, size and modification time for every file a scan would read, digested.

    Nothing is opened: the check that decides whether a saved index is still true has to cost a small
    part of the scan it protects, or the loop it belongs to would be better off ignoring it. `st_mtime_ns`
    rather than seconds because a one-character edit that keeps the file the same length is otherwise
    invisible inside the second it happened in, and an invisible edit is a stale map that answers with
    confidence.
    """
    parts = []
    for relative in files:
        try:
            info = (root / relative.replace("/", os.sep)).stat()
        except OSError:
            parts.append(relative + "|gone")
            continue
        parts.append(relative + "|" + str(info.st_size) + "|" + str(info.st_mtime_ns))
    return hashlib.sha256("\n".join(sorted(parts)).encode("utf-8")).hexdigest()[:16]


def fingerprint(root: str | Path) -> str:
    """The stamp of the tree as it is right now."""
    base = Path(root)
    return _stamp(base, _walk_files(base))


class RepoScanner:
    """Scan a project directory and produce a structured ProjectIndex.

    Usage::

        scanner = RepoScanner(root="/path/to/spring-project")
        index   = scanner.scan()
        scanner.save(index)            # writes project-index.json at project root
        print(scanner.summary(index))  # human-readable summary
    """

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise PolicyError("RepoScanner root must be an existing directory.")

    # ── Public API ──────────────────────────────────────────────────────────

    def scan(self) -> ProjectIndex:
        """Walk the repository and return a fully classified ProjectIndex."""
        files = _walk_files(self.root)
        source_rows: list[dict] = []
        config_rows: list[dict] = []
        sources_by_path: dict[str, str] = {}

        for relative in files:
            text = _read_safe(self.root, relative)
            if text is None:
                continue
            if symbols.indexable(relative):
                row = symbols.parse(relative, text)
                if row:
                    source_rows.append(row)
                    sources_by_path[relative] = text
            elif symbols.noteworthy(relative):
                crow = symbols.config_row(relative, text)
                if crow:
                    config_rows.append(crow)

        dep_map = symbols.dependencies(source_rows)
        modules = self._detect_modules(files)

        # Classify every source file into its architectural layer
        buckets: dict[str, list[dict[str, Any]]] = {layer: [] for layer in _ALL_LAYERS}
        for row in source_rows:
            text = sources_by_path.get(row["path"], "")
            layer = _classify_source(row, text)
            entry = self._build_file_entry(row, layer, dep_map)
            # Unknown layer → component (safety net)
            buckets.get(layer, buckets["component"]).append(entry.to_dict())

        build_files = [
            {"path": row["path"], "facts": row.get("facts", [])}
            for row in config_rows
        ]
        dep_graph = self._build_named_dep_graph(source_rows, dep_map)

        # Map layer names to their stats key (correct plurals)
        _LAYER_STAT_KEY = {
            "controller": "controllers",
            "service":    "services",
            "repository": "repositories",
            "entity":     "entities",
            "dto":        "dtos",
            "security":   "security",
            "config":     "configs",
            "messaging":  "messaging",
            "aspect":     "aspects",
            "test":       "tests",
            "util":       "utils",
            "component":  "components",
        }
        stats: dict[str, int] = {
            "total_files":   len(files),
            "indexed_files": len(source_rows),
            "modules":       len(modules),
            "build_files":   len(build_files),
        }
        for layer, stat_key in _LAYER_STAT_KEY.items():
            stats[stat_key] = len(buckets.get(layer, []))
        # Counted across every bucket, because the first question about a route map is whether this
        # project answers HTTP at all.
        stats["endpoints"] = sum(len(entry.get("endpoints") or [])
                                 for bucket in buckets.values() for entry in bucket)

        return ProjectIndex(
            root=str(self.root),
            generated_at=datetime.now(timezone.utc).isoformat(),
            modules=modules,
            controllers=buckets["controller"],
            services=buckets["service"],
            repositories=buckets["repository"],
            entities=buckets["entity"],
            dtos=buckets["dto"],
            security=buckets["security"],
            configs=buckets["config"],
            messaging=buckets["messaging"],
            aspects=buckets["aspect"],
            tests=buckets["test"],
            utils=buckets["util"],
            components=buckets["component"],
            build_files=build_files,
            dependency_graph=dep_graph,
            stats=stats,
            fingerprint=_stamp(self.root, files),
        )

    def save(self, index: ProjectIndex, output_path: str | Path | None = None) -> Path:
        """Persist the index as ``project-index.json`` (default: project root)."""
        dest = Path(output_path) if output_path else self.root / "project-index.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(
            json.dumps(index.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return dest

    def summary(self, index: ProjectIndex) -> str:
        """Human-readable one-page summary of the scanned project."""
        s = index.stats
        width = 14
        lines = [
            f"Project : {self.root.name}",
            f"Modules : {', '.join(index.modules) or '(root)'}",
            f"Indexed : {s['indexed_files']} / {s['total_files']} files",
            "",
        ]
        layer_labels = [
            ("Controllers",  "controllers"),
            ("Services",     "services"),
            ("Repositories", "repositorys"),    # normalised below
            ("Entities",     "entitys"),
            ("DTOs",         "dtos"),
            ("Security",     "securitys"),
            ("Configs",      "configs"),
            ("Messaging",    "messagings"),
            ("Aspects",      "aspects"),
            ("Tests",        "tests"),
            ("Utils",        "utils"),
            ("Components",   "components"),
            ("Build files",  "build_files"),
            ("Endpoints",    "endpoints"),
        ]
        for label, key in layer_labels:
            # normalise plural keys that don't follow the simple +s rule
            normalized = {
                "repositorys": "repositorys",   # stored as repository + s
                "entitys": "entitys",
                "securitys": "securitys",
                "messagings": "messagings",
            }.get(key, key)
            count = s.get(key, s.get(normalized, 0))
            lines.append(f"  {label:<{width}}: {count}")

        lines += ["", "Key dependency chains:"]
        chains = self._find_chains(index)
        if chains:
            for chain in chains[:8]:
                lines.append("  " + " \u2192 ".join(chain))
        else:
            lines.append("  (none detected)")
        return "\n".join(lines)

    # ── Private helpers ─────────────────────────────────────────────────────

    def _detect_modules(self, files: list[str]) -> list[str]:
        """Top-level Maven/Gradle module names (any pom.xml / build.gradle two+ levels deep)."""
        module_dirs: set[str] = set()
        for f in files:
            parts = f.split("/")
            if len(parts) >= 3 and parts[-1] in ("pom.xml", "build.gradle", "build.gradle.kts"):
                module_dirs.add(parts[0])
        return sorted(module_dirs) if module_dirs else ["."]

    def _build_file_entry(
        self,
        row: dict,
        layer: str,
        dep_map: dict[str, list[str]],
    ) -> FileEntry:
        types = [
            TypeEntry(
                name=item["name"],
                kind=item["kind"],
                package=row.get("package", ""),
                file=row["path"],
                members=list(item.get("members", [])),
                extends=list(item.get("extends", [])),
                line=item.get("line", 0),
            )
            for item in row.get("types", [])
        ]
        return FileEntry(
            path=row["path"],
            layer=layer,
            package=row.get("package", ""),
            module=symbols.module_of(row["path"]),
            types=types,
            depends_on=dep_map.get(row["path"], []),
            endpoints=list(row.get("endpoints") or []),
            routes_capped=bool(row.get("routes_capped")),
        )

    def _build_named_dep_graph(
        self,
        rows: list[dict],
        dep_map: dict[str, list[str]],
    ) -> dict[str, list[str]]:
        """Type-name-level dependency graph: ``{'LoginController': ['LoginService']}``."""
        path_to_type: dict[str, str] = {}
        for row in rows:
            if row.get("types"):
                primary = next(
                    (item["name"] for item in row["types"] if "." not in item["name"]),
                    row["types"][0]["name"],
                )
                path_to_type[row["path"]] = primary

        graph: dict[str, list[str]] = {}
        for row in rows:
            src = path_to_type.get(row["path"])
            if not src:
                continue
            targets = [
                path_to_type[dep]
                for dep in dep_map.get(row["path"], [])
                if dep in path_to_type
            ]
            if targets:
                graph[src] = targets
        return graph

    def _find_chains(self, index: ProjectIndex) -> list[list[str]]:
        """Controller → Service → Repository (and shorter) call chains."""
        graph = index.dependency_graph

        def _names(bucket: list[dict]) -> set[str]:
            return {t["name"] for entry in bucket for t in entry.get("types", [])}

        ctrl_names = _names(index.controllers)
        svc_names  = _names(index.services)
        repo_names = _names(index.repositories)

        chains: list[list[str]] = []
        for ctrl in ctrl_names:
            for svc in graph.get(ctrl, []):
                if svc in svc_names:
                    repos = [r for r in graph.get(svc, []) if r in repo_names]
                    if repos:
                        for repo in repos:
                            chains.append([ctrl, svc, repo])
                    else:
                        chains.append([ctrl, svc])
        for svc in svc_names:
            for repo in graph.get(svc, []):
                if repo in repo_names and not any(svc in c for c in chains):
                    chains.append([svc, repo])
        return chains


# ---------------------------------------------------------------------------
# index_boost — used by engine.py to improve file selection
# ---------------------------------------------------------------------------

# Keywords in a task that signal which architectural layer is most relevant.
# Each tuple: (set of task keywords, [layer, ...] in priority order).
# Multiple layers can match (e.g. "login security" hits both controller+security).
_TASK_LAYER_HINTS: list[tuple[set[str], list[str]]] = [
    # Security / Auth keywords
    ({"login", "auth", "authentication", "authorization", "jwt", "token",
      "password", "credential", "oauth", "security", "permission", "role",
      "access", "secure", "encrypt", "decrypt", "hash", "session", "cookie"},
     ["security", "controller", "service"]),

    # API / Endpoint keywords
    ({"endpoint", "api", "rest", "http", "request", "response", "controller",
      "route", "mapping", "path", "url", "post", "get", "put", "delete",
      "patch", "graphql", "grpc", "feign", "client", "swagger", "openapi"},
     ["controller", "dto"]),

    # Database / Persistence keywords
    ({"database", "db", "query", "sql", "table", "column", "entity", "model",
      "repository", "dao", "persist", "save", "find", "fetch", "delete",
      "update", "insert", "jpa", "hibernate", "mongo", "redis", "elastic"},
     ["repository", "entity", "config"]),

    # Business logic keywords
    ({"service", "logic", "business", "process", "calculate", "validate",
      "workflow", "orchestrat", "usecase", "command", "handler", "manager"},
     ["service"]),

    # Messaging / Events keywords
    ({"kafka", "rabbit", "queue", "topic", "message", "event", "publish",
      "subscribe", "consume", "produce", "listen", "async", "jms", "sqs",
      "amqp", "broker", "stream"},
     ["messaging", "service"]),

    # Testing keywords
    ({"test", "spec", "mock", "stub", "fixture", "assert", "verify", "unit",
      "integration", "coverage"},
     ["test"]),

    # Config / Infrastructure keywords
    ({"config", "configuration", "property", "setting", "bean", "profile",
      "environment", "startup", "bootstrap", "datasource", "cache", "cors",
      "scheduler", "thread", "pool"},
     ["config"]),

    # DTO / Data Transfer keywords
    ({"dto", "payload", "body", "form", "transfer", "request", "response",
      "serialize", "deserialize", "json", "xml", "validation", "field"},
     ["dto"]),
]

# Boost applied to files whose layer matches the task intent.
# Files in a matching layer score higher → they appear earlier in the context.
_LAYER_BOOST = 3          # added to symbols.rank score for layer-matched files
_SECONDARY_BOOST = 1      # for secondary layers in the same hint group


def _task_layers(task: str) -> list[tuple[str, int]]:
    """Return (layer, boost) pairs inferred from task keywords.

    Example: "fix login validation" → [("security", 3), ("controller", 3), ("service", 1)]
    """
    words = {w.casefold() for w in re.findall(r"[A-Za-z]{3,}", task)}
    seen: dict[str, int] = {}
    for hint_words, layers in _TASK_LAYER_HINTS:
        if words & hint_words:
            for i, layer in enumerate(layers):
                boost = _LAYER_BOOST if i == 0 else _SECONDARY_BOOST
                if seen.get(layer, 0) < boost:
                    seen[layer] = boost
    return sorted(seen.items(), key=lambda x: -x[1])


def load_index(root: str | Path) -> ProjectIndex | None:
    """Load project-index.json from the project root, or return None if absent.

    Never raises — a missing or malformed index simply means the scanner has
    not been run yet; the caller degrades gracefully to the baseline ranker.
    """
    try:
        path = Path(root) / "project-index.json"
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return ProjectIndex(
            root=data.get("root", str(root)),
            generated_at=data.get("generated_at", ""),
            modules=data.get("modules", []),
            controllers=data.get("controllers", []),
            services=data.get("services", []),
            repositories=data.get("repositories", []),
            entities=data.get("entities", []),
            dtos=data.get("dtos", []),
            security=data.get("security", []),
            configs=data.get("configs", []),
            messaging=data.get("messaging", []),
            aspects=data.get("aspects", []),
            tests=data.get("tests", []),
            utils=data.get("utils", []),
            components=data.get("components", []),
            build_files=data.get("build_files", []),
            dependency_graph=data.get("dependency_graph", {}),
            stats=data.get("stats", {}),
            fingerprint=data.get("fingerprint", ""),
        )
    except Exception:   # noqa: BLE001 — never crash the planning loop
        return None


def get_or_create_index(root: str | Path) -> ProjectIndex | None:
    """Load `project-index.json`, and re-scan whenever the tree no longer matches it.

    An index written by an older version carries no stamp, so the first call after an upgrade scans
    once and the stamp is in the file from then on. A scan that fails leaves the stale index standing
    rather than returning nothing: an out-of-date map still beats no map, and it says when it was
    written.
    """
    existing = load_index(root)
    stamp = ""
    try:
        stamp = fingerprint(root)
    except Exception:   # noqa: BLE001 — an unreadable tree cannot prove the index false
        stamp = existing.fingerprint if existing else ""
    if existing is not None and existing.fingerprint == stamp:
        return existing
    try:
        scanner = RepoScanner(root)
        index = scanner.scan()
        scanner.save(index)
        return index
    except Exception:   # noqa: BLE001 — safety net
        return existing


# The build-file facts worth remembering between tasks: stated outright by the build, cheap to carry,
# and each one changes what code a later proposal has to be.
FACT_LABELS = ("java", "spring boot", "artifact", "modules", "needs")
BUILD_TOOL = {"pom.xml": "maven", "build.gradle": "gradle", "build.gradle.kts": "gradle",
              "settings.gradle": "gradle", "settings.gradle.kts": "gradle",
              "package.json": "npm", "go.mod": "go modules", "cargo.toml": "cargo",
              "pyproject.toml": "python"}


def project_facts(index: ProjectIndex) -> dict[str, str]:
    """What this project *is*, as its own build files state it.

    Read once and stored in the project notes, so the next task is not asked to re-derive a Java
    version from a `pom.xml` it is no longer shown. Only what a file says is kept: nothing here is
    guessed from a filename or from the source, and a project that states no version produces an
    empty answer, which is the honest one.
    """
    facts: dict[str, str] = {}
    for entry in index.build_files:
        name = str(entry.get("path", "")).replace("\\", "/").rsplit("/", 1)[-1].casefold()
        if name in BUILD_TOOL:
            facts.setdefault("build", BUILD_TOOL[name])
        for pair in entry.get("facts") or []:
            try:
                label, value = str(pair[0]), str(pair[1])
            except (TypeError, IndexError, KeyError):
                continue
            if label in FACT_LABELS and value.strip():
                facts.setdefault(label, value.strip()[:120])
    if len(index.modules) > 1 and "." not in index.modules:
        facts["modules"] = ", ".join(index.modules[:12])
    return dict(sorted(facts.items())[:8])


def index_boost(
    rows: list[dict],
    task: str,
    index: ProjectIndex,
    limit: int = 5,
) -> list[dict]:
    """Re-rank symbol rows using architectural intent from the project index.

    Wraps ``symbols.rank()`` and adds a layer-based score on top.  Files whose
    architectural layer matches what the task is asking about are pushed to the
    front; everything else falls back to the baseline score unchanged.

    Returns a list of rank-entry dicts in the same format as ``symbols.rank()``,
    with an extra ``"layer"`` field so the caller can log it.

    If the index contains no relevant information (empty project, no layers
    matched) the function falls back to plain ``symbols.rank()`` so the
    planning loop is never worse than before.
    """
    from . import symbols as _sym   # avoid circular import at module level

    # 1. Get baseline scores from the existing ranker
    baseline: list[dict] = _sym.rank(rows, task, limit=limit * 3)

    # 2. Determine which layers the task is asking about
    task_layers = _task_layers(task)
    if not task_layers:
        # No layer signal — return baseline unchanged
        return baseline[:limit]

    if not baseline and not task_layers:
        return []

    # 3. Build a lookup: file path → layer from the index
    path_to_layer: dict[str, str] = {}
    layer_buckets: dict[str, list[dict]] = {
        "controller": index.controllers,
        "service":    index.services,
        "repository": index.repositories,
        "entity":     index.entities,
        "dto":        index.dtos,
        "security":   index.security,
        "config":     index.configs,
        "messaging":  index.messaging,
        "aspect":     index.aspects,
        "test":       index.tests,
        "util":       index.utils,
        "component":  index.components,
    }
    for layer, entries in layer_buckets.items():
        for entry in entries:
            path_to_layer[entry["path"]] = layer

    # 4. Build a boost map: path → extra score
    layer_boost_map: dict[str, int] = {layer: boost for layer, boost in task_layers}

    # 5. Re-score each baseline entry
    boosted: list[dict] = []
    for entry in baseline:
        file_layer = path_to_layer.get(entry["path"], "")
        extra = layer_boost_map.get(file_layer, 0)
        boosted.append({**entry, "score": entry["score"] + extra, "layer": file_layer})

    # 6. Also inject high-scoring layer files that symbols.rank() missed
    #    (e.g. a SecurityConfig that doesn't mention the word "security" in its code)
    baseline_paths = {e["path"] for e in baseline}
    for layer, boost in task_layers[:2]:            # top 2 layers only
        entries = layer_buckets.get(layer, [])
        for entry in entries[:3]:                   # top 3 files per layer
            if entry["path"] not in baseline_paths:
                boosted.append({
                    "path": entry["path"],
                    "score": boost,
                    "why": "layer",
                    "symbol": layer,
                    "layer": layer,
                })
                baseline_paths.add(entry["path"])

    # 7. Sort by final score and return top `limit`
    boosted.sort(key=lambda e: (-e["score"], e["path"]))
    return boosted[:limit]
