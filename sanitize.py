# -*- coding: utf-8 -*-
"""DEVLOG 脱敏脚本（通用版，不含任何真实凭据值）。

用法: python sanitize.py DEVLOG.md

处理规则：
1. YL-Ssid=xxx / YL-Token=xxx 的值打码（保留键名，说明认证方式即可）
2. 独立的 32 位 hex（sk 形态）打码
3. 本机用户名路径 C:\\Users\\<name> -> C:\\Users\\<you>
4. 本机会话 ID sess_xxxxxxxx -> sess_xxx
"""
import io
import re
import sys

p = sys.argv[1] if len(sys.argv) > 1 else "DEVLOG.md"
s = io.open(p, encoding="utf-8").read()

# cookie 值打码
s = re.sub(r"YL-Ssid=[0-9a-f]+", "YL-Ssid=****", s)
s = re.sub(r"YL-Token=[A-Za-z0-9._-]+", "YL-Token=****", s)
# 32 位 hex（sk 形态）打码；协议常量白名单除外（客户端硬编码、全安装相同，
# 属逆向资料应保留，如本地 WS 签名盐）
KEEP = {"6785bb9767289291d7257a8a5eb5ec83"}
s = re.sub(r"(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])",
           lambda m: m.group(0) if m.group(0) in KEEP else "****", s)
# 本机用户名路径
s = re.sub(r"(C:[/\\]+Users[/\\]+)([A-Za-z0-9_.-]+)", r"\1<you>", s)
# 会话 ID
s = re.sub(r"sess_[0-9a-f-]{8,36}", "sess_xxx", s)

io.open(p, "w", encoding="utf-8", newline="\n").write(s)

# 自检：不应再有 cookie 值 / 会话 ID / 非白名单 32 位 hex 残留
for pat, name in ((r"YL-Ssid=[0-9a-f]", "YL-Ssid value"),
                  (r"YL-Token=[A-Za-z0-9]", "YL-Token value"),
                  (r"sess_[0-9a-f]{8}", "sess id")):
    if re.search(pat, s):
        print("LEAK REMAINS:", name)
        sys.exit(1)
leaks = [h for h in re.findall(r"(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])", s) if h not in KEEP]
if leaks:
    print("LEAK REMAINS: 32-hex x%d" % len(leaks))
    sys.exit(1)
print("clean: %d chars" % len(s))
