from __future__ import annotations

import re

MIN_PASSWORD_LENGTH = 12

_COMMON_WEAK_PASSWORDS = {
    "password",
    "password1",
    "password12",
    "password123",
    "password1234",
    "admin",
    "admin123",
    "admin1234",
    "qwerty",
    "qwerty123",
    "welcome",
    "welcome1",
    "letmein",
    "changeme",
    "nexusdesk",
    "speedaf",
    "123456",
    "12345678",
    "123456789",
    "1234567890",
    "111111",
    "000000",
}

_KEYBOARD_SEQUENCES = (
    "abcdefghijklmnopqrstuvwxyz",
    "qwertyuiop",
    "asdfghjkl",
    "zxcvbnm",
    "0123456789",
)


class PasswordPolicyError(ValueError):
    pass


def _normalized(password: str) -> str:
    return re.sub(r"\s+", "", password).lower()


def _has_sequential_run(value: str, *, run_length: int = 6) -> bool:
    candidate = _normalized(value)
    if len(candidate) < run_length:
        return False
    for sequence in _KEYBOARD_SEQUENCES:
        for idx in range(0, len(sequence) - run_length + 1):
            forward = sequence[idx : idx + run_length]
            backward = forward[::-1]
            if forward in candidate or backward in candidate:
                return True
    return False


def _character_class_count(password: str) -> int:
    checks = [
        any(ch.islower() for ch in password),
        any(ch.isupper() for ch in password),
        any(ch.isdigit() for ch in password),
        any(not ch.isalnum() for ch in password),
    ]
    return sum(1 for item in checks if item)


def validate_admin_password_policy(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise PasswordPolicyError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
    if password.strip() != password:
        raise PasswordPolicyError("Password must not start or end with whitespace")
    lowered = _normalized(password)
    if lowered in _COMMON_WEAK_PASSWORDS:
        raise PasswordPolicyError("Password is too common")
    if len(set(lowered)) <= 3:
        raise PasswordPolicyError("Password has too little character variety")
    if password.isdigit() or password.isalpha():
        raise PasswordPolicyError("Password must include at least two character types")
    if _character_class_count(password) < 3:
        raise PasswordPolicyError("Password must include at least three character types")
    if _has_sequential_run(password):
        raise PasswordPolicyError("Password contains an unsafe sequence")
