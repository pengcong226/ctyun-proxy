# 天翼云·云智助手（ecloudAiAssistant）逆向分析报告

- 分析对象：`C:\Program Files (x86)\ctyun\ecloudAiAssistant`（v2.5.5，versionCode 202050500）
- 厂商：天翼云科技有限公司（中国电信）
- 分析方式：asar 解包 + JS 静态分析 + 凭据解密验证（本地，2026-09-11）
- 应用定位：云电脑场景 AI 助手（聊天/截屏/OCR/绘图/MCP 工具/文件搜索）

## 1. 技术架构

```
Electron 33 + Vue2 + ElementUI（主壳，157MB 单文件 exe）
├── dist/electron/main.js（主进程，webpack 打包 1MB）
├── static/dist/js/*（渲染层 UI，97 个 chunk）
├── resources/additional/
│   ├── ctmeta.exe（34KB C++ 原生辅助）
│   ├── aikernel.node / aiutil.node（原生 addon：划词/截屏内核）
│   └── _internal/（Python 3.10 运行时 + psutil —— 供本地工具进程）
├── servers/（filesearch、tiktok 子服务）
└── 依赖亮点：@modelcontextprotocol/sdk（MCP）、protobufjs、ws、crypto-js
```

## 2. 服务端点

| 域名 | 用途 |
|------|------|
| `https://eaichat.ctyun.cn:443` | EAI 主后端（聊天/用户配置/文件） |
| `https://gwyilian.ctyun.cn` | 网关（eaiSysInfo 下发服务器列表） |
| `https://desk.ctyun.cn:8810` | 云电脑桌面服务 |
| `https://desk.ctyun.cn/cloudB/dy/iam/...` | IAM 统一认证（CAS） |
| `gateway-test.bgzs.site / eai-test.bgzs.site` | 测试环境 |
| `ws://127.0.0.1:9002`（或注册表 HKLM\SOFTWARE\ecloudsoft\Mirror\ClinkAgent\WebSocketPort） | 本地云电脑代理 WS |

关键 API：
```
POST /ai/portal/v3/openai/chat/completions   ← 聊天（OpenAI 兼容，SSE 流式）
POST /ai/portal/v1/auth/iam/accessToken      ← IAM 换 token
POST /api/auth/client/exchangeToken          ← IAM token 刷新（gwyilian）
GET  /ai/portal/v1/ws/getAvailableNode       ← MCP WS 节点
POST /ai/portal/v1/file/upload / vector/upload / v3/image/ocr ...
本地模型回退：http://127.0.0.1:11168/v2/openai/chat/completions
```

## 3. 认证体系（三层）

### ① IAM 签名（gwyilian 网关请求）
```js
// getIamRequestSignature(userId, secretKey)：
sig = MD5( devicetype + requestId(uuid) + timestamp(uuid) + userId
         + 202050500 /*versionCode*/ + serverNodeId + apiPath + secretKey )
头: CTG-VERSION/CTG-USERID/CTG-DEVICETYPE/CTG-TIMESTAMP/CTG-REQUESTID/
    CTG-APPMODEL:28/CTG-DEVICECODE/CTG-SERVERNODE/CTG-SIGNATURE
```

### ② 本地凭据存储（electron-store，AES-CBC）
- 密钥=IV=`VwYav0grtc4XiJ6K`（硬编码，main.js 模块 69581）
- `config.json` 的 `ian` 项 = `{token: IAM-JWT, secretKey, userId}`（实测解密成功）
- `number_or_string` 项 = YL-Ssid/YL-Token 会话 cookie（实测解密成功）
- 第二密钥 `chinatelecom@cnn`（AES-ECB，解密网关下发的 eaiSysInfo）

### ③ 本地 WS 签名（ClinkAgent 云电脑通道）
```
sign = SHA256( "aiAssistant" + "6785bb9767289291d7257a8a5eb5ec83" + t + "25" )
ws://127.0.0.1:{port}?appid=aiAssistant&sign={sign}&t={t}&type=25
```

### ④ 租户密钥（渲染层）
```
tenantCode: "eai", tenantSecret: "35weqiskfudii8jh27qnus67294dusa"
```

## 4. 聊天请求结构（实测代码提取）

```json
POST {eaiHost}/ai/portal/v3/openai/chat/completions
Headers: Content-Type: application/json
         x-client-trace-id: {uuid}
         x-eai-xuid / x-eai-env / x-eai-version / x-user-agent
         （认证经 401→重登流程，token 由主进程注入）
Body: {
  "key_model": "<模型名>",
  "messages": [...],
  "stream": true,
  "client_retry": true,
  "web_search": false,
  "agentId": "...",           // 知识库 agent 时省略
  "tenantId": 14,
  "enable_thinking": bool,
  "message_id": n, "conversation_id": "...",
  "tools": [...]              // 可选
}
```
- 响应：SSE 流式（fetch + ReadableStream 解析）
- 401 → "会话已过期" → 重登

## 5. MCP 通道
- MCP WebSocket（protobuf 消息：client.login.req/heartbeat.req/popup.task.push）
- 消息解密用 `chinatelecom@cnn` AES-ECB

## 6. 防护评估

| 维度 | 状况 |
|------|------|
| 代码保护 | **无**：asar 未加密、无 fuses 加固、webpack 混淆仅压缩 |
| 密钥管理 | **弱**：AES 密钥/tenantSecret/aiScrt 全部硬编码在前端 JS |
| 凭据存储 | electron-store + 硬编码 AES（拿到文件即解密，无 DPAPI） |
| 请求签名 | MD5 拼接（密钥在 ian 存储内，可离线重算） |
| 复用难度 | **低**：解密 ian → 取 token/secretKey → 重算 CTG-SIGNATURE 或直接用 YL-Token |

