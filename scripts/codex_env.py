"""Set up and verify the repository's Codex environment using Python 3.11+."""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from queue import Empty, Queue
from threading import Thread
from time import monotonic

ROOT = Path(__file__).resolve().parents[1]
AGENTS = {
    "requirements_researcher",
    "solution_architect",
    "frontend_builder",
    "backend_data_builder",
    "quality_reviewer",
}
SMOKE_ROLES = AGENTS - {"frontend_builder", "backend_data_builder"}
SPEC_COMMANDS = {
    "analyze",
    "checklist",
    "clarify",
    "constitution",
    "converge",
    "implement",
    "plan",
    "specify",
    "tasks",
}
VERCEL_SKILLS = {
    "vercel-composition-patterns",
    "vercel-react-best-practices",
    "web-design-guidelines",
}
PLUGIN_SKILLS = {
    "ecc@ecc": "browser-qa",
    "backend-development@claude-code-workflows": "api-design-principles",
    "developer-essentials@claude-code-workflows": "debugging-strategies",
    "javascript-typescript@claude-code-workflows": "modern-javascript-patterns",
    "python-development@claude-code-workflows": "python-testing-patterns",
    "security-scanning@claude-code-workflows": "stride-analysis-patterns",
}


def tool(name):
    path = shutil.which(name)
    if not path:
        suffix = ".exe" if os.name == "nt" else ""
        candidate = Path.home() / ".local" / "bin" / (name + suffix)
        if candidate.is_file():
            return str(candidate)
        raise ValueError(f"{name} is missing from PATH")
    return path


def run(*args, timeout=30, **kwargs):
    return subprocess.run(
        [tool(args[0]), *args[1:]],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
        timeout=timeout,
        **kwargs,
    )


def output(*args, **kwargs):
    result = run(*args, **kwargs)
    if result.returncode:
        # Auth, MCP and configuration output can include private endpoints or tokens.
        raise ValueError(f"{' '.join(args[:3])} exited {result.returncode}")
    return result.stdout.strip()


def read_toml(path):
    return tomllib.loads(path.read_text(encoding="utf-8"))


def validate_agents():
    paths = sorted((ROOT / ".codex" / "agents").glob("*.toml"))
    names = set()
    for path in paths:
        agent = read_toml(path)
        for key in ("name", "description", "developer_instructions"):
            if not isinstance(agent.get(key), str) or not agent[key].strip():
                raise ValueError(f"{path.name} requires a non-empty {key}")
        if path.stem != agent["name"] or agent.keys() & {"model", "model_provider"}:
            raise ValueError(
                f"{path.name} must name its own role and inherit the model and provider"
            )
        names.add(agent["name"])
    if names != AGENTS:
        raise ValueError(f"missing {sorted(AGENTS - names)}, unexpected {sorted(names - AGENTS)}")
    return ", ".join(sorted(names))


def validate_plugins():
    installed = {
        plugin["pluginId"]: plugin
        for plugin in json.loads(output("codex", "plugin", "list", "--json"))["installed"]
        if plugin["installed"] and plugin["enabled"]
    }
    versions = []
    for plugin_id in PLUGIN_SKILLS:
        plugin = installed.get(plugin_id)
        repo = "affaan-m/ECC" if plugin_id == "ecc@ecc" else "wshobson/agents"
        if (
            not plugin
            or plugin.get("marketplaceSource", {}).get("source") != f"https://github.com/{repo}.git"
        ):
            raise ValueError(f"{plugin_id} is missing, disabled, or from an unexpected marketplace")
        versions.append(f"{plugin['name']} {plugin['version']}")
    return ", ".join(versions)


def validate_spec_kit():
    integration = json.loads((ROOT / ".specify" / "integration.json").read_text(encoding="utf-8"))
    if (
        integration["default_integration"] != "codex"
        or not integration["integration_settings"]["codex"]["parsed_options"]["skills"]
    ):
        raise ValueError("expected the Codex skills integration")
    if not (ROOT / ".specify" / "memory" / "constitution.md").is_file():
        raise ValueError("missing project constitution")
    artifacts = json.loads(output("specify", "artifact", "list", "--json"))
    missing = {"speckit." + name for name in SPEC_COMMANDS} - {
        artifact["name"] for artifact in artifacts if artifact["kind"] == "command"
    }
    if missing:
        raise ValueError(f"missing Spec Kit commands {sorted(missing)}")
    return f"{output('specify', '--version')}, {len(SPEC_COMMANDS)} Codex workflow commands"


def validate_tracked_files():
    files = output("git", "ls-files", "-z").split("\0")
    sensitive = []
    for file in files:
        name = Path(file).name.lower()
        private_env = re.fullmatch(r"\.env(?:\..+)?", name) and name != ".env.example"
        if (
            private_env
            or name.endswith((".pem", ".key", ".p12", ".pfx", ".sqlite", ".sqlite3"))
            or name
            in {
                "auth.json",
                "credentials.json",
                "hosts.yml",
                "id_rsa",
                "id_ed25519",
            }
        ):
            sensitive.append(file)
    if sensitive:
        raise ValueError(f"tracked sensitive-looking paths {sensitive}")
    if run("git", "check-ignore", "-q", "--no-index", ".env").returncode:
        raise ValueError(".env must be ignored")
    if run("git", "check-ignore", "-q", "--no-index", ".env.example").returncode != 1:
        raise ValueError(".env.example must be trackable")
    return "Git index has no known credential filenames, and .env is ignored"


