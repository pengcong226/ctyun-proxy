#!/usr/bin/env python3
"""
复刻 zcode（Claude Code 类编码 agent）的真实请求形态：
- 超长英文 system prompt（~8KB，含工具使用规范）
- 15+ 个工具的完整 JSON schema（Bash/Read/Write/Edit/Grep...）
- 两轮 agent 循环（第一轮 tool_calls -> 工具结果 -> 第二轮）
- 流式 + 非流式都测
用于复现"卡在第二步"的问题。
"""
import json
import sys
import time
import urllib.request
import urllib.error

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = "http://127.0.0.1:8317/v1/chat/completions"


def tool(name, desc, params):
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": params,
                       "required": list(params.keys())[:1]}}}


# 模拟 zcode 的工具集（15 个，schema 描述尽量真实长度）
TOOLS = [
    tool("Bash", "Executes a given bash command with timeout. Returns stdout/stderr. "
                  "Working directory persists between calls. Use for git, npm, build, test.",
         {"command": {"type": "string", "description": "The command to execute"},
          "timeout": {"type": "number", "description": "Optional timeout in ms"},
          "description": {"type": "string", "description": "Clear description of what this command does"}}),
    tool("Read", "Reads a file from the local filesystem. Returns content with line numbers. "
                 "Supports offset and limit for partial reads of large files.",
         {"file_path": {"type": "string", "description": "Absolute path to the file"},
          "offset": {"type": "number"}, "limit": {"type": "number"}}),
    tool("Write", "Writes a file to the local filesystem, overwriting if exists. "
                  "Fails if overwriting a file that was not Read first.",
         {"file_path": {"type": "string"}, "content": {"type": "string"}}),
    tool("Edit", "Performs exact string replacement in a file. The old_string must match exactly "
                 "including whitespace and be unique within the file.",
         {"file_path": {"type": "string"}, "old_string": {"type": "string"},
          "new_string": {"type": "string"}, "replace_all": {"type": "boolean"}}),
    tool("Glob", "Fast file pattern matching tool. Returns matching file paths sorted by modification time.",
         {"pattern": {"type": "string"}, "path": {"type": "string"}}),
    tool("Grep", "Searches file contents using regular expressions. Supports ripgrep-style syntax "
                 "with output modes content/files/count.",
         {"pattern": {"type": "string"}, "path": {"type": "string"},
          "include": {"type": "string"}, "output_mode": {"type": "string"}}),
    tool("TodoWrite", "Creates and manages a structured task list for the current session. "
                      "Use for complex multi-step tasks to track progress.",
         {"todos": {"type": "array", "items": {"type": "object"}}}),
    tool("WebFetch", "Fetches a URL, converts to markdown, and answers a prompt against the content "
                     "using a small fast model.",
         {"url": {"type": "string"}, "prompt": {"type": "string"}}),
    tool("WebSearch", "Searches the web for a query and returns results with titles, URLs and snippets.",
         {"query": {"type": "string"}}),
    tool("TaskOutput", "Retrieves output from a running or completed background task by its ID.",
         {"task_id": {"type": "string"}, "block": {"type": "boolean"}}),
    tool("TaskStop", "Stops a running background task by its ID.",
         {"task_id": {"type": "string"}}),
    tool("Agent", "Launches a subagent to handle complex multi-step tasks. Available types: "
                  "general-purpose, Explore, judge.",
         {"description": {"type": "string"}, "prompt": {"type": "string"}}),
    tool("AskUserQuestion", "Asks the user a clarifying question with options. Use only when blocked "
                            "on a decision that is genuinely the user's to make.",
         {"questions": {"type": "array"}}),
    tool("EnterPlanMode", "Transitions into plan mode for designing implementation approaches "
                          "before writing code.", {}),
    tool("Skill", "Executes a specialized skill by name. Only invoke skills that appear in the "
                  "available skills list.", {"skill": {"type": "string"}}),
]

SYSTEM = """You are an interactive coding agent operating in a terminal environment. You help users with software engineering tasks.

# Harness
- Text output is displayed as GitHub-flavored markdown in a terminal.
- Tools run behind a user-selected permission mode; a denied call means the user declined it.
- Prefer dedicated file/search tools over shell commands when one fits.
- Independent tool calls can run in parallel in one response.

# Environment
- Primary working directory: C:\\Users\\demo\\projects\\demo
- Platform: win32, Shell: Git Bash, OS: Windows 10 x64
- Today's date: 2026-09-11

# Communicating with the user
- Your text output is what the user reads. Write it for a teammate catching up.
- Lead with the outcome. First sentence should answer "what happened".
- Keep updates brief while working; give brief status notes when finding something load-bearing.
- Match response to question: simple questions get direct answers in prose.
- Reference code as file_path:line_number.

# Doing tasks
- When you have enough information to act, act. Do not re-derive established facts.
- For reversible actions that follow from the request, proceed without asking.
- Before running state-changing commands, check evidence supports that action.
- Write code that reads like the surrounding code.

# Tool use guidelines
- Use the Bash tool for running commands; Read for files; Grep/Glob for searching.
- Only write comments to state constraints the code cannot show.
- Test your changes when possible before reporting completion.
- Report outcomes faithfully: if tests fail, say so with the output.

# Task tracking
- For non-trivial multi-step work, use TodoWrite to maintain a task list.
- Keep one item in_progress at a time; mark items completed when done.

# Code style
- Match existing conventions in the codebase.
- Prefer standard library functions over reinventing them.
- Keep functions focused; extract helpers when it improves clarity.
""" + "# Additional context\n" + "\n".join(
    "- Guideline %d: Follow established patterns in the codebase. When modifying existing code, "
    "read the surrounding context first and match its style, naming, and idiom. Avoid introducing "
    "new dependencies without justification. Consider edge cases like empty inputs, unicode, and "
    "concurrent access. Document public interfaces." % i for i in range(1, 40))


