from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_FINDINGS = 200
MAX_PATHS = 200
MAX_ALLOWLIST_ENTRIES = 200

_TEXT_SUFFIXES = {
    ".cfg", ".conf", ".css", ".csv", ".env", ".go", ".graphql", ".html",
    ".ini", ".java", ".js", ".json", ".jsx", ".md", ".mjs", ".properties",
    ".ps1", ".py", ".rb", ".rst", ".sh", ".sql", ".toml", ".ts", ".tsx",
    ".txt", ".xml", ".yaml", ".yml",
}

_SKIP_PARTS = {".git", "node_modules", ".venv", "venv", "dist", "build", "coverage"}
_PLACEHOLDER_MARKERS = {
    "${{ secrets.", "<redacted", "[redacted", "example", "dummy", "fake", "fixture",
    "placeholder", "test-token", "test_token", "changeme", "replace-me", "replace_me",
    "your-api", "your_api", "not-emitted", "not_emitted",
}
_HASH_KEYS = {
    "fingerprint", "digest", "sha", "sha1", "sha256", "sha512", "hash",
    "source_sha256", "config_sha256", "payload_sha256", "runtime_signature",
}
_DEPENDENCY_REPORT_SCHEMA = "nexus_security_dependency_assurance_v1"
_SAFE_METADATA_RE = re.compile(r"^[A-Za-z0-9@._:/+\-]{1,160}$")
_HEX_64_RE = re.compile(r"^[a-f0-9]{64}$")

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("openai_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    ("bearer_token", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{24,}\b", re.IGNORECASE)),
)

_PII_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("email", re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)),
    ("phone", re.compile(r"(?<!\w)\+?\d[\d\s().-]{7,}\d(?!\w)")),
    ("provider_group", re.compile(r"\b\d{10,24}@g\.us\b", re.IGNORECASE)),
    ("tracking", re.compile(r"\b(?=[A-Z0-9._-]{10,48}\b)(?=(?:[A-Z0-9._-]*\d){4})(?=[A-Z0-9._-]*[A-Z])[A-Z0-9][A-Z0-9._-]+\b", re.IGNORECASE)),
)

_FORBIDDEN_JSON_KEYS = {
    "authorization", "api_key", "secret_key", "password", "private_key", "access_token",
    "refresh_token", "provider_payload", "raw_payload", "tool_arguments", "tool_results",
    "tracking_number", "waybill_code", "phone", "email", "address", "prompt", "transcript",
}


@dataclass(frozen=True)
class Finding:
    rule: str
    path: str
    line: int
    fingerprint: str

    def as_dict(self) -> dict[str, object]:
        return {"rule": self.rule, "path": self.path, "line": self.line, "fingerprint": self.fingerprint}


@dataclass(frozen=True)
class AllowlistEntry:
    rule: str
    path: str
    fingerprint: str
    reason: str
    expires_on: date

    @property
    def key(self) -> tuple[str, str, str]:
        return self.path, self.rule, self.fingerprint


def _fingerprint(rule: str, path: str, line_no: int, value: str) -> str:
    payload = f"{rule}\0{path}\0{line_no}\0{value}".encode("utf-8", errors="replace")
    return hashlib.sha256(payload).hexdigest()[:16]


def _is_placeholder(line: str) -> bool:
    lowered = line.lower()
    return any(marker in lowered for marker in _PLACEHOLDER_MARKERS)


def _is_text_candidate(path: Path) -> bool:
    if any(part in _SKIP_PARTS for part in path.parts):
        return False
    if path.name.startswith(".env"):
        return True
    return path.suffix.lower() in _TEXT_SUFFIXES or path.suffix == ""


def _read_text(path: Path) -> str | None:
    try:
        if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            return None
        raw = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in raw[:4096]:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def load_allowlist(path: Path, *, today: date | None = None) -> list[AllowlistEntry]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError) as exc:
        raise ValueError("secret_allowlist_invalid") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != "nexus_secret_scan_allowlist_v1":
        raise ValueError("secret_allowlist_schema_invalid")
    raw_entries = payload.get("entries")
    if not isinstance(raw_entries, list) or len(raw_entries) > MAX_ALLOWLIST_ENTRIES:
        raise ValueError("secret_allowlist_entries_invalid")
    current = today or date.today()
    entries: list[AllowlistEntry] = []
    seen: set[tuple[str, str, str]] = set()
    for raw in raw_entries:
        if not isinstance(raw, dict):
            raise ValueError("secret_allowlist_entry_invalid")
        path_value = str(raw.get("path") or "").strip()
        rule = str(raw.get("rule") or "").strip()
        fingerprint = str(raw.get("fingerprint") or "").strip().lower()
        reason = " ".join(str(raw.get("reason") or "").strip().split())[:240]
        try:
            expires_on = date.fromisoformat(str(raw.get("expires_on") or ""))
        except ValueError as exc:
            raise ValueError("secret_allowlist_expiry_invalid") from exc
        if (
            not path_value
            or path_value.startswith("/")
            or ".." in Path(path_value).parts
            or not re.fullmatch(r"[a-z0-9_:-]{2,80}", rule)
            or not re.fullmatch(r"[a-f0-9]{16}", fingerprint)
            or len(reason) < 8
            or expires_on < current
        ):
            raise ValueError("secret_allowlist_entry_invalid_or_expired")
        entry = AllowlistEntry(rule, path_value, fingerprint, reason, expires_on)
        if entry.key in seen:
            raise ValueError("secret_allowlist_duplicate")
        seen.add(entry.key)
        entries.append(entry)
    return entries


