from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from typing import Sequence

import paramiko

MAX_OUTPUT_CHARS = 32_000
MAX_JOURNAL_LINES = 200
SHELL_META_RE = re.compile(r"[;&|`$<>()\n\r]")

ALLOWED_BINARIES = frozenset(
    {
        "uptime",
        "hostname",
        "uname",
        "df",
        "free",
        "top",
        "ps",
        "w",
        "who",
        "systemctl",
        "journalctl",
        "tail",
        "cat",
        "ls",
        "ip",
        "ss",
        "dmesg",
    }
)

SYSTEMCTL_VERBS = frozenset({"status", "is-active", "is-failed", "show", "list-units", "cat"})
IP_VERBS = frozenset({"addr", "link", "route", "neigh", "-br", "-4", "-6"})
PATH_PREFIXES = (
    "/var/log/everoute/",
    "/var/log/messages",
    "/proc/loadavg",
    "/proc/meminfo",
    "/proc/cpuinfo",
)
SUDO_BINARIES = frozenset({"systemctl", "journalctl", "tail", "cat", "dmesg", "ss"})


@dataclass
class SshRuntime:
    allowed_hosts: frozenset[str]
    usernames: tuple[str, ...]
    password: str
    timeout_sec: int = 20
    resolved_username: str = ""


@dataclass
class ValidateSshReadReq:
    host: str
    argv: Sequence[str]
    allowed_hosts: frozenset[str]


@dataclass
class ValidateSshReadRes:
    ok: bool
    error: str
    argv: tuple[str, ...]
    use_sudo: bool


@dataclass
class RunSshReadReq:
    host: str
    argv: tuple[str, ...]
    usernames: tuple[str, ...]
    password: str
    timeout_sec: int
    use_sudo: bool
    preferred_username: str = ""


@dataclass
class RunSshReadRes:
    output: str
    username: str


def validate_ssh_read(req: ValidateSshReadReq) -> ValidateSshReadRes:
    host = (req.host or "").strip()
    if not host:
        return ValidateSshReadRes(ok=False, error="host is empty", argv=(), use_sudo=False)
    if host not in req.allowed_hosts:
        return ValidateSshReadRes(
            ok=False,
            error=f"host {host} is not allowed; only {sorted(req.allowed_hosts)}",
            argv=(),
            use_sudo=False,
        )
    if not req.argv:
        return ValidateSshReadRes(ok=False, error="argv is empty", argv=(), use_sudo=False)

    argv = tuple(str(part) for part in req.argv)
    for part in argv:
        if part == "":
            return ValidateSshReadRes(ok=False, error="argv contains an empty argument", argv=(), use_sudo=False)
        if SHELL_META_RE.search(part):
            return ValidateSshReadRes(
                ok=False,
                error=f"argv contains shell metacharacters: {part!r}",
                argv=(),
                use_sudo=False,
            )
        if ".." in part:
            return ValidateSshReadRes(ok=False, error="argv contains '..'", argv=(), use_sudo=False)

    binary = argv[0]
    if binary not in ALLOWED_BINARIES:
        return ValidateSshReadRes(
            ok=False,
            error=f"command {binary!r} is not in the read-only allowlist",
            argv=(),
            use_sudo=False,
        )

    normalized = argv
    if binary == "top":
        normalized = ("top", "-bn1")
    elif binary == "df":
        if len(argv) == 1:
            normalized = ("df", "-h")
    elif binary == "free":
        if len(argv) == 1:
            normalized = ("free", "-m")
    elif binary == "ps":
        if len(argv) == 1:
            normalized = ("ps", "aux")
    elif binary == "systemctl":
        if len(argv) < 2 or argv[1] not in SYSTEMCTL_VERBS:
            return ValidateSshReadRes(
                ok=False,
                error=f"systemctl verb must be one of {sorted(SYSTEMCTL_VERBS)}",
                argv=(),
                use_sudo=False,
            )
    elif binary == "journalctl":
        normalized = _normalize_journalctl(argv)
        if normalized is None:
            return ValidateSshReadRes(
                ok=False,
                error="journalctl only allows --no-pager, -n (<=200), -u, --since, --until, -p",
                argv=(),
                use_sudo=False,
            )
    elif binary in {"cat", "tail", "ls"}:
        if not _paths_allowed(binary, argv):
            return ValidateSshReadRes(
                ok=False,
                error=f"{binary} path must be under {PATH_PREFIXES}",
                argv=(),
                use_sudo=False,
            )
    elif binary == "ip":
        if len(argv) < 2 or argv[1] not in IP_VERBS:
            return ValidateSshReadRes(
                ok=False,
                error="ip only allows addr/link/route/neigh",
                argv=(),
                use_sudo=False,
            )
    elif binary == "ss":
        for flag in argv[1:]:
            if flag.startswith("-") and any(ch in flag for ch in "KFG"):
                return ValidateSshReadRes(ok=False, error="ss flags are limited to listing", argv=(), use_sudo=False)
    elif binary == "dmesg":
        if any(arg in {"-w", "--follow", "-W"} for arg in argv[1:]):
            return ValidateSshReadRes(ok=False, error="dmesg follow mode is not allowed", argv=(), use_sudo=False)

    return ValidateSshReadRes(
        ok=True,
        error="",
        argv=normalized,
        use_sudo=binary in SUDO_BINARIES,
    )


