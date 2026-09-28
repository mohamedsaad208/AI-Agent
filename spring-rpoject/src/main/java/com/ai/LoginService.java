package com.ai;

import org.springframework.stereotype.Service;

@Service
public class LoginService {

    public String authenticate(LoginRequest request) {
        // Implement authentication logic here
        return "Authenticated";
    }
}
