package com.example.auth.service;

import io.jsonwebtoken.JwtException;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

class JwtTokenProviderTests {

    private final JwtTokenProvider tokens =
            new JwtTokenProvider("change-me-change-me-change-me-32", 60);

    @Test
    void tokenRoundTripsToItsSubject() {
        assertEquals("admin", tokens.subjectOf(tokens.issue("admin")));
    }

    @Test
    void rejectsATamperedSignature() {
        String token = tokens.issue("admin");
        String tampered = token.substring(0, token.length() - 2) + "aa";
        assertThrows(JwtException.class, () -> tokens.subjectOf(tampered));
    }

    @Test
    void reportsTheConfiguredLifetime() {
        assertEquals(3600, tokens.lifetimeSeconds());
    }
}