def codex_discovery():
    """Use app-server JSON-RPC to check the effective configuration and skill catalog."""
    command = [tool("codex"), "app-server", "--strict-config", "--listen", "stdio://"]
    with subprocess.Popen(
        command,
        cwd=ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
    ) as proc:
        lines = Queue()

        def receive():
            for line in proc.stdout:
                lines.put(line)
            lines.put(None)

        Thread(target=receive, daemon=True).start()

        def send(method, params=None, request_id=None):
            request = {"method": method}
            if params is not None:
                request["params"] = params
            if request_id is not None:
                request["id"] = request_id
            proc.stdin.write(json.dumps(request) + "\n")
            proc.stdin.flush()
            if request_id is None:
                return None
            # The only unsolicited messages are server notifications. Bound the read even on a broken server.
            deadline = monotonic() + 15
            while monotonic() < deadline:
                try:
                    line = lines.get(timeout=max(0, deadline - monotonic()))
                except Empty as exc:
                    raise ValueError(f"{method} timed out") from exc
                if line is None:
                    raise ValueError("app-server exited before replying (check `codex doctor`)")
                response = json.loads(line)
                if response.get("id") == request_id:
                    if "error" in response:
                        raise ValueError(f"{method} failed ({response['error'].get('code')})")
                    return response["result"]
            raise ValueError(f"{method} timed out")

        try:
            send("initialize", {"clientInfo": {"name": "project-doctor", "version": "1"}}, 1)
            send("initialized")
            config = send("config/read", {"cwd": str(ROOT), "includeLayers": True}, 2)
            skills = send("skills/list", {"cwds": [str(ROOT)], "forceReload": True}, 3)
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
    return config, skills


def validate_discovery():
    config, catalog = codex_discovery()
    agents = config["config"].get("agents", {})
    if not agents.get("enabled") or agents.get("max_concurrent_threads_per_session") != 5:
        raise ValueError(
            "effective Codex config must enable agents with a limit of five spawned threads"
        )
    layers = config.get("layers") or []
    if not any(
        layer.get("name", {}).get("type") == "project" and not layer.get("disabledReason")
        for layer in layers
    ):
        raise ValueError("project config is inactive (review repository trust in Codex)")
    data = catalog["data"][0]
    if data.get("errors"):
        raise ValueError(f"Codex reported {len(data['errors'])} skill load errors")
    enabled = [skill for skill in data["skills"] if skill["enabled"]]
    expected = {
        (None, skill) for skill in VERCEL_SKILLS | {"speckit-" + name for name in SPEC_COMMANDS}
    }
    expected.add((None, "openai-docs"))
    expected.update(
        (plugin_id, f"{plugin_id.split('@')[0]}:{name}")
        for plugin_id, name in PLUGIN_SKILLS.items()
    )
    entries = Counter((skill.get("pluginId"), skill["name"]) for skill in enabled)
    missing = expected - entries.keys()
    if missing:
        identifiers = sorted(f"{plugin or 'local'}/{name}" for plugin, name in missing)
        raise ValueError(f"Codex did not discover {identifiers}")
    duplicates = sorted(name for (_, name), count in entries.items() if count > 1)
    if duplicates:
        raise ValueError(f"duplicate enabled skill identifiers {duplicates}")
    return f"loaded project config and {len(enabled)} enabled skills, including {len(expected)} verified entries"


def validate_mcp():
    servers = json.loads(output("codex", "mcp", "list", "--json"))
    if sum(server["enabled"] for server in servers if server["name"] == "chrome-devtools") != 1:
        raise ValueError("expected one enabled chrome-devtools MCP server")
    return "chrome-devtools registered"


def smoke_evidence(log, sessions):
    parent = None
    final = ""
    status = None
    for line in log.splitlines():
        event = json.loads(line)
        if event["type"] == "thread.started":
            parent = event["thread_id"]
        elif event["type"] in {"turn.completed", "turn.failed"}:
            status = event["type"]
        elif event["type"] == "item.completed" and event["item"]["type"] == "agent_message":
            final = event["item"]["text"]
    if not parent or status != "turn.completed" or not all(role in final for role in SMOKE_ROLES):
        raise ValueError("parent must have completed and summarized all three requested roles")

    completed = {completed_child_role(path, parent) for path in sessions}
    missing = SMOKE_ROLES - completed
    if missing:
        raise ValueError(f"no completed child session evidence for {', '.join(sorted(missing))}")
    return ", ".join(sorted(SMOKE_ROLES))


