# -*- coding: utf-8 -*-
"""截断修复单测：
A. tool_buf 1MB 上限 -> 截到最近完整 </tool_call> 边界（大参数调用不残缺）
B. 断流时 sink["_tool_buf_partial"] 暴露 + 恢复只取完整调用
   （不走修复器——修复会把 Write 半截参数补全成合法调用，静默写坏文件）
"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ctyun_proxy as P

passed = failed = 0
def check(label, cond):
    global passed, failed
    print("[%s] %s" % ("PASS" if cond else "FAIL", label))
    if cond:
        passed += 1
    else:
        failed += 1

# ---------- 公共构件 ----------
class FakeResp:
    """模拟上游响应：按块返回 SSE 字节，耗尽后抛异常（或干净 EOF）。"""
    def __init__(self, chunks, exc=None):
        self.chunks = list(chunks)
        self.exc = exc
        self.closed = False
    def read(self, n):
        if self.chunks:
            return self.chunks.pop(0)
        if self.exc is not None:
            raise self.exc
        return b""
    def close(self):
        self.closed = True

def sse(content):
    d = {"choices": [{"delta": {"content": content}}]}
    return ("data: " + json.dumps(d, ensure_ascii=False) + "\n\n").encode("utf-8")

def new_sink():
    return {"reasoning": "", "text": "", "tool_calls": [], "conversation_id": None}

TOOLS = [{"type": "function", "function": {"name": "Write",
          "parameters": {"required": ["file_path", "content"]}}},
         {"type": "function", "function": {"name": "Read",
          "parameters": {"required": ["file_path"]}}}]
KNOWN = ["Write", "Read"]
REQ = {"Write": ["file_path", "content"], "Read": ["file_path"]}

# ---------- 1. 大参数(>64KB)解析：3 个不同调用全部保留 ----------
big = "A" * 70000
buf = "".join('<tool_call>{"name": "Write", "arguments": {"file_path": "f%d.txt", "content": "%s"}}</tool_call>\n\n' % (i, big)
              for i in range(3))
r = P.parse_tool_calls(buf, known_names=KNOWN, required_params=REQ, strict_declared=set(KNOWN))
check("大参数(70KB x3, 各不相同)全部解析", len(r) == 3 and
      all(len(json.loads(a)["content"]) == 70000 for _, a in r))

# 连续相同调用去重是既定行为（防退化循环），单独断言
buf_same = '<tool_call>{"name": "Write", "arguments": {"file_path": "x.txt", "content": "%s"}}</tool_call>\n\n' % big
buf_same *= 3
r = P.parse_tool_calls(buf_same, known_names=KNOWN, required_params=REQ, strict_declared=set(KNOWN))
check("连续相同调用去重为 1（既定行为）", len(r) == 1)

# ---------- 2. 断流场景：_tool_buf_partial 暴露 ----------
partial_call = ('<tool_call>{"name": "Read", "arguments": {"file_path": "config.json"}}</tool_call>\n\n'
                '<tool_call>{"name": "Write", "arguments": {"file_path": "out.txt", "content": "半截')
resp = FakeResp(
    [sse("好的，我先读取配置文件，然后写入处理结果。"),
     sse(partial_call),
     sse("的内容还没有传完")],
    exc=P.UpstreamStalled("simulated drop"))
sink = new_sink()
out = []
try:
    for c in P.stream_openai_chunks(resp, "test-model", sink, known_names=KNOWN, tools=TOOLS):
        out.append(c)
except P.UpstreamStalled:
    pass
tb = sink.get("_tool_buf_partial", "")
check("断流时 _tool_buf_partial 已暴露", "config.json" in tb and "半截" in tb)

# 危险性对照：不切边界直接解析，修复器会把半截 Write 补成"合法"调用
r_raw = P.parse_tool_calls(tb, known_names=KNOWN, required_params=REQ, strict_declared=set(KNOWN))
w_repaired = [a for n, a in r_raw if n == "Write"]
check("对照：无边界切割时修复器确实会造出半截 Write（危险）",
      len(w_repaired) == 1 and json.loads(w_repaired[0])["content"] == "半截的内容还没有传完")

# 恢复分支语义（与 read_stream_with_retry 内实现一致）：切到最近完整边界
last_end = tb.rfind("</tool_call>")
tb_cut = tb[:last_end + len("</tool_call>")] if last_end >= 0 else ""
r_cut = P.parse_tool_calls(tb_cut, known_names=KNOWN, required_params=REQ, strict_declared=set(KNOWN))
check("恢复分支：只救出完整 Read，丢弃半截 Write",
      len(r_cut) == 1 and r_cut[0][0] == "Read" and
      json.loads(r_cut[0][1])["file_path"] == "config.json")

# ---------- 3. 1MB 上限：截到完整边界，后续块不再读 ----------
def big_call(path, content, close=True):
    s = '<tool_call>{"name": "Write", "arguments": {"file_path": "%s", "content": "%s"}}' % (path, content)
    return s + ("</tool_call>\n\n" if close else "")
payload = (big_call("a.txt", "A" * 500000) +      # 完整调用 A
           big_call("b.txt", "B" * 500000) +      # 完整调用 B
           big_call("c.txt", "C" * 200000, close=False))  # 半截调用 C
marker = sse("SHOULD_NEVER_BE_READ")
resp = FakeResp([sse("<tool_call>"), sse(payload), marker])
sink = new_sink()
out = list(P.stream_openai_chunks(resp, "test-model", sink, known_names=KNOWN, tools=TOOLS))
calls = sink["tool_calls"]
check("1MB 保护触发后连接被关闭且后续块未读", resp.closed and len(resp.chunks) == 1)
check("1MB 截断保留 A/B 完整调用、丢弃半截 C",
      len(calls) == 2 and
      json.loads(calls[0][1])["content"] == "A" * 500000 and
      json.loads(calls[1][1])["content"] == "B" * 500000)
tail = [c for c in out if '"finish_reason":"tool_calls"' in c or '"finish_reason": "tool_calls"' in c]
check("1MB 截断后正常收尾（finish=tool_calls + [DONE]）",
      bool(tail) and out[-1].strip() == "data: [DONE]")

# ---------- 4. 干净流回归：正常 [DONE] 收尾不受影响 ----------
resp = FakeResp([sse("普通回答文本，不调用工具。"), b"data: [DONE]\n\n"])
sink = new_sink()
out = list(P.stream_openai_chunks(resp, "test-model", sink, known_names=KNOWN, tools=TOOLS))
check("干净流：正文下发 + stop + [DONE]",
      sink["text"].startswith("普通回答") and not sink["tool_calls"] and
      out[-1].strip() == "data: [DONE]" and resp.closed)

print("\n通过 %d / 失败 %d" % (passed, failed))
sys.exit(1 if failed else 0)