## 7. 本机数据路径

| 数据 | 路径 |
|------|------|
| 配置+凭据 | `%APPDATA%\ecloudAiAssistant\config.json` |
| 缓存 | `%APPDATA%\ecloudAiAssistant\Cache` 等 |

## 8. 风险提示
- 复用登录态调用接口违反天翼云用户协议
- 本资料仅用于安全研究/互操作性理解

## 9. 动态验证（2026-09-11，CDP 抓包闭环）

方法：`--remote-debugging-port=19233` + CDP Network 域监听 webview target，
通过 Runtime.evaluate 向 contenteditable 注入消息 + 派发 Enter 触发真实发送。

### 实测聊天请求（完整闭环）

```
POST https://eaichat.ctyun.cn/ai/portal/v3/openai/chat/completions

Headers（认证载体确认）:
  cookie: YL-Ssid=****（已打码）; YL-Token=****（已打码）   ← 认证就是这对 cookie
          （与 config.json number_or_string 项 AES 解密结果完全一致）
  x-eai-env: pubInternet
  x-eai-mode: eai
  x-eai-xuid: pubinter_pubinter_b3926c37-a4d1-443b-9680-2abc61be087a
  x-eai-source: app-eai
  x-eai-tenant-id: 14
  x-eai-version: 202050500
  x-user-agent: Windows_NT 10.0.28000 x64;win32;10.0.28000;202050500
  x-client-trace-id: <uuid>
  Content-Type: application/json

Body:
{"key_model":"TEXT_QWEN_3.7",
 "messages":[{"role":"user","content":"...","verify_id":"<uuid>",
              "ref":{"type":"file","file":[]}}],
 "stream":true,"client_retry":true,"web_search":true,
 "tenantId":14,"enable_thinking":false}
```

### 关键结论

1. **认证 = YL-Ssid + YL-Token cookie**（无 Authorization 头、无签名）——
   这对 cookie 就存在 electron-store 里、用硬编码 AES 密钥加密，
   静态解密结果与运行时请求**逐字节一致**（闭环验证）
2. YL-Token 是 HS256 JWT（iss=ctg.yilian.sso, clientId=eaiapp, role=loginUser），
   内含 `token: YL-SSOIAM-<uuid>`，有效期约 7 天
3. CTG-SIGNATURE（IAM 签名）只用于 gwyilian 网关的 exchangeToken 等接口，
   聊天接口本身不签名
4. 复用 PoC 最短路径：解密 config.json → 取 YL-Ssid/YL-Token →
   带 x-eai-* 头 + cookie 直接 POST completions（SSE 流式）

样本存档：`chat-request-capture.json`（含请求头/体/cookie 全量）

## 10. 模型与额度（2026-09-11 实测）

### 模型清单（GET /ai/portal/v2/openai/chat/queryModels，实时返回）

| 显示名 | key_model | 后端真实模型 | 特性 |
|--------|-----------|--------------|------|
| 通义千问 Qwen3.7 | TEXT_QWEN_3.7 | qwen3.7-plus-ctyun-pt-api | 思考模式 |
| DeepSeek-V4-Flash | TEXT_DEEPSEEK_V4 | deepseek-v4-flash-0731-ctyun-pt-api（主）+ v4-ctyunmass-api（备） | 百万上下文 |
| 智谱 GLM-5.2 | TEXT_GLM_5.2 | glm5.2-cdn-api（主/备双路由） | 1M 上下文 |
| DeepSeek-V4-Pro | TEXT_DEEPSEEK_V4_PRO | deepseek-v4-pro-0813-ctyun-pt-api | 百万级上下文 |
| 智谱 GLM-5.3 | TEXT_GLM_5.3 | glm-5.3-ctyun-pt-api | 最新旗舰 |
| 通义千问 Qwen3.8-Max | TEXT_QWEN3.8_MAX | qwen3.8-max-ctyun-pt-api | 视觉理解 |

- 全部 `status=avaiable`、`accessType=public`（租户 14 = 天翼云公众版云电脑）
- `metadata: {"hasThinkMode":true}` → 全部支持深度思考（enable_thinking）
- 另有自定义模型 API（/ai/portal/v1/model/{add,del,update,getCustomModelList}）
  和本地模型回退（127.0.0.1:11168，qwen3:32b 等 Ollama 风格）
- 静态注册表（TEXT_A1~A20/EDU/MED/PSY/LAW）是旧版兜底，oldname 显示
  早期后端为 xingchen_12b/llama3/chatglm3/glm-4v

### 额度机制（实测结论）

1. **无客户端额度显示**：UI 无用量/次数/token 计数，响应头无 quota 字段，
   SSE 流无 usage 统计 —— 计费/限额完全在服务端黑箱执行
2. **VIP 体系**（/ai/portal/v1/user/queryUserInfo）：
   - `vipType: "vip" | "normal"`（实测本机当前为 vip，vipExpireDate=2024-12-31
     已过期但类型仍为 vip —— 服务端未强制降级）
   - VIP 领取活动：/ai/portal/v1/activity/claimVip（"活动截止2025年12月31日"）
   - **VIP 门控的功能**（前端硬编码列表）：PPT生成、Word写作、AI会议助手、
     OCR图片转文字、海报设计、人像美化、智能阅卷、校园帮办、PPT课件助手、
     写作指导、心灵树洞 —— 非 vip 弹"此功能仅支持高级版用户使用"
   - **基础聊天不受 VIP 门控**（实测过期 VIP 状态下 6 个模型全部可用）