def apply_allowlist(findings: list[Finding], entries: list[AllowlistEntry]) -> tuple[list[Finding], int]:
    allowed = {entry.key for entry in entries}
    remaining = [finding for finding in findings if (finding.path, finding.rule, finding.fingerprint) not in allowed]
    return remaining, len(findings) - len(remaining)


def scan_secret_text(relative_path: str, text: str) -> list[Finding]:
    """Scan decoded text and return only redacted, fingerprinted findings."""
    findings: list[Finding] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if _is_placeholder(line):
            continue
        for rule, pattern in _PATTERNS:
            match = pattern.search(line)
            if match:
                findings.append(
                    Finding(
                        rule,
                        relative_path,
                        line_no,
                        _fingerprint(rule, relative_path, line_no, match.group(0)),
                    )
                )
                if len(findings) >= MAX_FINDINGS:
                    return findings
    return findings


def scan_secret_files(root: Path, relative_paths: Iterable[str]) -> list[Finding]:
    findings: list[Finding] = []
    for relative in sorted(set(relative_paths)):
        if len(findings) >= MAX_FINDINGS:
            break
        path = root / relative
        if not _is_text_candidate(path):
            continue
        text = _read_text(path)
        if text is None:
            continue
        remaining = MAX_FINDINGS - len(findings)
        findings.extend(scan_secret_text(relative, text)[:remaining])
    return findings


def _hash_like_key(key: str) -> bool:
    normalized = key.strip().lower()
    return normalized in _HASH_KEYS or normalized.endswith(("_hash", "_sha", "_sha256", "_digest", "_fingerprint"))


def _safe_metadata_string(value: object, *, max_length: int = 160) -> bool:
    return isinstance(value, str) and len(value) <= max_length and bool(_SAFE_METADATA_RE.fullmatch(value))


def _bounded_nonnegative_int(value: object, *, maximum: int = 1_000_000) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= maximum


def _dependency_report_error(value: object) -> str | None:
    if not isinstance(value, dict):
        return "not_object"
    expected_top = {
        "schema_version",
        "status",
        "exit_codes",
        "python_vulnerability_count",
        "npm_vulnerability_counts",
        "findings",
        "findings_truncated",
        "sbom_sha256",
    }
    if set(value) != expected_top:
        return "top_level_keys"
    if value.get("schema_version") != _DEPENDENCY_REPORT_SCHEMA:
        return "schema"
    if value.get("status") not in {"pass", "fail"}:
        return "status"

    exit_codes = value.get("exit_codes")
    expected_exit_keys = {"pip_audit", "pip_sbom", "npm_audit", "npm_sbom"}
    if not isinstance(exit_codes, dict) or set(exit_codes) != expected_exit_keys:
        return "exit_codes"
    if any(not _bounded_nonnegative_int(item, maximum=255) for item in exit_codes.values()):
        return "exit_code_value"

    if not _bounded_nonnegative_int(value.get("python_vulnerability_count")):
        return "python_count"
    npm_counts = value.get("npm_vulnerability_counts")
    allowed_count_keys = {"info", "low", "moderate", "high", "critical", "total"}
    if not isinstance(npm_counts, dict) or not set(npm_counts).issubset(allowed_count_keys):
        return "npm_counts"
    if any(not _bounded_nonnegative_int(item) for item in npm_counts.values()):
        return "npm_count_value"

    if not isinstance(value.get("findings_truncated"), bool):
        return "truncated"
    findings = value.get("findings")
    if not isinstance(findings, list) or len(findings) > 200:
        return "findings"
    for finding in findings:
        if not isinstance(finding, dict):
            return "finding_object"
        ecosystem = finding.get("ecosystem")
        if ecosystem == "python":
            if set(finding) != {"ecosystem", "package", "version", "advisory", "fix_versions"}:
                return "python_finding_keys"
            if not all(_safe_metadata_string(finding.get(key)) for key in ("package", "version", "advisory")):
                return "python_finding_value"
            fixes = finding.get("fix_versions")
            if not isinstance(fixes, list) or len(fixes) > 10 or any(not _safe_metadata_string(item, max_length=80) for item in fixes):
                return "python_fix_versions"
        elif ecosystem == "npm":
            if set(finding) != {"ecosystem", "package", "severity", "advisories", "fix_available"}:
                return "npm_finding_keys"
            if not _safe_metadata_string(finding.get("package")):
                return "npm_package"
            if finding.get("severity") not in {"info", "low", "moderate", "high", "critical", "unknown"}:
                return "npm_severity"
            advisories = finding.get("advisories")
            if not isinstance(advisories, list) or len(advisories) > 10 or any(not _safe_metadata_string(item) for item in advisories):
                return "npm_advisories"
            if not isinstance(finding.get("fix_available"), bool):
                return "npm_fix_available"
        else:
            return "ecosystem"

    sbom = value.get("sbom_sha256")
    if not isinstance(sbom, dict) or set(sbom) != {"python", "webapp"}:
        return "sbom"
    for digest in sbom.values():
        if digest is not None and (not isinstance(digest, str) or not _HEX_64_RE.fullmatch(digest)):
            return "sbom_digest"
    return None


