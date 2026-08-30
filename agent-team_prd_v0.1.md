# Agent-Team 产品需求文档

> **版本**：v0.1<br>
> **最近修订**：2026-08-16<br>
> **状态**：已实现并完成本地真实场景验证<br>
> **目标读者**：产品负责人、使用者、维护者和集成开发者

## 1. 文档边界

本文定义 Agent-Team v0.1 的产品问题、目标用户、功能范围、验收标准和已知限制。

- [技术设计文档](agent-team_technical_design_v0.1.md)定义实现合同、数据结构、状态转换和恢复不变量；
- [README](README.md)提供安装与快速上手，[用户指南](docs/user-guide.md)提供完整操作说明；
- [`docs/validation/README.md`](docs/validation/README.md)索引真实运行的验证材料与结果。

发生冲突时，产品范围以本文为准，运行时行为以技术设计和当前测试共同约束；README
不得扩展二者未承诺的能力。

## 2. 产品定义

Agent-Team 是一个本地的、任务级的 Coding Agent 团队运行时。用户用自然语言临时
定义角色、职责、Harness、交接规则和完成条件；运行时负责可靠地传输执行权、恢复
角色会话、保存审计证据，并把 Completion 或 Block 返回最初的 Origin Session。

v0.1 的核心取舍是：

> 结构化传输和技术生命周期，不结构化业务协作语义。

因此，Review 是否通过、Finding 是否合理、下一步该交给谁等业务判断仍由本次
`PROTOCOL.md` 和 Agent 承担；Run、Role、Turn、Event、会话、进程、工作区事实和
审计轨迹由运行时结构化管理。

## 3. 用户与核心场景

### 3.1 目标用户

- 希望从 Codex、Claude Code、OpenCode 或 DeepSeek Harness 发起临时团队，并让
  这四种 Harness 在同一代码任务中协作的开发者；
- 需要 Developer/Reviewer 等多轮闭环，但不想维护固定工作流 DSL 的团队；
- 需要可恢复会话、显式交接、工作区排他和本地审计证据的高级用户；
- 评估不同 Agent/Harness 协作质量的维护者和研究者。

### 3.2 核心场景

1. Developer 修改，Reviewer 独立审查，Finding 循环直到完成；
2. Planner、Developer、Reviewer 等任意动态顺序拓扑；
3. Codex、Claude Code、OpenCode 与 DeepSeek Harness 角色混合，并按角色恢复各自原 Session；
4. Origin 只作为控制面，全部业务角色使用 External Binding 并开启 Full Audit；
5. 运行中断后，在不猜测业务路线的前提下诊断、确定性恢复或返回用户 Block。
6. DeepSeek Harness 加载共享 Skill作为 Origin，或由 Agent-Team 作为交互式 External Role 启动。

## 4. 产品目标

v0.1 必须做到：

1. 用户以一次自然语言请求定义临时团队，不需要预先编写图或状态机；
2. 支持任意名称的动态角色、Origin/External Binding，以及 Codex、Claude Code、
   OpenCode、DeepSeek Harness External Adapter；DeepSeek Harness 也可作为 Origin；
3. 任意时刻只有一个业务角色持有执行 Token，Handoff 目标显式且可审计；
4. External Role 可选择 `resume` 或 `fresh` Session Policy；
5. 用户留在 Origin Session 即可收到 Completion 或必须处理的 Block；
6. 对请求、协议、事件、Turn、会话、工作区边界和进程生命周期提供完整性保护；
7. 对 External Turn 提供可筛选、可校验、具备原始序列引用的审计轨迹；
8. 在崩溃、取消、超时或权限问题下，宁可 Block，也不重复启动 Harness 或猜测路由。

## 5. 非目标

v0.1 不承诺：

- 多角色真正并行、Fan-out/Join、Barrier 或任意 DAG；
- 多 Worktree、多仓库根目录、跨机器或云端调度；
- 自动理解或机器验证自然语言 Reviewer Verdict；
- 阻止用户、IDE 或其他非 Agent-Team 进程并发修改工作区；
- 防御拥有完整本机 Shell 权限的恶意 Agent；
- 容器级进程隔离或对主动逃逸进程的完整证明；
- 自动重新唤醒已经结束的宿主 Agent Turn；
- 获取、推断或保存模型私有的隐藏 Chain of Thought；
- Prometheus、OpenTelemetry、远程日志服务、Web 控制台或自动清理 Run Store。

