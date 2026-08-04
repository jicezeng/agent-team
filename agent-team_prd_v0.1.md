# Agent-Team 产品需求文档

> **版本**：v0.1<br>
> **最近修订**：2026-08-04<br>
> **状态**：已实现并完成本地真实场景验证<br>
> **目标读者**：产品负责人、使用者、维护者和集成开发者

## 1. 文档边界

本文定义 Agent-Team v0.1 的产品问题、目标用户、功能范围、验收标准和已知限制。

- [技术设计文档](agent-team_technical_design_v0.1.md)定义实现合同、数据结构、状态转换和恢复不变量；
- [README](README.md)提供安装和使用说明；
- [`docs/validation`](docs/validation)保存真实运行的验证材料与结果。

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

- 希望让 Codex 与 Claude Code 在同一代码任务中协作的开发者；
- 需要 Developer/Reviewer 等多轮闭环，但不想维护固定工作流 DSL 的团队；
- 需要可恢复会话、显式交接、工作区排他和本地审计证据的高级用户；
- 评估不同 Agent/Harness 协作质量的维护者和研究者。

### 3.2 核心场景

1. Developer 修改，Reviewer 独立审查，Finding 循环直到完成；
2. Planner、Developer、Reviewer 等任意动态顺序拓扑；
3. Codex 与 Claude Code 角色混合，并按角色恢复各自原 Session；
4. Origin 只作为控制面，全部业务角色使用 External Binding 并开启 Full Audit；
5. 运行中断后，在不猜测业务路线的前提下诊断、确定性恢复或返回用户 Block。

## 4. 产品目标

v0.1 必须做到：

1. 用户以一次自然语言请求定义临时团队，不需要预先编写图或状态机；
2. 支持任意名称的动态角色、Origin/External Binding，以及 Codex/Claude Code
   External Adapter；
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
- 生成 Schema 2 `team.json`，冻结 Role Binding、Session Policy、Launch Profile、
  Wall Time、最大 Turn 数和 Observability Policy；
- `init` 必须原子创建完整 UNSTARTED Run，`start` 才获取 Workspace Ownership 并
  提交唯一 Kickoff；
- Kickoff 后不得在线修改 Request、Protocol、Team、Profile 或安全上限。

### 6.2 动态角色与执行权

- Role ID 由本次任务定义，不从 `developer`、`reviewer` 等名称推断权限；
- Binding 支持 `origin` 与 `external`；External Adapter 支持 `codex` 和
  `claude-code`；
- External Role 支持 `resume` 与 `fresh`；
- Codex 与 Claude Code 都提供 `default`、`trusted-workspace` 和 `full-access`
  三个显式 Launch Profile；后两者只能由用户主动选择；
- Launch Profile 不继承本机可变权限配置；Start/Resume 参数及 Hash 在 Kickoff 前
  冻结，`full-access` 明确表示关闭 Harness 宿主沙箱；
- Handoff、Complete、Block 只能通过正式 CLI 动作提交；
- Event Journal 是 Token Owner 和 Run Status 的唯一业务转换来源；
- tmux Pane、普通输出或自然语言完成声明不能改变 Run 状态。

### 6.3 会话与协作闭环

- 每个 External Role 独立持久化 Session Ref 和 Generation；
- 同一 `resume` Role 的后续 Turn 恢复已校验 Session；
- Session 不可用时必须先 Block，经新的明确用户指令后才能在后续 Turn 降级；
- Completion 可以由任意协议指定角色提交，最终由 Origin 向用户交付；
- Block 必须先返回用户，禁止同一 Agent Turn 自动 Resume；
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
- Normalized Trace 支持 Agent Message、Tool Call/Result、File Change、Usage、
  Error、Session、Turn、Fallback，以及 Harness 明确暴露的 Reasoning Summary；
- 每个事件保留 stdout/stderr 原始 Sequence 范围；未知结构化记录不得静默丢失；
- `status`、`diagnose`、`watch` 提供稳定运行状态；`transcript`、`tail` 提供 Role/
  Turn Filter 和机器可读审计输出；
- Full Audit 要求所有业务 Role 为 External，Origin 只做控制面，Raw 或 Normalized
  Capture 截断必须产生技术 Block；
- Audited Handoff、Completion 和 Agent Block 必须包含非空 `Decision rationale` 与
  `Evidence`，但不得声称这是隐藏 Chain of Thought。

### 6.6 安装与诊断

- Python 包必须包含 CLI、Codex Skill 和 Claude Code Plugin；
- 支持从源码或平台无关 wheel 安装；
- `agent-team install` 安装当前账号的集成副本；
- `doctor` 检查 Harness、认证可见性、Profile、Resume、Git/tmux、文件系统能力、
  集成一致性、状态目录权限和 Workspace Owner；
- 不允许把某台机器的 `.agent-team/`、tmux 或 Harness Session 复制到另一台机器继续。

## 7. 标准用户旅程

1. 用户在目标 Git Worktree 打开 Origin Agent，并描述团队和任务；
2. Bootstrap Skill 保存 Request、生成 Protocol、选择 Binding/Profile/Policy；
3. `init` 与 `start` 建立 Run、Ownership、Kickoff 和所需 External Worker；
4. 当前 Token Role 领取冻结 Input，执行任务并提交唯一正式动作；
5. Handoff 目标领取下一 Turn；同一 Role 按 Session Policy Resume 或 Fresh；
6. 若发生 Block，Origin 展示证据并等待下一条明确用户指令；
7. Completion Authority 确认条件满足后提交 Completion；
8. Origin 核验 Completion、Workspace Facts、测试和 Trace，再向用户交付。

## 8. 验收标准

v0.1 的发布验收必须同时满足：

- Codex/Codex 与 Claude Code/Codex 真实循环均可完成；
- Finding 能经历提出、接受或有证据拒绝、修复、同 Session 复审和关闭；
- 至少五个后续 Turn 可恢复同一 External Role Session；
- 生命周期、完整性、崩溃点、进程身份、Workspace Ownership 和观察接口测试通过；
- 每个完成的 Full Audit External Turn 都有可验证 Manifest 锚点，且无未声明截断；
- Transcript 汇总可报告事件、工具和 Harness 提供的 Token/Cost/Duration；
- 安装后的 Skill/Plugin 与包内副本一致，wheel/sdist 可构建；
- Run 终态健康、Owner 释放、受管进程与 tmux Runtime 清空；
- 没有开放 P0-P3 缺陷；任何剩余限制以 P4 或产品边界明确披露。

现有证据见：

- [`docs/validation/runtime-lifecycle-v0.1-validation-report.md`](docs/validation/runtime-lifecycle-v0.1-validation-report.md)；
- [`docs/validation/observability-claude-codex-report.md`](docs/validation/observability-claude-codex-report.md)。

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

- Workflow IR、Typed Handoff 和机器可验证 Transition Guard；
- 宿主 API/SDK 集成与可靠唤醒；
- SQLite 索引、可配置保留策略和远程可观测性；
- Fan-out/Join、多 Worktree、多机器 Worker 和合并策略。
