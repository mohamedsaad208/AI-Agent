"""Keep credentials out of the text a build run leaves behind.

Command output is stored on the session and fed back to the model on the next repair
turn, so anything the project printed lands in a log file and possibly in a cloud
request. Projects print their own connection strings often enough that the guarantee
cannot be "nobody printed a secret"; the value is removed where it is captured.
"""
from __future__ import annotations

import re

# Placeholder text is not a secret and redacting it would only confuse the model.
SKIP_VALUES = {"null", "none", "true", "false", "undefined", "***", "[redacted]", "string",
               "change-me", "changeme", "secret", "password", "your-key-here", "<token>"}

PATTERNS = (
    # Whole PEM private keys, across lines.
    re.compile(r"(?s)-----BEGIN[A-Z ]*PRIVATE KEY-----.*?-----END[A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),                      # AWS access key id
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),                      # GitHub token
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),                    # Slack token
    re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),                          # Google API key
    re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_\-]{20,}\b"),           # OpenAI/Anthropic key
    re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{4,}\.[A-Za-z0-9_\-]{4,}\b"),  # JWT
    re.compile(r"(?i)\bglpat-[A-Za-z0-9_\-]{20,}\b"),                   # GitLab token
)

# user:password inside any URL or JDBC-style connection string.
URL_CREDENTIALS = re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://)[^\s/@:]+:[^\s/@]+@")
# "password = hunter2", "API_KEY: 'abcd'", -Dtoken=abcd, and friends. The optional qualifier in
# front exists because `\b` does not fire after an underscore: without it the two shapes that build
# logs really print — SPRING_DATASOURCE_PASSWORD=… and AWS_SECRET_ACCESS_KEY=… — passed straight
# through, into the session file and on to the next model request.
ASSIGNMENT = re.compile(
    r"(?i)\b([\w.]*_)?(password|passwd|pwd|secret|api[_-]?key|access[_-]?key|secret[_-]?key|"
    r"client[_-]?secret|auth[_-]?token|token)(\s*[=:]\s*)([\"']?)([^\s,;\"']{6,})(\4)")


def _assignment(match: re.Match) -> str:
    value = match.group(5)
    if value.casefold().strip("'\"") in SKIP_VALUES or value.startswith(("${", "$(", "%{")):
        return match.group(0)
    return ((match.group(1) or "") + match.group(2) + match.group(3) + match.group(4)
            + "[redacted]" + match.group(6))


def redact(text: str) -> str:
    """Replace credential-shaped runs of characters; leave the surrounding log intact."""
    if not isinstance(text, str) or not text:
        return text
    for pattern in PATTERNS:
        text = pattern.sub("[redacted]", text)
    text = URL_CREDENTIALS.sub(lambda m: m.group(1) + "[redacted]@", text)
    return ASSIGNMENT.sub(_assignment, text)