## 6. 功能需求

### 6.1 Bootstrap 与不可变输入

- 保存用户原始请求为 `REQUEST.md`；
- 生成可读的自然语言 `PROTOCOL.md`，明确角色、路由、循环、Completion Authority、
  Block/Resume 规则、假设和安全上限；
- 生成 Schema 7 `team.json`，冻结 Role Binding、Session Policy、Launch Mode、
  Launch Profile、role-scoped Model / Reasoning Effort / Codex 或 Claude Provider /
  Codex Fast Mode、可选 DSH Plugin、Wall Time、最大 Turn 数和 Observability
  Policy；
- `init` 必须原子创建完整 UNSTARTED Run，`start` 才获取 Workspace Ownership 并
  提交唯一 Kickoff；
- Kickoff 后不得在线修改 Request、Protocol、Team、Profile 或安全上限。

### 6.2 动态角色与执行权

- Role ID 由本次任务定义，不从 `developer`、`reviewer` 等名称推断权限；
- Binding 支持 `origin` 与 `external`；External Adapter 支持 `codex`、
  `claude-code`、`opencode` 和 `deepseek-harness`；
- External Role 支持 `resume` 与 `fresh`；
- External Role 默认使用 `interactive`，通过受管 PTY 在角色 tmux Pane 中显示原生
  Codex、Claude Code、DeepSeek Harness TUI 或 OpenCode Direct-interactive Terminal；
  Codex、Claude Code 与 OpenCode 可在用户明确要求时选择 `headless`，DeepSeek Harness
  只支持 `interactive`；旧 Schema 1–3 Run 固定按 `headless` 读取；
- Codex、Claude Code、OpenCode 与 DeepSeek Harness 都提供 `default`、`trusted-workspace` 和 `full-access`
  三个显式 Launch Profile；新 External Role 省略 Profile 时默认冻结为
  `full-access`（YOLO），`default` 与 `trusted-workspace` 作为显式受限选项；
- 任一 External Role 使用 `full-access` 时，新 Run 的首次 `start` 必须在 Adapter
  预检、Ownership、Kickoff 和 Worker 创建前校验一次用户确认；Skill 先向用户披露
  Host Filesystem、Network 与凭据风险，再传 `--confirm-full-access`。确认写入不可变
  Kickoff Payload；同一 Run 的后续 Turn、Handoff、Resume、Recover 与重复 Start 不再
  询问；Claude `full-access` 映射复用该确认设置
  Run 私有 `CLAUDE_CONFIG_DIR` 记录本 Run 的确认，并同时设置
  `skipDangerousModePermissionPrompt=true` 兼容新版 CLI，不再把危险模式二次确认写入
  用户级 Claude 状态；Interactive
  Workspace Trust 是 Claude 独立的一次性工作区前提，缺失时仍在 Kickoff 前拒绝；
- Launch Profile 不继承 Codex 或 Claude 的用户级可变权限配置；Claude 额外排除
  User/Project/Local Setting Sources；Codex Headless 忽略 User Config 与
  User/Project Rules，Interactive 使用私有 Home，两种模式都冻结权限键并设置
  `features.hooks=false`。受信任 Workspace 内其余 Project Config、Instruction 与
  Extension 仍属于 Workspace Trust Boundary；Start/Resume 参数及 Hash 在 Kickoff
  前冻结，`full-access` 明确表示关闭 Harness 宿主沙箱；
- OpenCode 使用每个 Run/Role 独立的 `XDG_CONFIG_HOME`、内联高优先级配置、
  `OPENCODE_DISABLE_PROJECT_CONFIG=1` 与 `--pure`，不继承用户/项目 Permission、MCP、
  Agent 或外部 Plugin；认证与 Session Data 仍使用本机 OpenCode Store。OpenCode
  没有 Bash OS Sandbox，因此 `default`/`trusted-workspace` 只开放工作区内置文件工具
  和三类 Formal Action，任意 Bash 保持 Deny；`trusted-workspace` 额外开放内置 Web
  工具，`full-access` 才开放 Host Shell；