3. **实测直连**：用解密 cookie 直接 POST completions，HTTP 200 + SSE 流式
   正常返回（模型 qwen3.7-plus，答案正确）—— 无频控触发
4. 推测：额度按"云电脑订阅"捆绑（天翼云电脑高级版自带 AI 权益），
  聊天本身可能不限次，VIP 只解锁增值工具 —— 服务端限流策略未知

样本：`model-list.json`（完整 queryModels 响应）

## 11. 2.6.4 升级差异与适配（2026-09-11）

### 升级概况
2.5.5 (202050500) → 2.6.4 (202060402)，核心变化是**新增请求签名体系**。

### 逐项验证结果

| 项 | 2.5.5 | 2.6.4 | 影响 |
|---|-------|-------|------|
| AES 密钥 VwYav0grtc4XiJ6K | ✓ | ✓ 未变 | 凭据解密仍有效 |
| aiScrt / tenantSecret / chinatelecom@cnn | ✓ | ✓ 未变 | — |
| 聊天端点 | /ai/portal/v3/... | **/ai/portal/wenc/v3/...** | 路径加 /wenc/（加密网关通道） |
| 请求体 | key_model/messages/verify_id/... | 同 + 可选 action 字段（PPT 等） | 兼容 |
| versionCode | 202050500 | 202060402 | UA/x-eai-version 同步更新 |
| **签名** | 无 | **Web-Signature（SHA256）** | 新增 |
| 新头 | — | YL-Main-Version: 202060401, YL-Product-Id: 5 | 新增 |

### Web-Signature 签名体系（新逆向，已 100% 离线验证）

```
origin = sorted_params(k=v&...) [& MD5(body_json)] & sk & timestamp_ms & random8
sign   = SHA256(origin)

头:
  Web-Signature: <sign hex>
  Web-Timestamp: <毫秒时间戳>
  Web-Random:    <8位随机字母数字>
  YL-Main-Version: 202060401
  YL-Product-Id: 5
```

- POST：params 为空 → origin = MD5(body) + "&" + sk + "&" + ts + "&" + rnd
- GET：origin = 排序后的 query 参数 + "&" + sk + "&" + ts + "&" + rnd
- 双样本（POST 聊天 + GET 遥测）离线验证全部命中

### sk（签名密钥）体系
- 来源：登录时密钥协商 —— 客户端生成随机 clientKey（16字符）→
  RSA-PKCS1v1.5 加密（服务端 ssopk 公钥，forge 库）→
  `/sso/login/v2/iam/encryptedTokenAuthorize` → 服务端返回 AES-ECB 加密的
  会话密钥 → 客户端解密得到 sk（32位hex，实测 ****（32位hex，已打码））
- sk 仅存内存（MS_ACCOUNT.clientSessionKey），不落盘、不进 localStorage
- 公网模式也启用（2.5.5 的"无签名"结论已过时）

### 反代适配（ctyun_proxy.py 已更新）
1. 路径自动走 /wenc/
2. Web-Signature 全量计算（与官方逐字段一致）
3. sk 获取：`--sk` 参数 / CTYUN_SK 环境变量 / **CDP 自动提取**（云智助手以
   `--remote-debugging-port=19233` 启动时，代理通过 console.log hook 自动读取）
4. versionCode/UA/YL 头全部对齐 2.6.4
5. 验证：6 模型 + 流式 + 多轮记忆 + 深度思考全部通过

### 风险提示
- 旧版无签名请求目前仍被服务端接受（向后兼容），但签名已是客户端默认行为，
  长期看强制校验是趋势——反代已提前适配

## 12. Agent 多步任务限制与工具调用翻译层（2026-09-11）

### 问题现象
反代接入 agent 客户端（Cline/Claude Code 类）后模型只能一问一答，
无法执行超过两步的任务。

### 根因（协议探测实测）
上游 `/wenc/v3/openai/chat/completions` 虽是 OpenAI 兼容路径，但它是
**带服务端工具执行器的 agent 网关**：
1. 请求带自定义 `tools` 时模型确实会尝试调用（协议层支持 function calling），
   但服务端有**工具名白名单**，拦截并以正文形式返回 `工具名称不存在: xxx`
   ——只允许它自家 11 个内置工具（PPT 生成、翻译等 VIP 工具）
2. `max_tokens` 被忽略（非输出长度限制问题）
3. `system` 角色可用但有风险：注入长工具提示词时触发模型退化循环
   （实测 5474 个事件重复输出同一片段）；同样的提示词注入 user 消息则 100% 干净

### 解法：提示词级工具调用（双向翻译层）
请求方向（OpenAI 协议 -> 提示词）：
- `tools` 数组 -> 工具定义注入首条 user 消息（system 内容一并合并，避开 system 退化）
- `assistant.tool_calls` -> 还原为 `<tool_call>{"name":..,"arguments":{..}}</tool_call>` 文本
- `role:"tool"` 消息 -> user 消息中的 `<tool_result>` 标签
- 连续同角色消息自动合并（上游按官方客户端 u/a 交替习惯设计）
- 工具请求走无状态模式（每轮全量上下文），普通聊天保留 conversation_id 续传

响应方向（提示词 -> OpenAI 协议）：
- 模型输出 `<tool_call>{...}</tool_call>` -> 流式解析为 `tool_calls` delta，
  `finish_reason="tool_calls"`，agent 客户端可直接循环执行
