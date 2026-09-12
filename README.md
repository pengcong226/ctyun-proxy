# ctyun-proxy — 云智助手 OpenAI 兼容反代

把天翼云「云智助手」桌面客户端（v2.6.4）的对话接口，包成一个本地 **OpenAI 兼容 API**（`/v1/chat/completions`），支持 **SSE 流式**、**思维链（reasoning_content）**、**Agent 工具调用**——可以直接接入 zcode / Cline / Cherry Studio 等任何认 OpenAI 协议的客户端，让云智助手里免费的大模型变成你的日常 agent 引擎。

```
Agent 客户端 (zcode/Cline/...)          云智助手客户端
      │  OpenAI 协议 (tools/SSE)             │ 登录态 + 签名
      ▼                                      ▼
   ctyun-proxy  ──── 提示词级工具调用翻译 ───►  eaichat.ctyun.cn
   127.0.0.1:8317/v1                        (wenc 签名通道)
```

> **免责声明**：本项目通过对官方客户端的本地逆向分析实现，**仅供学习交流与个人研究**。使用它访问上游服务可能违反天翼云用户协议，存在账号被限制的风险；请自行评估，风险自负。请勿用于商业用途或对外提供代理服务。

## 为什么需要"工具调用翻译层"

上游 `/wenc/v3/openai/chat/completions` 名义上兼容 OpenAI 协议，但服务端**自带工具执行器 + 工具名白名单**：请求携带自定义 `tools` 时，模型尝试调用后会被服务端以 `"工具名称不存在: xxx"` 拦截。所以 agent 客户端的多步任务（读文件→改代码→跑测试）根本跑不起来。

本反代在中间做了一层**提示词级翻译**，实测可稳定支撑多步 agent 任务：

| 方向 | OpenAI 协议 | 翻译成 |
|------|------------|--------|
| 请求 | `tools` 数组 | 工具定义注入首条 user 消息 |
| 请求 | `assistant.tool_calls` | `<tool_call>{"name":..,"arguments":{..}}</tool_call>` 文本 |
| 请求 | `tool` 角色结果 | user 消息中的 `<tool_result>` 标签 |
| 响应 | 模型输出 `<tool_call>` | 流式解析回 `tool_calls` delta + `finish_reason="tool_calls"` |

配套的稳定性机制（详见 [DEVLOG.md](DEVLOG.md)）：

- **格式漂移容错解析**：模型偶发名字外置 / XML 参数标签 / 截断 JSON 等 4 种漂移格式，解析器全部兜住；截断 JSON 有修复器补全
- **叙述型中断自愈**：任务中模型"口头描述下一步却不调用工具"时递进施压重试（zcode 长任务中断的主要模式）
- **断流看门狗**：首字节 45s / 中途 90s 超时；10038/10053/10054/IncompleteRead 等断连自动分类重试
- **思维链 + 正文分级流式**：thinking_card → `reasoning_content` 实时下发；正文短文本缓冲（自愈窗口）超 300 字切换实时
- **上游身份注入隔离**：上游服务端会注入"云智助手"角色模板，模型有时会在回复里评论它——缓冲期自动剥离
- **严格工具白名单**：本轮未声明的工具名一律拦截（防提示注入伪造调用）

## 环境要求

- Windows + 已安装并**登录过一次**「云智助手」客户端（反代从 `%APPDATA%\ecloudAiAssistant\config.json` 读取加密凭据并自动解密，客户端本身不需要保持运行）
- Python 3.8+
- `pip install pycryptodome websocket-client`

## 快速开始

```bash
pip install pycryptodome websocket-client
python ctyun_proxy.py            # 默认监听 127.0.0.1:8317
```

看到 `[*] 凭据: YL-Ssid=...` 即启动成功。验证：

```bash
curl http://127.0.0.1:8317/v1/models
```

### 可选：sk（签名密钥）

上游有新旧两条路径：旧路径 `/ai/portal/v3/` 无需签名即可用；新路径 `/ai/portal/wenc/v3/` 需要 Web-Signature 签名（含 sk）。反代默认先走 wenc，拿不到 sk 或被 401 拒绝时自动回退旧路径，**所以 sk 不是必需的**。

要启用 wenc（推荐，更稳定），三选一：

1. `python ctyun_proxy.py --sk <32位hex>`（从客户端会话密钥解出）
2. 环境变量 `CTYUN_SK`
3. **CDP 自动提取**：以调试端口启动云智助手客户端，反代自动 hook 出 sk：
   ```
   "C:\Program Files (x86)\ctyun\ecloudAiAssistant\ecloudAiAssistant.exe" --remote-debugging-port=19233
   ```

### 接入 Agent 客户端

任何 OpenAI 兼容客户端均可：

| 配置项 | 值 |
|--------|-----|
| Base URL | `http://127.0.0.1:8317/v1` |
| API Key | 任意值（如 `sk-ctyun`，不校验） |
| 模型 | 见下表 |

zcode（`~/.zcode/settings.json`）：