- DeepSeek Harness 使用 Agent-Team 固定版本的受管 Runtime、每个 Run/Role 独立的
  `DSH_HOME` 和 bundled 最小交互式 TUI，不载入用户 Profile、Skill、Subagent 或
  Workflow。Session 以私有 JSONL Store 持久化并通过 DSH 原生 Resume 恢复；认证只从
  环境读取。DSH Sandbox 只约束文件写效果，因此 `default` 与
  `trusted-workspace` 都限制写入 Workspace，但不限制读取、进程或网络；
- 声明 Workspace DSH Plugin 的候选消费 Role 必须使用 `fresh`；每次进入该 Role 都按
  Session Generation 创建新的私有 Home，冻结当前候选 Bundle，保留旧代制品和证据；
  Bundle 位置在 `init` 时可以尚不存在，由前序 Role 在 Run 内创建，首次路由时才必须
  满足安装合同。候选消费 Role 的 finding 按自然语言 Protocol 走正常 Handoff，同一 Run
  可用下一代制品继续验证，
  不得仅因上一代已冻结而要求 Block 或新 Run。目标 Bundle 在 Outbox/Event 提交前被
  判定为不可安装时，CLI 返回可审计的 `ROUTE_PREFLIGHT_REJECTED`，保留当前 Turn 的
  Token，由当前 Role 按自然语言 Protocol 选择具备能力的下一 Role；只有冻结 Profile 漂移或 Outbox
  提交后的目标变化继续 Fail Closed。候选 Bundle 已完成隔离复制、但在真实 DSH Loader
  激活期间令 Harness 于 Fresh Session 持久化前退出时，Agent-Team 不解析 Loader 文本、
  不复刻 DSH Plugin 语义；它把该代标记为不可用，并自动生成 Candidate Activation
  Finding，以 `system_handoff_reason=candidate_activation_failed` 结构化标记并回交给发送
  Role。接收方依据保留 Trace 选择 Protocol 允许的下一跳；只有证据证明是编排器、运行时
  或权限故障时才 Block；
- Codex 的管理员 Requirements 与 Claude 的 Enterprise Managed Settings 都不能由
  Agent-Team 覆盖，不进入 `launch_profile_sha256`，也不能由 `doctor` 证明云端或最终
  有效内容；Codex Requirements 可以约束并拒绝不兼容的 Sandbox、Approval、Permission
  Profile 或 Feature 选择，也可以强制重新启用 Managed Hook 或配置具有宿主副作用的
  Log Path；
  Claude 托管配置还可以覆盖标量并合并数组。因而 `default` / `trusted-workspace` 的
  产品写边界以管理员没有增加宿主可写路径、Sandbox Exclusion 或宿主执行 Hook 为前提，
  只允许 Workspace、Harness 必需的临时目录和经过固定参数校验的
  Formal Action；Codex 显式冻结空的额外 `writable_roots`，共享 Workspace Lock / Owner
  目录不作为 Harness 通用可写根；
