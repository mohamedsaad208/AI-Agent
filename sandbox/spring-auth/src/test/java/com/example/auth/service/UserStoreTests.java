package com.example.auth.service;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class UserStoreTests {

    private final UserStore users = new UserStore();

    @Test
    void acceptsTheConfiguredCredentials() {
        assertTrue(users.matches("admin", "Admin123!"));
    }

    @Test
    void rejectsAWrongPassword() {
        assertFalse(users.matches("admin", "Admin123"));
    }

    @Test
    void rejectsAnUnknownUser() {
        assertFalse(users.matches("guest", "Admin123!"));
    }

    @Test
    void rejectsNullInputs() {
        assertFalse(users.matches(null, "Admin123!"));
        assertFalse(users.matches("admin", null));
    }
}