```json
{
  "providers": {
    "ctyun": {
      "baseUrl": "http://127.0.0.1:8317/v1",
      "apiKey": "sk-ctyun",
      "models": ["TEXT_GLM_5.3"]
    }
  }
}
```

### 模型列表

| key_model | 显示名 |
|-----------|--------|
| TEXT_GLM_5.3 | 智谱 GLM-5.3 |
| TEXT_GLM_5.2 | 智谱 GLM-5.2 |
| TEXT_DEEPSEEK_V4 | DeepSeek-V4-Flash |
| TEXT_DEEPSEEK_V4_PRO | DeepSeek-V4-Pro |
| TEXT_QWEN_3.7 | 通义千问 Qwen3.7 |
| TEXT_QWEN3.8_MAX | 通义千问 Qwen3.8-Max |

中文名 / 小写名作为 `model` 传入也可以（自动别名映射）。

## 测试

`tests/` 下三个测试脚本（需要反代已启动，除 `test_truncation_fix.py` 为纯离线单测）：

```bash
# 离线单测：工具缓冲截断 / 断流恢复（不需要反代）
python tests/test_truncation_fix.py

# 端到端：天气+计算器多轮 agent 闭环（需要反代 + 登录态）
python tests/test_agent_loop.py

# 压力形态：15 工具 zcode 形态多轮任务（需要反代 + 登录态）
python tests/test_zcode_sim.py
```

## 工作原理（一分钟版）

1. **凭据**：客户端把会话 cookie（YL-Ssid/YL-Token）AES-CBC 加密存在 `config.json`，密钥硬编码在客户端主进程里（`VwYav0grtc4XiJ6K`，全安装相同）——反代读取并解密
2. **请求**：OpenAI 格式 → 上游格式（`key_model`/`enable_thinking`/`tenantId` 等），tools 走提示词注入（见上表）
3. **签名**（wenc 路径）：`Web-Signature = SHA256(sorted_params & MD5(body) & sk & timestamp & random)`，sk 从客户端 webview 的 console 日志 hook（CDP）
4. **响应**：上游 SSE → OpenAI chunks；`thinking_card` 类型 delta 映射为 `reasoning_content`；`<tool_call>` 文本流式解析回协议

完整逆向过程（asar 解包、三层认证体系、签名算法定位、七轮实测修复）见 **[DEVLOG.md](DEVLOG.md)**——25 节攻坚记录。

## 已知限制

- **运行时会生成 `debug/` 目录**（请求快照 + 日志，上限 200 份自动清理）——**内含你的对话内容和凭据，不要外传或提交到 git**
- **提示词级工具调用的固有攻击面**：`<tool_result>` 内容未做伪 XML 转义，工具输出里构造 `</tool_result><tool_call>...` 可伪造已声明工具的调用（白名单挡住未声明工具，但已声明工具的伪造调用能通过）
- usage 统计恒 0（上游不返回 token 计数）
- 非流式路径的 reasoning 不做身份回显剥离（回显几乎只在流式 content 中出现）
- 上游偶发限流/风控，反代内置 2.5s 最小请求间隔（`throttle`）缓解，密集并发仍可能触发
- 仅在 Windows + 客户端 v2.6.4（versionCode 202050500）实测过；客户端大版本更新可能改变协议

## 目录结构

```
ctyun-proxy/
├── ctyun_proxy.py     # 反代主脚本（单文件，无第三方 web 框架）
├── model-list.json    # 上游模型列表抓包（参考）
├── DEVLOG.md          # 完整逆向 + 25 节攻坚记录（脱敏版）
├── sanitize.py        # DEVLOG 脱敏脚本（发布前跑过，可复用于更新）
├── FORUM_POST.md      # 论坛发帖草稿
└── tests/
    ├── test_truncation_fix.py   # 离线单测：截断修复
    ├── test_agent_loop.py       # 端到端：agent 多轮闭环
    └── test_zcode_sim.py        # zcode 形态压力测试
```

建议加 `.gitignore`：`debug/`、`__pycache__/`、`proxy-*.log`。

## 社区

- 本项目通过 [LINUX DO](https://linux.do) 社区分享，感谢社区的开源交流氛围

## 项目状态：快照发布，不维护

这是一次性的**代码快照**，分享逆向思路和可用实现。作者不承诺维护：

- 客户端大版本更新导致失效属预期，不会跟进适配
- 不看 Issue、不接 PR——**欢迎直接 fork 改进**
- 如果你做出了更完善的版本，欢迎在分享帖下回复仓库链接，方便后人找到

已知的技术盲区（供接手者参考）：

- 提示词级工具调用 vs 更协议原生的绕法（服务端工具名白名单校验了 schema，伪装内置工具未成功）
- 工具结果伪造攻击面：`<tool_result>` 未做伪 XML 转义，已声明工具的伪造调用能通过
- 上游断流（10038/10053）目前靠分类重试 + 已流式内容收尾，可能有更优雅的方案
- macOS/Linux 客户端未验证