- Codex、Claude Code、OpenCode 与 DeepSeek Harness External Role 都可显式选择 Model 和 Reasoning Effort，
  Codex 与 Claude Code 还可显式选择 Model Provider，Codex 可启用 Fast Mode；未显式选择的每个字段继承并冻结用户级 Harness
  默认值，但不得同时载入用户 Permission、MCP、Hook 等其他配置；新建 Codex Role
  把未启用 Fast Mode 的有效结果明确冻结为 `false`，不留给后续 Harness 默认值漂移；
  Codex 自定义 Provider 只冻结安全结构和 Credential 环境变量名，拒绝明文 Token、
  静态 Header/Query 与可执行 Auth；仅把实际引用的非空环境变量值注入对应 tmux
  Worker，不写入 Run State、LaunchSpec、Journal 或 Trace。Start 与 Resume 必须显式
  重放同一 Provider；
  Claude Provider 限于 `anthropic|bedrock|vertex|foundry|gateway`，只冻结 Route 所需的
  Endpoint/Region/Project/Resource 等非秘密结构与 Credential 环境变量名；省略显式
  选择时从 Claude 原生 Route 环境识别，冲突 Fail Closed。未选 Route 和未冻结凭据在
  每次启动时显式清空，外部 Route 不复制 Claude 私有登录凭据；
  Claude 托管策略仍可能在执行时覆盖冻结的请求 Model，产品不得把请求值误报为已证明
  的最终有效 Model；
  OpenCode Model 必须冻结为 `provider/model`，Reasoning Effort 映射为不透明的
  Provider-specific Variant；未显式给 Model 时从目标 Workspace 的有效配置解析，
  无法解析为完整 ID 时在 `init` Fail Closed；DeepSeek Harness 的显式 Model 同样使用
  `provider/model`，Reasoning Effort 限于 `off|high|max`；两者省略时 Agent-Team
  保持 `null`，由 DSH 私有 Profile 的原生 `agentDefaultModel` 服务选择，不增加
  Agent-Team 自定义环境变量或回退值；
- Handoff、Complete、Block 只能通过正式 CLI 动作提交；
- Event Journal 是 Token Owner 和 Run Status 的唯一业务转换来源；
- tmux Pane、普通输出或自然语言完成声明不能改变 Run 状态。

### 6.3 会话与协作闭环

- 每个 External Role 独立持久化 Session Ref 和 Generation；
- 同一 `resume` Role 的后续 Turn 恢复已校验 Session；
- Harness 以专用结构化信号明确报告单次输出预算耗尽时，只有在 Runner 已静止、
  Full Audit 未截断、无权限/完整性故障、精确 `resume` Session 可用且 Run 上限仍允许的
  情况下，Runtime 才可在尚未产生 Block 前提交同角色 Automatic Continuation Handoff；
  它创建并计数一个新的业务 Turn，不增加权限，也不等价于 Resume Block；连续两个没有
  Git 可见 Workspace 进展的此类 Turn 必须停止并 Block；
- Session 不可用时必须先 Block，经新的明确用户指令后才能在后续 Turn 降级；
- Completion 可以由任意协议指定角色提交，最终由 Origin 向用户交付；
- 任一 Block 一旦提交都必须先返回用户，禁止同一 Agent Turn 自动 Resume；
- Limit、Profile Changed 或不可变配置变化必须新建 Run。

### 6.4 本地安全与恢复

- 一个规范化 Git Worktree 同时最多由一个 Agent-Team Run 持有；
- 不支持非 Git 根目录、Sparse Checkout、Gitlink 或被跟踪的 `.agent-team/`；
- Turn 前后保存 Git 可见 Workspace Facts；
- Supervisor 与 Runner 使用可验证的 PID、PGID、Start ID 和单次启动许可；
- Launch Nonce、Origin Claim 等不透明随机值必须带非选项前缀，内部 CLI 传递不得让
  以 `-` 开头的历史值被参数解析器误判为选项；
- Cancel、Deadline 和异常退出必须清理可验证的受管进程组；
- `recover` 只执行由持久化证据唯一推出的技术收口，不创建 Resume 或选择业务路线；
- `unlock` 只在精确 Owner 匹配且所有已知执行身份安全结束时解除 Ownership。

### 6.5 可观测性与审计

- 每个完成收口的 External Turn 生成 `trace.jsonl` 和 `trace-manifest.json`；
- Manifest 记录 Policy、Capture 计数、摘要及保留 Artifact 的大小和 SHA-256，并由
  Turn Runtime 设置一次 Hash 锚点；
- Retention 改写或删除必须由可恢复的瞬态 Receipt 保护；任一收口写入点崩溃后可幂等
  继续，Manifest 必须精确覆盖所有受支持的实际保留 Artifact 并验证 Retention 语义；
- Normalized Trace 支持 Agent Message、Tool Call/Result、File Change、Usage、
  Error、Session、Turn、Diagnostic 与 Harness Event Fallback，以及 Harness 明确暴露的
  Reasoning Summary；
- 每个事件保留 stdout/stderr/terminal 原始 Sequence 范围；交互式 TUI 字节作为
  Diagnostic Event 保留，未知结构化记录不得静默丢失；
