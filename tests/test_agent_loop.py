#!/usr/bin/env python3
"""
模拟真实 agent 客户端的多步工具调用闭环测试。
完全按 Cline / Claude Code 等 OpenAI 兼容 agent 的方式调用反代：
  system + tools + tool_choice=auto，流式聚合 tool_calls，本地执行工具，
  回传 assistant(tool_calls) + tool(结果)，循环直到 finish_reason=stop。
"""
import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8317/v1/chat/completions"

TOOLS = [
    {"type": "function", "function": {
        "name": "get_weather",
        "description": "获取指定城市的当前天气，返回天气状况和气温",
        "parameters": {"type": "object",
                       "properties": {"city": {"type": "string", "description": "城市名"}},
                       "required": ["city"]}}},
    {"type": "function", "function": {
        "name": "calculator",
        "description": "计算数学表达式，支持加减乘除和括号",
        "parameters": {"type": "object",
                       "properties": {"expression": {"type": "string", "description": "如 30-25"}},
                       "required": ["expression"]}}},
]

WEATHER = {"北京": "晴，气温 25°C，东南风2级", "上海": "多云，气温 30°C，微风"}

def exec_tool(name, args):
    if name == "get_weather":
        return WEATHER.get(args.get("city", ""), "未收录该城市天气")
    if name == "calculator":
        try:
            return str(eval(args.get("expression", ""), {"__builtins__": {}}, {}))
        except Exception as e:
            return "计算错误: %s" % e
    return "未知工具: %s" % name

def chat(messages, stream=True):
    body = {"model": "TEXT_GLM_5.3", "messages": messages, "tools": TOOLS,
            "tool_choice": "auto", "stream": stream}
    req = urllib.request.Request(BASE, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    resp = urllib.request.urlopen(req, timeout=300)
    if not stream:
        return json.loads(resp.read().decode("utf-8"))
    # SSE 聚合成与非流式相同的结构
    content = ""
    reasoning = ""
    tcs = {}  # index -> {"id":, "name":, "args": ""}
    finish = None
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
            ch = (d.get("choices") or [{}])[0]
            delta = ch.get("delta") or {}
            content += delta.get("content") or ""
            reasoning += delta.get("reasoning_content") or ""
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
    tool_calls = []
    for i in sorted(tcs):
        slot = tcs[i]
        if slot["name"]:
            tool_calls.append({"id": slot["id"] or ("call_%d" % i), "type": "function",
                               "function": {"name": slot["name"], "arguments": slot["args"] or "{}"}})
    msg = {"role": "assistant", "content": content or None}
    if reasoning:
        msg["reasoning_content"] = reasoning
    if tool_calls:
        msg["tool_calls"] = tool_calls
    if tool_calls and finish != "stop":
        finish = "tool_calls"
    return {"choices": [{"message": msg, "finish_reason": finish or "stop"}]}

def run_agent(label, system_prompt, task, stream=True, max_steps=12):
    print("=" * 72)
    print("[%s] stream=%s" % (label, stream))
    messages = [{"role": "system", "content": system_prompt},
                {"role": "user", "content": task}]
    for step in range(1, max_steps + 1):
        r = chat(messages, stream=stream)
        ch = r["choices"][0]
        msg = ch["message"]
        print("  -- 第 %d 轮 finish=%s" % (step, ch["finish_reason"]))
        if msg.get("content"):
            print("     [content] %s" % msg["content"][:160].replace("\n", " "))
        if ch["finish_reason"] == "tool_calls" or msg.get("tool_calls"):
            messages.append({"role": "assistant", "content": msg.get("content"),
                             "tool_calls": msg["tool_calls"]})
            for tc in msg["tool_calls"]:
                try:
                    args = json.loads(tc["function"]["arguments"])
                except Exception:
                    args = {}
                result = exec_tool(tc["function"]["name"], args)
                print("     [tool] %s(%s) -> %s" % (tc["function"]["name"],
                                                    json.dumps(args, ensure_ascii=False), result))
                messages.append({"role": "tool", "tool_call_id": tc["id"],
                                 "name": tc["function"]["name"], "content": result})
            continue
        print("  [最终回答] %s" % (msg.get("content") or "")[:500])
        return step
    print("  !! 超过最大步数")
    return -1

if __name__ == "__main__":
    tests = sys.argv[1:] or ["zh", "zh-ns", "en"]

    if "zh" in tests:
        run_agent("中文短system+流式",
                  "你是一个严谨的任务执行助手，善于利用工具完成多步任务，不要编造数据。",
                  "帮我查一下北京和上海现在的天气，然后用计算器算出两地温差是多少摄氏度，最后给出出行建议。",
                  stream=True)

    if "zh-ns" in tests:
        run_agent("中文短system+非流式",
                  "你是一个严谨的任务执行助手，善于利用工具完成多步任务，不要编造数据。",
                  "帮我查一下北京和上海现在的天气，然后用计算器算出两地温差是多少摄氏度，最后给出出行建议。",
                  stream=False)

    if "en" in tests:
        # 模拟真实编码 agent 的长英文 system prompt（截取 Claude Code 风格）
        long_sys = (
            "You are a coding agent operating inside a terminal environment. "
            "You have access to tools that let you interact with the local system. "
            "Follow these principles strictly:\n\n"
            "1. ALWAYS use tools to gather information before answering questions about "
            "files, system state, or external data. Never fabricate tool results.\n\n"
            "2. For multi-step tasks, break the problem down: call tools as needed, "
            "observe results, then continue reasoning. Do not try to answer everything "
            "in one step.\n\n"
            "3. When you decide to call a tool, emit the call and stop. Wait for the "
            "result before continuing.\n\n"
            "4. Be concise in your final answers. Use markdown formatting when helpful.\n\n"
            "5. If a task is ambiguous, state your assumptions and proceed with the most "
            "reasonable interpretation.\n\n"
            "Current environment: Windows 11, working directory C:\\projects\\demo, "
            "shell is Git Bash. The user prefers answers in the language of their query. "
            "You have file system access tools, a weather lookup tool, and a calculator."
        )
        run_agent("英文长system+流式",
                  long_sys,
                  "Check the current weather in Beijing and Shanghai, calculate the "
                  "temperature difference between the two cities, then recommend which "
                  "city is better for an outdoor walk today.",
                  stream=True)