def chat(messages, stream=True, label=""):
    body = {"model": "TEXT_GLM_5.3", "messages": messages, "tools": TOOLS,
            "tool_choice": "auto", "stream": stream}
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(BASE, data=data, headers={"Content-Type": "application/json"})
    print("[%s] 请求体 %d bytes, msgs=%d, stream=%s" % (label, len(data), len(messages), stream))
    t0 = time.time()
    try:
        resp = urllib.request.urlopen(req, timeout=240)
    except urllib.error.HTTPError as e:
        print("  !! HTTP %d after %.1fs: %s" % (e.code, time.time() - t0,
                                                e.read().decode("utf-8", "replace")[:300]))
        return None
    if not stream:
        d = json.loads(resp.read().decode("utf-8"))
        ch = d["choices"][0]
        print("  -> %.1fs finish=%s content=%d字 tool_calls=%d" % (
            time.time() - t0, ch["finish_reason"], len(ch["message"].get("content") or ""),
            len(ch["message"].get("tool_calls") or [])))
        return d
    # 流式：记录首块时间和总时长
    content, tcs, finish = "", {}, None
    t_first = None
    n_chunks = 0
    try:
        for blk in resp.read().decode("utf-8").split("\n\n"):
            for line in blk.split("\n"):
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if not payload or payload == "[DONE]":
                    continue
                try:
                    d = json.loads(payload)
                except Exception:
                    continue
                if t_first is None:
                    t_first = time.time()
                n_chunks += 1
                ch = (d.get("choices") or [{}])[0]
                delta = ch.get("delta") or {}
                content += delta.get("content") or ""
                for tc in delta.get("tool_calls") or []:
                    i = tc.get("index", 0)
                    slot = tcs.setdefault(i, {"id": None, "name": "", "args": ""})
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        slot["name"] = fn["name"]
                    if fn.get("arguments"):
                        slot["args"] += fn["arguments"]
                if ch.get("finish_reason"):
                    finish = ch["finish_reason"]
    except Exception as e:
        print("  !! 流中断 %.1fs (已收 %d chunks): %r" % (time.time() - t0, n_chunks, e))
        return None
    tool_calls = [{"id": tcs[i]["id"], "type": "function",
                   "function": {"name": tcs[i]["name"], "arguments": tcs[i]["args"] or "{}"}}
                  for i in sorted(tcs) if tcs[i]["name"]]
    msg = {"role": "assistant", "content": content or None}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    print("  -> 首块 %.1fs 总 %.1fs finish=%s chunks=%d content=%d字 tool_calls=%s" % (
        (t_first - t0) if t_first else -1, time.time() - t0, finish, n_chunks,
        len(content), [t["function"]["name"] for t in tool_calls]))
    return {"choices": [{"message": msg, "finish_reason": finish or "stop"}]}


def run_two_rounds(stream, label):
    print("=" * 72)
    print("[%s]" % label)
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": "帮我看一下这个项目里有没有硬编码的密钥或密码。先列出项目文件，再搜索可疑模式，最后给出结论。"},
    ]
    # 第 1 轮
    r = chat(messages, stream=stream, label="第1轮")
    if not r:
        return
    ch = r["choices"][0]
    msg = ch["message"]
    if not msg.get("tool_calls"):
        print("  (模型直接回答，未调工具)")
        return
    messages.append({"role": "assistant", "content": msg.get("content"),
                     "tool_calls": msg["tool_calls"]})
    # 模拟工具执行
    for tc in msg["tool_calls"]:
        result = {
            "Bash": "main.py\nutils.py\nconfig.json\nREADME.md\nrequirements.txt",
            "Grep": "config.json:2: \"api_key\": \"sk-abc123...\"\nmain.py:15: password = \"admin123\"",
        }.get(tc["function"]["name"], "(空输出)")
        messages.append({"role": "tool", "tool_call_id": tc["id"],
                         "name": tc["function"]["name"], "content": result})
    # 第 2 轮（zcode 卡住的地方）
    r2 = chat(messages, stream=stream, label="第2轮")
    if r2:
        m = r2["choices"][0]["message"]
        print("  [第2轮回答] %s" % ((m.get("content") or "")[:300]))


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "both"
    if which in ("stream", "both"):
        run_two_rounds(True, "流式两轮（zcode 形态）")
    if which in ("nonstream", "both"):
        run_two_rounds(False, "非流式两轮")