- `status`、`diagnose`、`watch` 提供稳定运行状态；`transcript`、`tail` 提供 Role/
  Turn Filter 和机器可读审计输出；
- Full Audit 要求所有业务 Role 为 External，Origin 只做控制面，Raw 或 Normalized
  Capture 截断必须产生技术 Block；
- Full Audit（以及显式启用 rationale/evidence 合同的 Standard Audit）中的 Handoff、
  Completion 和 Agent Block 必须包含非空 `Decision rationale` 与 `Evidence`，但不得
  声称这是隐藏 Chain of Thought。

### 6.6 安装与诊断

- Python 包必须包含 CLI、Codex/DeepSeek Harness 共享 Skill、OpenCode Skill、
  Claude Code Plugin 和 DeepSeek Harness 交互式 TUI Plugin；
- 支持从源码或平台无关 wheel 安装；
- `agent-team install` 只安装当前账号的集成副本和由同一解析器确定的
  `$DSH_HOME/skills/agent-team`，不得因任一未选择的 Harness、认证或 DSH 的 Node.js /
  pnpm 前提缺失而失败；
- External Role 选择对应 Harness 时才检查其可执行文件与认证；首次选择 DSH Role 时按
  版本和 npm integrity 固定值按需安装受管 DSH Runtime；
- `doctor` 检查 Harness、认证可见性、Profile、Resume、Git/tmux、文件系统能力、
  集成一致性、状态目录权限和 Workspace Owner；DSH External Adapter 检查受管 Runtime
  与 TUI 合同，DSH Origin 的独立 CLI 仍是可选项，Doctor 不声称证明 Origin 最终加载来源；
- 不允许把某台机器的 `.agent-team/`、tmux 或 Harness Session 复制到另一台机器继续。

## 7. 标准用户旅程

1. 用户在目标 Git Worktree 打开 Origin Agent，并描述团队和任务；
2. Bootstrap Skill 保存 Request、生成 Protocol、选择 Binding/Profile/Policy；
3. 若默认或显式选择了 `full-access`，用户在新 Run 启动前确认一次 YOLO 边界；随后
   `init` 与带确认参数的 `start` 建立 Run、Ownership、Kickoff 和所需 External Worker；
4. 当前 Token Role 领取冻结 Input；默认在可只读 Attach 的原生 TUI 中执行任务，
   并提交唯一正式动作；
5. Handoff 目标领取下一 Turn；同一 Role 按 Session Policy Resume 或 Fresh；
6. 若发生 Block，Origin 展示证据并等待下一条明确用户指令；
7. Completion Authority 确认条件满足后提交 Completion；
8. Origin 核验 Completion、Workspace Facts、测试和 Trace，再向用户交付。

## 8. 验收标准

v0.1 的发布验收必须同时满足：

- Codex/Codex 与 Claude Code/Codex 真实循环均可完成；
- OpenCode External Role 必须通过真实 Start、正式 Handoff、Session Resume 与
  Completion 的多 Turn 端到端闭环；
- DeepSeek Harness 必须真实加载安装的共享 Skill，以显式 `deepseek-harness` Origin
  创建 restricted External Role Run，并收到该 Role 的 Completion；同时必须作为
  interactive External Role 完成原生 Session 创建、跨进程 Resume 和多 Turn 正式闭环；
- Finding 能经历提出、接受或有证据拒绝、修复、同 Session 复审和关闭；
- 每轮完整 Review 都重新对照原始 Request 和权威验收源；不得通过删除测试、缩小审查
  范围或降低验收条件关闭 Finding；
- DSH 候选消费 Role 对冻结制品提出 finding 后，可在同一 Run 按 Protocol 回到候选生产/
  修复 Role，再以新 Session Generation 验证新制品，同时旧代 Hash 与 Profile 保持可审计；
- 候选消费 Role 的声明位置可在 `init` 时不存在；正式路由前遇到可修复的 Bundle 预检失败
  时，不创建 Outbox/Event、不产生技术 Block，并能由同一 Turn 选择新的 Protocol 合法
  Handoff；预检后漂移仍 Block；