- 标签前后的普通文本正常透传（保留 12 字符尾部防止标签被 SSE 分片切开）
- 退化循环保护：tool 缓冲超 64KB 截断 + 连续重复调用去重
- 解析失败时原文作为 content 吐出（可见性优于静默丢弃）

### 验证（test_agent_loop.py，模拟真实 agent 行为）
| 场景 | 结果 |
|------|------|
| 中文短 system + 流式 | 4 轮闭环：查北京→查上海→算温差→最终回答 ✓ |
| 中文短 system + 非流式 | 同上 4 轮闭环 ✓ |
| 英文长 system（Claude Code 风格）+ 流式 | 4 轮闭环，**第一轮并行调用 2 个工具**，工具报错后自动用中文城市名重试 ✓ |
| 纯聊天回归（无 tools） | 正常，多轮记忆不受影响 ✓ |

### 结论
"模型被限制自动执行步数"的猜测不成立——模型本身的 agent 能力完好
（并行调用、错误恢复、多步推理都有），限制在上游网关的工具白名单。
翻译层绕开后，云智助手 6 个模型可作为完整 agent 后端使用。

## 13. Agent 客户端实战问题修复（2026-09-11，zcode 实测反馈）

### 问题：zcode 接入后"卡在第二步"
反代日志显示请求全部 200，但客户端连接挂起 4 分钟不结束。

### 根因（两个叠加）
1. **SSE 流永不终止（卡死直接原因）**：反代用 HTTP/1.1 keep-alive 但流式响应
   既无 Content-Length 也无 Connection:close——客户端收到 [DONE] 后仍在等
   连接层 EOF，无限挂起。测试脚本用 urllib（自动 Connection:close）没暴露，
   zcode 用 keep-alive 连接才触发
2. **模型输出格式漂移（agent 循环断掉根因）**：长上下文 + 多工具时模型
   确实想调工具，但格式跑偏成三种变体（实测抓到）：
   - `<tool_call>Bash>{"arguments": {...}}`（工具名在 JSON 外）
   - `<tool_call>Grep<arg_key>pattern</arg_key><arg_value>...</arg_value>`（自造 XML 参数标签）
   - 截断的混乱体（流中断）
   旧解析器只认标准 JSON → 解析失败 → 整段当文本返回 → 客户端等不到 tool_calls

### 修复
1. 流式响应加 `Connection: close` + `close_connection = True`，[DONE] 后立即关连接
2. 解析器重写：容忍全部三种漂移格式（JSON 前文本提取工具名 + XML 参数标签
   解析 + 已知工具名模糊匹配兜底 + 截断容错），单元测试 7 种格式全过
3. 工具提示词强化：用第一个真实工具构造具体调用示例（具体示例比抽象格式
   描述更能约束输出），明确"工具名只能出现在 JSON 的 name 字段里"
4. 上游偶发读超时自动重试（5xx/超时重试 2 次，401 刷新凭据）
5. 请求级调试日志（debug/proxy-debug.log：请求落盘 + 里程碑 + 异常）

### 验证
| 场景 | 结果 |
|------|------|
| keep-alive 原生 socket 收 [DONE] 后连接关闭 | 3.6s EOF，不再挂死 ✓ |
| zcode 形态（15 工具+14KB system）流式两轮 | 第1轮并行 Bash+Grep，第2轮完整报告 ✓ |
| 同上非流式两轮 | 连续工具调用正常 ✓ |
| 纯聊天回归 | 正常 ✓ |

## 14. 思考内容混入正文的修复（2026-09-11，zcode 实测反馈）

### 问题
思考模式（enable_thinking=true）下，模型的推理过程直接输出在正文里
（如 "The user asks..." 这类第一人称推理文本），污染最终回答。

### 根因
上游 SSE 的 delta 用 **type 字段**区分输出类型（官方 UI 靠它渲染思考折叠卡片）：
- `type: "thinking_card"`：思考过程（带 meta_data.thinking_card 状态：
  GENERATING/DONE、卡片 id 等）
- `type: "text"`：正式回答

反代的流解析只看 `delta.content` 不看 `type`，两种都当正文下发。
实测 GLM-5.3 思考开启时 675 个事件是 thinking_card、314 个是 text，
DeepSeek 思考时甚至会把内部 system prompt 带进思考流。

### 修复
流解析按 type 分流：
- `thinking_card` / `reasoning_content` -> OpenAI `reasoning_content` 字段
  （zcode/Cline 等会渲染成思考区，不进正文）
- `text` -> content 正文
- 工具调用缓冲逻辑不变（thinking_card 不进 tool_buf）

### 验证
| 场景 | 结果 |
|------|------|
| 思考开（GLM-5.3，"9.11 vs 9.8"） | content=462字纯回答，reasoning=1605字推理，完全分离 ✓ |
| 思考开+工具调用 | reasoning=587字 + tool_calls=get_weather，互不干扰 ✓ |
| 思考关回归 | content="34"，无 reasoning ✓ |

## 15. 长程任务随机中断的修复（2026-09-11，zcode 实测反馈）

### 问题
长任务进行中随机中断：可能第 2 步断，也可能跑好几步后突然断，位置不固定。

### 根因（调试日志实锤，两类叠加）
**A. 叙述型中断（主因）**：模型口头叙述下一步（"我现在来查看..."）却不输出
<tool_call> 标签 → 反代原样透传纯文本 → 客户端等不到 tool_calls，循环终止。
实测 zcode 会话 3 次中断均为该模式（58字/127字/747字无调用）。
同一请求重发 21 秒后却能正常返回工具调用——模型输出随机漂移，与输入无关。

