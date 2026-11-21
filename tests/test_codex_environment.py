import json
from pathlib import Path
from runpy import run_path

import pytest

smoke_evidence = run_path(str(Path(__file__).parents[1] / "scripts" / "codex_env.py"))[
    "smoke_evidence"
]

ROLES = ("requirements_researcher", "solution_architect", "quality_reviewer")


def rollout(path, role, parent="smoke-parent", completed=True):
    events = [
        {
            "type": "session_meta",
            "payload": {
                "source": {
                    "subagent": {"thread_spawn": {"parent_thread_id": parent, "agent_role": role}},
                }
            },
        }
    ]
    if completed:
        events.append(
            {
                "type": "event_msg",
                "payload": {"type": "task_complete", "last_agent_message": "Completed output"},
            }
        )
    path.write_text("\n".join(map(json.dumps, events)), encoding="utf-8")
    return path


def log(end="turn.completed"):
    return "\n".join(
        map(
            json.dumps,
            [
                {"type": "thread.started", "thread_id": "smoke-parent"},
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": ", ".join(ROLES)},
                },
                {"type": end},
            ],
        )
    )


def test_smoke_verifies_completed_named_roles(tmp_path):
    sessions = [rollout(tmp_path / role, role) for role in ROLES]
    assert smoke_evidence(log(), sessions) == ", ".join(sorted(ROLES))


def test_smoke_ignores_unreadable_unrelated_session_records(tmp_path):
    unrelated = tmp_path / "unrelated"
    unrelated.write_text("{", encoding="utf-8")
    sessions = [
        unrelated,
        tmp_path / "removed",
        *(rollout(tmp_path / role, role) for role in ROLES),
    ]
    assert smoke_evidence(log(), sessions) == ", ".join(sorted(ROLES))


@pytest.mark.parametrize(
    ("role", "parent", "completed", "end"),
    [
        ("worker", "smoke-parent", True, "turn.completed"),
        (ROLES[-1], "another-session", True, "turn.completed"),
        (ROLES[-1], "smoke-parent", False, "turn.completed"),
        (ROLES[-1], "smoke-parent", True, "turn.failed"),
    ],
)
def test_smoke_rejects_unproven_roles_or_failed_parent(tmp_path, role, parent, completed, end):
    sessions = [rollout(tmp_path / name, name) for name in ROLES[:-1]]
    sessions.append(rollout(tmp_path / "third", role, parent, completed))
    with pytest.raises(ValueError, match="completed"):
        smoke_evidence(log(end), sessions)