def run_ssh_read(req: RunSshReadReq) -> RunSshReadRes:
    usernames = _username_order(req)
    last_error: Exception | None = None
    for username in usernames:
        try:
            output = _run_ssh_once(req, username)
        except paramiko.AuthenticationException as exc:
            last_error = exc
            continue
        return RunSshReadRes(output=output, username=username)
    tried = ", ".join(usernames)
    raise RuntimeError(f"ssh auth failed for users [{tried}]: {last_error}")


def _username_order(req: RunSshReadReq) -> tuple[str, ...]:
    names = [name for name in req.usernames if name]
    if req.preferred_username:
        names = [req.preferred_username] + [name for name in names if name != req.preferred_username]
    if not names:
        raise RuntimeError("no SSH usernames configured")
    return tuple(names)


def _run_ssh_once(req: RunSshReadReq, username: str) -> str:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=req.host,
            username=username,
            password=req.password,
            timeout=req.timeout_sec,
            allow_agent=False,
            look_for_keys=False,
        )
        remote = shlex.join(req.argv)
        if req.use_sudo:
            remote = f"sudo -S -p '' {remote}"
        stdin, stdout, stderr = client.exec_command(remote, timeout=req.timeout_sec)
        if req.use_sudo:
            stdin.write(req.password + "\n")
            stdin.flush()
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        status = stdout.channel.recv_exit_status()
    finally:
        client.close()

    text = out
    if err.strip():
        text = (text + "\n" if text else "") + f"[stderr]\n{err}"
    text = f"[user {username}] [exit {status}]\n{text}"
    if len(text) > MAX_OUTPUT_CHARS:
        text = text[:MAX_OUTPUT_CHARS] + "\n...[truncated]"
    return text


def _normalize_journalctl(argv: tuple[str, ...]) -> tuple[str, ...] | None:
    parts: list[str] = ["journalctl", "--no-pager"]
    i = 1
    saw_n = False
    while i < len(argv):
        arg = argv[i]
        if arg == "--no-pager":
            i += 1
            continue
        if arg in {"-n", "-u", "--since", "--until", "-p", "--unit"}:
            if i + 1 >= len(argv):
                return None
            value = argv[i + 1]
            if arg == "-n":
                if not value.isdigit() or int(value) > MAX_JOURNAL_LINES:
                    return None
                saw_n = True
            parts.extend([arg, value])
            i += 2
            continue
        if arg.startswith("-"):
            return None
        return None
    if not saw_n:
        parts.extend(["-n", "80"])
    return tuple(parts)


def _paths_allowed(binary: str, argv: tuple[str, ...]) -> bool:
    paths: list[str] = []
    i = 1
    while i < len(argv):
        arg = argv[i]
        if binary == "tail" and arg == "-n":
            i += 2
            continue
        if arg.startswith("-"):
            i += 1
            continue
        paths.append(arg)
        i += 1
    if not paths:
        return False
    return all(any(path.startswith(prefix) for prefix in PATH_PREFIXES) for path in paths)
