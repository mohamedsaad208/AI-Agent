# Spring Boot authentication service — build plan

Target: Java 17, Spring Boot 3.3.4, Maven. Package `com.example.auth`.
No database: users live in memory. No new dependencies beyond the ones listed.

## Phase 1 — project skeleton

- `pom.xml` — parent `spring-boot-starter-parent` 3.3.4, Java 17, dependencies:
  `spring-boot-starter-web`, `spring-boot-starter-security`, `spring-boot-starter-test` (test scope),
  `io.jsonwebtoken:jjwt-api:0.12.6` with `jjwt-impl` and `jjwt-jackson` (runtime scope).
- `src/main/java/com/example/auth/AuthApplication.java` — `@SpringBootApplication` main class.
- `src/main/resources/application.yml` — `server.port: 8080`, `spring.application.name: auth`.
- `src/main/java/com/example/auth/dto/LoginRequest.java` — record with `username`, `password`.
- `src/main/java/com/example/auth/dto/LoginResponse.java` — record with `token`, `expiresIn`.

## Phase 2 — login and token handling

- `src/main/java/com/example/auth/service/UserStore.java` — `@Service`, in-memory map with one
  user `admin` / password `Admin123!` compared without storing plaintext (hash with SHA-256 and
  compare digests). Expose `boolean matches(String username, String password)`.
- `src/main/java/com/example/auth/service/JwtTokenProvider.java` — `@Service`, issues an HS256
  JWT with jjwt 0.12.6 (`Jwts.builder()...signWith(key)`), secret read from
  `@Value("${jwt.secret:change-me-change-me-change-me-32}")`, `String subjectOf(String token)`
  validates and returns the subject.
- `src/main/java/com/example/auth/web/AuthController.java` — `@RestController`,
  `POST /api/login` returns `LoginResponse` for valid credentials and `401` for invalid ones.
- `src/main/java/com/example/auth/config/SecurityConfig.java` — permit `/api/login`, require
  authentication elsewhere, disable CSRF for the stateless API.

## Phase 3 — tests

- `src/test/java/com/example/auth/AuthApplicationTests.java` — context loads.
- `src/test/java/com/example/auth/service/UserStoreTests.java` — right password matches, wrong
  password and unknown user do not.
- `src/test/java/com/example/auth/service/JwtTokenProviderTests.java` — a token round-trips to
  its subject; a tampered token is rejected.

Definition of done for every phase: `mvn -B test` finishes with at least one test run and no
failures or compile errors.
