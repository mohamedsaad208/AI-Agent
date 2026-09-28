package com.example.auth.dto;

public record LoginResponse(String token, long expiresIn) {
}