**B. 上游读流挂起（次因）**：上游偶发"连接成功但永不发数据"（实测 4 分钟零
字节）。原 180s 超时太长，且超时后只重试一次。任何轮次都可能撞上，
表现为"在哪一步断不一定"。

### 修复（三层防御）
1. **提示词防叙述规则**：工具提示词加行为规则——"需要执行动作时必须直接输出
   <tool_call>，严禁只用文字描述你要做什么；任务未完成时必须继续调用工具"
2. **叙述型中断自愈**：工具模式整包缓冲，检测"任务进行中 + 短文本(<300字) +
   无工具调用"时，自动带继续指令重试上游，成功则替换整包（客户端无感知）
3. **读流看门狗**：连接/首字节 45s 超时（正常 2~15s），读流中途 90s 超时，
   超时抛 UpstreamStalled 自动重试（最多 2 次）

### 验证
| 场景 | 结果 |
|------|------|
| GitHub 月榜 trending 长任务（11 轮 40+ 工具调用） | 全程零中断，报告完整 ✓ |
| 同任务第二轮（trending 解析器优化后） | 5 轮完成，模型自选 OpenViking（月增8469星）深抓 4 源码文件 ✓ |
| zcode 形态模拟 × 4 遍（此前 3 遍挂 2 遍） | 8 次请求全部正常，无挂起无中断 ✓ |
| 看门狗单测（socket.timeout→UpstreamStalled、首字节后放宽） | 通过 ✓ |
| 自愈检测单测（4 场景判定矩阵） | 通过 ✓ |

### 附带优化
fetch_url 工具新增 GitHub trending 页结构化解析（此前模型要绕 7 轮才能
从导航 DOM 里淘出榜单，现在一次拿到 23 个项目的干净清单含月增长数据）。

## 16. 中断自愈体系强化（2026-09-11，第二个 zcode 会话反馈）

### 问题
第 15 节修复后仍有一个会话中断（sess_xxx）。请求落盘（debug/req-*.json）
+ 3 次重放暴露了三个残留缺陷：
1. 自愈只重试 1 次就放弃——重放显示同一请求 3 次结果各异（叙述/正常/空参数），
   单次重试命中率不够
2. 空参数调用：模型输出被截断/漂移时解析器只捞到工具名，参数兜底成 {}，
   发给客户端得到 InputValidationError 浪费一整轮（该会话第 26 条实锤）
3. 裸标签泄漏：全部调用被丢弃后，兜底分支把 <tool_call> 原文吐进正文

### 修复
1. **自愈递进 3 次**（heal_stall）：第 1 次温和提醒，第 2/3 次明确要求
   "不要再输出说明文字，直接输出工具调用"；成功判定 = 拿到工具调用
   或回答明显变长（>=300 字且翻倍）
2. **截断 JSON 修复器**（_repair_truncated_json）：补全悬空字符串/未闭合
   括号/尾部逗号，还原已写出的键值——实测截断的 Bash 调用 command 参数
   可完整还原
3. **空参数调用丢弃**：已知工具 + required 参数缺失的调用直接丢弃（无
   required 的工具如 EnterPlanMode 空参数合法，不丢），交给自愈拿完整调用
4. **裸标签不再泄漏**：解析失败时不把 tool_buf 原文吐进 content

### 验证（原中断请求重放 4 次，走完整反代链路）
| 次数 | 结果 |
|------|------|
| 第1-4次 | 全部 OK：3-4 个 Bash 工具调用、无空参数、无标签泄漏 |
| 自愈介入 | 第2次重放触发：叙述型中断→第1次无改善→**第2次成功救回 3 个调用** |
| 单测 | 截断修复/空参丢弃/未知工具保留/正常调用/无required工具/坏调用只丢单个 6 项全过 |

## 17. 第三方审计修复（2026-09-12，WorkBuddy AI 审计反馈）