def completed_child_role(path, parent):
    try:
        with path.open(encoding="utf-8") as stream:
            session = json.loads(stream.readline())
            if session["type"] != "session_meta":
                return None
            source = session["payload"].get("source")
            if not isinstance(source, dict):
                return None
            spawned = source.get("subagent", {}).get("thread_spawn", {})
            role = spawned.get("agent_role")
            if spawned.get("parent_thread_id") != parent or role not in SMOKE_ROLES:
                return None
            for line in stream:
                event = json.loads(line)
                if event["type"] == "event_msg":
                    payload = event["payload"]
                    if payload["type"] == "task_complete" and payload.get("last_agent_message"):
                        return role
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        # Other sessions can be changing. A missing or malformed record cannot attest a role.
        return None
    return None


def recent_sessions():
    codex_home = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
    today = date.today()
    for offset in (0, -1, 1):
        day = today + timedelta(days=offset)
        folder = codex_home / "sessions" / day.strftime("%Y/%m/%d")
        yield from folder.glob("rollout-*.jsonl")


def smoke():
    result = run(
        "codex",
        "--ask-for-approval",
        "never",
        "exec",
        "--strict-config",
        "--enable",
        "multi_agent_v2",
        "--sandbox",
        "read-only",
        "--json",
        "-",
        input=(ROOT / "scripts" / "smoke-agents.txt").read_text(encoding="utf-8"),
        timeout=300,
    )
    if result.returncode:
        raise ValueError(
            f"codex exec exited {result.returncode} (check login and runtime permissions)"
        )
    return smoke_evidence(result.stdout, recent_sessions())


def report(label, check, optional=False):
    try:
        print(f"PASS {label} — {check()}", flush=True)
        return True
    except (OSError, ValueError, KeyError, IndexError, subprocess.TimeoutExpired) as exc:
        status = "PARTIAL" if optional else "FAILED"
        print(f"{status} {label} — {exc}", flush=True)
        return optional


def verify(live=False):
    checks = {
        "Codex CLI": lambda: output("codex", "--version"),
        "Codex authentication": lambda: auth_status(
            ("codex", "login", "status"), "Logged in using ChatGPT"
        ),
        "uv": lambda: output("uv", "--version"),
        "Git": lambda: output("git", "--version"),
        "Node": lambda: output("node", "--version"),
        "npm": lambda: output("npm", "--version"),
        "custom agent definitions": validate_agents,
        "native plugin registration": validate_plugins,
        "Spec Kit integration": validate_spec_kit,
        "effective Codex configuration and skill discovery": validate_discovery,
        "browser MCP registration": validate_mcp,
        "tracked credential filenames": validate_tracked_files,
    }
    results = [report(label, check) for label, check in checks.items()]
    if live:
        results.append(report("live Codex subagent smoke", smoke))
    else:
        print("PARTIAL live Codex subagent smoke requires --smoke")
    return 0 if all(results) else 1


def doctor():
    report(
        "GitHub CLI authentication",
        lambda: auth_status(
            ("gh", "auth", "status", "--hostname", "github.com"), "Logged in to github.com account"
        ),
        optional=True,
    )
    for label, args in {
        "GitHub CLI": ("gh", "--version"),
        "Docker Engine": ("docker", "--version"),
        "Docker Compose": ("docker", "compose", "version"),
        "Docker daemon": ("docker", "info", "--format", "{{.ServerVersion}}"),
    }.items():
        report(label, lambda args=args: output(*args).splitlines()[0], optional=True)
    return verify()


def auth_status(args, expected):
    status = run(*args)
    if status.returncode or expected not in status.stdout + status.stderr:
        raise ValueError("existing sign-in could not be verified")
    return expected


def setup(skip_db):
    tool("uv")
    env_file = ROOT / ".env"
    if not env_file.exists():
        defaults = (ROOT / ".env.example").read_bytes()
        with tempfile.TemporaryDirectory(prefix=".env.setup-", dir=ROOT) as temporary:
            staged = Path(temporary) / ".env"
            staged.write_bytes(defaults)
            staged.chmod(0o600)
            try:
                os.link(staged, env_file)
            except FileExistsError:
                pass
    subprocess.run([tool("uv"), "sync", "--locked", "--extra", "dev"], cwd=ROOT, check=True)
    if not skip_db:
        report(
            "PostgreSQL readiness",
            lambda: (
                output(
                    "docker",
                    "compose",
                    "--env-file",
                    ".env",
                    "up",
                    "-d",
                    "--wait",
                    "--wait-timeout",
                    "90",
                    "postgres",
                    timeout=120,
                )
                or "healthy"
            ),
            optional=True,
        )
    return verify()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("setup").add_argument("--skip-db", action="store_true")
    commands.add_parser("verify").add_argument("--smoke", action="store_true")
    commands.add_parser("doctor")
    args = parser.parse_args()
    try:
        if args.command == "setup":
            return setup(args.skip_db)
        if args.command == "verify":
            return verify(args.smoke)
        return doctor()
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"FAILED setup — {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