- 至少五个后续 Turn 可恢复同一 External Role Session；
- DSH 的专用 `max-tokens` 终止在满足安全门时可自动创建同角色新业务 Turn；`resume`
  复用同一 Session，`fresh` 创建新 Generation。普通 Crash、已有 Outbox、Turn/Deadline
  耗尽、审计截断或权限问题必须 Fail Closed，且任何已提交 Block 都不得被自动 Resume；
- 生命周期、完整性、崩溃点、进程身份、Workspace Ownership 和观察接口测试通过；
- 每个完成的 Full Audit External Turn 都有可验证 Manifest 锚点，且无未声明截断；
- Transcript 汇总可报告事件、工具和 Harness 提供的 Token/Cost/Duration；
- 安装后的 Skill/Plugin 与包内副本一致，wheel/sdist 可构建；
- Run 终态健康、Owner 释放、受管进程与 tmux Runtime 清空；
- Interactive External Turn 的三路 stdio 均连接受管 PTY，Pane 可实时观察；正式
  Outbox 与 Session 验证后，Supervisor 以独立 `action` 终止类型清空进程组，Pane
  文本不参与状态转换；
- 没有开放 P0-P3 缺陷；任何剩余限制以 P4 或产品边界明确披露。

现有证据见：

- [`docs/validation/runtime-lifecycle-v0.1-validation-report.md`](docs/validation/runtime-lifecycle-v0.1-validation-report.md)；
- [`docs/validation/observability-claude-codex-report.md`](docs/validation/observability-claude-codex-report.md)；
- [`docs/validation/interactive-runtime-v0.1.2-validation-report.md`](docs/validation/interactive-runtime-v0.1.2-validation-report.md)；
- [`docs/validation/deepseek-harness-origin-v0.1.4-validation-report.md`](docs/validation/deepseek-harness-origin-v0.1.4-validation-report.md)；
- [`docs/validation/deepseek-harness-interactive-v0.1.4-validation-report.md`](docs/validation/deepseek-harness-interactive-v0.1.4-validation-report.md)。

前两份报告是 Headless/Observability 历史基线，保留当时的版本和测试计数；第三份记录
v0.1.2 验证时点的 Interactive Codex PTY、Action Termination、Session Resume、
274 项回归验证，以及 Claude Code → Codex → 同一 Claude Session 恢复的三 Turn
Interactive 实机闭环。报告中的计数都是证据快照，不随之后新增测试回写。该证据仍不
包含 Interactive Claude Code/Claude Code 双 Claude 循环，也不替代前两份报告对
Headless 结构化事件路径的覆盖。

## 9. 隐私与数据保留

- `standard` Redaction 只启发式处理常见 Token 和敏感字段，不是 Secret Manager；
- Normalized Trace 不保留私有 `thinking` 或通用 `reasoning` 正文；
- `redacted` Raw Retention 仍可能保留 Harness 输出的 Prompt、代码、Tool 数据或私有文本；
- `keep` 保存原始流，`delete` 删除 Raw，但 Full Audit 不允许 `delete`；
- Request、Protocol、Frozen Input、正式 Payload 和 Workspace Artifact 为保持权威性不
  自动改写；
- v0.1 没有 TTL 或 Purge 命令，Run Store 由用户在确认无 Owner 和进程后自行管理。

## 10. 当前限制与后续方向

已知限制包括单执行 Token、单 Worktree、本地文件协调、协作式 Origin Deadline、
启发式脱敏和宿主 Turn 无自动唤醒。以下只是候选方向，不是 v0.1 承诺：

DeepSeek Harness External Role 当前只支持交互式 Launch；其 restricted Profile 只约束
文件写效果，不约束读取、进程和网络。Agent-Team 不采集 DSH Origin 的私有内部过程，
也不保存 External TUI 输出中的私有 reasoning 正文。

- Workflow IR、Typed Handoff 和机器可验证 Transition Guard；
- 宿主 API/SDK 集成与可靠唤醒；
- SQLite 索引、可配置保留策略和远程可观测性；
- Fan-out/Join、多 Worktree、多机器 Worker 和合并策略。
