package com.example.auth.web;

import com.example.auth.dto.LoginRequest;
import com.example.auth.dto.LoginResponse;
import com.example.auth.service.JwtTokenProvider;
import com.example.auth.service.UserStore;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api")
public class AuthController {

    private final UserStore users;
    private final JwtTokenProvider tokens;

    public AuthController(UserStore users, JwtTokenProvider tokens) {
        this.users = users;
        this.tokens = tokens;
    }

    @PostMapping("/login")
    public ResponseEntity<LoginResponse> login(@RequestBody LoginRequest request) {
        if (!users.matches(request.username(), request.password())) {
            return ResponseEntity.status(401).build();
        }
        return ResponseEntity.ok(new LoginResponse(tokens.issue(request.username()),
                                                   tokens.lifetimeSeconds()));
    }
}