def _walk_json(value: object, *, path: str, findings: list[Finding], key: str = "", depth: int = 0) -> None:
    if len(findings) >= MAX_FINDINGS or depth > 8:
        return
    if isinstance(value, dict):
        for raw_key, child in list(value.items())[:200]:
            normalized = str(raw_key).strip().lower()
            if normalized in _FORBIDDEN_JSON_KEYS:
                findings.append(Finding(f"json_key:{normalized}", path, 0, _fingerprint(normalized, path, 0, normalized)))
                if len(findings) >= MAX_FINDINGS:
                    return
            _walk_json(child, path=path, findings=findings, key=normalized, depth=depth + 1)
    elif isinstance(value, list):
        for child in value[:200]:
            _walk_json(child, path=path, findings=findings, key=key, depth=depth + 1)
    elif isinstance(value, str):
        for rule, pattern in _PATTERNS:
            match = pattern.search(value)
            if match:
                findings.append(Finding(f"artifact:{rule}", path, 0, _fingerprint(rule, path, 0, match.group(0))))
                if len(findings) >= MAX_FINDINGS:
                    return
        if _hash_like_key(key):
            return
        for rule, pattern in _PII_PATTERNS:
            match = pattern.search(value)
            if match:
                findings.append(Finding(f"artifact:{rule}", path, 0, _fingerprint(rule, path, 0, match.group(0))))
                if len(findings) >= MAX_FINDINGS:
                    return


def scan_artifact_files(root: Path, relative_paths: Iterable[str]) -> list[Finding]:
    findings: list[Finding] = []
    for relative in sorted(set(relative_paths))[:MAX_PATHS]:
        path = root / relative
        text = _read_text(path)
        if text is None:
            continue
        if path.suffix.lower() == ".json":
            try:
                value = json.loads(text)
            except (json.JSONDecodeError, RecursionError):
                findings.append(Finding("invalid_json", relative, 0, _fingerprint("invalid_json", relative, 0, "invalid")))
                continue
            if isinstance(value, dict) and value.get("schema_version") == _DEPENDENCY_REPORT_SCHEMA:
                error = _dependency_report_error(value)
                if error:
                    findings.append(
                        Finding(
                            "dependency_report_invalid",
                            relative,
                            0,
                            _fingerprint("dependency_report_invalid", relative, 0, error),
                        )
                    )
                continue
            _walk_json(value, path=relative, findings=findings)
        else:
            findings.extend(scan_secret_files(root, [relative]))
        if len(findings) >= MAX_FINDINGS:
            break
    return findings[:MAX_FINDINGS]


def bounded_report(
    *,
    schema: str,
    findings: list[Finding],
    scanned_files: int,
    suppressed_count: int = 0,
) -> dict[str, object]:
    by_rule: dict[str, int] = {}
    for finding in findings:
        by_rule[finding.rule] = by_rule.get(finding.rule, 0) + 1
    return {
        "schema_version": schema,
        "status": "pass" if not findings else "fail",
        "scanned_files": max(0, int(scanned_files)),
        "finding_count": len(findings),
        "suppressed_count": max(0, int(suppressed_count)),
        "by_rule": dict(sorted(by_rule.items())),
        "findings": [finding.as_dict() for finding in findings[:MAX_FINDINGS]],
        "truncated": len(findings) > MAX_FINDINGS,
    }


def write_report(path: Path, report: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    if len(encoded.encode("utf-8")) > 64 * 1024:
        raise ValueError("security_report_too_large")
    path.write_text(encoded + "\n", encoding="utf-8")