### 背景
用户将本项目交给另一个 AI（WorkBuddy AI）审计，产出交接文档
（`C:\Users\<you>\WorkBuddy AI/<audit-run>/outputs\`）：
30 项问题 + 39 项离线验证（36 复现 + 3 基线）。作者逐项抽查 15 项关键
指控全部属实，本次修复其中 6 项高优先级问题。

### 已修复
| 审计项 | 问题 | 修复 |
|--------|------|------|
| 10.1 (C23) | 监听 0.0.0.0 无鉴权，提示却显示 127.0.0.1 | 默认 BIND_HOST=127.0.0.1（netstat 确认只绑回环），--host 参数可显式覆盖 |
| 10.11 (C06) | `not args.get(p)` 把 false/0/"" 误判为参数缺失 | 改 `p not in args`（falsy 是合法值） |
| C16 | 同一 delta 里 content+reasoning_content 并存时 content 丢失 | elif 改两个独立 if，分别下发 |
| C13 | 上游 finish_reason=length 被改写成 stop（截断不可见） | 透传 upstream_finish（length/stop 均原样） |
| C10 | CRLF/CR 分隔的 SSE 整个丢失 | 事件分隔兼容 \n\n / \r\n\r\n / \r\r |
| C15 | 收到 [DONE] 只 continue，白等上游关连接 | [DONE] 即关上游连接并终止双层读循环 |
| 10.21 (C25) | messages=null / 非法结构 TypeError 崩线程 | 结构校验返回 400（body 必须对象、messages 非空数组且每项带 role、tools 必须数组） |
| 10.3 (C04/C24) | 未声明工具名被放行；tool_choice=none 仍可能产生调用 | parse_tool_calls 加 strict_declared 白名单（未声明一律拦截，防提示注入伪造）；force_no_tools 全链路接线（不注入提示词也不解析回调用） |

### 修复中引入的 bug（已修）
[DONE] break 只跳出内层分隔符循环，外层 read 循环继续——回归测试当场
抓到（测试响应在 DONE 后抛 AssertionError）。改为 done_seen 标志终止
双层循环。

### 验证
- 单元回归 11 项全过（falsy 保留/复合 delta/CRLF/DONE 即停/length 透传/
  白名单拦截/none 强制/截断修复不回归）
- 端到端：绑定 127.0.0.1 ✓ messages=null→400 ✓ 纯聊天 34 ✓
  agent 闭环（4 轮天气+温差）✓ tool_choice=none 无调用无标签泄漏 ✓

### 审计中评估后暂不采纳的建议
- 10.5 移除自愈：批评正确（合法短答案会被强制继续），但自愈是实测
  "长任务随机中断"的直接解药，协议层无法区分叙述型中断与合法短答案，
  当前取舍偏向任务连续性。后续可考虑客户端侧任务状态判定。
- 截断 JSON 补齐风险：修复只使用模型已输出的文本 + required 检查兜底，
  实测救回过完整 command 参数，收益大于风险。
- 测试脚本 eval/fetch_url 列 P1：演示脚本非反代本体，风险敞口不同。
- 其余 P2（tool_call_id 关联、SSE 多行 data、usage 伪造等）记录在案，
  按需处理。

## 18. sk 过期导致"退回一问一答"的修复（2026-09-12，第三个 zcode 会话反馈）

### 问题
新会话模型只回 30/72 字纯文本、零工具调用，"退回一问一答"。

### 根因
**sk（Web-Signature 会话密钥）过期**。sk 是会话级密钥（客户端登录时协商、
仅存内存），客户端会话轮换后旧 sk 失效。反代拿过期 sk 签名请求 → wenc
路径全部 401 → zcode 收到错误后降级。Cookie 本身仍有效（旧路径无签名
请求 200 验证）——401 专属于签名层。

### 修复
upstream_chat 加 401 自动回退：wenc 路径 401 且带签名时，清除过期 sk
缓存（下次 get_sk 走 CDP 重提取，需客户端开 19233 调试端口），剥掉签名头
改走旧路径 `/ai/portal/v3/`（服务端仍接受无签名请求）重试。

### 验证（用当前已过期的 sk 实测）
- 纯聊天：正常回答（自动回退旧路径）✓
- 工具调用：并行 2 个 get_weather 调用 ✓（agent 循环恢复）

### 追加排查（2026-09-12 下午）
用户重启客户端并发消息后仍不行。进一步发现：
1. 客户端普通启动（hidden=true）不带调试端口 → CDP 19233 未监听 →
   无法提取 sk。需显式带 `--remote-debugging-port=19233` 启动
2. 带 CDP 端口重启客户端后提取到 sk —— **与昨天完全相同**
   （****（32位hex，已打码）），且该 sk 在 wenc 路径实测 200 有效
3. 修正结论：sk 并不随客户端重启轮换（绑定登录会话）。昨天下午的
   401 更可能是服务端对旧签名的临时拒绝（时间戳偏移/风控），而非
   sk 真正失效。回退机制依然有价值：无论 401 原因，都能保功能不中断
4. 客户端重启后 Cookie 也刷新了，全链路（纯聊天+工具闭环）恢复

### 运维提示
- 401 不再致命：自动降级旧路径，功能不中断
- 想恢复 wenc 签名模式：以 `--remote-debugging-port=19233` 启动云智助手
  （注意：从任务栏/桌面图标普通启动不带此参数），客户端产生任意签名
  请求后，反代下次请求自动重提取 sk

## 19. 上游断连 10038 修复 + 真实长任务验证（2026-09-12，第四次实测反馈）

### 问题
用户实测仍不行。调试日志抓到现行：请求抛 `OSError 10038`（WSAENOTSOCK，
Windows 下上游 RST 断连的表现），流异常后 zcode 拿到空响应。

### 根因
上游服务端偶发 RST 连接（与之前的"读流挂起"是同族问题）。`_WatchedResponse`
只把 socket.timeout 和 "timed out" 归类为可重试的 UpstreamStalled，
连接类 OSError 直接冒泡，整个请求失败。

### 修复
1. `_WatchedResponse.read`：OSError / 10038 / 10054 / connection reset
   等断连异常全部转 UpstreamStalled → 触发自动重试
2. `read_stream_with_retry` 重试耗尽/重发失败时：若已收内容非空，
   补 stop + [DONE] 终结块用已收内容收尾，绝不让客户端拿到空流
3. 单测：10038→UpstreamStalled ✓ 10054→UpstreamStalled ✓ 正常EOF不受影响 ✓

### 真实长任务验证（test_longtask_real.py）
完全复刻 zcode 会话形态：真实请求样本（192 工具 + 7.8KB system + 思考模式）
+ 模拟工具执行的多轮 agent 循环，3 个长任务：

| 任务 | 轮数 | 工具调用 | 最终回答 | 结果 |
|------|------|---------|---------|------|
| 项目安全审查 | 6 | 15 | 3234字 | 完成 |
| 项目架构分析 | 4 | 8 | 4167字 | 完成 |
| 单元测试方案 | 3 | 7 | 7805字 | 完成 |

全程零流异常、零空响应。自愈机制两次实战触发均成功救回
（其中一次自愈重试本身也遇到 10038，第 2 次成功——多层防御生效）。

### 结论
"实测不行"的直接原因就是上游断连 10038 未被处理（每次撞上整个请求就废）。
修复后 3 任务 13 轮 30 次工具调用全通。

## 20. 思维链实时下发（2026-09-12，首字延迟优化）

### 问题
用户实测：单步首字时间非常长，且首字是思维链的第一个字。

### 根因（两因素叠加，实测数据）
上游本身 6-9 秒就开始吐思考流，但工具模式为了叙述型中断检测和整包重试，
把**包括思维链在内的一切**都缓冲到上游流结束——客户端首字时间 = 完整
生成时间（实测 60~226s，思考流经常占 90% 以上，如 128 字正文配 11177
个思考块）。

### 修复
`read_stream_with_retry` 区分处理：**思维链块（reasoning_content）实时
下发**（仅首次，重试/自愈的新思考不重发避免重复），正文/工具块仍整包
缓冲（自愈替换依赖）。自愈替换整包时剔除思考块。

### 验证
- 真实 zcode 形态（192 工具+思考）：客户端首字节 21.8s（此前为完整
  生成时间 60-170s）；轻思考轮次首块 4.8~5.8s
- 完整 agent 闭环回归：8 轮 14 次工具调用全通；叙述型中断自愈正常
  触发并救回（第 1 次成功拿 2 个调用）；思维链实时下发日志确认

### 效果
客户端从"死等 1-3 分钟突然蹦全部内容"变为"几秒看到思考开始滚动，
思考结束后正文/工具调用立即到达"。

## 21. 思维链流式修复补丁（2026-09-12）

### 问题
第 20 节修复引入 bug：`reasoning_live["sent"]` 标志写成全局——第一个
思考块发出后置位，后续所有思考块被静默丢弃。表现为"吐一个词后卡住，
几十秒后缓冲内容瞬间涌出"。

### 修复
标志改为按尝试次数判定：首次尝试（attempt==0）的思考块全部实时下发；
重试（attempt>0）的新思考流静默丢弃（原思考已发过，避免重复）。

### 验证（真实 zcode 形态，逐块计时）
- 思考块 985 个，首块 7.3s，末块 25.3s，平均间隔 0.02s/块——连续滚动 ✓
- 最大间隔 3.5s（模型思考停顿，正常）
- 正文+工具调用在思考结束后到达（工具模式缓冲设计，自愈依赖）
- agent 闭环回归通过

## 22. 上游身份提示词注入的隔离（2026-09-12，第五次实测反馈）

### 问题
用户 zcode 会话中，模型在思考与正文里反复评论"工具结果中夹带冒充系统
指令、要求我以云智助手身份作答的文本"，污染输出、浪费轮次。

### 根因（纯净上下文对照实验实锤）
上游服务端给**每个请求**自动注入"云智助手"角色 system prompt（模型思考
流原话："According to the system prompt, I am 云智助手"）。该指令无法
移除（服务端行为）。模型把它误判为"工具结果里的提示注入"并在输出中
反复评论——下游 agent 看到这些评论，进一步放大混淆。

### 修复（双层防御）
1. **提示词层**：工具提示词新增【运行环境说明】——告知模型本环境由
   API 网关附加默认身份模板，属正常现象而非注入，严禁在思考或回复中
   提及、评论或分析身份指令
2. **输出层**：strip_identity_echo 剥离"（说明：...）（注：...）"
   形态的身份回显括号段（特征词：云智助手/改换身份/系统指令等）；
   流式工具模式经 strip_chunks_identity 整包重建；非流式直接剥离。
   剥后不足原文 30% 视为整段回显，返回剩余。单测 7 用例全过
   （含正常括号说明不误伤、整段回显清空）

### 验证
- 重放用户真实会话请求（38 消息 112 工具）：身份回显零残留，
  3561 字对比报告干净完整，正常括号说明正确保留 ✓
- agent 闭环回归通过 ✓

### 附带修复
_WatchedResponse 首字节后 settimeout(90) 在连接被服务端 RST 时抛
10038——包 try 忽略（读操作自己会报）。

## 23. 正文流式下发（2026-09-12，第六次实测反馈）

### 问题
思维链流式了（第 21 节），但正文仍整包缓冲——长回答要等全部生成完才
一次性到达。

### 根因
工具模式的整包缓冲是为叙述型中断自愈（整包替换）服务的。但自愈的
触发条件是"正文 <300 字且无工具调用"——正文超过 300 字后自愈不可能
触发，缓冲就没有必要了。

### 修复（read_stream_with_retry 分级下发）
- 思维链块：全部实时下发（第 21 节）
- 正文块：累计 <300 字时缓冲（自愈替换窗口）；**超过 300 字后切换
  实时下发**——切换时先按序刷出已缓冲块（防文本乱序），后续直通
- 工具调用块：始终缓冲（流结束统一解析，不变）
- 断流处理分级：正文未实时下发 → 照旧整包重试；已实时下发 → 无法
  撤回，直接用已收内容补终结块收尾

### 验证
- 工具模式下长文生成（3216 字/182 块）：首块 10.5s，跨度 41.3s
  （总 51.9s 的 80%），10%/50%/80% 内容分别在 21%/57%/80% 时长到达
  ——真流式 ✓
- agent 闭环回归：3 轮工具调用正常 ✓
- 真实形态长任务回归：4 轮 8 次调用 + 2843 字最终报告，自愈机制
  未受影响 ✓

### 效果
agent 场景下：思考实时滚动 → 正文 300 字后开始流式 → 工具调用在
流尾统一到达。短回复（工具间过渡说明 <300 字）仍走缓冲（自愈需要），
对体验无影响。

## 24. 全脚本 Review 修复（2026-09-12）

对 ctyun_proxy.py 全量 review（1313 行），发现 4 个实际 bug + 4 个资源/
稳定性问题，全部修复：

### 高优先级修复
| # | 问题 | 修复 |
|---|------|------|
| 1 | 正文流式（>300字切换实时下发）旁路了身份回显剥离——strip_chunks_identity 只处理缓冲块 | 切换点前瞻检测：正文含身份回显特征（_IDENTITY_PAREN_RE）时不切换流式，保持缓冲供剥离；干净文本才切换 |
| 2 | MANUAL_SK 失效后每个请求都走 401 弯路（401 回退只清 _sk_state，不动 MANUAL_SK） | 401 确认后置 manual_disabled 标志；get_sk 检查——不再盲信手动值，走 CDP 重提取（CDP 不可用时返回 None 走旧路径） |
| 3 | tools 数组元素未校验——`tools:["Bash"]` 在流式路径响应头已发出后才炸（客户端拿到截断流） | do_POST 校验元素为 dict 且有 function.name，返回 400 |
| 4 | IncompleteRead（截断 chunked 响应）未归类断流，不重试直接截断流 | _WatchedResponse 归类为 UpstreamStalled；同时收紧 OSError 分类（只认连接类 errno 10038/10053/10054 和 Connection*，磁盘错误不再误当断流重试） |

### 资源与稳定性修复
| # | 问题 | 修复 |
|---|------|------|
| 5 | 客户端断开/异常时上游响应不关闭，半开连接积累 | 流式分支 finally 关闭 + 非流式分支收尾关闭 |
| 6 | 自愈/断流重试/外层重试绕过 throttle()——最坏一个请求几秒内打向上游 6+ 次，恰是节流要防的突发 | 全部 4 处重试点重发前调用 throttle() |
| 7 | debug/ 无限增长（82+ 份请求快照 9MB+，含对话内容和 sk） | req-*.json 上限 200 份，超量删最旧（按 mtime） |
| 8 | --host/--sk 作为末参数缺值时 IndexError 崩启动 | _arg_value() 安全解析，缺值警告并忽略 |

### 记录在案（未修，已知取舍）
- 工具输出内容不做伪 XML 转义：`<tool_result>` 标签体内含
  `</tool_result><tool_call>...` 可伪造对话历史。strict_declared 白名单
  挡住未声明工具，但**已声明工具的伪造调用（攻击者选参数）能通过**——
  提示词级工具调用方案的固有攻击面
- 非流式路径 reasoning_content 不做身份剥离（回显几乎只在 content 中）
- usage 恒 0、CDP 响应不按 id 匹配、req_id 可能碰撞（均低危，审计已录）

### 验证
- 单测 9 项全过（manual_disabled 语义/参数解析/IncompleteRead/磁盘
  OSError 不误归类/解析器×2/剥离器/畸形 tools×2）
- 端到端：畸形 tools 400 ✓ 纯聊天 ✓ agent 闭环 ✓ 长任务 6 轮完成
  （含 3 次自愈触发全部救回）✓

## 25. 输出截断修复（2026-09-12，第七次实测反馈）

### 根因：不是 max_tokens
反代**从不转发 max_tokens**（zcode 传的 128000 被忽略，上游按自身上限生成）。
实测上游单次 29213 字长文完整生成、finish=stop 正常收尾——截断发生在反代内部。

### 真实截断源 A：tool_buf 64KB 上限硬截（大参数调用残缺）
工具模式缓冲上限 64KB，Write 大文件类调用（content 轻松超 64KB）触发硬截，
`</tool_call>` 闭合标签丢失。下游两条路都坏：
- 截断修复器把半截 JSON 补全成"合法"调用 → **静默写坏文件**（最恶劣）
- 必填参数检查丢弃 → 白白损失一轮

修复：上限 64KB→1MB，且截到**最近一个完整 `</tool_call>` 边界**——保留
所有完整调用，只丢不完整尾部；一个完整调用都没有时退回 64KB 硬截。
触发后主动 close 上游连接停止读取。

### 真实截断源 B：正文已流式后断流，尾部工具块整体丢失
正文超 300 字切换实时下发后，若此时上游断流（10038/10053/IncompleteRead
等），原逻辑直接补 stop 收尾——已缓冲的完整工具调用被整体丢弃，agent
表现为"做完一步就停"。

修复：
1. `stream_openai_chunks` 工具模式下持续暴露 `sink["_tool_buf_partial"]`
   （generator 被异常打断时局部变量丢失，外层从 sink 取）
2. 断流收尾分支解析已缓冲工具块：**只取最近完整 `</tool_call>` 边界**，
   解析成 tool_calls chunks 追加下发（finish=tool_calls），无完整调用
   才补 stop。**不走截断修复器**——断流时无闭合标签必然不完整，修复器
   会把 Write 半截参数补全成合法调用静默写坏文件，残缺调用宁可丢弃

### 验证
- 单测 9 项全过（test_truncation_fix.py）：大参数 70KB×3 全解析 /
  断流 partial 暴露 / 修复器危险对照（证明边界切割必要性）/ 恢复只救
  完整调用 / 1MB 截断保 A/B 丢半截 C 且后续块不读 / 正常收尾×2
- 端到端回归：agent 闭环 3 场景（中/英/非流式）全部多轮 tool_calls→stop
  完成；长任务回归（真实 112/192 工具请求形态）见当日验证记录
