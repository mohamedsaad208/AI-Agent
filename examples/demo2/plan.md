# Spring Boot Authentication Demo — Implementation Plan

## Goal
Build a small authentication application that I can run and test manually.

## Stack
- Java 21 and Maven.
- Keep the existing Spring Boot version. For an empty project, use Spring Boot 4.0.8.
- Spring Web MVC, Spring Security, Thymeleaf.
- Spring Data JPA, H2 for local development, and Bean Validation.
- Session-based authentication using Spring Security.

## Features
1. Register using name, email, password, and password confirmation.
2. Log in using email and password.
3. View a protected dashboard showing the current user's name and email.
4. Log out.
5. Show clear validation messages and generic login errors.

## Pages and Routes
- GET / — public home page.
- GET /register — registration form.
- POST /register — validate and create a USER account.
- GET /login — login page.
- POST /login — processed by Spring Security, not a custom password-checking controller.
- GET /dashboard — authenticated users only.
- POST /logout — processed by Spring Security.

## Security Requirements
- Hash passwords using Spring Security's PasswordEncoder; never store plaintext.
- For BCrypt, reject passwords exceeding 72 UTF-8 bytes. Require at least 12 characters.
- Normalize email addresses and enforce uniqueness in the database.
- Handle duplicate registration without a server error, including concurrent requests.
- Never accept roles from the registration form.
- Keep CSRF protection enabled and include tokens in forms.
- Use POST for logout and invalidate the authenticated session.
- Retain Spring Security's session-fixation protection.
- Never log passwords or expose password hashes.
- Do not enable the H2 console.
- Use development-only database configuration; no production credentials.

## Phase 1 — Project Foundation
Create or adapt the Maven project, application entry point, dependencies, and local configuration. Add a README with setup instructions. Keep the project small and use package com.example.authdemo.

## Phase 2 — Registration Backend
Implement the user entity, repository, registration DTO, password encoder, and registration service. Add input validation, password-confirmation checks, and duplicate-email handling.

## Phase 3 — Authentication
Implement database-backed UserDetailsService and SecurityFilterChain. Configure email-based form login, protected dashboard access, and logout. Add the necessary MVC controllers.

## Phase 4 — User Interface
Create simple English Thymeleaf pages for home, registration, login, and dashboard. Include validation feedback, login-error messages, and a logout button. Use local CSS without external CDN dependencies.

## Phase 5 — Manual Handoff
Update the README with startup instructions and these manual scenarios:
- Successful registration, login, and logout.
- Invalid email, short password, and mismatched confirmation.
- Duplicate email, including different letter casing.
- Wrong-password login.
- Anonymous access to the dashboard.
- Dashboard access after logout.
- Form submission without a valid CSRF token.

## Working Rules
- Implement Phase 1 only in this task.
- Read existing files before changing them.
- Keep each proposal within eight changed files.
- Show the proposed changes for my approval.
- Do not run builds, tests, or the application.
- Do not claim anything has passed verification.
- Do not add JWT, OAuth, MFA, email delivery, or password reset yet.