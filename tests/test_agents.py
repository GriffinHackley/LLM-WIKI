import json

import pytest

from wiki_cli import agents
from wiki_cli.agents import AFTER_TOOL, BEFORE_TOOL, EDIT, READ, SESSION_START, SHELL, AgentError, Reply

CLAUDE = agents.CLAUDE_CODE
COPILOT = agents.COPILOT


class TestNames:
    def test_old_names_still_resolve_and_repeats_go(self):
        assert agents.resolve(["claude", "copilot", "claude-code"]) == ("claude-code", "copilot")
        assert agents.resolve("opencode") == ("opencode",)
        assert "claude" in agents.NAMES and "claude-code" in agents.NAMES

    def test_unknown_names_are_refused(self):
        with pytest.raises(AgentError, match="no agent 'cursor'"):
            agents.resolve(["cursor"])
        with pytest.raises(AgentError, match="list of agent names"):
            agents.resolve([1])

    def test_every_agent_with_hooks_can_read_and_reply(self):
        for agent in agents.AGENTS:
            if agent.hook_file:
                assert agent.read_event and agent.write_reply
                assert {kind for kind, _ in agent.events} == {SESSION_START, BEFORE_TOOL, AFTER_TOOL}


class TestClaudeCode:
    """Payload shapes as captured from Claude Code's VS Code extension on Windows."""

    def test_an_edit(self):
        event = CLAUDE.read("PreToolUse", {
            "session_id": "s1", "cwd": "c:\\code", "hook_event_name": "PreToolUse", "tool_name": "Edit",
            "tool_input": {"file_path": "c:\\code\\src\\a.py", "old_string": "x", "new_string": "y"}})
        assert (event.kind, event.session, event.tool, event.paths) == (BEFORE_TOOL, "s1", EDIT, ("c:\\code\\src\\a.py",))

    def test_a_shell_command_and_a_read(self):
        shell = CLAUDE.read("PreToolUse", {"hook_event_name": "PreToolUse", "tool_name": "PowerShell",
                                           "tool_input": {"command": "git commit -m x"}})
        assert (shell.tool, shell.command) == (SHELL, "git commit -m x")
        read = CLAUDE.read("PostToolUse", {"hook_event_name": "PostToolUse", "tool_name": "Read",
                                           "tool_input": {"file_path": "a.py"}, "tool_response": {}})
        assert (read.kind, read.tool) == (AFTER_TOOL, READ)

    def test_a_session_start_and_another_tool(self):
        start = CLAUDE.read("SessionStart", {"hook_event_name": "SessionStart", "source": "resume", "session_id": "s"})
        assert (start.kind, start.source) == (SESSION_START, "resume")
        other = CLAUDE.read("PreToolUse", {"hook_event_name": "PreToolUse", "tool_name": "Grep", "tool_input": {}})
        assert other.tool is None and other.tool_name == "Grep"

    def test_copilots_snake_case_payload_is_not_claude_codes(self):
        assert CLAUDE.read("PreToolUse", {"hook_event_name": "PreToolUse", "timestamp": "2026-10-10T00:00:00Z",
                                          "tool_name": "Read"}) is None
        assert CLAUDE.read("PreToolUse", "not a dict") is None

    def test_replies(self):
        assert CLAUDE.reply(SESSION_START, Reply(context="hi")) == {
            "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "hi"}}
        assert CLAUDE.reply(BEFORE_TOOL, Reply(decision="deny", reason="no")) == {
            "hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": "no"}}
        assert CLAUDE.reply(BEFORE_TOOL, Reply(context="notes"))["hookSpecificOutput"]["additionalContext"] == "notes"
        assert CLAUDE.reply(AFTER_TOOL, Reply()) is None


class TestCopilot:
    """Payload shapes from GitHub's hooks reference; to confirm with `wiki hook --log`."""

    def test_tool_arguments_given_as_json_text(self):
        event = COPILOT.read("preToolUse", {"sessionId": "s2", "timestamp": 1, "cwd": "/w", "toolName": "edit",
                                            "toolArgs": json.dumps({"path": "src/a.py"})})
        assert (event.kind, event.session, event.tool, event.paths) == (BEFORE_TOOL, "s2", EDIT, ("src/a.py",))
        shell = COPILOT.read("preToolUse", {"toolName": "powershell", "toolArgs": {"command": "git commit"}})
        assert (shell.tool, shell.command) == (SHELL, "git commit")
        assert COPILOT.read("preToolUse", {"toolName": "view", "toolArgs": "not json"}).paths == ()

    def test_a_patch_names_its_files(self):
        patch = "*** Begin Patch\n*** Update File: src/a.py\n@@\n-x\n+y\n*** Add File: src/b.py\n+z\n*** End Patch"
        event = COPILOT.read("postToolUse", {"toolName": "apply_patch", "toolArgs": {"input": patch}})
        assert (event.kind, event.paths) == (AFTER_TOOL, ("src/a.py", "src/b.py"))

    def test_the_vs_code_format_is_left_to_claude_code(self):
        assert COPILOT.read("PreToolUse", {"hook_event_name": "PreToolUse", "tool_name": "Read"}) is None

    def test_replies(self):
        """A before-tool hook can only allow, deny or ask: text for the model is dropped."""
        assert COPILOT.reply(BEFORE_TOOL, Reply(context="notes")) is None
        assert COPILOT.reply(BEFORE_TOOL, Reply(decision="ask", reason="commit?")) == {
            "permissionDecision": "ask", "permissionDecisionReason": "commit?"}
        assert COPILOT.reply(AFTER_TOOL, Reply(context="notes")) == {"additionalContext": "notes"}
        assert not COPILOT.context_before_tool and CLAUDE.context_before_tool


def test_an_agent_without_hooks_reads_nothing():
    assert agents.OPENCODE.read("x", {}) is None and agents.OPENCODE.reply(SESSION_START, Reply(context="x")) is None
