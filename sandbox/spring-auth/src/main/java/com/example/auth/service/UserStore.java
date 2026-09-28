package com.example.auth.service;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.Map;
import org.springframework.stereotype.Service;

@Service
public class UserStore {

    private static final Map<String, String> PASSWORD_DIGESTS =
            Map.of("admin", digestOf("Admin123!"));

    public boolean matches(String username, String password) {
        String expected = username == null ? null : PASSWORD_DIGESTS.get(username);
        if (expected == null || password == null) {
            return false;
        }
        return MessageDigest.isEqual(expected.getBytes(StandardCharsets.UTF_8),
                                     digestOf(password).getBytes(StandardCharsets.UTF_8));
    }

    private static String digestOf(String value) {
        try {
            byte[] hash = MessageDigest.getInstance("SHA-256")
                    .digest(value.getBytes(StandardCharsets.UTF_8));
            StringBuilder hex = new StringBuilder(hash.length * 2);
            for (byte b : hash) {
                hex.append(String.format("%02x", b));
            }
            return hex.toString();
        } catch (NoSuchAlgorithmException exc) {
            throw new IllegalStateException("SHA-256 is required by the JDK.", exc);
        }
    }
}
