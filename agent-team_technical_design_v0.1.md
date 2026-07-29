# Agent-Team 技术设计文档

> **版本**：v0.1<br>
> **日期**：2026-07-25<br>
> **最近修订**：2026-07-29<br>
> **状态**：Stage 1 MVP 设计基线
> **目标读者**：产品负责人、架构师、Codex/Claude Code 开发者、开源贡献者

---

## 1. 文档摘要

Agent-Team 是一个**由自然语言即时定义、跨 Agent Harness 运行、以主动 Handoff 驱动的临时 Agent 团队运行时**。

用户只需在当前打开的 Coding Agent 中描述：

- 本次任务是什么；
- 需要哪些角色；
- 每个角色由哪个 Agent Harness 承担；
- 各角色的职责和权限；
- 谁先开始；
- 何时、向谁 Handoff；
- 哪些条件导致循环、回退或结束；
- 谁负责判断完成；
- 最终如何返回当前用户会话。

例如：

> 落地 Stage 2。你作为 Reviewer，一个 Claude Code 作为 Developer。Developer 每轮修改后交给 Reviewer；Reviewer 只审查不修改，审查意见交回 Developer 独立判断并处理；持续循环，直到 Reviewer 确认没有 P3 及以上问题，最后由当前 Codex 交付结果。

Agent-Team 不把这个示例固化为产品流程。另一个任务可以临时定义 Planner、Backend、Frontend、Integrator、QA 等完全不同的角色、Harness 和拓扑。

Stage 1 的核心取舍是：

> **结构化传输层，不结构化协作语义。**

即：

- 角色 ID、Harness、会话、当前执行权、消息目标等运行时信息使用最小机器结构；
- 角色职责、动态拓扑、Handoff 条件、退出条件仍保存在自然语言 `PROTOCOL.md` 中，由 Agent 根据共享 Skill 理解并执行；
- 不开发独立的 LLM Manager，不引入通用工作流引擎，不在第一阶段解析自然语言 Verdict；
- 使用 tmux 承载各角色 Worker，并通过 Harness 原生 Session Resume 保持每个 Agent 的会话连续性；
- Agent 主动调用 `agent-team handoff` / `complete` 或对应的 Origin 命令完成正式交接，系统不从终端输出中猜测下一步。

Stage 1 首先验证两个最关键的产品假设：

1. Agent 能否根据本次动态协议，在正确时机主动 Handoff；
2. 不同 Harness 的多个 Agent 能否在保持各自会话的情况下，形成连续、有价值的协作。

---

## 2. 背景与问题定义

现有 Coding Agent 通常以“一个用户对一个 Agent 会话”为核心。即使产品支持 Subagent 或 Agent Team，也通常存在以下限制：

1. 团队角色由产品预置，难以由用户在任务开始时临时定义；
2. 多个角色往往运行在同一个 Harness 中，难以组合 Codex、Claude Code、Pi、OpenCode 等异构 Harness；
3. 多 Agent 协作依赖一个中心 Manager Agent，增加新的模型不确定性、成本和上下文瓶颈；
4. Agent 之间的交接通常是一次性摘要，缺少持续的原会话恢复；
5. 交接时机、目标和内容往往依赖自由文本推断，容易误触发、遗漏或产生偏见；
6. 用户需要切换多个终端或重复复制上下文，无法始终留在最初的 Agent 会话中；
7. 通用工作流引擎虽然可靠，但要求用户先学习图、DSL、节点和状态机，偏离自然语言入口。

Agent-Team 希望建立一种新的使用方式：

> 用户不是选择一个预定义工作流，而是在首条任务指令中临时“组建一支 Agent 团队”。

团队只在本次任务中存在；角色、Harness、职责、拓扑和退出条件全部由本次自然语言协议决定。

---

## 3. 产品定位

### 3.1 一句话定义

**Agent-Team 是一个自然语言定义的、任务级、跨 Harness、多 Agent 会话协作运行时。**

### 3.2 它是什么

- 一个安装在 Codex、Claude Code 等 Harness 中的通用 Skill；
- 一个很轻的本地 CLI；
- 一个基于 tmux 的角色进程和会话承载层；
- 一套 Agent 主动 Handoff 协议；
- 一个共享的任务目录和自然语言团队章程；
- 一个把最终结果返回 Origin Session 的闭环。

### 3.3 它不是什么

- 不是预置 Developer → Reviewer → QA 的固定产品；
- 不是 LangGraph 一类通用工作流框架；
- 不是一个中心 LLM Manager；
- 不是多个 Agent 的群聊工具；
- 不是只会启动多个终端的 tmux 包装器；
- Stage 1 不是强类型状态机或生产级分布式协调系统；
- Stage 1 不支持真正的并行 Fan-out / Join。

### 3.4 与传统编排器的差异

| 维度 | 传统多 Agent 编排器 | Agent-Team Stage 1 |
|---|---|---|
| 流程来源 | 预置图、DSL、代码 | 用户本次自然语言任务 |
| 角色 | 系统预定义 | 本次任务动态定义 |
| Harness | 通常同一框架 | 可绑定不同 CLI Harness |
| 中心控制 | Manager Agent 或状态机 | Agent 按共享协议主动 Handoff |
| 语义结构化 | 强 | 暂不结构化 |
| 运行时结构化 | 强 | 仅保留最低限度 |
| 会话连续性 | 常重新创建节点 | 每个角色可恢复原 Harness Session |
| 用户入口 | 独立 UI/工作流编辑器 | 当前 Codex/Claude Code 会话 |
| 最终交付 | 编排器 UI | 原始用户会话 |

---

## 4. 设计目标与非目标

## 4.1 Stage 1 目标

1. 用户仅通过一条自然语言请求定义本次 Agent 团队；
2. 当前入口 Agent 能识别角色、Harness、职责、初始角色、Handoff 关系和退出条件；
3. 自动启动用户要求的外部 Agent Harness；
4. 使用 tmux 为每个外部角色建立可观察、可恢复的运行窗口；
5. 同一角色多轮工作时可恢复原 Harness Session；
6. Agent 可根据自然语言协议主动选择 Handoff 时机和目标；
7. Handoff 载荷可以是自然语言 Markdown，不要求 JSON Schema；
8. 不通过正则解析终端输出来判断 Review 通过、QA 失败等语义；
9. 用户始终停留在 Origin Session；只要 Origin Turn 持续存活，任务结束后自动由该 Session 交付；
10. 保存完整轮次、Handoff、运行日志和最小运行状态，便于后续评估。

## 4.2 Stage 1 非目标

1. 不支持多个角色真正并行工作；
2. 不支持跨机器或云端调度；
3. 不支持任意 DAG、Fan-out、Join、Barrier；
4. 不对自然语言 Handoff 做强 Schema 校验；
5. 不自动验证 Review Verdict、QA 结果或退出条件；
6. 不提供 Exactly-once 分布式消息语义，但本地执行 Token 转移必须具有明确的原子提交点和可恢复规则；
7. 不防御拥有完整本机 Shell 权限的恶意 Agent；
8. 不提供复杂拖拉拽工作流 UI；
9. 不把 tmux TUI 屏幕文本作为工作流事实来源；
10. 不保证无人值守长时间生产运行。

---

## 5. 核心设计原则

### 5.1 用户定义团队，系统不预设团队

系统不得假设一定存在 Developer、Reviewer 或 QA。所有角色均由本次任务动态产生。

### 5.2 Origin Session 不等于 Coordinator

用户最初打开的 Codex 或 Claude Code 是 `Origin Session`，它是：

- 用户请求入口；
- Bootstrap Skill 的执行会话；
- 默认最终用户交付通道。

它可能同时承担 Reviewer、Planner、Integrator 等业务角色，也可能完全不参与团队执行。

### 5.3 协作语义属于自然语言协议

Stage 1 的职责、拓扑、条件和退出标准保存在 `PROTOCOL.md` 中，不编译成完整图状态机。

### 5.4 路由必须显式，不能靠自由文本猜测

Agent 必须明确调用：

```bash
agent-team handoff --to <role-id> --file <handoff.md>
```

或：

```bash
agent-team complete --file <completion.md>
```

系统不得从“看起来已经完成”“建议交给 QA”等文本推断正式状态转换。

绑定 `origin` 的动态 Role 使用显式携带 Run / Turn / Role / Claim 的 `origin-handoff`、`origin-complete`、`origin-block`；语义相同，但 `origin-handoff` 还负责在提交后立即保持等待。

### 5.5 tmux 承载 Worker，而不是直接操纵 Harness TUI

对每个 External Binding，tmux Window 运行一个长期存活的 `agent-team worker`。每个业务 Turn 再由一个短生命周期的 `agent-team turn-supervisor` 承载 Harness；纯 Origin Run 不创建 tmux Runtime。

这样可以同时获得：

- tmux 的进程持续性和可观测性；
- 组外 Turn Supervisor 的启动监控，以及独立 Harness 进程组的完整终止边界；
- Harness 原生 Session 的上下文连续性；
- 确定的 Turn 开始和结束边界；
- 避免把 Handoff 文本误输入到权限确认框、Slash 命令菜单或其他 TUI 状态。

### 5.6 tmux 只承载进程和 Best-effort 通知

正式 Handoff、取消、限额和运行状态始终先提交 Event Journal。tmux 不承载业务消息，也不通过 `send-keys` 建立控制协议。

Event 提交后，Runtime 可以使用：

```bash
tmux wait-for -S agent-team:<run-id>:<role-id>:changed
```

作为低延迟提示。Worker 被唤醒后仍重新扫描 Event Journal；同名通知可能合并，tmux Server 丢失后通知也不会保留，因此固定周期扫描始终是可靠路径。

`capture-pane` 只用于人工诊断，不用于判断 Agent 是否完成或决定路由。

### 5.7 发送方叙述不是事实

Handoff 应明确区分：

- 可核查事实；
- 发送方判断；
- 未知和争议；
- 下一角色动作。

系统自动附加 Git HEAD、Diff Stat、运行日志、子进程退出状态等事实信息。

### 5.8 角色会话与任务状态分离

- tmux 保持 External Worker 进程；
- `harness-runner` 在一个业务 Turn 内保持稳定的 Harness 进程组身份；
- Turn Supervisor 留在该进程组之外负责监控和清理；
- Harness Session Ref 按 Role 的 Resume / Fresh Policy 保持或轮换会话；
- 共享目录保持团队任务状态；
- Git / 文件系统保持实际工作产物。

任何单个 Agent Session 都不是团队事实的唯一来源。

### 5.9 第一阶段采用单执行权模型

任意时刻只有一个团队角色拥有主执行权。Handoff 相当于转移一枚执行 Token。

这允许动态循环和条件路由，同时避免第一阶段过早引入并行合并、冲突和 Join 状态。

---

## 6. 核心概念

### 6.1 Team Run

一次用户任务对应一个临时团队实例。

```text
Team Run = Original Request + Protocol + Roles + Runtime Sessions + Handoffs + Artifacts
```

### 6.2 Origin Session

用户最初输入任务的 Agent 会话。

默认规则：

- 负责 Bootstrap；
- 可绑定零个、一个或多个逻辑角色；
- 等待团队事件；
- 最终向用户交付。

### 6.3 Role

本次任务中由用户定义的逻辑职责，例如：

- `planner`
- `developer`
- `reviewer`
- `backend`
- `integrator`
- `qa`

Role 不是模型，也不是 Harness。

### 6.4 Harness

承载 Agent 的运行表面，例如：

- `codex`
- `claude-code`
- 后续支持 `pi`、`opencode` 等。

External Binding 通过 Harness Adapter 管理 CLI 子进程；当前入口 Harness 由 Origin Executor 使用，不进入外部 Adapter 生命周期。

### 6.5 Role Binding 与 Runtime

一个 Role 在本次 Team Run 中的实际运行实体：

```text
Role Binding =
  OriginBinding
  | ExternalBinding(Harness Adapter, Launch Profile, Session Policy)

Role Runtime = Role + Role Binding + (Embedded Origin | tmux Worker)
```

Stage 1 的所有 Role Runtime 共享 `team.json.workspace` 作为工作目录，不提供 per-role CWD。

### 6.6 External Session Policy

外部 Harness 支持：

| 策略 | 含义 | 典型用途 |
|---|---|---|
| `resume` | 每轮恢复同一个 Harness Session | 需要保留多轮上下文的 Role |
| `fresh` | 每次被激活时启动新 Session | 需要独立判断或隔离上下文的 Role |

Origin Binding 固定使用当前入口会话，不再用伪造的 `session_policy=origin` 或 `launch_profile=embedded` 表达。

### 6.7 Protocol

`PROTOCOL.md` 是本次团队的自然语言章程，描述：

- 任务目标；
- 角色和职责；
- Origin / External Binding；
- 初始角色；
- Handoff 规则；
- 循环和回退条件；
- 退出条件；
- 完成判定角色；
- External Session 策略；
- 用户交付方式；
- Bootstrap Agent 做出的假设。

### 6.8 Turn

某个 Role 获得执行权后的一次连续工作阶段。

一个业务 Turn 从领取 Kickoff、Handoff 或 Resume Input 开始，以以下动作之一结束：

- `handoff`
- `complete`
- `block`
- `cancel`（仅显式 `agent-team cancel` 调用，不从 Agent 文本推断）
- 无正式动作异常退出

### 6.9 Handoff

一个角色将主执行权显式交给另一个角色。

Stage 1 中：

- 路由元数据结构化；
- Handoff 内容自然语言化；
- 语义正确性由 Agent 和 Skill 负责；
- 系统只校验目标角色存在、当前角色拥有执行权、每 Turn 只能产生一个终止动作。

### 6.10 Completion Authority

根据用户协议，有权判断本次任务退出条件已满足的角色。

Stage 1 不机器校验该权限，依靠协议和 Skill；Stage 2 下沉为 Transition Guard。

### 6.11 User-facing Delivery Channel

默认始终为 Origin Session。

完成判定角色可以不是 Origin。它提交 Completion Package 后，Origin Session 读取结果并向用户展示。

Stage 1 只支持 `embedded_poll`：只要当前 Origin Turn 仍在等待，就可以自动收到结果；若该 Turn 已结束，Event 仍会持久保存，用户回到原 Session 继续后即可交付。自动重新激活一个已经结束的宿主 Turn 不属于 Stage 1。

---

## 7. 用户旅程

## 7.1 标准旅程

1. 用户打开 Codex；
2. 用户用自然语言描述任务和临时团队；
3. Codex 的 Bootstrap Skill 识别该请求；
4. Codex 保存原始请求，生成 `PROTOCOL.md`；
5. Codex 通过 CLI 声明角色和 Harness 映射；
6. Agent-Team 完成所需预检并原子获取 Workspace Ownership；
7. 系统先提交 Kickoff Event；之后仅为 External Binding 创建 tmux Session 和外部角色 Worker。外部 Worker 通过 Journal 扫描发现任务，tmux `wait-for` 可提供额外的低延迟提示；初始角色绑定 Origin 时由当前会话直接领取；
8. 各角色根据协议主动 Handoff；
9. 外部角色按各自 Session Policy 恢复原 Session 或创建 Fresh Session；
10. 若 Handoff 目标是 Origin 绑定角色，原 Codex 会话继续执行该角色；
11. 某个角色根据协议调用 `complete`；
12. Completion Event 进入 Origin 的 Durable Event 视图；
13. Origin Turn 仍存活时自动交付；否则用户回到原 Session 继续后交付，无需复制上下文。

## 7.2 示例旅程

用户输入：

> 落地 Stage 2。你作为 Reviewer，一个 Claude Code 作为 Developer。Developer 每轮修改后交给 Reviewer；Reviewer 只审查不修改；意见给回 Developer 独立判断并处理，直至 Reviewer 没有 P3 以上问题。本次任务最后由你交付。

Bootstrap 结果：

```text
Origin Session: 当前 Codex
Roles:
  reviewer -> origin
  developer -> claude-code, session_policy=resume
Initial role: developer
Protocol semantics: 自然语言 PROTOCOL.md
Final user channel: origin
```

运行：

```text
用户 → Origin Codex Bootstrap
              ↓
    Claude Developer Turn 1
              ↓ handoff reviewer
      Origin Codex Review 1
              ↓ origin-handoff developer（提交并等待）
    Claude Developer Turn 2（resume）
              ↓ handoff reviewer
      Origin Codex Review 2
              ↓ complete
       Origin Codex 最终交付
```

## 7.3 完全不同的拓扑示例

用户也可以输入：

> 先让 Claude Code 作为架构师输出设计，再让一个 Codex 作为实现者落地；实现后交给 Pi 做审查。Pi 有阻塞问题就退回 Codex，没问题就交回 Claude Code 做架构一致性检查；Claude Code 通过后完成，最后返回当前会话汇报。

Agent-Team 不需要预置这一流程，只需动态生成对应自然语言协议和角色运行时。

---

## 8. 阶段划分

## 8.1 Stage 1：自然语言协议 MVP

核心能力：

- 动态角色；
- 动态 Origin / External Binding；
- 动态顺序拓扑和循环；
- 单执行 Token；
- 自然语言 Handoff；
- tmux Worker；
- Harness Session Resume；
- 不可变 Event Journal；
- Workspace State Root；
- Workspace Ownership Marker；
- Origin Session 最终交付；
- 本地文件持久化；
- 人工可观测和介入。

## 8.2 Stage 2：结构化可靠性

在真实失败案例基础上增加：

- Workflow IR；
- 结构化条件和状态转换；
- Typed Handoff；
- Evidence；
- Candidate Revision；
- Transition Guard；
- SQLite；
- 分布式 Lease、跨机器幂等和无人值守自动重放；
- Completion Authority 强制；
- 宿主唤醒与结构化长连接 Adapter；
- 自动评测。

## 8.3 Stage 3：并行与分布式

- Fan-out / Join；
- 多 Worktree；
- 并行角色；
- 合并和冲突处理；
- 多机器 Worker；
- Web 控制台；
- 远程 Agent Runtime。

---

## 9. Stage 1 总体架构

```mermaid
flowchart TB
    U[用户] --> O[Origin Agent Session]
    O --> BS[Bootstrap Skill]
    BS --> P[REQUEST.md / PROTOCOL.md]
    BS --> CLI[agent-team CLI]
    CLI --> ROOT[Workspace State Root]
    ROOT --> OWN[Workspace Ownership Marker]
    CLI -.->|存在 External Binding| TMUX[tmux Team Session]

    TMUX --> W1[Role Worker A]
    TMUX --> W2[Role Worker B]
    TMUX --> W3[Role Worker C]

    W1 --> S1[Turn Supervisor A]
    W2 --> S2[Turn Supervisor B]
    W3 --> S3[Turn Supervisor C]

    S1 --> R1[harness-runner A<br/>独立进程组]
    S2 --> R2[harness-runner B<br/>独立进程组]
    S3 --> R3[harness-runner C<br/>独立进程组]

    R1 -->|exec| H1[Codex / Claude / Other Harness]
    R2 -->|exec| H2[Codex / Claude / Other Harness]
    R3 -->|exec| H3[Codex / Claude / Other Harness]

    H1 --> SHARED[Shared Run Directory]
    H2 --> SHARED
    H3 --> SHARED
    O --> SHARED

    SHARED --> JOURNAL[Immutable Event Journal]
    JOURNAL --> CLI
    JOURNAL -->|周期扫描| W1
    JOURNAL -->|周期扫描| W2
    JOURNAL -->|周期扫描| W3
    JOURNAL -->|embedded poll| O
    CLI -.->|wait-for 提示| TMUX
```

### 9.1 组件列表

| 组件 | 职责 |
|---|---|
| Bootstrap Skill | 理解用户团队描述，生成协议，启动 Team Run |
| Coordination Skill | 指导任意角色如何读取协议、工作和 Handoff |
| `agent-team` CLI | 创建 Run、路由消息、等待事件、查看状态、恢复和取消 |
| Run Store | 保存自然语言协议、不可变 Event、Handoff、Turn Runtime、日志和产物 |
| Observation Projector | CLI 进程内从同一锁内 Snapshot 只读派生 Status、Health、诊断码与技术建议，不保存观测状态 |
| Workspace State Root | 把 Workspace 与 Run Store 绑定到当前 OS 账号唯一、固定的本机状态目录 |
| Workspace Ownership | 使用单个持久化原子 Owner 文件声明归属，并用短期 per-workspace 操作锁串行化获取、恢复和删除 |
| tmux Runtime | 为每个外部角色承载长期 Worker，并可发送 Best-effort `wait-for` 提示 |
| Role Worker | 为 External Role 监听 Durable Event、创建 Turn Runtime 并驱动 Turn Supervisor |
| Turn Supervisor | 留在受管进程组外，监控单个 `harness-runner`，持久化 Harness 原始输出，并在 Runner 进程组清空后报告结果 |
| Harness Runner | 先在独立 Session / 进程组中自持久化身份，获得唯一启动许可后原地 `exec` Harness |
| Harness Adapter | 为 Codex、Claude Code 等 External CLI 生成 LaunchSpec，并解析结构化输出、Session Ref 和退出结果 |
| Origin Loop | 让当前用户会话接收角色 Handoff 或 Completion Event |

### 9.2 为什么不需要独立协调器

Stage 1 没有常驻的中心语义协调服务。

- 谁应当接手：当前 Agent 根据 `PROTOCOL.md` 判断；
- 是否完成：用户指定的角色判断；
- CLI 只负责显式路由和最低限度运行约束；
- Workspace Ownership 只负责本地工作区互斥，不参与语义判断；
- tmux 只负责进程承载和可丢失的变更提示；
- Origin Session 只在本身承担角色或收到最终完成事件时执行。

因此系统中不存在一个额外 LLM Manager，不会形成新的智能瓶颈。

---

## 10. Bootstrap Skill 设计

## 10.1 触发条件

当用户明确要求以下任一能力时触发：

- 多个 Agent 或多个角色共同完成任务；
- 指定不同 Harness；
- 角色之间主动 Handoff；
- 多轮 Review / Fix / QA / Planning 循环；
- 用户在首条任务中描述协作流程和退出条件。

不应因用户仅仅讨论“多 Agent”概念或询问产品方案而自动启动团队。

## 10.2 Bootstrap 输出

Bootstrap Skill 生成两类内容。

### 自然语言语义文件

- `REQUEST.md`
- `PROTOCOL.md`

### 最小运行时配置

- 角色 ID；
- Origin / External Binding；
- External Harness 与 Session Policy；
- 唯一 Workspace；
- External Adapter 按 Session Policy 所需的 Launch Profile 路径；
- 初始角色；
- 最大 Turn 和 Wall Time 上限。

### 关键边界

Bootstrap 不把以下内容编译成机器状态机：

- `Reviewer 有 P3 问题则返回 Developer`；
- `QA 通过后结束`；
- `Developer 可拒绝不合理意见`；
- `架构师认为一致后完成`。

这些内容进入 `PROTOCOL.md`。

## 10.3 Bootstrap 流程

```mermaid
sequenceDiagram
    participant User
    participant Origin as Origin Agent
    participant Skill as Bootstrap Skill
    participant CLI as agent-team CLI
    participant Store as Run Store
    participant Root as Workspace State Root
    participant Owner as Workspace Ownership
    participant Tmux as tmux

    User->>Origin: 自然语言任务 + 团队描述
    Origin->>Skill: 匹配 Agent-Team Skill
    Skill->>Skill: 提取角色、Harness、初始角色、会话策略
    Skill->>Skill: 生成 REQUEST.md 与 PROTOCOL.md
    Skill->>CLI: init --role ... --initial ...
    CLI->>Root: 在固定 Workspace 操作锁下建立或验证 State Root
    CLI->>CLI: 校验所需 External Harness 能力
    Skill->>CLI: start run-id
    CLI->>Owner: 原子提交 workspace-hash.json
    Owner-->>CLI: Acquired or conflict
    CLI->>Store: 运行一次 Git 可见 Snapshot 可行性检查
    CLI->>Store: 原子提交 Kickoff Event
    opt 存在 External Binding
        CLI->>Tmux: 创建 External Role Worker Windows
    end
    alt 初始角色绑定外部 Harness
        CLI-->>Tmux: wait-for -S role:changed（Best-effort）
    else 初始角色绑定 Origin
        CLI-->>Origin: ORIGIN_KICKOFF_READY
    end
    Skill->>CLI: wait-origin
```

`start` 在获取 Ownership 前完成配置和所需 Capability 预检；只有存在 External Binding 时才要求 tmux 与 Start ID 能力。获取成功后、Kickoff 前，再用 13.4 的同一算法做一次 Git 可见 Snapshot 可行性检查；失败时删除精确匹配本 Run 的 Owner 并明确拒绝启动，不创建 Event 或 Worker。检查通过后先提交 Kickoff，再创建外部 Role Worker。若进程在 Kickoff 前崩溃，不会留下 Worker；同 Run 的下一次 `start` 继续完成唯一 Kickoff。若在 Kickoff 后崩溃，只要持久化 Owner 仍完整属于本 Run，`start` 或 `recover` 就只补建缺失 Worker；Owner 丢失或错配按完整性故障处理，不能用恢复流程重建。`initial_role` 绑定外部 Harness 时可以在提交后发送 `wait-for` 提示，但 Worker 即使没有收到提示也会通过周期扫描发现 Event；绑定 Origin 时不创建或寻找 tmux Window，`wait-origin` 原子创建 Origin Turn 与 Claim 后返回该 Kickoff Event。

## 10.4 模糊信息处理

Bootstrap 应遵循：

1. 优先保持用户原话；
2. 所有推断写入 `PROTOCOL.md` 的“解释与假设”；
3. 不静默改变用户拓扑；
4. 如果用户描述包含 Stage 1 不支持的并行 Join，明确中止启动或要求改为串行；
5. 若缺少初始角色但可明显推断，则采用推断并记录；
6. 若缺少完成条件，则不得无限运行，应要求补充或使用明确的安全停止条件；
7. 严重歧义、危险权限或不可用 Harness 必须返回 Origin 处理。

---

## 11. Coordination Skill 设计

所有参与 Harness 都应加载同一套通用协调 Skill。

## 11.1 通用规则

每个角色被激活后必须：

1. 读取 `REQUEST.md`；
2. 读取 `PROTOCOL.md`；
3. 确认当前 `role_id`；
4. 读取当前 Input Event 的类型、ID 与 `input.md`；它可能是 Kickoff、Handoff 或 Resume；
5. 根据本角色职责完成工作；
6. 判断本次 Turn 应交给谁或是否完成；
7. 创建自然语言 Handoff / Completion 文件；
8. 调用正式 CLI；Origin 绑定 Role 必须保留本 Turn 的 Claim，并使用对应的 `origin-*` 命令；
9. 调用成功后停止本 Turn。

## 11.2 禁止行为

- 不得直接改写 `events/` 或 Turn `runtime.json`；
- 不得通过普通对话假设已经正式 Handoff；
- 不得向不存在的角色发送；
- 不得在一个 Turn 中提交多个正式终止动作；
- 不得把私有思维链作为必要 Handoff 内容；
- 不得只写“已完成”“没问题”而不给出可执行信息；
- 不得绕过用户协议中的角色边界；
- 不得启动通过 `setsid`、`setpgid`、双重 Fork 等方式逃离当前 Runner 进程组的后台 daemon；
- Origin Session 不得共享、猜测或替换另一个 Session 的 Claim；Stage 1 不提供活跃 Origin Turn 的 Claim Takeover；
- 任何 Block 都必须展示给用户；Origin 可以先诊断或执行确定性技术收口，但不得替用户自动 Resume；
- Skill 查询运行状态时必须使用结构化 Observation 信封，不解析 Status / Watch 文本、Pane 或普通日志决定控制动作；
- Handoff 后不得继续进行新的业务修改。

## 11.3 动态角色约束

Skill 不内置 Developer、Reviewer、QA 的固定行为，而要求角色从 `PROTOCOL.md` 读取职责。

例如，只有当协议中声明“Reviewer 只审查不修改”时，该角色才适用只读边界。

## 11.4 Handoff 判定原则

Agent 应回答：

1. 我是否已经完成本角色当前 Turn 的职责？
2. 根据协议，当前事实对应哪个分支？
3. 下一角色是否能基于现有信息独立继续？
4. 是否存在必须显式暴露的不确定性、争议或未完成项？
5. 我是否有资格宣布任务完成？

---

## 12. 自然语言协议 `PROTOCOL.md`

## 12.1 模板

```markdown
# Agent Team Protocol

## Original objective

本次任务的目标。

## Source of truth

原始需求、代码、设计文档和其他权威输入。

## Team roles

### <role-id>

- Binding: origin | external
- Harness: <external only>
- Session policy: resume | fresh (external only)
- Responsibilities:
  - ...
- Restrictions:
  - ...

## Initial role

<role-id>

## Collaboration protocol

用自然语言描述：

- 谁在什么情况下交给谁；
- 哪些情况会返回上一角色；
- 如何处理争议；
- 是否要求重新审查；
- 是否允许跳过某些步骤。

## Completion condition

谁判断完成，以及怎样才算完成。

## Final delivery

完成包由谁准备，最终通过哪个 Origin Session 向用户展示。

## Session continuity

哪些角色恢复原会话，哪些角色每轮使用新会话。

## Shared context policy

每个角色默认应看到哪些内容，不应继承哪些内容。当前 Input Event 可能是 Kickoff、Handoff 或 Resume；三者都必须作为本 Turn 的直接输入。不同 External Session 可以只传递显式材料；绑定同一个 Origin Session 的多个 Role 共享宿主上下文，不得声称彼此隔离。

## Block and resume policy

任何 Block 都先返回用户。Origin 可以自动执行只读诊断和确定性的 `recover` 技术收口；后者可能补交已经冻结的动作或生成固定技术 Block，但不能凭空创建业务 Turn、选择新路由或 Resume。若补交的是此前已经正式暂存的 Handoff，目标 Worker 可以按正常协议继续。只有可 Resume Block 在收到新的、明确的用户指令后才能提交 Resume；Limit / Profile Changed Block 始终要求新 Run。缺少输入、权限不足和高风险确认不得自动恢复。Resume Payload 必须记录所依据的用户指令和用户选择的目标角色。若用户改变原始目标、协议、角色/Binding、Workspace、Launch Profile 或安全上限，则取消旧 Run 并创建新 Run，不使用 Resume。

## Assumptions made during bootstrap

列出所有 Bootstrap Agent 的解释和假设。

## Safety limits

最大 Turn、Wall Time、External / Origin 不同的 Deadline 保证、Workspace 排他运行前提和人工中止规则。Adapter 原始结构化输出可以保留 Token / Cost 供离线评测，但 Stage 1 不把跨 Harness 不可统一验证的数据投影为运行状态或硬限制。
```

## 12.2 指令与事实的权威性

Agent-Team 产生的请求、协议和 Handoff 始终受宿主 Harness 的 System、Developer、Safety 和仓库级强制指令约束；`PROTOCOL.md` 不能覆盖这些更高优先级规则。

在 Agent-Team 自身材料内，指令优先级固定为：

1. 用户原始请求 `REQUEST.md`；
2. 用户明确指定的仓库文档或验收标准；
3. 当前 Resume Payload 中用于解除所引用 Block 的后续用户指令；
4. `PROTOCOL.md` 对本次运行的解释；
5. 当前 Input Event 为 Handoff 时，其中的 Requested Next Action。

Resume 指令只能补充“如何在同一 Run 内继续”，不能覆盖前两项，也不能改变 Kickoff 后不可变的原始目标、协议、角色/Binding、Workspace、Launch Profile 或安全上限。出现这类变化时，Origin 不得提交 Resume，应取消旧 Run 并重新 Bootstrap。

事实与证据使用另一套顺序：

1. 对最新工作区、产物和命令结果的直接检查；
2. Runtime 自动采集的 System Facts；
3. Handoff 中可复核且已经验证的事实；
4. Handoff 中尚未独立验证的发送方判断。

高优先级指令不能把与实际工作区矛盾的陈述变成事实。若 `PROTOCOL.md` 与原始请求冲突，或观察到的事实使协议前提不再成立，Agent 不得自行选择有利解释，应 `block` 并返回 Origin。

---

## 13. 最小运行时结构

Stage 1 不结构化工作流语义，但必须结构化传输、会话映射和技术生命周期。这里的 Event / Turn 状态只回答“谁持有 Token、进程执行到哪里”，不表达 Review 是否通过等业务条件。

## 13.1 `team.json`

```json
{
  "schema_version": 2,
  "run_id": "at-20260725-7f3a",
  "workspace": "/repo/project",
  "origin": {
    "harness": "codex",
    "session_mode": "embedded"
  },
  "roles": {
    "reviewer": {
      "binding": "origin"
    },
    "developer": {
      "binding": "external",
      "adapter": "claude-code",
      "session_policy": "resume",
      "launch_profile": "default",
      "launch_profile_sha256": "..."
    }
  },
  "initial_role": "developer",
  "limits": {
    "max_turns": 20,
    "max_wall_time_seconds": 7200
  },
  "observability": {
    "audit_mode": "standard",
    "redaction": "standard",
    "max_trace_bytes": 67108864,
    "raw_retention": "redacted",
    "required_payload_sections": [
      "Decision rationale",
      "Evidence"
    ]
  }
}
```

`REQUEST.md`、`PROTOCOL.md` 和 `team.json` 在 Kickoff Event 提交后不可变。需要改变原始请求、协议、团队定义或安全上限时创建新 Run，不在运行中修改这些配置。

`roles.<role-id>` 是由 `binding` 区分的闭合集合：

- `binding=origin` 不允许出现 `adapter`、`session_policy` 或 `launch_profile`；
- `binding=external` 必须同时提供 `adapter`、`session_policy=resume|fresh`、`launch_profile` 和 `launch_profile_sha256`。

固定 Validator 还必须保证：`run_id` 与最终 Run 目录名完全相同；规范化 `workspace` 与 `.agent-team/root.json` 相同；`roles` 非空且所有 Key 都满足 Role ID 规则；`initial_role` 正好引用其中一个 Role；`origin.session_mode` 在 Stage 1 固定为 `embedded`。任一不变量在 Kickoff 前失败都拒绝 `start`，Kickoff 后被改写则由配置 Hash 直接进入 `CORRUPTED`。

`limits.max_turns` 与 `limits.max_wall_time_seconds` 都必须是正整数；`init` 在二者小于 `1` 时拒绝配置，保证 Kickoff 至少允许创建首个业务 Turn。

`observability.audit_mode=standard|full` 固定本 Run 的审计边界。`standard`
允许 Origin Business Role，但必须把这类 Turn 标记为只覆盖正式输入、正式输出和
Workspace 边界，不能声称采集到宿主的内部 Tool Stream。`full` 要求所有 Business
Role 都使用 External Binding，Origin 只承担 Bootstrap、等待、终态审计和用户交付；
任一 Turn 的 Raw Capture 或 Normalized Trace 被大小上限截断时，Runtime 提交
Recovery Block，不能把不完整审计当作正常完成。

`redaction=standard|none`、`max_trace_bytes` 和
`raw_retention=redacted|keep|delete` 分别固定派生 Trace 的启发式脱敏策略、每 Turn
stdout/stderr Source Byte 与 Normalized Trace Byte 上限，以及 Raw Stream 在
Trace 生成后的保留方式。`full` 不允许 `delete`。`required_payload_sections`
是大小写不敏感、不可重复的 Markdown 标题闭集；Full Audit 至少要求
`Decision rationale` 与 `Evidence`。Schema 1 的历史 Run 继续按
`redaction=none, raw_retention=keep` 读取，不就地改写 `team.json`。

外部 Binding 的 `launch_profile` 是 Adapter 自己定义并由 Capability Probe 返回的闭集标识，只描述 Harness 的技术启动权限，不表达 Reviewer、Developer 等业务角色。每个 Profile 必须显式设置所有权限相关参数，不能把可变的用户默认配置当作 Profile 的一部分；Bootstrap 显式选择并记录该值，不得根据 Role ID 或 `PROTOCOL.md` 中的 `read-only` 自动映射。

`launch_profile_sha256` 是对 Adapter 标识与版本、Harness 可执行文件真实路径与版本、以及该 Session Policy 实际需要的规范化 Start / Resume 权限映射做长度前缀编码后的 SHA-256。`init` 由 Probe 生成，`start` 和每个 External Turn 在启动前重新计算并要求完全相等。Kickoff 前不一致直接拒绝；Kickoff 后不静默采用新映射，由已创建 Turn 提交不可 Resume 的 `block_reason=profile_changed`。系统 Payload 记录 Profile 名称、冻结 / 当前 Hash、Adapter 与 Harness 版本，用户只能取消旧 Run 并用新 Run 接受新 Profile 含义。

Stage 1 不接受 per-role CWD；所有外部 Harness 都以规范化后的 `workspace` 为工作目录。需要修改多个根目录时必须拆成多个 Run 或等待后续版本，不能只锁其中一个目录。

Stage 1 只接受一个 Git Worktree 根目录：规范化后的 `workspace` 必须等于 `git rev-parse --show-toplevel` 的规范化结果。非 Git 目录、Worktree 子目录或同时包含多个仓库的上层目录都在 `init` 阶段明确拒绝，不提供非 Git Snapshot 分支。

Stage 1 同时拒绝启用了 Sparse Checkout 的 Worktree，以及索引中包含 `160000` Gitlink 的仓库。Coordination Skill 也明确禁止运行中启用 Sparse Checkout 或新增 Gitlink；用户任务若本身要求这些操作，Bootstrap 应在 Kickoff 前说明 Stage 1 不支持并停止，而不是先执行再让 Run 损坏。首版不为缺省未检出的 Sparse 路径或 Submodule 工作树定义另一套 Snapshot 语义。

这里故意没有：

- Edge；
- Condition；
- Severity Rule；
- Completion Transition Guard；
- Reviewer Verdict Schema。

## 13.2 Event 元数据

每次 Kickoff、Handoff、Complete、Block、Resume 或 Cancel 动作对应一个不可变 Event 文件：

```json
{
  "schema_version": 1,
  "event_id": "handoff-0004",
  "event_seq": 4,
  "prev_event_id": "handoff-0003",
  "event_type": "handoff",
  "from_role": "developer",
  "to_role": "reviewer",
  "turn_id": "turn-0003",
  "payload_path": "handoffs/0004-developer-to-reviewer.md",
  "payload_sha256": "...",
  "created_at": "2026-07-25T21:43:10-07:00"
}
```

Event 只描述传输，不描述“为什么 Review 失败”等业务语义。

`events/<event-seq>-<event-id>.json` 的原子 `rename` 是一次状态转换的唯一提交点：

- `event_seq` 在 Run 内严格递增；
- `prev_event_id` 必须等于当前 Journal Tail；
- 结束业务或管理 Turn 的 Event 必须携带 `turn_id`；Kickoff，以及没有活跃 Turn 时的 Cancel 可以没有 `turn_id`；
- 同一个 `turn_id` 最多产生一个终止 Event；重复请求返回已有结果或明确拒绝冲突内容；
- Event 一旦提交不得修改；
- 角色 Inbox、Run Status 和当前 Token Owner 都直接由 Journal 与 Turn Runtime 推导。

Stage 1 的 `event_type` 是以下闭合集合。这里定义的只是技术转换，不判断 Review、QA 或其他业务条件：

| `event_type` | 允许的当前状态 | 必填类型字段 | 新状态 | 新 Token Owner |
|---|---|---|---|---|
| `kickoff` | `UNSTARTED` | `to_role`、Payload、`request_sha256`、`protocol_sha256`、`team_sha256` | `RUNNING` | `to_role` |
| `handoff` | `RUNNING` 且允许创建下一业务 Turn | `turn_id`、`from_role`、`to_role`、Payload | `RUNNING` | `to_role` |
| `complete` | `RUNNING` | `turn_id`、`from_role`、Payload | `COMPLETED` | 无 |
| `block` | `RUNNING` | `turn_id`、`from_role`、`block_reason`、Payload；`block_reason=limit` 时还必须有 `limit_reason=deadline` 或 `limit_reason=max_turns` | `BLOCKED` | 无 |
| `resume` | `BLOCKED`、Tail 的 `block_reason` 不属于 `{limit, profile_changed}`、无未解除的 `recovery_required` 且允许创建下一业务 Turn | 管理 `turn_id`、`to_role`、Payload | `RUNNING` | `to_role` |
| `cancel` | `RUNNING` 或 `BLOCKED` | `request_id`、`cancel_reason=user`；存在活跃 Turn 时还必须有 `turn_id` | `CANCELLED` | 无 |

`block_reason` 只记录技术来源，Stage 1 固定支持：

```text
agent | limit | profile_changed | recovery | start_failure | no_action | permission
```

Runtime 生成技术 Block 时不经过 Outbox：先写一份不可变的系统 Payload，记录 `turn_id`、原因和可用技术事实，再按 15.4 的同一事务提交 Block Event。

Limit、Profile Changed、Recovery 和 Start Failure 不再使用新的 Event 类型，分别写成 `event_type=block` 与对应 `block_reason`。Cancel 和 Limit 一旦决定就直接提交 Event，不再先写第二套持久化请求。`resume` 只能在用户明确授权后由 Origin 管理命令生成；Limit 与 Profile Changed Block 永远拒绝 Resume。Stage 1 不保留没有确定生产路径的通用 `fail` Event；可恢复的技术故障统一 Block，无法安全追加 Event 的完整性故障统一推导为 `CORRUPTED`。所有提交者都必须在 Run 锁内根据上表校验当前 Journal Tail，终止状态不接受后续 Event。

`CORRUPTED` 不是 Event 转换，而是以下任一情况直接推导出的只读故障状态：

- 最终 Run Directory 中的 `journal.lock` 缺失、不是普通文件或被替换；State Root 已提交后，对应固定 Workspace 操作锁缺失或无效；
- Journal 序号、Tail 链或 Event Payload Hash 校验失败；
- 已提交 Event 的类型、必填字段、前置状态或安全守卫不符合 13.2；
- Kickoff 后 `REQUEST.md`、`PROTOCOL.md` 或规范化 `team.json` 的当前 Hash 与 Kickoff 记录不一致；
- 按 22.4 尚未满足安全释放条件的 Run，其 Workspace Owner 缺失、损坏或不再属于本 Run；
- Kickoff 后的边界校验发现 Workspace 不再是原规范化 Git Worktree 根目录、启用了 Sparse Checkout、索引出现 Gitlink、索引开始跟踪 `.agent-team/`，或 `.agent-team/root.json` 不再有效；
- 业务 Turn 的 Before Facts 在 Turn Runtime 提交前无法完整采集，因而不存在可合法生成技术 Block 的 `turn_id`；
- 已存在的不可变 `runner.json` 或 `launch-authorized.json` 无法通过固定 Schema、Turn、Nonce、身份和 Launch Profile 一致性校验，或它们已被其他有效快照引用后又缺失；
- Runtime、Turn Supervisor、Runner、Outbox 或 Workspace Facts 损坏后，无法从 Journal 和目录结构唯一确定当前活跃 Turn；
- 已存在的 Session 快照无法通过固定 Schema 校验。首次 Session 尚未产生时文件可以不存在，但不得把损坏文件猜测性改写为 `unavailable`。

此时禁止追加 Event、启动 Harness、重建 Worker 或自动获取 / 释放 Ownership。Runtime 可以依据完整的 PID / PGID / Start ID 做执行安全清理，但不能把清理结果写成新的业务 Event。确认无活进程后，用户才可显式 Unlock；若 Owner 在发现故障前已经按终态规则安全释放，则只报告审计损坏，不重新获取 Owner。

状态读取先扫描不可变 Journal 以判断合法终态与 Owner 是否本应存在，再执行其余完整性校验；任何 `CORRUPTED` 条件都优先于 `RUNNING | BLOCKED | COMPLETED | CANCELLED` 的正常展示。这样同一损坏 Run 不会因调用的是 `status`、`claim` 还是 `recover` 而得到不同状态。

Stage 1 的 Run 有较小 `max_turns` 和 Wall Time 上限，`status`、`claim` 和 `recover` 直接按文件名顺序扫描并校验 `events/`，再读取必要的 Turn Runtime。不维护第二份语义状态缓存，避免缓存更新和恢复协议。

## 13.3 Worker 与 Turn Runtime

Worker 在启动 Harness 前冻结 `workspace-facts-before.json`，并原子创建引用其 Hash 的 `turns/<turn-id>/runtime.json`：

```json
{
  "schema_version": 1,
  "turn_id": "turn-0004",
  "business_turn_seq": 4,
  "input_event_id": "handoff-0004",
  "input_payload_sha256": "...",
  "role_id": "developer",
  "executor": "worker",
  "phase": "starting",
  "outcome": null,
  "session_generation": 3,
  "launch_profile": "default",
  "launch_profile_sha256": "...",
  "launch_nonce": null,
  "supervisor_pid": null,
  "supervisor_start_id": null,
  "runner_pid": null,
  "runner_pgid": null,
  "runner_start_id": null,
  "agent_execution_started": false,
  "group_quiescent": null,
  "workspace_facts_before_sha256": "...",
  "workspace_facts_after_sha256": null,
  "process_exit_code": null,
  "adapter_completed": false,
  "termination_kind": null,
  "terminal_event_id": null,
  "trace_manifest_sha256": null,
  "created_at": "2026-07-25T21:43:23-07:00",
  "updated_at": "2026-07-25T21:44:00-07:00"
}
```

External Runtime 必须同时记录非空 `launch_profile` 与 `launch_profile_sha256`；Origin 和管理 Runtime 的两者都必须为 `null`。启动许可中的两值必须与 External Runtime、`team.json` 和本次 `LaunchSpec` 完全一致。

`created_at` 在 Runtime 首次提交时写入且不得改变，`updated_at` 随原子替换前进。Status 的 `active_turn.age_seconds` 只按 `observed_at - created_at` 计算并下限取 `0`，不使用文件时间或会被更新的 `updated_at`。

`trace_manifest_sha256` 只适用于 External Turn，初始为 `null`。Supervisor 已退出且
Runner Group 被证明清空后，Worker 生成 Turn Trace Manifest，把其原始字节
SHA-256 一次性写入该字段；非空后不可替换。Schema 2 Run 中已经执行且
`phase=finalized` 的 External Turn 缺少该锚点属于损坏。

每个外部 Worker 在进入事件循环前原子创建或替换 `roles/<role-id>.json`：

```json
{
  "schema_version": 1,
  "role_id": "developer",
  "worker_pid": 12345,
  "worker_start_id": "os-process-start-id",
  "tmux_session": "agent-team-at-20260725-7f3a",
  "tmux_pane_id": "%4",
  "updated_at": "2026-07-25T21:43:50-07:00"
}
```

该文件只描述长期 Worker 的技术身份，不参与 Token 或 Run Status 推导。Worker 重建时整文件替换；因此首个 Turn 前、两个 Turn 之间和 tmux 恢复时都有可验证的 Worker 身份。

领取 Kickoff、Handoff 或 Resume Event 时，Runtime 先校验 Event Payload Hash，再把其当前字节原子复制为不可变的 `turns/<turn-id>/input.md`；`input_payload_sha256` 必须等于 Event 中的 `payload_sha256`。因此每个业务 Turn 都有统一的当前输入，不把 Resume 指令降级成仅存在于 Journal 中的附注。

Stage 1 中一个 External 业务 Turn 只允许一次 Harness 启动，不引入自动启动重试。Worker 先调用无副作用的 `prepare_launch()`；失败就直接提交 Start Failure Block。准备成功后，Worker 生成不可猜测的 `launch_nonce`，再启动短生命周期的 `agent-team turn-supervisor`。

Supervisor 不加入受管 Harness 进程组。它必须先原子创建一份 `state=starting` 的自身快照：

```json
{
  "schema_version": 1,
  "turn_id": "turn-0004",
  "launch_nonce": "...",
  "state": "starting",
  "supervisor_pid": 23456,
  "supervisor_start_id": "os-process-start-id",
  "runner_pid": null,
  "runner_pgid": null,
  "runner_start_id": null,
  "agent_execution_started": false,
  "adapter_completed": false,
  "permission_required": false,
  "observed_session_ref": null,
  "process_exit_code": null,
  "termination_kind": null,
  "group_quiescent": false,
  "updated_at": "2026-07-25T21:44:01-07:00"
}
```

只有这份快照提交完成后，Supervisor 才以 `start_new_session` 启动 `agent-team harness-runner`。Runner 成为新 Session 和新进程组的 Leader，并且必须在读取任何启动许可前，自行原子创建不可变的 `process/runner.json`：

```json
{
  "schema_version": 1,
  "turn_id": "turn-0004",
  "launch_nonce": "...",
  "runner_pid": 23457,
  "runner_pgid": 23457,
  "runner_start_id": "os-process-start-id",
  "created_at": "2026-07-25T21:44:01-07:00"
}
```

Supervisor 等到并验证 Runner 身份后，把自己的可变快照更新为：

```json
{
  "schema_version": 1,
  "turn_id": "turn-0004",
  "launch_nonce": "...",
  "state": "waiting_authorization",
  "supervisor_pid": 23456,
  "supervisor_start_id": "os-process-start-id",
  "runner_pid": 23457,
  "runner_pgid": 23457,
  "runner_start_id": "os-process-start-id",
  "agent_execution_started": false,
  "adapter_completed": false,
  "permission_required": false,
  "observed_session_ref": null,
  "process_exit_code": null,
  "termination_kind": null,
  "group_quiescent": false,
  "updated_at": "2026-07-25T21:44:01-07:00"
}
```

`state` 固定为 `starting | waiting_authorization | running | stopping | finished`，只能沿该顺序向前移动；不适用的中间状态可以跳过，但不能回退。固定解析器还必须校验以下状态不变量：

- `starting`：Supervisor 身份非空，Runner 三元组必须全部为 `null`，`agent_execution_started=false`；
- `waiting_authorization`：Supervisor 与 Runner 身份都必须完整，`agent_execution_started=false`；
- `running`：Supervisor 与 Runner 身份都必须完整，`agent_execution_started=true`；
- `stopping`：Supervisor 与 Runner 身份都必须完整，并保留进入停止流程前已经观察到的 `agent_execution_started` 值；
- `finished`：`group_quiescent` 必须为 `true`；只有 Runner 创建本身已经明确失败时，Runner 三元组才允许全部为 `null`，否则必须完整；
- `finished` 之前 `group_quiescent` 必须为 `false`；Runner 三个身份字段只能同时为空或同时非空，不能接受部分身份。

Supervisor 的固定 Evidence 字段为 `agent_execution_started`、`adapter_completed`、`permission_required` 和 `observed_session_ref`。前三者初始都为 `false`，Session Ref 初始为 `null`；它们只能由 Adapter 的结构化记录单调更新。`adapter_completed=true` 必须蕴含 `agent_execution_started=true`，并与 `permission_required=true` 互斥。Turn Runtime 在 Finalize 时复制最终的 `agent_execution_started` 与 `adapter_completed`。恢复过程只信已经原子持久化的 Evidence Snapshot；原始 JSONL 只供审计和诊断，不能在 Supervisor 退出后通过重放补造或升级 Evidence。

Supervisor 必须先持久化自身身份再创建 Runner。Supervisor 在 Runner 身份提交前崩溃时，最多留下一个尚未登记、也不可能获得许可的惰性 Runner；它等待超时后退出，不能创建 Harness 或 Session 副作用。

Worker 读回并用操作系统验证 Supervisor 与 Runner 身份后，在同一个 Run 锁临界区重新校验 Journal Tail、Workspace Ownership、配置 Hash、当前 Launch Profile Fingerprint、Deadline 和 `recovery_required`，把两份身份、Profile 与 `launch_nonce` 写入 Turn Runtime，并原子创建：

```json
{
  "schema_version": 1,
  "turn_id": "turn-0004",
  "launch_nonce": "...",
  "supervisor_pid": 23456,
  "supervisor_start_id": "os-process-start-id",
  "runner_pid": 23457,
  "runner_pgid": 23457,
  "runner_start_id": "os-process-start-id",
  "launch_profile": "default",
  "launch_profile_sha256": "...",
  "authorized_at": "2026-07-25T21:44:02-07:00"
}
```

`process/launch-authorized.json` 是该 Turn 唯一且不可撤销的 Harness 启动许可。只有 Runner 可以消费它；Runner 必须同时校验 Turn、Nonce、Supervisor PID / Start ID、自身 PID / PGID / Start ID，以及 LaunchSpec 的 Profile 名称与 Hash。文件不存在时，任何路径都不得执行 Harness；文件一旦存在，同一 Turn 的恢复路径永远不得再次启动 Runner 或 Harness。Cancel 或 Limit Event 若先取得 Run 锁，后续许可必须拒绝；许可若先提交，Runner 的稳定进程组身份和实际授权 Profile 已经持久化，稍后提交的终止 Event 可以定位原执行。

Runner 获得许可后不再 Fork 第二个主进程，而是原地 `exec` LaunchSpec 中的 Harness；PID、PGID 和 Start ID 因此在 `exec` 前后保持不变，不存在“已创建 Harness、身份尚未落盘”的窗口。Runner 使用一个 `close-on-exec` 状态管道：`exec` 失败时向 Supervisor 写入结构化错误并退出；文件描述符关闭还要结合进程退出状态和 Adapter 的结构化启动证据判断。只有证据完整时才记录 `agent_execution_started=true`，Runner 意外死亡或管道异常关闭一律是 `start_unknown`。许可已提交但 Supervisor 最终快照缺失时同样至少是 `start_unknown`，且仍然禁止第二次启动。

Supervisor 留在 Runner 进程组之外，持久化 Harness 原始 stdout/stderr，并同时监听 Journal、Deadline、结构化 Permission Evidence，以及 Kickoff Hash、当前 Event / `input.md` Hash、Runner / 启动许可、State Root 和 Workspace Owner 的固定完整性守卫。Permission Evidence 出现后立即进入停止流程，不能在非交互 Turn 中无限等待人工输入；完整性失败时只终止已验证的 Runner 进程组并记录技术结果，不追加 Event。主 Harness 退出后，Supervisor 先等待短暂宽限期，再对 Runner PGID 发送温和终止，超时后发送强制终止。因为 Supervisor 不属于该 PGID，`killpg` 不会误杀负责写最终状态的 Supervisor。只有确认记录的 Runner PGID 已经不存在，Supervisor 才写入 `group_quiescent=true` 的最终快照并退出。该字段只证明受管 PGID 已清空，不证明系统中不存在曾由 Harness 创建、后来主动脱离该进程组的进程。

Stage 1 的 Coordination 合同禁止角色启动通过 `setsid`、`setpgid`、双重 Fork 等方式逃离 Runner 进程组的后台 daemon。Adapter Probe 只能拒绝“CLI 启动器自身会立即脱离进程组”的实现，并通过集成测试验证主进程和普通子进程的受管行为；它不能证明模型以后执行的任意工具命令都不会逃逸。运行时观察到可确认的逃逸证据时提交 Recovery Block 并保留 Ownership；未被观察到的逃逸进程属于 Stage 1 明确接受的本机协作前提。Stage 1 不把 `group_quiescent` 描述为容器级隔离或完整后代进程证明。

`worker_start_id`、`supervisor_start_id` 与 `runner_start_id` 都是操作系统可验证的进程创建标识，不是 Runtime 自己生成的当前时间。存在 External Binding 且当前平台无法提供稳定 Start ID 时，`start` 直接拒绝启动；纯 Origin Run 不需要伪造该能力。终止时优先向 PID / Start ID 匹配的 Supervisor 发请求，由它清理 Runner 进程组；Supervisor 不响应时，只有 Runner PID、PGID 与 Start ID 全部匹配才可直接 `killpg`。若 Runner Leader 已退出、Supervisor 也无法验证而 PGID 仍疑似有成员，则身份未知，不发送信号。PID 存在但 Start ID 不同表示 PID 已复用，禁止向新进程发送信号。

Turn Runtime 只保留五个技术阶段：

```text
starting
running
exited
finalized
recovery_required
```

正常路径为：

```text
starting → running → exited → finalized
```

失败启动、取消和无终止动作不再扩展为独立阶段，而记录为 `finalized` 的 `outcome`：

```text
success | failed | cancelled | stalled
```

只有 Supervisor 或 Runner 进程组可能仍存活、身份无法验证或无法证明 Runner 进程组已经清空时进入 `recovery_required`。已明确退出且 Runner 进程组已清空的异常 Harness 直接以 `phase=finalized, outcome=failed` 提交 Recovery Block，不再要求多一次 `recover`。Turn Runtime 记录“执行到了哪里”，但不能单独转移 Token；只有新的 Event Journal Tail 可以转移 Token 或结束 Run。

`termination_kind` 固定为 `normal | cancelled | deadline | signal | crash | unknown`。只有 Adapter 明确观察到正常 Turn 完成、退出码符合该 Adapter 的成功约定且 `termination_kind=normal` 时，`adapter_completed` 才能为 `true`。

Origin 领取业务 Turn 时同样先冻结 `workspace-facts-before.json` 并创建 `runtime.json`，但使用 `executor=origin`，不创建 Supervisor，也不填写 Session Generation、Launch Profile、Launch Nonce 或进程身份，并保存不可猜测的 `origin_claim_id`。该 Claim 没有自动超时，只在 Turn 终止或 Run 被取消时失效；Stage 1 不允许另一个 Session 替换活跃 Claim。Run 被其他命令终止时，Claim 立即失去业务和管理写权限，但原 Session 仍可用它调用 `wait-origin` 确认终态并把 Origin Runtime 收口为 `finalized`。

宿主没有“当前模型采样已经结束”的结构化信号，因此 Origin 的终止动作分两类处理：

- `origin-handoff` / `origin-resume` 提交 Event 后在同一 CLI 进程中继续等待，宿主 Agent 尚未重新获得执行窗口，旧 Origin Runtime 可以直接 `finalized`；
- `origin-complete` / `origin-block` 以及异步 Cancel 只把旧 Origin Runtime 写到 `phase=exited` 并使 Claim 的业务写权限失效，不把“CLI 已返回”误当成宿主 Agent Turn 已结束。Coordination Skill 在该 Agent Turn 中此后只能向用户交付 Completion / Block，不得再调用工具或修改业务文件；下一次用户 Agent Turn 携带原 Claim 调用 `wait-origin` 时才确认并写成 `finalized`。若原 Session 或原 Claim 永久丢失，则只能由用户在确认旧 Turn 已停止后显式 Unlock。

Block 后创建的 Origin 管理 Turn 不承载业务 Token、不采集 Workspace Facts，也不计入 `max_turns`；其 `business_turn_seq` 与 Facts Hash 字段为 `null`，Resume 或 Cancel Event 使用其 `turn_id` 收口该管理 Turn。

唯一允许业务 Turn 的 Before Facts Hash 为 `null` 的情况，是 Wall Time 已在合法 Kickoff / Handoff / Resume Event 提交后、目标 Claim 前到期。Claim 先检查 Deadline，不尝试 Snapshot 或启动 Executor；它只创建带连续 `business_turn_seq` 的技术 Turn，立即写成 `phase=finalized, outcome=cancelled, termination_kind=deadline` 并提交 Limit Block。除此之外，业务 Turn 必须先成功冻结 Before Facts 才能提交 Runtime。

`max_turns` 精确定义为“已创建的业务 Turn Runtime 数量”。业务 Turn 的 `business_turn_seq` 从 `1` 开始连续递增；External 与 Origin 都计数，管理 Turn 不计数。Stage 1 没有同一 Turn 内的自动启动重试；用户授权 Resume 后创建的新业务 Turn 正常计数。`business_turn_count` 是已校验 Runtime 中最大的连续 `business_turn_seq`，目标领取 Token 时在 Run 锁内写入下一个序号。缺号、重复或与 Event 输入链不一致时不得猜测计数，按 22.3 处理 Runtime 损坏。所有实现只使用一个守卫：

```text
can_create_business_turn =
  business_turn_count < max_turns
  and now < kickoff.created_at + max_wall_time_seconds
```

两类 Executor 使用同一个 Wall Time 判定，但执行保证不同：External Turn 的 Supervisor 能异步观察并终止受管 Runner，因此它是受管进程范围内的硬 Deadline；Origin Turn 只能在下一次 `wait-origin` 或 `origin-*` CLI 边界发现超时，是明确的协作式 Deadline。Stage 1 不把后者描述成宿主级强制中断。

该守卫必须在 Run 锁内用于外部 Handoff Candidate 接受、Handoff / Resume Event 提交，并在目标领取 Token 时再次检查：

- 当前第 `max_turns` 个业务 Turn 仍可 Complete、Agent Block 或接受用户 Cancel，但其 Handoff Candidate 不交付，Runtime 改为在当前 Turn 提交 Limit Block；
- Blocked Run 在守卫失败时拒绝 Resume，保留原 Block 和管理 Claim，只允许 Cancel、查询状态或创建新 Run；
- 若 Kickoff / Handoff / Resume Event 提交后才跨过 Wall Time，目标领取方创建一个不启动 Harness 的业务 Turn Runtime，立即以 `outcome=cancelled` 和 `termination_kind=deadline` 提交 Limit Block；
- 同一个 Run 锁判定中的优先级固定为“已有 Cancel Event > 已到期 Deadline > Max-Turn Handoff > 普通 Outbox”；Event 一旦提交就按 Journal 顺序处理，用户仍可从 Blocked 状态追加 Cancel。不得用 Complete 或普通 Block 绕过已经到期的 Deadline。

Journal 扫描必须按 Event 提交时的业务 Turn 数和 `created_at` 验证 Handoff / Resume 守卫。若 Event 在提交时就已超过 `max_turns` 或 Wall Time，它是无效的已提交转换，直接推导为 `CORRUPTED`；只有 Event 合法提交后才跨过 Wall Time，才走“不启动目标 Harness 并提交 Limit Block”的正常竞态收口。

`recovery_required` 是独立于 Turn 数量的安全门。只要旧 Supervisor 或 Runner 进程组仍存活、身份无法验证或未证明清空，Resume Event、Origin 业务 Claim 和新 Harness 启动都必须拒绝；`recover` 只有在证明 Supervisor 已结束且 Runner 进程组已清空后才能把该 Runtime 收口为 `finalized` 并解除安全门。无法证明时只允许 Cancel 和诊断；Unlock 也必须拒绝，不能以新的 Turn 或新的 Run 覆盖不确定执行。

Claim 用于阻止两个正常 Origin Session 意外并发，不是对拥有 Run Store 读写权限的恶意本机进程的安全边界。

## 13.4 恢复关键快照

`workspace-facts-before.json` 与 `workspace-facts-after.json` 使用同一个最小 Schema：

```json
{
  "schema_version": 1,
  "turn_id": "turn-0004",
  "boundary": "before",
  "snapshot_scope": "git_visible",
  "captured_at": "2026-07-25T21:44:00-07:00",
  "workspace_realpath": "/repo/project",
  "git_head": "0123456789abcdef...",
  "git_status_sha256": "...",
  "business_tree_sha256": "...",
  "workspace_state_sha256": "...",
  "tracked_path_count": 412,
  "untracked_path_count": 3,
  "diff_stat": "4 files changed, 31 insertions(+), 8 deletions(-)"
}
```

其中 `boundary` 固定为 `before | after`，`snapshot_scope` 固定为 `git_visible`，无提交的仓库允许 `git_head=null`。Fingerprint 规则只有一套：

1. 先重新验证 `workspace_realpath` 仍等于 Git Worktree 根目录、`.agent-team/root.json` 与当前 Workspace 匹配、Sparse Checkout 仍关闭、Git 索引没有 `.agent-team/` 路径，且 `git ls-files --stage` 中没有 `160000` Gitlink；任一条件失败都不生成 Facts，Kickoff 后按 `CORRUPTED` 处理；
2. 在该根目录分别运行 `git ls-files -z --cached` 和 `git ls-files -z --others --exclude-standard`，得到 tracked / untracked 集合；
3. 排除 `.agent-team/`，对并集按原始路径字节排序，并在每条记录中写入 `tracked | untracked`；
4. 对每个路径使用 `lstat`，文件类型闭集只有 `regular | symlink | missing`：普通文件记录规范化的 `100644 | 100755` 与内容 SHA-256；符号链接记录 `120000` 与链接目标字节 SHA-256；tracked 路径不存在，或原 tracked 文件路径当前已成为目录时，都把原索引项编码为 `missing`，其模式固定为 `000000`、内容使用固定空标记；目录下由 `git ls-files --others` 返回的新文件仍各自按 untracked 记录；
5. untracked 路径在采集时消失，或任一路径遇到设备、FIFO 等其他类型时，本次 Snapshot 明确失败，不生成部分 Facts；untracked 列表若异常返回目录本身也明确失败；
6. 对长度前缀编码的完整记录流计算 `business_tree_sha256`，不使用 mtime；
7. 对过滤掉 `.agent-team/` 的 `git status --porcelain=v2 -z --untracked-files=all` 原始字节计算 `git_status_sha256`；
8. 对 `git_head`、`git_status_sha256` 和 `business_tree_sha256` 的定长拼接计算 `workspace_state_sha256`。

`missing` 表示原 tracked 索引项当前不再是可读取的普通文件或符号链接，包括直接删除和文件被目录替换；它不是读取错误。Sparse Checkout 与 `160000` Gitlink 在 `init` 和每次边界 Snapshot 都重新拒绝，因此不会把运行中改变的 Workspace 形态误记成普通删除。

该 Snapshot 明确只覆盖 tracked 与未被 ignore 的 untracked 路径，不是全文件系统快照。测试产生的 ignored cache、依赖目录或构建输出可以作为临时副作用变化，但 Runtime 不对它们做连续性证明；Handoff、Review 和 Completion 不得把 ignored 路径中的内容当作已验证的跨 Turn 交付物。任务若确实要求交付 ignored 文件，应先复制到 Git 可见路径；无法这样做时 Block，而不是把 `workspace_state_sha256` 描述为覆盖它。

边界失败的转换固定如下：

- Kickoff 前的可行性检查失败：`start` 释放精确 Owner 并拒绝启动；
- Kickoff 后，Before Facts 尚未完成且 Turn Runtime 尚未提交：直接推导为 `CORRUPTED`，不得构造一个没有稳定 `turn_id` 的 Recovery Block；
- 已有活跃 Turn 时 After Facts 采集失败：不交付 Outbox，由该 Turn 提交 Recovery Block；恢复过程不能在稍后的 Workspace 上补造历史 After Facts。

比较“当前 Workspace 是否仍等于某个边界”只比较 `workspace_realpath` 与 `workspace_state_sha256`；`captured_at` 不参与。Turn Runtime 中的 Facts Hash 则是对已冻结 JSON 文件原始字节计算的 SHA-256，用于验证那份历史快照没有被改写。`diff_stat` 和计数只用于展示，不参与等价判断。

普通 Handoff 的目标 Turn 在启动前，必须确认自己的 Before `workspace_state_sha256` 等于来源 Turn 已冻结的 After 值；不一致时提交 Recovery Block，不启动 Harness。技术故障若没有可信 After Facts，则不能伪造连续性；用户明确 Resume 后，新 Turn 以当前 Before Facts 建立新基线，并在 Prompt 与日志中标记 `WORKSPACE_CONTINUITY_UNKNOWN`。

每个 External Role 的 `sessions/<role-id>.json` 使用：

```json
{
  "schema_version": 1,
  "role_id": "developer",
  "adapter": "claude-code",
  "generation": 3,
  "status": "available",
  "session_ref": "550e8400-e29b-41d4-a716-446655440000",
  "effective_launch_profile": "default",
  "effective_launch_profile_sha256": "...",
  "created_turn_id": "turn-0001",
  "updated_turn_id": "turn-0004",
  "unavailable_reason": null,
  "updated_at": "2026-07-25T21:44:10-07:00"
}
```

`status` 固定为 `available | unavailable`。`available` 必须有非空 `session_ref`、`effective_launch_profile` 和 `effective_launch_profile_sha256`；`unavailable` 必须令 `session_ref=null`、保留最后实际使用的 Profile 名称与 Hash，并记录 `unavailable_reason`。创建新的 Harness Session 时递增 `generation`；恢复同一 Session 时保持不变。`session_policy=fresh` 因而每个业务 Turn 都创建新一代，`resume` 只有在旧 Session 不可用并经用户明确 Resume 后才创建新一代。Worker 在 Run 锁内以 Runtime 的 `session_generation` 做比较后再替换 Session 文件，旧 Turn 不得覆盖新一代 Session。

首次 Session Ref 尚未产生时允许 Session 文件不存在；需要 Resume 时文件仍不存在则按 unavailable 处理，不得猜造 Session Ref。新 Session 的候选 Generation 先写入当前 Turn Runtime，Adapter 观察到结构化 Session Ref 后再在 Run 锁内提交 Session 文件；Start Failure 不提交 Session 文件，后续用户授权的新 Turn 从最后一份已提交 Session 状态重新计算 Generation；`start_unknown` 不得据此猜造 Session Ref。

这些 JSON 的未知字段在同一 `schema_version` 下拒绝，缺失必填字段也拒绝。Stage 1 不引入通用 Schema Registry；实现中为这几种固定文件各保留一个解析器即可。

---

## 14. 单执行 Token 模型

## 14.1 模型定义

任意时刻只有一个团队角色拥有主执行权：

```text
Token owner = derive_owner(event_journal_tail)
```

- `kickoff` 的 `to_role` 获得初始 Token；
- `handoff` Event 将 Token 从 `from_role` 转移给 `to_role`；
- `complete` Event 将 Team Run 进入 Completed，并通知 Origin；
- `block` Event 将 Team Run 进入 Blocked，业务 Token 暂时为空，并把管理控制交回 Origin；
- `resume` Event 从可 Resume 的 Blocked 状态重新指定 Token Owner；
- `cancel` Event 将 Team Run 进入 Cancelled，并通知 Origin；
- 在 Running 状态下，只有 Journal Tail 的 Token Owner 可以提交下一业务 Event；
- 在可 Resume 的 Blocked 状态下，只有显式 Origin Resume / Cancel 管理命令可以生成恢复或终止 Event；Limit / Profile Changed Block 只允许 Cancel 或创建新 Run。

## 14.2 可表达的流程

Stage 1 可以表达：

```text
A → B → C
A ↔ B
A → B，B 根据自然语言条件选择 A 或 C
A → B → A → B → Done
```

## 14.3 不可表达的流程

Stage 1 不支持：

```text
A 同时启动 B 和 C
等待 B、C 都完成
再 Join 到 D
```

如果用户请求并行，Bootstrap 必须明确告知当前版本限制，不能静默把并行改成串行。

---

## 15. tmux Runtime 设计

## 15.1 Session 映射

存在 External Binding 时，一个 Team Run 对应一个 tmux Session；纯 Origin Run 不创建 tmux Session：

```text
agent-team-at-20260725-7f3a
├── developer
│   └── agent-team worker --role developer
├── qa
│   └── agent-team worker --role qa
└── architect
    └── agent-team worker --role architect
```

Origin 绑定角色默认不创建 Window，因为其会话已经由用户当前 Codex/Claude Code 承载。

`agent-team watch` 按需从用户当前终端运行，不占用常驻 Window。Workspace Ownership 也不依赖 tmux 进程，其持久化规则见 22.4。

## 15.2 为什么 Pane 中运行 Worker

不直接在 Pane 中长期运行交互式 Harness TUI，原因包括：

- TUI 可能处于输入框、工具执行、权限提示、选择器等不同状态；
- `send-keys` 无法理解这些语义；
- 完整 Handoff 可能被误输入到错误界面；
- 很难确定一轮工作何时结束；
- 多行文本、ANSI、Alternate Screen 和动态刷新会增加解析风险。

默认架构：

```text
tmux Pane
└── agent-team worker
      ├── 定时检查 Durable Event Journal
      ├── 可选等待 tmux wait-for 变更提示
      ├── 为每个 Turn 启动短生命周期 Supervisor
      ├── Supervisor 在组外启动独立进程组的 harness-runner
      ├── Runner 自写身份、等待许可并原地 exec Harness
      ├── Supervisor 通过 PIPE 持久化 Harness 原始日志
      ├── Worker 与 Supervisor 同时监听 Journal 和 Deadline
      ├── Supervisor 从组外清空 Runner 进程组
      ├── Worker 分类 Normal / Cancel / Deadline / Crash 并收口 Turn
      └── 回到 IDLE
```

Supervisor、Runner 和 Harness 都不得继承 Worker 的 tmux stdin，自动控制也不得向 Harness TUI 模拟键盘输入。

## 15.3 Best-effort 通知与诊断

外部目标角色的 Event 完成持久化后，可以发送同一个无载荷提示：

```bash
tmux wait-for -S agent-team:at-20260725-7f3a:developer:changed
```

Worker 可以异步等待同名 Channel；返回后只执行一次 Journal 扫描，然后重新注册等待。

该提示有意不携带 Event ID：

- 同名多次 Signal 可能合并，不是计数消息队列；
- tmux Server 重启会丢失 Channel 状态；
- Signal 失败不得回滚已经提交的 Event；
- Worker 无论是否使用提示，都至少每两秒扫描一次持久化状态。

Stage 1 不使用 `send-keys` 发送自动控制行，也不使用 `wait-for -L/-U` 代替 Run Journal 锁或 Workspace Ownership。

`capture-pane` 可由诊断命令采集 Worker 最后若干行：

```bash
tmux capture-pane -p -J -S -200 -t <pane-id>
```

采集结果只是故障线索；Harness JSONL、Worker 日志和 Event Journal 才是运行记录。

## 15.4 Durable Event Journal 与 Inbox

tmux 变更提示不是事实来源。

Event 提交顺序：

1. 获取 Run 级 `flock`；
2. 读取并校验当前 Journal Tail，按 13.2 验证 Event 类型、前置状态、Claim / Turn 权限和必填字段；
3. 将最终 Payload 写入临时文件，`fsync` 后原子 `rename`，再 `fsync` Payload 父目录；
4. 生成包含 Payload Hash、`event_seq` 和 `prev_event_id` 的 Event 临时文件；
5. `fsync` 后将 Event 原子 `rename` 到 `events/`；这一步是唯一提交点；
6. `fsync events/` 目录；
7. 释放锁；
8. 若目标是外部角色，最佳努力发送对应的 tmux `wait-for -S ...:changed` 提示。

角色 Inbox 是“当前 Journal Tail 指向该角色，且尚未产生下一 Event”的逻辑视图，不复制第二份权威 Event。Worker 至少每两秒检查一次 Journal；因此即使通知丢失，也会在周期扫描、Worker 重启或 `recover` 时恢复。

若进程在步骤 3 后、步骤 5 前崩溃，只会留下未被 Event 引用的孤立 Payload，恢复时忽略或清理。若步骤 5 已完成，Token 转移已经生效；后续通知是否成功不影响结果。

## 15.5 可观察性

每个 Worker 的技术日志写入 `logs/<role-id>.jsonl`，该 Role 的 Worker 是此文件的唯一写者；Supervisor 的事实写入对应 Turn 的 Process Snapshot 与 Stream。Pane 只显示 Worker 记录的人类可读渲染，不维护第二套日志语义。每条记录固定包含：

```json
{
  "schema_version": 1,
  "observed_at": "2026-07-25T21:44:00.123Z",
  "producer_seq": 42,
  "level": "info",
  "component": "worker",
  "message_code": "TURN_CLAIMED",
  "run_id": "at-20260725-7f3a",
  "role_id": "developer",
  "turn_id": "turn-0004",
  "event_id": "handoff-0004",
  "pid": 12345,
  "process_start_id": "os-process-start-id",
  "message": "claimed input event"
}
```

`producer_seq` 只在同一 PID / Start ID 内单调递增，进程重启后从 `1` 开始；不适用的关联字段写 `null`。`message_code` 用于筛选，`message` 只供人阅读，Runtime 和诊断逻辑都不得解析它。单一写者先序列化完整 JSON 行，再循环写满该行；Pane 渲染必须把 ESC / C0 / C1 控制字符转成可见转义。日志不参与状态转换，因此不为它增加独立锁或索引。

Harness stdout / stderr 仍逐 Turn 写入 `process/stream.jsonl`。Supervisor 在启动 Runner 前创建空文件并 `fsync` 文件及其父目录，之后作为唯一写者追加记录。Worker 日志、Raw Stream 和可选的 `stderr.log` 都以 no-follow、append、close-on-exec 方式打开，在文件描述符上验证普通文件并由唯一写者持有；路径被替换不能把后续写入重定向到 Run Store 外。`seq` 在 Turn 内从 `1` 开始并按其观察到字节块的顺序递增；它保证每条来源管道内部的字节顺序，不声称还原两个独立管道在 Harness 内的绝对发出先后。每条外层 `RawStreamChunk` 固定包含 `schema_version`、`seq`、`observed_at`、`source=stdout|stderr`、`encoding=utf-8|base64` 和 `data`。有效 UTF-8 直接保存，否则 Base64 编码，保证任意原始字节都能形成合法 JSONL。`stderr.log` 只是便于阅读的镜像，不参与 Evidence 或状态推导。

Supervisor 对两个来源合计执行 `max_trace_bytes` Source Byte 上限，并在关闭
Stream FD 后原子写入不可变的 `process/capture.json`。该文件记录
`source_bytes`、`stored_source_bytes`、`dropped_source_bytes`、
`chunks_observed`、`chunks_stored`、`truncated` 与 `closed_at`。为保证
Process Evidence 不因审计存储上限改变，Supervisor 仍对当前收到的完整字节执行
在线 Framing；但超限字节不进入 Raw Store，Full Audit 因而必须 Block。

两类日志的 `observed_at` 都使用 UTC RFC 3339；排序与重放只认 `producer_seq` 或 `seq`，不认墙钟先后。

Worker 日志和外层 Stream JSONL 都以完整换行记录为读取边界；Supervisor 崩溃留下的末尾半条外层记录只作为日志截断忽略，不能据此生成 Adapter Evidence、改变 Run Status 或把历史内容补猜出来。Supervisor 使用确定性 Framer：分别按 `source`、`seq` 解码并拼接完整 `RawStreamChunk`，只在读到 Harness 换行时向 Adapter 发出一条完整 `StreamRecord`。后者固定包含 `source`、`first_seq`、`last_seq`、取最后一个 Chunk 值的 `observed_at`、`encoding=utf-8|base64`，以及包含换行分隔符的可逆 `data`。Harness 在 EOF 前留下的未换行字节仍可从 Raw Stream 恢复，但不产生 Evidence。Supervisor 在持久化由某条 `StreamRecord` 派生的 Evidence Snapshot 前，必须先完整追加并 `fsync` 覆盖该记录的所有 Raw Stream Chunk；普通无 Evidence Chunk 可以批量刷新，但 Turn 收口前必须 `fsync`。Evidence 提交和 Turn 收口前还要复核路径仍指向所持有的 Raw Stream inode；不一致时不得交付 Outbox。Supervisor 退出后任何恢复路径都不得重放 Raw Stream 生成新 Evidence。

Turn 进入 Quiescent 边界后，Worker 可重放**已保留** Stream 来生成只读审计派生物，
但该重放不得产生或升级上述 Process Evidence、Session、Event 或业务结论。每个
Adapter 的 `normalize_stream_record()` 把记录映射为统一 `trace.jsonl`：
`agent_message`、`tool_call`、`tool_result`、`file_change`、`usage`、
`error`、`reasoning_summary` 以及 Session/Turn/Diagnostic Fallback。无法识别的
结构化记录仍以 `harness_event` 保存，非 JSON 记录以 `diagnostic` 保存，因此
上限内的内容不会因为缺少专用映射而静默消失。每条 Trace Event 含连续
`trace_seq`、Run/Turn/Role/Adapter、`observed_at` 和指向原始
`source + first_seq + last_seq` 的 `raw_ref`。`reasoning_summary` 只表示
Harness 明确导出的摘要，Runtime 不请求、推断或伪造隐藏 Chain of Thought。

同一收口事务随后写入不可变 `trace-manifest.json`。Manifest 包含冻结的
Observability Policy、Capture/截断/脱敏/事件计数、Tool 与 Usage 汇总，以及
`input.md`、LaunchSpec、Capture、保留的 Raw/Stderr、Outbox/Formal Payload、
Harness Final Message 和 `trace.jsonl` 中实际存在项的相对路径、Byte Size 与
SHA-256。`trace.jsonl` 必须被 Manifest 覆盖；Manifest Hash 按 13.3 写入
Runtime。Observation、Transcript 与 Recovery 都重新校验 Runtime Anchor、
Manifest 身份、Policy、Artifact Hash/Size、Trace Sequence、Raw Ref 和 Summary。

Retention 在 Manifest 提交前执行：`redacted` 把 Raw Chunk 重写为带原始 Seq
范围的 Schema 2 Redacted Record，并同步重写 `stderr.log`；`keep` 保留原始
Schema 1 Chunk；`delete` 在 Normalized Trace 成功落盘后删除 Raw/Stderr。标准
Redaction 同时处理常见 Token Pattern 与 JSON Sensitive Key，但它是启发式防线，
不是 Secret Manager。Normalized Trace 不保留私有 `thinking` 或通用 `reasoning`
正文，但 Retained Raw 是另一条隐私边界：`redacted` 仅执行启发式 Secret
替换，Harness 主动输出的 Tool 参数/结果、Prompt、代码乃至 Thinking/Reasoning
仍可能保留；`keep` 则保留原始 Stream。Request、Protocol、Frozen Input、
LaunchSpec、正式 Payload 和 Workspace Artifact 为了权威性保持原始字节，不受
派生 Trace Redaction 改写。首版没有 TTL 或自动 Purge；保留项与 Run Store
同生命周期。

用户可以执行：

```bash
agent-team attach <run-id>
agent-team attach <run-id> --role developer
agent-team diagnose <run-id> --role developer
agent-team transcript <run-id> --role developer --json
agent-team tail <run-id> --role developer --follow --jsonl
```

`attach` 在 Stage 1 始终使用 tmux 只读 Client；纯 Origin Run 返回 `NO_TMUX_RUNTIME`，但仍可使用 `status` / `diagnose` 读取 Run Store。`capture-pane` 和人类日志只提供线索，不能改变结构化诊断结论。直接操作外部 Harness TUI 不属于正常工作流。

`transcript` 输出选中 Turn 的 Policy-filtered Frozen Input、Harness Prompt、统一
Events、Formal Output 与 Turn/Run Usage Summary；`tail` 支持 Role/Turn Filter
和 Live Follow。两者都是只读审计面，不能成为路由或 Completion 的事实来源。

---

## 16. Role Worker 设计

## 16.1 Worker 状态

```text
IDLE
RUNNING
STOPPED
```

Worker 只描述是否正在承载 Turn；启动、退出、收口和恢复细节统一记录在 Turn Runtime 的 `phase` 与 `outcome` 中。Blocked Run 的 Worker 保持 `IDLE`，终态 Journal Tail 使所有 Worker 进入 `STOPPED`。

## 16.2 Worker 事件循环

```python
async def worker_loop():
    change_notice = tmux_change_notice(run_id, role_id)
    journal_tick = periodic_timer(seconds=2)

    while True:
        run_status = derive_run_status()
        if run_status == "CORRUPTED":
            await stop_verified_managed_processes_without_event()
            return
        if run_status in {"COMPLETED", "CANCELLED"}:
            await release_workspace_if_terminal_and_safe()
            return

        turn = claim_event_and_create_turn()
        if turn is None:
            await wait_first(change_notice.next(), journal_tick.next())
            continue

        deadline = run_deadline()
        if deadline.is_expired():
            await commit_limit_block(turn, reason="deadline")
            continue

        try:
            launch = adapter.prepare_launch(turn)
        except LaunchProfileChangedError as error:
            await block_profile_changed_and_finalize(turn, error)
            continue
        except LaunchPreparationError as error:
            await block_start_failure_and_finalize(turn, error)
            continue

        outcome = await start_via_supervisor(turn, launch, deadline)
        if outcome.kind == "start_failed":
            if outcome.error.kind == "profile_changed":
                await block_profile_changed_and_finalize(turn, outcome.error)
            elif outcome.error.kind == "permission_required":
                await block_permission_and_finalize(turn, outcome.error)
            else:
                await block_start_failure_and_finalize(turn, outcome.error)
            continue
        if outcome.kind == "start_unknown":
            await block_uncertain_start(turn, outcome)
            continue

        supervisor = outcome.supervisor
        while not supervisor.result_ready():
            signal = await wait_first(
                supervisor.wait(),
                change_notice.next(),
                journal_tick.next(),
                deadline.wait(),
            )

            terminal = terminal_event_for(turn)
            if terminal is not None:
                await supervisor.request_termination()
            elif derive_run_status() == "CORRUPTED":
                await supervisor.request_termination()
            elif supervisor.adapter_evidence().permission_required:
                await supervisor.request_termination()
            elif signal.is_deadline():
                await commit_limit_block(turn, reason="deadline")
                await supervisor.request_termination()

        result = supervisor.read_result()
        persist_supervisor_result(turn, result)

        if derive_run_status() == "CORRUPTED":
            await finalize_safety_cleanup_without_event(turn, result)
            return
        elif not result.group_quiescent:
            await mark_recovery_required(turn, result)
            if terminal_event_for(turn) is None:
                await commit_recovery_block(turn, result)
        elif terminal_event_for(turn) is not None:
            await finalize_from_terminal_event(turn, result)
        elif result.adapter_evidence.permission_required:
            await finalize_permission_block(turn, result)
        elif adapter.classify_result(
            result, result.adapter_evidence
        ).is_normal_completion:
            await finalize_turn_from_outbox(turn, result)
        else:
            await finalize_known_abnormal_exit(turn, result)
```

`claim_event_and_create_turn()` 在同一个 Run 锁临界区内检查 Journal Tail 并创建 `runtime.json`，避免两个 Worker 先后“检查成功”后重复启动。它校验：

- Journal Tail 是否仍将 Token 指向本角色；
- 该输入 Event 是否已有活跃 Turn；
- `can_create_business_turn` 是否仍为真；
- 活跃 Worker、Turn Supervisor 和 Runner 的 PID / PGID / Start ID 是否与已有快照匹配；
- Workspace Ownership 是否仍记录为本 Run；
- 是否存在未解除的 `recovery_required`；存在时拒绝 Claim，而不是静默重复执行。

`claim_event_and_create_turn()` 在同一临界区为新 Runtime 写入 `business_turn_seq = business_turn_count + 1`，并把已验证的当前 Event Payload 冻结为 `input.md`。当前守卫为真时正常启动；若输入 Event 合法提交后仅因 Wall Time 到期而使守卫变假，则仍创建该序号但不启动 Harness，并按 13.3 直接提交 Limit Block 后返回 `None`。

`start_via_supervisor()` 是 Runtime 的统一进程启动边界，只返回三种结果：

```text
started(supervisor, runner)
start_failed(error)
start_unknown(error, known_identity?)
```

只有 `prepare_launch()` 失败，或 Supervisor 明确报告 Agent 执行尚未开始且 Runner 进程组已经清空时，才能得到 `start_failed`。普通 Start Failure 还必须证明 Session 和其他副作用均未产生；结构化 `permission_required` 可以携带已经观察到的 Session Ref，但必须证明 Agent 执行尚未开始。普通错误产生 Start Failure Block，权限证据产生 Permission Block；Profile Fingerprint 不一致产生不可 Resume 的 Profile Changed Block。它们都不在同一 Turn 自动重试；证据不足一律是 `start_unknown`。前两者在用户处理问题后可显式 Resume，Profile Changed 必须取消旧 Run 并以新 Fingerprint 建立新 Run。

`start_via_supervisor()` 必须在提交 `process/launch-authorized.json` 前持有 Run 锁完成最后一次 Journal、Ownership 与 Deadline 校验；该锁一直持有到 Supervisor / Runner 身份写入 Turn Runtime 和启动许可提交完成。Worker 在这段临界区崩溃时，许可不存在则 Runner 只能超时退出并由恢复流程提交 Start Failure Block；许可存在则已有可恢复的稳定 Runner 身份，且同一 Turn 永远不再启动第二次。

启动许可已经提交但无法判断 `exec` 或模型执行是否发生时返回 `start_unknown`，不得重试；`block_uncertain_start()` 只在 Journal 仍为 Running 时提交 Recovery Block。已知的 Supervisor / Runner 身份必须一并保存，Ownership 保留到能够证明 Supervisor 已结束且 Runner 进程组已清空。

`agent-team cancel` 在 Run 锁内直接提交唯一 Cancel Event，再最佳努力发送 `wait-for -S ...:changed`。Deadline 到期或 Max-Turn Handoff 被拒绝时，同样直接提交带 `limit_reason` 的 Limit Block。Worker 与 Supervisor 都扫描 Journal；看到结束当前 Turn 的 Cancel / Limit Event 后，Worker 只向已验证身份的 Supervisor 发终止提示，Supervisor 从组外先温和终止 Runner 进程组，超过宽限期后强制终止，并在清空后写最终快照。只有 Supervisor 不响应时，恢复路径才在 Runner 身份三元组完整匹配后直接 `killpg`。Start ID 不匹配时不得向复用 PID 发信号；身份无法验证时 Event 仍是权威状态，但 Runtime 进入 `recovery_required`、Ownership 保留且禁止启动新 Run。

完整性检查进入 `CORRUPTED` 时使用同一套身份验证与进程组清理，但绝不提交 Cancel、Limit 或 Recovery Block。Supervisor 只持久化技术退出结果；Worker 不交付 Outbox、不采集可供业务交付的 After Facts，也不从损坏后的配置继续执行。

Worker 崩溃时，已被当前 Turn Runtime 记录的 Supervisor 可以在 Run 锁内为该 Turn 幂等提交 Deadline Limit Block；除此之外它不能生成 Handoff、Complete、Agent Block、Resume 或 Cancel。Worker 与 Supervisor 同时到达 Deadline 时，转换表和单 Turn 单终止 Event 使后提交者只返回已有结果。

取消或 Limit 触发后，Worker 不交付 Harness 已暂存但尚未提交的 Outbox。只有 Supervisor 证明 Runner 进程组清空后，才以 `phase=finalized` 收口；Cancel / Deadline 使用 `outcome=cancelled`，Max-Turn 使用 `outcome=stalled`。Limit Block 不能在同一 Run 内通过修改上限恢复；用户需要取消旧 Run，并用新上限创建新 Run。

第 `max_turns` 个业务 Turn 请求 Handoff 时，CLI 不创建 Outbox，而在同一 Run 锁内直接提交 `block_reason=limit, limit_reason=max_turns`。CLI 返回 `TEAM_BLOCKED`，Supervisor 给 Harness 一个短暂退出宽限期，随后清空 Runner 进程组。若 Deadline 与 Max-Turn 在同一次锁内判定同时成立，只提交 `limit_reason=deadline`；若某个 Limit Block 已经提交，不再改写其原因。用户 Cancel 仍可从 Blocked 状态追加为最终 Cancel Event。

## 16.3 Turn Prompt 组成

默认只注入：

1. 通用 Coordination Skill；
2. 当前 Role ID；
3. `REQUEST.md` 路径；
4. `PROTOCOL.md` 路径；
5. 当前 Input Event 的类型、ID 与不可变 `input.md` 路径；Kickoff、Handoff、Resume 一视同仁；
6. 当前 Before Facts 的路径与 Hash；若 Input 来自上一 Turn，再提供来源 Runtime 和其 Before / After Facts 的独立路径与 Hash；
7. 当前 Turn 目录；
8. 正式 CLI 用法；
9. 角色可用工具和权限；
10. 要求 Turn 结束前调用 `handoff`、`complete` 或 `block`。

Input Event 是本 Turn 的直接输入。若它是 Resume，Prompt 必须明确标注该 Payload 对所引用 Block 的补充指令优先于 `PROTOCOL.md` 和旧 Handoff，但不能覆盖 `REQUEST.md`、仓库强制要求或不可变 Run 配置。

若 `required_payload_sections` 非空，Prompt 在动作说明前列出每个必需 Markdown
Heading。CLI 在复制与 Hash Payload 之前执行同一 Validator：标题按大小写不敏感
精确匹配，标题到下一标题之间必须有非空内容。缺失、空节或非 UTF-8 Payload 返回
`PAYLOAD_CONTRACT_VIOLATION`，不创建 Outbox。Full Audit 固定要求
`## Decision rationale` 和 `## Evidence`，用于保存可审计的显式判断与可复现实证，
而不是要求 Agent 暴露隐藏 Chain of Thought。

不同 External Session 之间不默认注入其他角色的完整会话记录。绑定同一个 Origin Session 的多个逻辑角色天然共享宿主会话上下文，不具备这种隔离，具体边界见 19.3。

## 16.4 Deferred Delivery

外部 Agent 在 Turn 中调用：

```bash
agent-team handoff --to reviewer --file handoff.md
```

该命令先在 Run 锁内检查 Journal Tail、Deadline、`can_create_business_turn`，以及目标 External Role 的 Launch Profile Fingerprint。Deadline 已到时不创建 Outbox，直接提交 `block_reason=limit, limit_reason=deadline`；仅 Max-Turn 守卫失败时直接提交 `block_reason=limit, limit_reason=max_turns`；目标 Profile 已漂移时提交 `block_reason=profile_changed`。三种情况都返回 `TEAM_BLOCKED`。守卫为真时把 `--file` 的当前字节复制为本 Turn 的不可变 `outbox-payload.md`，再写入 `outbox.json`，不立即唤醒下一角色。`outbox.json` 的最小结构为：

```json
{
  "schema_version": 1,
  "turn_id": "turn-0004",
  "action": "handoff",
  "to_role": "reviewer",
  "block_reason": null,
  "payload_path": "turns/turn-0004/outbox-payload.md",
  "payload_sha256": "...",
  "created_at": "2026-07-25T21:44:30-07:00"
}
```

`action` 固定为 `handoff | complete | block`；只有 Handoff 填写 `to_role`，只有 Agent Block 填写 `block_reason=agent`。CLI 在 Run 锁内一次读取源文件，先原子提交 Payload 副本，再原子创建 `outbox.json`；`outbox.json` 的出现是 Outbox Candidate 的提交点。若只留下未被 Outbox 引用的 Payload 副本，恢复时忽略。Outbox 创建后不得重新读取或依赖原始 `--file`，因此 Agent 后续修改原文件不能改变已接受动作。

`complete` 和 `block` 也只写同一个 Outbox Candidate，使用完全相同的延迟提交与异常退出规则。它们不受 `max_turns` 的“下一 Turn”限制，但 Deadline 仍优先；已经到期时直接提交 Deadline Limit Block，不创建或交付普通 Outbox。

只有 Adapter 明确报告正常 Turn 完成、Harness 进程成功退出，且 Supervisor 已证明 Runner 进程组清空后，Worker 才：

1. 确认只有一个终止动作；
2. 原子创建不可变的 `workspace-facts-after.json`，并把其 Hash 写入 Turn Runtime；
3. 只用不可变 Outbox Payload、`workspace-facts-before.json` 和 `workspace-facts-after.json` 生成附带 System Facts 的最终 Payload；
4. 原子提交 Handoff；
5. 若目标是外部角色，最佳努力发送 tmux 变更提示。

最终 Facts 是 Deferred Delivery 事务的一部分。若 After Facts 或其 Runtime Hash 未持久化完整，即使进程已正常退出且 Outbox 有效，也只能提交 Recovery Block；恢复过程不得重新扫描当前 Workspace 来补造 Turn 结束时的事实。

异常退出、Cancel 或 Deadline 即使已经存在 Outbox 也不得走这条交付路径。这样可以减少：

- Agent Handoff 后仍继续修改；
- 下一个角色读取到中间状态；
- Handoff 文本和最终代码版本不一致。

Origin 嵌入角色没有外部 Worker，使用一个单调用的“提交并等待”命令：

```bash
agent-team origin-handoff \
  --run <run-id> \
  --turn <turn-id> \
  --claim <origin-claim-id> \
  --from-role <role-id> \
  --to <role-id> \
  --file <handoff.md> \
  --wait-timeout 90
```

该命令在同一个工具调用内完成：

1. 原子冻结 Origin Turn 的 `workspace-facts-after.json` 并记录 Hash；
2. 提交 Handoff Event，并在目标是外部角色时发送 Best-effort 变更提示；
3. 不返回控制权给 Origin Agent，而是立即进入 `wait-origin`；
4. 仅在 Token 再次回到某个 Origin Role、Run 终止或等待超时时返回。

若 `can_create_business_turn` 为假，`origin-handoff` 不提交 Handoff，而在当前 Origin 业务 Turn 上直接提交带 `limit_reason` 的 Limit Block，并返回 `TEAM_BLOCKED`。Wall Time 已到时，`origin-complete` 和 `origin-block` 同样直接提交 Deadline Limit Block。

若 Origin 写命令在 After Facts 已冻结、Event 尚未提交时中断，匹配 Claim 的重试只能复用该 Facts；当前 Workspace 与冻结 Facts 不同时改为 Recovery Block，不得覆盖 After Facts 或把后续修改归入原动作。

若等待超时，返回 `TIMEOUT_TOKEN_NOT_OWNED`。旧 Claim 已失效，Coordination Skill 此时只能不带 Claim 再次等待或由用户显式中止，不得继续业务操作。这把“提交后再调用 wait”之间的竞态窗口收口到一个 CLI 调用中。

`origin-complete` 与 `origin-block` 不继续等待，因为当前 Agent 还要向用户交付 Completion 或 Block。它们冻结 After Facts、提交 Event、使 Claim 的业务写权限失效，并把 Origin Runtime 留在 `phase=exited` 后返回。返回值是本 Agent Turn 的最后一个工具结果；Coordination Skill 此后只能生成用户回复。下一次用户 Agent Turn 携带原 Claim 调用 `wait-origin`，才把旧 Runtime 收口为 `finalized`。这是一条显式协作边界，不伪装成宿主沙箱或采样中断能力。

## 16.5 无终止动作

Harness 正常退出、Supervisor 已证明 Runner 进程组清空，但没有调用任何正式动作时：

```text
TURN_ENDED_WITHOUT_ACTION
```

系统不得根据最终回复自动猜测目标角色。Worker 提交 `event_type=block, block_reason=no_action`，并以 `phase=finalized, outcome=stalled` 收口该 Turn。

---

## 17. Harness Adapter 设计

## 17.1 统一接口

```python
class HarnessAdapter(Protocol):
    def probe(self) -> CapabilityReport: ...
    def prepare_launch(self, context: TurnLaunchContext) -> LaunchSpec: ...
    def parse_stream_record(self, record: StreamRecord) -> AdapterEvidence | None: ...
    def normalize_stream_record(
        self, record: StreamRecord
    ) -> list[NormalizedTraceEvent]: ...
    def classify_result(
        self,
        result: ProcessResult,
        evidence: AdapterEvidenceSnapshot,
    ) -> ExitInfo: ...
```

`TurnLaunchContext` 只包含当前 Turn、Prompt、External Session Policy、已持久化 Session Ref，以及 Launch Profile 名称与冻结 Hash。`prepare_launch()` 先复核 Hash，再生成可序列化的 argv、环境、stdin、Adapter ID 和输出格式，不创建进程；它根据 Context 确定 Start 或 Resume。Worker 用 `asyncio.create_subprocess_exec` 启动 Turn Supervisor，Supervisor 再创建等待许可的 Runner，Runner 获得许可后原地 `exec` LaunchSpec。

Supervisor 按 `adapter_id` 加载同一 Adapter，先把 stdout / stderr 的来源、原始字节、顺序号和观测时间封装成 `RawStreamChunk`，按 15.5 的可逆外层格式持久化；同一确定性 Framer 再把每个来源的 Chunk 还原为完整、带换行边界的 `StreamRecord`，逐条调用纯函数 `parse_stream_record()`。一次 Pipe Read 可以拆开或合并多条 Harness 记录，不能直接当作 Adapter Record。返回值只能表达固定技术证据，例如 Session Ref、模型执行已开始、Turn 正常完成或权限请求；普通文本和无法解析的记录只写日志，不产生证据。Supervisor 把归一化 Evidence Snapshot 与最终进程结果一起持久化，Worker 再调用纯函数 `classify_result()`。因此 `start_via_supervisor()` 的 `started | start_failed | start_unknown` 与最终 `adapter_completed` 都有同一条可测试的证据路径，不依赖 Worker 持有已经退出的进程对象，也不把 Pane 文本当作协议。

Adapter 不拥有 Worker、PID、重试或 Cancel 生命周期。Session Ref 解析与结果归一化属于上述两个纯解析接口，不再隐藏成无法由 Supervisor 调用的隐式逻辑。

`LaunchSpec`、`RawStreamChunk`、`StreamRecord`、`AdapterEvidence`、`AdapterEvidenceSnapshot`、`ProcessResult` 和 `ExitInfo` 都定义在 `adapters/base.py`，且只含可序列化值；`ProcessResult` 是 Runtime 从 Supervisor 最终快照投影出的适配器输入，不是 Runtime 对象引用。这样依赖方向固定为 `runtime → adapters`，Adapter 实现不反向导入 Worker、Supervisor 或 Run Store。

`CapabilityReport` 必须返回该 External Adapter 支持的 `launch_profile` 闭集、每个 Role 所需路径的 `launch_profile_sha256`，分别说明 Profile 在 Start 与 Resume 路径上的确定映射，并确认 CLI 启动器自身不会立即脱离 Runner 进程组。权限相关键必须由命令行参数或同等高优先级的显式覆盖给出；Probe 集成测试应在隔离配置中改变用户默认值，确认有效权限仍由 Profile 决定。`session_policy=fresh` 只要求已验证的 Start 映射；`session_policy=resume` 才要求 Start 与 Resume 都可用且保持同一技术权限语义。缺少该 Role 实际会使用的路径时在 Kickoff 前拒绝，不要求 Fresh-only 配置证明一条永远不会执行的 Resume 路径。`prepare_launch()` 每次都先复核冻结 Hash，不能在同名 Profile 漂移后生成 LaunchSpec。Probe 和 Adapter 声明都不能为 Agent 以后执行的任意子命令提供完整进程 containment 保证。每个 Adapter 还必须在集成测试中定义“正常完成”的结构化证据和允许的退出码；仅看到进程消失、最终文本或任意退出码不能令 `ExitInfo.is_normal_completion` 为真。

## 17.2 Stage 1 External Adapter 范围

首版实现：

- `claude-code`
- `codex`

扩展接口预留：

- `pi`
- `opencode`
- 任意 CLI Harness

## 17.3 Claude Code Adapter

推荐使用非交互 Print Mode：

- 首次启动时生成确定 UUID 作为 Session ID；
- `--session-id` 指定会话；
- 后续使用 `--resume` 恢复；
- `--output-format stream-json` 获取事件流；
- 不使用 `--no-session-persistence`；
- Start 与 Resume 都使用 `team.json` 中已经过 Probe 校验的同一 `launch_profile` 语义；
- Prompt 以 argv 或 stdin 传入，不通过 Shell 字符串拼接。

概念命令：

```bash
claude -p \
  --session-id <uuid> \
  --output-format stream-json \
  --verbose \
  "<turn-prompt>"
```

后续：

```bash
claude -p \
  --resume <uuid> \
  --output-format stream-json \
  --verbose \
  "<turn-prompt>"
```

适配器必须以运行时能力探测结果为准，分别维护 Start / Resume 的 Profile 参数映射，不能把 CLI 参数顺序暴露为 Agent-Team 的公共协议。

## 17.4 Codex Adapter

推荐使用非交互 `codex exec`：

- `--json` 输出 JSONL；
- 从 `thread.started` 事件获取 Session / Thread ID；
- 后续使用 `codex exec resume <SESSION_ID>`；
- 不使用会禁用 Session 持久化的 `--ephemeral`；
- 不根据 `reviewer`、`developer` 等角色名称自动选择权限；
- `PROTOCOL.md` 中的 `read-only` 表示“不得修改业务文件”的自然语言职责约束，不等同于 Codex `--sandbox read-only`；
- Bootstrap 从 Adapter Probe 返回的闭集里显式选择 `launch_profile`，Adapter 负责分别把该 Profile 确定性映射到 Start 与 Resume；
- Stage 1 已要求 Git Worktree 根目录，因此 Adapter 不传 `--skip-git-repo-check`；
- 不默认使用 danger-full-access；
- 可通过 `-o` 保存最终消息到本 Turn 的 `output.md`，但它只用于诊断，不产生 Handoff、Completion 或正常完成证据。

概念命令：

```bash
codex exec --json --sandbox workspace-write - < turn-prompt.md
```

后续：

```bash
codex exec resume <session-id> --json <resume-profile-args> - < turn-prompt.md
```

当前 Codex CLI 的 Resume 子命令不保证接受 Start 路径的 `--sandbox` 形式；Adapter 必须使用该版本 Probe 证明可用的配置覆盖或等价机制，不能简单复用 Start argv。实际参数顺序留在 Adapter 内部，集成测试验证 Start 与 Resume 的有效技术权限一致。Session 快照同时记录 `effective_launch_profile` 与 Hash，恢复时不得仅凭 Profile 名称假定旧 Session 的实际权限映射。

## 17.5 Origin Executor

Origin Executor 不是 Harness Adapter，不执行 Capability Probe，不创建 Worker Runtime，也没有 Launch Profile。

Stage 1 的 Origin Executor 只有 `embedded_poll`：当前 Agent Turn 通过 CLI 短轮询读取 Event。普通 Session Resume 只保证用户稍后可以继续原会话，不代表系统能够自动唤醒一个已经结束的 Agent Turn。

当 Event 目标 Role 绑定 Origin：

1. `agent-team wait-origin` 在 Run 锁内领取 Kickoff / Handoff / Resume Event，创建 `executor=origin` 的 Turn Runtime 与随机 `origin_claim_id`，冻结 `input.md`，并返回 Event、Turn ID、Input、当前 / 来源 Facts 的路径与 Hash，以及 Claim ID；
2. Bootstrap / Coordination Skill 指导当前会话读取当前 Input；
3. 当前 Agent 执行该角色；
4. 当前 Agent 携带 Claim ID 调用 `origin-handoff`、`origin-complete` 或 `origin-block`；
5. `origin-handoff` 在提交后直接保持等待，不把已失去 Token 的执行窗口返回给 Agent。

## 17.6 Session 恢复降级

以下降级规则只适用于已经通过固定 Schema 校验、但被 Adapter 以结构化结果判定为不可恢复的 Session。Session 快照本身损坏时直接进入 `CORRUPTED`，不进入本节：

若 External Harness Session 无法恢复：

1. Adapter 有结构化证据证明模型执行尚未开始时，把原 Session 标记为 unavailable，以 `phase=finalized, outcome=failed` 提交 Start Failure Block；
2. Supervisor 已证明 Runner 进程组清空、但模型是否执行过不确定时，以 `phase=finalized, outcome=failed` 提交 Recovery Block；只有 Adapter 能证明 Session Ref 仍可恢复时才保留 `available`，否则标记 unavailable；
3. Supervisor 仍存活、Runner 身份未知或无法证明 Runner 进程组清空时才进入 `recovery_required`，先由 `recover` 收口进程，不创建第二个 Harness；
4. 三种情况都不得在原 Turn 内自动启动 Fresh。Block 展示给用户后，只有用户明确授权的 `origin-resume` 才能创建下一业务 Turn；
5. Session 仍 available 时按既定 `resume` 策略继续；unavailable 时创建新一代 Fresh Session，并注入原始请求、协议、该角色历史 Handoff 索引和当前 Input Event Payload；
6. Fresh 降级在日志中标记 `SESSION_RECOVERED_AS_FRESH`，不声称上下文完全等价；新 Session Ref 成功持久化后，后续 Turn 再按原 `session_policy=resume` 使用它。

---

## 18. Origin Event Loop

## 18.1 Stage 1 交付边界

Stage 1 只实现 `embedded_poll`，因此 `team.json` 不保存可配置的交付模式字段，也不探测宿主唤醒能力。

它保证：

- 面向 Origin 的 Event 持久化且不会因当前工具调用超时而丢失；
- 当前 Origin Turn 持续执行 `wait-origin` 时可以自动闭环；
- Origin Turn 结束后，用户回到原 Session 继续即可领取 Event，无需复制上下文。

它不保证自动重新激活已经结束的 Agent Turn。基于 Codex App Server、SDK 或其他宿主 API 的结构化唤醒属于 Stage 2。

## 18.2 `wait-origin`

Origin Agent 在 Bootstrap 后调用：

```bash
agent-team wait-origin --run <run-id> --timeout 90 [--claim <origin-claim-id>]
```

该命令返回以下之一：

- `ORIGIN_KICKOFF`
- `HANDOFF_TO_ORIGIN_ROLE`
- `RESUME_TO_ORIGIN_ROLE`
- `TEAM_COMPLETED`
- `TEAM_BLOCKED`
- `TEAM_CANCELLED`
- `TEAM_CORRUPTED`
- `TIMEOUT`
- `TIMEOUT_TOKEN_NOT_OWNED`
- `ORIGIN_TURN_ALREADY_CLAIMED`

领取 Origin Event、创建 Turn Runtime 和生成 Claim ID 是同一 Run 锁临界区操作：

- 领取业务 Token 前先检查 `can_create_business_turn`；若只有 Wall Time 在 Event 提交后到期，则创建 `origin_claim_id=null`、不启动 Origin 业务执行的 Turn，提交 Limit Block 并返回 `TEAM_BLOCKED`；
- 存在未解除的 `recovery_required` 时不得创建 Origin 业务 Claim，只返回 Recovery Block 对应的管理视图；
- 没有活跃 Origin Turn 时，首次领取发给 Origin Role 的 Kickoff / Handoff / Resume 会返回新的业务 Turn ID、`input.md` 路径和 Claim ID；
- Journal Tail 为 Block 时，首次领取会创建 `executor=origin, role_id=null` 的管理 Turn，并返回管理 Turn ID 和 Claim ID；只有 `block_reason` 不属于 `limit | profile_changed`，安全守卫允许、Recovery Gate 已解除且用户给出新的明确指令时才可 Resume，Gate 未解除时只能观察、Cancel 或提示先运行 `recover`，不可 Resume 的 Block 则说明需要新 Run；管理 Claim 都不能执行普通业务动作；
- 携带匹配 Claim ID 的重复调用返回原 Turn，不创建第二个 Turn；
- 已有活跃 Origin Turn 但未携带匹配 Claim 时返回 `ORIGIN_TURN_ALREADY_CLAIMED`，不得返回 Handoff 内容；
- `origin-handoff`、`origin-complete`、`origin-block` 和 `origin-resume` 都必须在 Run 锁内校验 Claim；
- Turn 终止后旧 Claim 失去业务和管理写权限；`origin-complete`、`origin-block` 或异步 Cancel 留下的 `phase=exited` Origin Runtime，只接受后续用户 Agent Turn 携带该原 Claim 的 `wait-origin` 确认并收口，所有 `origin-*` 写操作都必须拒绝；
- 后续 `wait-origin` 在同一 Run 锁内先收口旧 Origin Runtime，再返回终态，或在 Blocked 状态创建唯一管理 Turn；同一次调用不能同时保留旧业务 Claim 和创建管理 Claim。

Stage 1 不提供 Origin Claim Takeover。若活跃 Claim 丢失或原 Origin Session 无法继续，另一个 Session 可以只读诊断，但不能领取同一个业务或管理 Turn。最小可靠路径是由用户取消旧 Run；若旧 Origin Turn 无法确认已经停止，继续保留 Ownership，直到用户显式确认并执行安全 Unlock。随后以当前 Workspace 和新的请求重新 Bootstrap，不在同一 Run 内制造第二个实际写入者。

仍拥有活跃 Origin Turn 时若返回 `TIMEOUT`，Skill 可携带原 Claim 再次等待。`origin-handoff` / `origin-resume` 提交后旧 Claim 已失效；此时返回的 `TIMEOUT_TOKEN_NOT_OWNED` 表示 Origin 当前没有执行 Token，Skill 只能不带 Claim 继续等待、查询状态或响应用户中止，不允许执行新的业务修改。

短轮询避免单个工具调用无限阻塞，也无需通过 tmux 操纵用户当前 Codex TUI；它本身不构成跨 Agent Turn 的持久唤醒。

## 18.3 Origin 作为团队角色

当收到 `ORIGIN_KICKOFF`、`HANDOFF_TO_ORIGIN_ROLE` 或 `RESUME_TO_ORIGIN_ROLE`：

1. 读取目标 role_id；
2. 读取本次协议、Input Event 类型、`input.md`，以及 CLI 返回的当前 / 来源 Facts 路径与 Hash；
3. 切换到该逻辑角色；
4. 完成本 Turn；
5. 携带 Claim ID 通过 `origin-handoff` Handoff，或调用 `origin-complete` / `origin-block`；
6. 若仍未终止，则继续等待。

`initial_role` 绑定 Origin 时，Bootstrap 提交的 Kickoff Event 会由这里直接领取，不向不存在的 tmux Window 发送变更提示。

## 18.4 Origin 不参与团队

若用户声明当前 Agent 仅负责启动和最终展示，则 Origin 只循环等待 Completion、Block、Cancel 或 Corruption。

## 18.5 Origin Turn 中断与恢复

`embedded_poll` 模式下，用户关闭客户端、主动中断或 Harness 结束当前 Agent Turn，不会丢失 Team Run：

1. 外部 Worker 继续依据 Event Journal 工作；
2. 面向 Origin 的 Event 保留在 Journal；
3. 用户回到原 Session 后，由 Skill 使用 Run ID 和原 Claim ID 再次调用 `wait-origin`；
4. 若无法恢复原 Session，新 Session 可以读取 Run Store 和终态材料，但不能接管尚未结束的 Origin Turn；用户必须取消旧 Run，并在确认旧 Turn 已停止、Ownership 可安全释放后创建新 Run。

因此，“一次输入后自动闭环”只在 Origin Turn 持续存活时成立；它不是所有 Harness 表面的无条件保证。

## 18.6 最终交付

收到 Completion 后，Origin 应读取：

- `REQUEST.md`；
- `PROTOCOL.md`；
- Completion Package；
- Completion Event 提交时捕获的最终工作区事实；
- 必要的测试/审查产物；
- Team Run 摘要。

然后向用户输出最终结果。

Origin 不应只原样转发最后一个 Agent 的一句“完成了”。

Origin Role 自己提交 Completion 时，Workspace Ownership 会保留到后续用户 Agent Turn 确认旧 Origin Runtime 已停止；这不妨碍当前 Turn 读取 Completion 并交付。若 Completion 来自 External Role、Ownership 已安全释放，而当前 Workspace 与 Completion Snapshot 不同，Origin 必须明确提示“工作区在团队完成后又发生变化”，不能把当前状态误报为本次 Team Run 的验证对象。

Coordination Skill 在同一 Origin Session 的下一次用户 Agent Turn 开始时，先用保存的 Run ID 和原 Claim 调用一次 `wait-origin` 完成上述确认，再启动新的 Team Run。该确认只收口旧技术 Runtime，不改变已经终止的业务状态。

---

## 19. Handoff 内容设计

## 19.1 Stage 1 Handoff 模板

```markdown
# Handoff

## From

当前角色。

## To

目标角色。

## My responsibility in this turn

本轮职责。

## Work completed

本轮实际完成的工作。

## Artifacts and workspace state

关键文件、Commit、Diff、文档、测试产物。

## Verified observations

实际观察、运行结果、复现步骤和可核查证据。

## My judgment and claims

发送方的判断，明确标记为判断而非事实。

## Uncertainties and disagreements

未知项、争议、不接受上一角色意见的理由。

## Requested next action

希望下一角色执行的工作。

## Protocol basis

根据 PROTOCOL.md 的哪一条，现在应当交给该角色。

## Decision rationale

为何选择本次动作和目标角色；记录可审计的显式判断，不记录或声称隐藏思维链。

## Evidence

接收方可复现的检查、命令、结果、Hash 与 Artifact 路径。
```

当 Run 配置 `required_payload_sections` 时，对应标题和非空正文是 CLI
接受 Handoff、Completion 与 Agent Block 的前置条件；它们不是由模型自由文本
推断出的 Verdict。

## 19.2 System Facts 附录

Worker 在提交前自动附加：

```markdown
---

## Agent-Team System Facts

- Run ID: ...
- Event ID: ...
- Turn ID: ...
- From role: ...
- To role: ...
- Harness: ...
- Harness session: ...
- Session generation: ...
- Effective launch profile: ...
- Effective launch profile SHA-256: ...
- Turn started at: ...
- Turn ended at: ...
- Process exit code: ...
- Adapter completed: ...
- Termination kind: ...
- Recorded runner PGID quiescent: ...
- Git HEAD before: ...
- Git HEAD after: ...
- Git-visible workspace state SHA-256 before: ...
- Git-visible workspace state SHA-256 after: ...
- Git diff stat: ...
- Uncommitted changes: ...
- Full log: ...
```

这些事实由系统采集，Agent 不能覆盖。

Markdown 附录只为人类阅读。目标 Turn Prompt 还必须把来源 Turn Runtime、Before / After Facts 的路径与 Hash 作为独立系统字段传入；接收方不得通过搜索发送方 Markdown 中的同名标题来确定事实。这样发送方伪造标题或留下未闭合代码块，也不能替换 Runtime 事实来源。

## 19.3 降低 Handoff 偏见

目标角色默认看到：

- 原始用户请求；
- 自然语言协议；
- 最新工作区；
- 当前 Input Event Payload（Kickoff、Handoff 或 Resume）；
- 系统事实；
- 与本角色相关的历史 Finding / Artifact。

默认不看到：

- 发送方完整私有推理；
- 发送方所有聊天历史；
- 与当前职责无关的叙述；
- 被发送方删除或改写的系统事实。

上述上下文隔离只适用于不同 External Session。多个 Role 如果都绑定同一个 Origin Session，只是共享宿主上下文的逻辑角色切换：后一个 Role 可能看到前一个 Origin Role 的完整会话内容，Agent-Team 无法删除或隐藏这些上下文。若用户要求独立 Review、盲评或避免前序角色判断产生锚定，Bootstrap 必须使用独立 External Session，或明确告知该要求在当前 Origin Binding 下不能满足。

### 推荐 Session 策略

- 实现型角色：`resume`；
- 独立验证型角色：可配置 `fresh`；
- 当前入口 Agent：使用 Origin Binding，不配置 External Session Policy。

Stage 1 不自动根据角色名称选择策略；Bootstrap 根据用户描述和合理默认生成并记录。

---

## 20. Handoff 生命周期

```mermaid
sequenceDiagram
    participant A as Role A Harness
    participant CLI as agent-team CLI
    participant SA as Role A Supervisor
    participant WA as Role A Worker
    participant Store as Run Store
    participant Tmux as tmux
    participant WB as Role B Worker
    participant SB as Role B Supervisor
    participant RB as Role B Runner
    participant B as Role B Harness

    A->>CLI: handoff --to B --file handoff.md
    CLI->>Store: 冻结 Payload 并写当前 Turn outbox
    CLI-->>A: accepted, end this turn
    A-->>SA: Harness 退出
    SA-->>WA: Runner 进程组清空 + Adapter 结果
    WA->>Store: 采集最终事实与 Payload
    WA->>Store: 原子提交不可变 Event
    WA-->>Tmux: wait-for -S role-B:changed（Best-effort）
    Tmux-->>WB: 解除等待
    WB->>Store: 读取 Journal Tail 并创建 Turn Runtime
    WB->>Store: 冻结 Input Event Payload 为 input.md
    WB->>SB: 启动组外 Supervisor
    SB->>RB: start_new_session
    RB->>Store: 自持久化 Runner 身份并等待
    WB->>Store: 验证两份身份并提交启动许可
    RB->>B: 原地 exec start/resume Turn
```

### 20.1 最低限度校验

Stage 1 仅校验：

- 当前角色是否拥有执行 Token；
- 目标角色是否存在；
- 目标角色的 Binding 与 External Adapter 配置是否有效；
- 目标为 External 时，当前 Launch Profile Fingerprint 是否仍等于冻结值；
- Handoff 文件是否位于当前 Run 目录；
- 当前 Turn 是否尚未提交终止动作；
- `prev_event_id` 是否仍是当前 Journal Tail；
- Workspace Ownership 是否仍记录为本 Run；
- Deadline 是否已经到期；
- 对 Handoff / Resume，`can_create_business_turn` 是否仍为真。

Handoff 的提交不要求目标 Worker、tmux Window 或 Origin Turn 当前在线。Event 先持久化；目标 External Worker 可由 `recover` 稍后重建，目标 Origin Role 可在原 Session 下一次 `wait-origin` 时领取。

Runtime 可以代表当前 Token Owner 生成 `event_type=block` 且 `block_reason=limit|profile_changed|recovery|start_failure|no_action|permission` 的固定技术事件；其中安全守卫拒绝 Handoff 时必须生成 Limit Block，Profile Fingerprint 漂移必须生成 Profile Changed Block，不能先启动目标 Harness 再处理。Runtime 不能替 Agent 选择普通业务 Handoff 目标。只有显式 `agent-team cancel` 调用可以生成 `cancel` 并终止 Run；Runtime 不从 Agent 输出自行推断取消。能够安全追加 Event 的技术故障进入 Blocked 并通知 Origin；无法安全追加 Event 的完整性故障只推导为 `CORRUPTED`。

`cancel_reason=user` 表示“来自显式管理调用”，不是宿主级用户身份签名。Stage 1 无法区分人在终端调用与拥有同一 Shell 权限的模型调用；Coordination Skill 必须禁止业务 Role 未经用户指令主动执行 `cancel`。这与 21.3 的 Resume 授权边界一样属于可审计的协作约束，不伪装成技术认证。

Stage 1 不校验：

- 自然语言条件是否满足；
- Reviewer 是否真的没有 P3；
- QA 是否真的执行测试；
- 当前角色是否有 Completion Authority；
- Handoff 内容是否完整。

这些正是 MVP 需要观察的核心行为。

---

## 21. Completion 与 Block

## 21.1 Complete

```bash
agent-team complete --file completion.md
```

Completion 文件建议包括：

- 是否满足用户目标；
- 最终产物；
- 验证结果；
- 尚存非阻塞问题；
- 团队循环摘要；
- 建议 Origin 如何向用户展示。

外部 Worker 在 Adapter 明确正常完成、Harness 成功退出且 Supervisor 证明 Runner 进程组清空后提交 Completion Event，并通知 Origin。

Origin 绑定 Role 使用：

```bash
agent-team origin-complete --run <run-id> --turn <turn-id> --claim <origin-claim-id> --from-role <role-id> --file completion.md
```

该命令返回后只能完成本次用户交付，不得再调用工具或修改业务文件；旧 Origin Runtime 在后续用户 Agent Turn 的 `wait-origin` 中才最终收口。

## 21.2 Block

当角色遇到以下情况时调用：

```bash
agent-team block --file blocker.md
```

Agent 主动调用的 Block 固定使用 `block_reason=agent`；其他 `block_reason` 只能由 Runtime 生成。

适用情况：

- 协议冲突；
- 缺少关键输入；
- 多轮争议无法消解；
- Harness 权限不足；
- 发现高风险操作需要用户确认；
- Stage 1 不支持的并行或合并需求。

Block 将业务 Token 置空，并把管理控制返回 Origin。`wait-origin` 为该 Block 创建独占的管理 Turn / Claim。所有 Block 都必须先展示给用户；Origin 可以自动执行只读诊断和确定性 `recover` 收口，但后者可能追加固定技术 Event，不能被描述成严格只读，也不能自行选择 Resume 目标。只有 `block_reason` 不属于 `limit | profile_changed`，安全守卫允许、Recovery Gate 已解除且用户给出新的明确指令时才能 Resume；不可 Resume 的 Block 只能取消旧 Run 并创建新 Run。

Origin 绑定 Role 使用 `agent-team origin-block --run <run-id> --turn <turn-id> --claim <origin-claim-id> --from-role <role-id> --file blocker.md`。

该命令同样是当前 Agent Turn 的最后一个工具调用。它不在返回前创建管理 Claim；后续用户 Agent Turn 调用 `wait-origin` 时，Runtime 先收口旧业务 Turn，再为当前 Block 创建管理 Turn。

`wait-origin` 返回 `TEAM_BLOCKED` 后，Coordination Skill 必须在展示 Block（以及可选诊断结果）后结束当前 Agent Turn；即使 `recover` 已解除技术门禁，也不得在同一 Turn 自动调用 `origin-resume`。管理 Claim 不自动过期，用户下一条明确指令到达后再使用。

## 21.3 Resume

Run 只有在 Journal Tail 为可 Resume Block（`block_reason` 不属于 `limit | profile_changed`）、Origin 已领取对应管理 Turn、没有未解除的 `recovery_required`、安全上限仍允许继续，且用户已对该 Block 给出新的明确指令时才能 Resume：

```bash
agent-team origin-resume \
  --run <run-id> \
  --claim <origin-claim-id> \
  --to <role-id> \
  --file <resume.md> \
  --wait-timeout 90
```

`--file` 只提供用户后续指令，优先保存原文，不能只写“用户同意”。CLI 不解析调用方手写的 Block ID、Role 或 Scope；它在 Run 锁内从当前 Block Tail 与管理 Claim 取得 Block Event ID，用已校验的 `--to` 取得目标 Role，然后生成最终 Resume Payload：

```markdown
# Resume

- Block event: <current-block-event-id>
- Block reason: <validated-block-reason>
- Block payload: <validated-block-payload-path>
- Block payload SHA-256: <validated-block-payload-hash>
- To role: <validated-role-id>
- Scope: continue_same_run

## User instruction

<exact bytes copied from --file>
```

Block 原因、Payload 路径和 Hash 也全部取自已校验的 Journal Tail，使接收方可以复核原始阻塞内容。这样 Block 引用、目标 Role 与 Payload 不会出现两份互相矛盾的输入。CLI 提交带管理 `turn_id`、`to_role` 和该 Payload 的 Resume Event；提交后旧管理 Claim 失效。命令随后像 `origin-handoff` 一样直接进入 `wait-origin`：目标绑定 Origin 时返回新的业务 Turn 与 Claim；目标为外部角色时保持等待，不把已经失去管理控制的执行窗口返回给 Origin。

Resume Event 是下一业务 Turn 的当前 Input Event。领取方必须把它冻结为 `input.md` 并直接注入 Prompt；其中的用户后续指令在解除该 Block 的范围内优先于旧 Handoff 和 `PROTOCOL.md`，不允许仅把 Resume 当作审计附件。

如果用户后续指令改变 `REQUEST.md` 中的目标或约束、`PROTOCOL.md`、角色/Binding、Workspace、Launch Profile 或安全上限，Origin 不得调用 `origin-resume`。最小可靠路径是先 Cancel 旧 Run，再以新输入重新 Bootstrap；Stage 1 不尝试把这类语义变化在线合并到不可变配置。

Stage 1 没有宿主用户消息的签名能力，也不能机器判断一段自然语言是否偷偷改变了原始目标。因此 CLI 只校验 Claim、Block Tail、目标 Role 和输入文件边界，并自行生成 Payload 结构；“必须来自后续用户 Turn”以及“仍属于同一 Run”由 Coordination Skill 和审计记录执行。宿主级强授权属于 Stage 2，文档不把这个自然语言边界伪装成沙箱保证。

若 `can_create_business_turn` 为假，CLI 返回 `LIMIT_REACHED`，不提交 Resume Event、不改写已有 Block，也不废止管理 Claim；该 Claim 此后只可用于观察状态或 Cancel。Stage 1 不增加 `BLOCKED → BLOCKED` 的第二种 Limit 转换。

目标为 External 且 Profile Fingerprint 已变化时，CLI 同样不提交 Resume Event、不改写已有 Block 或废止管理 Claim，返回 `PROFILE_CHANGED_NEW_RUN_REQUIRED`。Blocked 状态不增加第二个 Profile Changed Event；用户只能取消旧 Run，并以新 Fingerprint 创建新 Run。

若存在未解除的 `recovery_required`，CLI 返回 `RECOVERY_REQUIRED` 并保持同一管理 Claim；用户必须先运行 `recover` 或诊断，不能通过选择另一个 Role 绕过旧进程身份不确定性。

`origin-resume` 是 Stage 1 唯一的“可 Resume Blocked → Running”路径，也是唯一可以向恢复 Turn 携带人工说明的路径。它不修改 `REQUEST.md`、`team.json`、`PROTOCOL.md`、Launch Profile Fingerprint 或安全上限；Limit / Profile Changed Block 和任何不可变输入变化都必须通过新 Run 处理。

---

## 22. 共享目录设计

默认放在项目根目录：

```text
.agent-team/
├── root.json
└── runs/
    └── at-20260725-7f3a/
        ├── REQUEST.md
        ├── PROTOCOL.md
        ├── team.json
        ├── journal.lock
        ├── roles/
        │   └── developer.json
        ├── events/
        │   ├── 0001-kickoff-0001.json
        │   ├── 0002-handoff-0002.json
        │   └── 0003-handoff-0003.json
        ├── handoffs/
        │   ├── 0001-origin-to-developer.md
        │   ├── 0002-developer-to-reviewer.md
        │   └── 0003-reviewer-to-developer.md
        ├── resumes/
        │   └── 0004-block-to-developer.md
        ├── turns/
        │   ├── turn-0001/
        │   │   ├── input.md
        │   │   ├── output.md
        │   │   ├── outbox-payload.md
        │   │   ├── outbox.json
        │   │   ├── runtime.json
        │   │   ├── process/
        │   │   │   ├── supervisor.json
        │   │   │   ├── runner.json
        │   │   │   ├── launch-authorized.json
        │   │   │   ├── stream.jsonl
        │   │   │   └── stderr.log
        │   │   ├── workspace-facts-before.json
        │   │   └── workspace-facts-after.json
        │   └── turn-0002/
        ├── sessions/
        │   └── developer.json
        ├── artifacts/
        ├── completion/
        └── logs/
            └── developer.jsonl
```

`.agent-team/` 是 Agent-Team 的保留目录。Stage 1 不允许环境变量或 CLI 参数改变用户状态目录；CLI 根据当前 OS 账号固定推导唯一 `state_dir_realpath`。macOS 使用账号数据库 Home 下的 `Library/Application Support/agent-team`，Linux 使用账号数据库 Home 下的 `.local/state/agent-team`，均不读取 `$HOME`、`XDG_STATE_HOME` 或项目配置。需要可迁移、可配置的状态后端时进入 Stage 2，而不是在本地文件协议里开放第二个 Owner Namespace。

`root.json` 首次创建后不可变，只包含固定 `schema_version`、规范化 `workspace_realpath` 及其 SHA-256、固定 `state_dir_realpath` 及其 SHA-256。该文件用于校验 Workspace / Run Store 绑定，不是状态目录选择器；记录路径与当前账号的固定推导不一致时直接失败。

保留目录本身必须是非符号链接目录。`init` 先对 Workspace 与固定状态目录完成文件系统能力探测，再取得固定状态目录中的 per-workspace 操作锁；持锁时只接受 `.agent-team/` 原本不存在、为空、只包含本工具未提交的 Root 临时文件，或已经具有与当前 Workspace 完全匹配的有效 `root.json`。Marker 不存在时若固定 Owner 已存在，说明可能有 Run Store 被删除但执行仍未收口，`init` 必须拒绝并要求诊断 / Unlock，不能重建 Marker 或创建新 Run；Owner 不存在时才可用同目录原子写建立 Marker。其他非空目录、损坏 Marker、Git 索引中存在 `.agent-team/` 路径或冲突 Run ID 都明确拒绝。

`roles/` 与 `sessions/` 只为 `binding=external` 的 Role 创建文件；Origin Binding 只使用对应的 Turn Runtime 和 Claim。

Workspace Ownership 位于 Run Store 之外的用户级持久化状态目录：

```text
<fixed-user-state-dir>/
├── workspace-locks/
│   └── <sha256-workspace-realpath>.lock
└── workspaces/
    └── <sha256-workspace-realpath>.json
```

`doctor` 必须显示固定状态目录、State Root 记录值、两者是否一致，以及当前 Workspace Owner。`workspace-locks/` 中的文件是稳定的短期操作锁，不能与会被 Unlock 删除的 Owner 文件复用。

Stage 1 要求 Workspace Run Store 和用户状态目录位于本机可提供进程间 `flock`、同目录原子 `rename` 与 `fsync` 的文件系统。`doctor` / `init` 在两个位置分别执行无业务数据的能力探测；已知不满足这些语义的网络或虚拟文件系统直接拒绝。文档不把本地文件协议宣传为跨机器一致性协议。

### 22.1 Git 处理

Stage 1 的 Workspace 必须是规范化后的 Git Worktree 根目录。`init` 用 `git rev-parse --show-toplevel` 校验；非 Git 目录、Worktree 子目录、Sparse Checkout、包含 Gitlink 的索引，以及 Git 已跟踪 `.agent-team/` 的仓库直接拒绝。每次 Turn 边界按 13.4 重新校验会影响 Fingerprint 语义的这些不变量，不能假定 `init` 的结果永久成立。

默认不修改仓库 `.gitignore`、`.git/info/exclude` 或其他 Git 元数据。Agent-Team 自己的 Snapshot、Diff Stat 和状态命令显式排除 `.agent-team/`；用户可以在自己的 Git 配置中忽略这个保留目录，但不得把它加入索引。需要版本化的协议、Handoff 或报告应显式复制到保留目录之外。

保留目录和用户状态目录默认以 `0700`、其中普通文件以 `0600` 创建。Harness 原始输出仍可能包含敏感业务内容，因此 `doctor` 必须在 `.agent-team/` 未被用户 Git ignore 时给出明确警告，Coordination Skill 禁止执行会把该目录加入索引的 `git add`。Stage 1 不尝试用不完整的关键字规则“自动脱敏”，也不把本地 Run Store 当作秘密管理系统。

Turn 边界的“Git 可见业务 Snapshot”按 13.4 的唯一算法采集，并固定排除 ignored 路径、`.agent-team/` 和 Git 内部元数据；Agent-Team 自身日志、Runtime 和锁变化不能被计为业务修改。`REQUEST.md`、`PROTOCOL.md` 和 `team.json` 的完整性由 Kickoff Hash 单独校验。

### 22.2 Event Journal 原子提交

Stage 1 使用：

- `fcntl.flock`；
- 临时文件；
- `fsync`；
- 原子 `rename`；
- 不可变 Event 文件；
- Journal Tail 链接校验。

`events/<seq>-<event-id>.json` 的出现是唯一提交点：

- 未被 Event 引用的临时 Payload、Handoff 或 Completion 文件属于孤立文件，不影响 Token；
- 启动、`status`、`claim` 和 `recover` 都直接校验 Event 序号、`prev_event_id` 与 Payload Hash；
- Stage 1 面向短 Run，并受 Turn / Wall Time 上限约束，因此不维护额外 Tail 指针、Run 状态文件或流式索引；
- 若后续扫描成本成为真实问题，Stage 2 直接迁移到 SQLite，而不是先增加文件缓存协议。

### 22.3 技术快照

Event 是唯一语义提交点，但恢复判断还依赖完整的技术快照。以下文件都必须在对应锁内以“同目录临时文件 → `fsync` 文件 → 原子 `rename` → `fsync` 父目录”的方式整文件替换，禁止原地截断改写：

- `turns/<turn-id>/runtime.json`；
- `turns/<turn-id>/process/supervisor.json`；
- 首次创建后不可变的 `turns/<turn-id>/process/runner.json`；
- 首次创建后不可变的 `turns/<turn-id>/process/launch-authorized.json`；
- 首次创建后不可变的 `turns/<turn-id>/input.md`；
- 首次创建后不可变的 `turns/<turn-id>/workspace-facts-before.json`；
- 首次创建后不可变的 `turns/<turn-id>/workspace-facts-after.json`；
- 首次创建后不可变的 `turns/<turn-id>/outbox-payload.md`；
- 首次创建后不可变的 `turns/<turn-id>/outbox.json`；
- 外部 Worker 的 `roles/<role-id>.json`；
- `sessions/*.json`；
- 首次创建后不可变的 `.agent-team/root.json`；
- Workspace 操作锁保护下的 `workspaces/<workspace-hash>.json`。

`journal.lock` 是整个 Run 的同一把锁，不只保护 Event 文件：除 State Root / Owner 外，上述 Run 内技术快照的首次创建和每次替换都必须持有它的排他锁；Observation 持共享锁读取。State Root 只由 Workspace 操作锁保护，Owner 的提交与删除同时遵守先 Workspace、后 Run 的固定顺序。`process/stream.jsonl`、Worker 日志和 `output.md` 是唯一不逐次取得 Run 锁的追加 / 诊断文件，因此它们不能参与 Status、Health、Recommended Action 或恢复状态推导。

`init` 先完成配置、路径和所需 Adapter Capability 预检，再在 `.agent-team/runs/` 下的临时目录完整写入并校验 `REQUEST.md`、`PROTOCOL.md`、`team.json` 和空的普通文件 `journal.lock`。这些文件及临时目录都 `fsync` 后，CLI 重新取得固定的 Workspace 操作锁，验证 State Root、Owner 仍不存在且目标 Run ID 仍未占用，再把整个临时目录原子 `rename` 为最终 Run 目录并 `fsync runs/`；最终目录的出现是 `init` 的提交点。Probe 失败或提交前崩溃只留下可清理的临时目录，不能留下半个可启动 Run。最终 Run 的后续命令只能 no-create 打开 `journal.lock`；缺失或类型错误一律拒绝，不猜测修复。

所有带 `schema_version` 的 JSON 都按各自固定解析器校验必填字段、类型和未知字段；不支持的版本明确失败，不做猜测性兼容或字段回填。

所有协议、Payload 和快照文件都必须是 Run Store 内的普通文件；读取时使用不跟随符号链接的打开方式并在文件描述符上复核类型。符号链接、设备或其他文件类型按对应文件的损坏规则处理，不能通过路径检查后再跟随到 Run Store 外部。

快照损坏时不得从 Pane 文本、最终回复或当前 Workspace 猜测历史内容。恢复规则按文件类型固定：

- `launch-authorized.json` 不存在，且 Runtime / Supervisor / Runner 的所有有效快照都证明许可从未提交时，Runner 不得启动 Harness；恢复过程等待许可超时或终止已验证的 Supervisor / Runner，证明 Runner 进程组清空后，在尚无终止 Event 时提交 Start Failure Block，不重试该 Turn；
- Supervisor 已进入 `running`，任一 Adapter Evidence 已离开初始值，或 close-on-exec 结果已证明许可被消费时，`launch-authorized.json` 缺失直接推导为 `CORRUPTED`；`runner.json` 被有效 Supervisor / Runtime 身份字段引用后缺失同理；
- `runner.json` 或 `launch-authorized.json` 已存在但不能通过完整固定校验时直接推导为 `CORRUPTED`；不得把损坏许可当成“未授权”，也不得创建新身份或第二份许可；
- 启动许可存在时，不论 Supervisor 是否存活，都不得再次授权、启动 Runner 或启动 Harness；身份匹配的 Supervisor 继续观察或按 Journal 终止原 Runner 进程组；
- 启动许可存在且 `supervisor.json` 已明确记录 `group_quiescent=true` 时，按该最终快照分类并收口；
- 启动许可存在但 Supervisor / Runner 身份、最终结果或 Runner 进程组清空状态无法确认时，写入最小 `recovery_required` Runtime 并禁止新 Harness；
- Turn Runtime、Outbox 或 Workspace Facts 损坏：只有在 Journal、唯一 Turn 目录和未损坏字段能唯一确定 `turn_id`、`role_id` 与输入 Event 时才允许提交 Recovery Block；进程组已证明清空则直接 Finalize，仍不确定才写入最小 `recovery_required` Runtime，否则推导为 `CORRUPTED`；
- `input.md` 缺失时不得启动 Harness；若尚未启动，可从仍通过 Hash 校验的 Event Payload 完成首次创建，若已经启动则按 Recovery Block 处理；已存在但 Hash 不一致表示不可变快照被改写，直接推导为 `CORRUPTED`；
- Outbox Payload、Before Facts 或 After Facts 缺失、Hash 不匹配时不得交付 Outbox，也不能从原始 `--file`、当前 Workspace、Pane 文本或 Harness 最终回复重新生成；
- Worker Runtime 损坏不改变 Journal 状态，但在无法证明旧 Worker 已退出时禁止重建 Worker、发送信号或释放 Ownership；
- Session 文件不存在只在“首次 Session Ref 尚未产生”的既定状态下合法；已存在的 Session 文件若无法通过固定 Schema 校验，直接推导为 `CORRUPTED`，不覆盖原文件、不猜测 Generation，也不降级为 Fresh。只有 Adapter 对一份有效 Session 快照给出结构化 unavailable 结论时，才按 17.6 等待用户明确 Resume 后降级。

`process/stream.jsonl` 和日志允许追加写，`output.md` 允许由 Adapter 作为诊断产物写入；Turn 收口前都必须刷新。它们不参与 Token 推导，也不能替代 Outbox 或结构化 Adapter Evidence。

### 22.4 Workspace Ownership

Stage 1 对同一规范化 Workspace 隐式采用排他 Ownership，不在 `team.json` 中提供第二种模式。

所有外部 Role 都以该 Workspace 为工作目录。若任务需要修改两个互不包含的根目录，Bootstrap 必须明确说明 Stage 1 不支持并停止启动。

`workspaces/<workspace-hash>.json` 是唯一 Ownership Marker，完整结构为：

```json
{
  "schema_version": 1,
  "run_id": "at-20260725-7f3a",
  "workspace_realpath": "/repo/project",
  "workspace_sha256": "...",
  "acquired_at": "2026-07-25T21:43:00-07:00"
}
```

文件名、`workspace_sha256` 与 `workspace_realpath` 的 SHA-256 必须一致；未知或缺失字段按 Ownership 损坏处理。

获取规则：

1. 计算 Workspace `realpath` 的 SHA-256；
2. 打开固定状态目录中的 `workspace-locks/<hash>.lock` 并获取排他 `flock`；只有首次 `init` 在 State Root 尚不存在时可以原子创建这把稳定锁，State Root 一旦提交，所有命令都只能 no-create 打开，避免删除旧 inode 后形成两把同名锁；
3. 在持锁期间先检查最终 Owner 文件；已存在时绝不覆盖或替换；
4. 最终文件不存在时，完整生成 Owner JSON，写入 `workspaces/` 下的同目录临时文件并 `fsync`；
5. 以原子 `rename` 把临时文件提交为 `workspaces/<hash>.json`，再 `fsync workspaces/`；最终 Owner 文件的出现是唯一 Ownership 提交点；
6. 最终文件属于同一 Run、Journal 尚无 Kickoff 时，只有 `start` 可以继续提交唯一 Kickoff；`recover` 明确拒绝并提示使用 `start`；
7. 最终文件属于同一 Run、Journal 已有 Kickoff 时，`start` / `recover` 都按 Journal 状态执行相同收口；只有 Running / Blocked Run 会核对并补建缺失的外部 Worker，终态不创建 Worker；
8. 最终文件属于其他 Run，或文件存在但元数据损坏时，明确失败，不启动 Worker；
9. Kickoff 前确定失败时删除精确匹配本 Run 的 Owner 文件并 `fsync` 父目录；Kickoff 后 Worker 创建失败时保留 Ownership 和 Running Journal，返回可由 `start` / `recover` 重试的明确错误；
10. 未完成 `rename` 的临时文件不代表 Ownership，可在持锁时清理；Worker 已启动或操作确定失败后释放操作锁。操作锁不在 Run 存续期间长期持有。

`init`、`start`、`recover`、终态释放和 `unlock` 必须以排他模式使用同一把 per-workspace 操作锁；`status`、`diagnose` 和每次 Watch Snapshot 以共享模式使用它。观察命令只能以 no-create 方式打开已经存在的 Workspace 与 Run 锁文件，不能为了查询而补建持久状态。终态释放和 `unlock` 还必须在持锁期间完成存活性检查、精确 Owner 文件删除和父目录 `fsync`。固定锁顺序为先 Workspace 操作锁、再 Run 锁，禁止反向获取；观察命令不得跨轮询持锁。

Kickoff 提交后、终态安全释放条件满足前，Run 必须始终存在一份完整且精确属于本 Run 的 Owner 及其既有 Workspace 操作锁。普通进程或 tmux 崩溃不会删除这些原子持久状态，因此缺失、损坏或归属改变不是可自动修复的崩溃状态；它直接使 Run 进入 `CORRUPTED`。`start` / `recover` 不得为已有 Kickoff 的 Run 重新获取 Owner、补建锁、重建 Worker 或追加 Block，只能安全清理身份已验证的受管进程并返回诊断。

Ownership 在进程、Pane 或 tmux Server 崩溃后仍保留，避免自动释放造成两个 Run 重叠。Run 进入 Completed / Cancelled，且所有 Supervisor 都已结束、所有已记录 Runner PGID 都已证明清空、没有尚未确认结束的 Origin Turn 后，Runtime 才在操作锁内删除与自身 `run_id` 精确匹配的 Owner 文件并 `fsync` 父目录；仍存活的 Worker 已由终态 Journal 限制为只退出，且所有写入前仍会校验 Ownership。Origin Role 自己调用 `origin-complete` / `origin-block` 时，其 Runtime 先停在 `exited`，所以不会在 CLI 返回、宿主仍可继续采样时释放 Owner。这里的自动释放只证明 Agent-Team 受管进程组已经结束，并以角色遵守“不主动逃逸进程组”的 Coordination 合同为前提，不声称发现所有系统进程。Cancel 发生在 Origin Turn 运行期间时同样先使 Claim 和后续 Origin 动作失效并保留 Ownership；后续用户 Agent Turn 通过 `wait-origin` 确认后再释放，若原 Session 永久丢失则只能走显式 Unlock。

过期 Ownership 不按时间自动抢占。用户只能显式执行：

```bash
agent-team unlock \
  --workspace <path> \
  --expect-run <run-id> \
  [--confirm-origin-stopped]
```

`unlock` 始终从当前 OS 账号的固定状态目录定位操作锁和 Owner，不依赖 `.agent-team/root.json` 可读，因此 State Root 损坏时也不会猜测第二个路径。它要求固定目录中恰好存在 Workspace Hash 与 `--expect-run` 都匹配的完整 Owner，否则拒绝；该命令只删除已验证的既有 Owner，不能重建 State Root 或获取新 Ownership。

CLI 必须持有 Workspace 操作锁，并用 PID、PGID 和 Start ID 验证对应 tmux Session、Worker、Supervisor 与已记录 Runner 进程组均不存活或已明确清空。保存的 Runner PGID 已无成员时可直接判定该受管组清空；仍有成员时必须验证 Supervisor 身份，Supervisor 已退出则只能用已记录且仍存活的 Runner PID / PGID / Start ID 三元组做后备验证，二者都无法验证便拒绝 Unlock。PID 已复用时不得向新进程发送信号；若操作系统无法查询 Start ID，则 Unlock 必须拒绝并要求人工诊断。该检查无法发现未留下可观测证据的逃逸 daemon，CLI 必须在确认提示中明确这一边界。Stage 1 也无法从操作系统确认宿主 Origin Turn 是否仍在采样，因此存在未收口的 Origin Runtime 时还必须由用户提供 `--confirm-origin-stopped`。由于 `start` / `recover` 也必须先获取同一把锁，它们不能在检查与删除之间启动新 Worker。Worker 在领取 Turn 和提交 Event 前校验 Ownership 仍属于本 Run，但不维护额外 Guard 进程或 Heartbeat。

若整个 Run Store 已丢失，CLI 没有可信的 Supervisor / Runner 身份可供上述验证，`unlock` 必须安全失败；Stage 1 不提供猜测性的 Force Unlock。用户只能先在 Agent-Team 之外完成人工进程审计与清理，再显式处理精确 Owner 文件。固定状态目录本身属于运行时持久状态，活跃 Run 期间不得由外部工具删除。

进程在临时文件写入后、最终 `rename` 前崩溃时没有获得 Ownership；临时文件可以在下次持锁操作中清理。最终 Owner 文件要么完整存在，要么不存在，不再定义“目录已提交但元数据缺失”的半状态。

Ownership 只阻止另一个 Agent-Team Run，不声称能阻止用户、IDE 或其他进程修改文件。Stage 1 将“运行期间不并发编辑同一 Workspace”列为明确前提，并在 Turn 边界记录 Git HEAD、工作区指纹和已知 Turn 事实。系统只能检测部分 Snapshot 偏差，不能可靠归因当前 Turn 内混入的外部编辑；一旦发现可疑变化就 Block，而不是继续产生可能过期的审查或测试结论。

---

## 23. CLI 设计

## 23.1 安装与诊断

```bash
agent-team install
agent-team doctor
agent-team doctor --json
```

`doctor` 检查：

- Python 版本；
- tmux；
- Codex CLI；
- Claude Code CLI；
- 当前认证状态的可用性；
- Session Resume 能力；
- Adapter 对各 Role 实际 Session Policy 所需的 Launch Profile 路径，以及 CLI 主进程能否留在受管进程组；该检查不声称证明 Agent 后续启动的任意进程；
- 操作系统进程 Start ID 查询能力；
- Git，以及规范化 Workspace 是否恰好为一个有效 Worktree 根目录；
- `.agent-team/` 是否被跟踪、是否被用户 ignore，以及目录 / 文件权限；
- Skill / Plugin 安装位置；
- 当前目录权限；
- 当前 OS 账号的固定状态目录、State Root 记录路径及二者是否一致；
- Workspace Run Store 与固定状态目录的 `flock`、同目录原子 `rename` 和 `fsync` 能力；
- 规范化 Workspace 是否已被其他 Run 持有。

## 23.2 Run 管理

```bash
agent-team init [options]
agent-team start <run-id>
agent-team status [<run-id>] [--json]
agent-team watch [<run-id>] [--jsonl]
agent-team attach <run-id> [--role <role-id>]
agent-team diagnose [<run-id>] [--role <role-id>] [--json]
agent-team cancel <run-id>
agent-team recover <run-id>
agent-team unlock --workspace <path> --expect-run <run-id> [--confirm-origin-stopped]
```

`status`、`watch` 和 `diagnose` 省略 Run ID 时，只从当前 Workspace 的固定 Owner 解析活跃 Run；Owner 不存在就返回 `RUN_NOT_FOUND` / Exit `3` 并要求显式 Run ID，Owner 损坏或无法在既有 Workspace 锁下安全读取则返回 `OBSERVATION_IO_ERROR` / Exit `4`，都不猜测“最近一个”审计目录。显式 Run ID 没有最终 Run Directory 且未被当前有效 Owner 引用时同样是 `RUN_NOT_FOUND`；有效 Owner 指向丢失的 Run Directory 则是可确定的完整性故障，返回该 Run 的最小 Corrupted 报告。文本输出与结构化输出必须由 27.1 的同一派生对象渲染。

`init` 只校验并原子提交尚未运行的 Run Store，包括在固定 Workspace 操作锁下建立或验证 `.agent-team/root.json`、拒绝任何可配置状态目录、Git 跟踪该保留目录、非 Git Worktree 根目录、Sparse Checkout、Gitlink、per-role CWD，并按各 External Role 的 Session Policy 验证实际需要的 Launch Profile 路径。固定 Owner 已存在时不允许新建 State Root 或 Run；State Root 可以在其他预检失败后保留，但最终 Run 目录只能由 22.3 的整目录提交产生。`start` 先完成无需 Ownership 的预检，再从固定状态目录获取 Workspace Ownership；随后完成 Snapshot 可行性检查，成功才把 `REQUEST.md` / `PROTOCOL.md` / `team.json` Hash 写入唯一 Kickoff Event 并创建外部 Role Worker。重复 `start` 不得创建第二个 Kickoff：UNSTARTED 的同 Run 继续启动事务；已有 Kickoff 时执行与 `recover` 相同的状态收口，只有 Owner 仍完整属于本 Run 的 Running / Blocked Run 才补建缺失 Worker；Owner 丢失直接进入 `CORRUPTED`，Completed / Cancelled 只做身份验证、确定性技术收口和满足 22.4 条件后的安全 Owner 释放。

`recover` 拒绝 UNSTARTED Run；对已有 Kickoff 的 Running / Blocked Run 只重建 tmux / Worker 并收口可确定恢复的 Turn Runtime，绝不提交 Resume Event，也不能让 Blocked Run 回到 Running。Completed / Cancelled 不创建 Worker 或 Event，只清理身份已验证的受管执行、补完确定性 Runtime 收口，并在安全条件满足时释放仍属于本 Run 的 Owner。可 Resume Blocked → Running 只能使用 `origin-resume`；Limit / Profile Changed Block 不允许恢复。

Stage 1 不提供删除 Run Store 的 `clean` 命令。UNSTARTED、Completed、Cancelled 和 Corrupted Run 的审计目录默认保留；若用户需要归档或删除，必须先确认没有 Workspace Ownership 和存活进程，再在 Agent-Team Runtime 之外自行处理。

## 23.3 Agent 内部命令

```bash
agent-team context
agent-team handoff --to <role-id> --file <path>
agent-team complete --file <path>
agent-team block --file <path>
```

Agent 不需要传 Run ID 和 Role ID；Worker 注入：

```text
AGENT_TEAM_RUN_ID
AGENT_TEAM_ROLE_ID
AGENT_TEAM_TURN_ID
AGENT_TEAM_RUN_DIR
```

## 23.4 Origin 命令

```bash
agent-team wait-origin --run <run-id> --timeout 90 [--claim <origin-claim-id>]
agent-team origin-context --run <run-id> --event <event-id> [--claim <origin-claim-id>]
agent-team origin-handoff \
  --run <run-id> \
  --turn <turn-id> \
  --claim <origin-claim-id> \
  --from-role <role-id> \
  --to <role-id> \
  --file <path> \
  --wait-timeout 90
agent-team origin-complete --run <run-id> --turn <turn-id> --claim <origin-claim-id> --from-role <role-id> --file <path>
agent-team origin-block --run <run-id> --turn <turn-id> --claim <origin-claim-id> --from-role <role-id> --file <path>
agent-team origin-resume \
  --run <run-id> \
  --claim <origin-claim-id> \
  --to <role-id> \
  --file <path> \
  --wait-timeout 90
```

Origin 写命令显式携带 Run、Claim，以及业务动作所需的 Turn 和动态 Role，因为 Origin 不是 Worker 子进程，不依赖 Worker 注入的环境变量。CLI 必须验证 Claim 对应当前 Journal Tail 的活跃业务或管理 Turn。读取活跃 Origin Turn 的 `origin-context` 同样要求匹配 Claim；读取 Completed / Cancelled / Corrupted 的终态材料不要求 Claim。Stage 1 不提供 Claim Takeover 命令；Claim 丢失时只能只读诊断、取消旧 Run 并在安全释放 Ownership 后新建 Run。`origin-resume` 只能在用户已对当前 Block 给出新指令后调用，其 Payload 必须记录 Block Event、用户指令摘要和目标角色。`origin-handoff` 与 `origin-resume` 成功提交后必须在同一进程内进入等待；它们不能先返回“提交成功”再要求 Agent 发起第二个命令。Bootstrap Skill 可以封装这些命令，使用户无需直接使用。

---

## 24. Skill 与插件打包

建议仓库：

```text
agent-team/
├── pyproject.toml
├── src/agent_team/
├── skills/
│   ├── codex/
│   │   └── agent-team/
│   │       ├── SKILL.md
│   │       ├── references/
│   │       └── scripts/
│   └── shared/
│       └── COORDINATION.md
├── plugins/
│   └── claude-code/
│       └── agent-team/
├── templates/
├── tests/
└── docs/
```

### 24.1 Codex

Codex 侧 Skill 包含：

- 触发描述；
- Bootstrap 指令；
- Origin Claim、Claim 丢失后的取消/新 Run 规则，以及 `origin-handoff` / `origin-resume` 提交并等待语义；
- 下一用户 Agent Turn 先确认并收口旧 `phase=exited` Origin Runtime 的规则；
- `status --json` / `diagnose --json` 的固定信封和 Recommended Action 处理；建议码不授予 Resume、Unlock 或 Claim 权限；
- 协议模板；
- CLI 脚本引用。

### 24.2 Claude Code

外部 Claude Code Session 通过 `--plugin-dir` 加载 Agent-Team Coordination Skill，或由 Turn Prompt 显式要求读取共享 Coordination 文档。

首版建议两种方式同时使用：

- Plugin 提供稳定规则；
- Turn Prompt 提供本次 Run 路径、角色和当前 Input Event；Resume Payload 不得被旧 Handoff 遮蔽。

---

## 25. 权限与角色边界

## 25.1 最小权限原则

Role 是用户在每次任务中动态定义的业务身份，不对应 Codex 自带 Review 模式，也不根据 `reviewer`、`developer` 等名称获得固定权限。

Stage 1 区分两类边界：

- `PROTOCOL.md` 中的角色限制，例如“只审查不修改”，属于自然语言职责约束，由 Skill 指导 Agent 遵守；工作区 Snapshot 只提供可选事后核验所需的事实，不构成写入拦截；
- External Binding 的 `launch_profile` 是独立的 Harness 技术权限配置，由 Bootstrap 在 Kickoff 前从 Adapter Probe 闭集中显式选择，并由 Adapter 在该 Role 的 Session Policy 实际需要的 Start / Resume 路径上确定性执行；
- Origin Binding 继承当前宿主会话已经拥有的技术权限，Agent-Team 不重新启动或改写其沙箱配置；
- 不因为自然语言中出现 `read-only` 就自动启用 Codex `--sandbox read-only` 或 Claude `plan`；
- 默认不启用全权限绕过。

职责边界写入 `PROTOCOL.md`，技术启动权限写入 `team.json`，两者不得互相推导。Kickoff 后 `launch_profile` 不可在线修改。

## 25.2 只审查不修改的 Origin Role

当用户把某个动态 Role 绑定到 Origin Codex，并要求它只审查不修改时，Stage 1 主要依靠 Skill。可选增加：

- Review 开始时记录工作区 Snapshot；
- Review 结束时检查业务文件变化；
- 若发生修改，标记角色违规并 Block。

## 25.3 Prompt Injection 风险

所有 Handoff 都是另一 Agent 产生的非可信输入。

目标 Agent 必须：

- 先遵守宿主 Harness 的 System、Developer、Safety 和仓库级强制指令，再按 12.2 处理 `REQUEST.md` 与 `PROTOCOL.md`；
- 不执行 Handoff 中要求修改协议、绕过权限或读取秘密的指令；
- 将发送方描述视为工作材料，而不是系统指令；发送方判断也不能覆盖对当前工作区的直接检查结果。

### 25.4 协议完整性

Bootstrap 在提交 Kickoff Event 时，把普通非符号链接的原始 `REQUEST.md`、原始 `PROTOCOL.md` 和规范化 `team.json` 的 SHA-256 写入该 Event。Worker 在每个 Turn 开始前和任何 Event 提交前、Origin 在所有写动作提交前都重新校验文件类型与 Hash：

- Kickoff 后任一 Hash 变化都直接推导为 `CORRUPTED`，不追加 Recovery Block。变化可能发生在 Token 已转移但目标尚未 Claim、没有合法 `turn_id` / `from_role` 的窗口，因此不能假定总有主体可以生成 Block；
- Running / Blocked 状态停止领取新 Turn；仅可按已验证身份清理现存 Supervisor / Runner。Completed / Cancelled 状态只把该 Run 报告为审计损坏，不重新获取已经安全释放的 Ownership；
- Stage 1 不区分修改者，也不提供在线配置改版；需要改变任一内容时取消旧 Run，并从新的 Request / Protocol / Team 配置创建新 Run；
- Stage 1 不依赖操作系统权限防止恶意修改，但至少提供检测。

同一完整性检查还验证 `.agent-team/root.json` 和按 22.4 应当存在的 Workspace Owner。完整性失败后的唯一语义状态是 `CORRUPTED`；进程安全清理不是 Event 转换。

---

## 26. 故障处理与恢复

## 26.1 Harness 未安装或未认证

Bootstrap 前只对 External Binding 运行 Adapter Probe。失败时最多保留有效 State Root 和未提交临时目录，不出现最终 Run 目录，直接返回 Origin；纯 Origin 团队不执行伪造的 Harness Probe。

## 26.2 Harness Turn 启动失败

- Stage 1 每个业务 Turn 只有一次启动机会，不配置也不实现自动重试；
- Profile Fingerprint 不一致不按普通 Start Failure 处理，而按 13.1 提交不可 Resume 的 Profile Changed Block；
- 结构化权限请求按 26.9 提交 Permission Block；
- `prepare_launch()` 失败、Runner 未获许可即超时，或 `exec` 以结构化错误失败且 Runner 进程组已清空时，提交唯一 `event_type=block, block_reason=start_failure`，并以 `phase=finalized, outcome=failed` 收口；
- 已有 Cancel / Limit Event 时只按该终止 Event 收口，不再追加 Start Failure Block；
- Start Failure 不转移 Token。用户修正认证、权限或环境后，可明确选择目标 Role 执行 `origin-resume`；这会创建并计数一个新的业务 Turn；
- 许可已提交但无法判断 `exec`、模型执行或 Session 副作用是否发生时是 `start_unknown`，不得重试；它先服从已提交的 Cancel / Limit Event，否则提交 Recovery Block 并保留 Supervisor / Runner 身份和 Workspace Ownership；
- 只有 Supervisor 仍可能存活、Runner 身份未知或无法证明 Runner 进程组清空时进入 `recovery_required`；若已证明全部清空，则直接以 `phase=finalized, outcome=failed` 收口。

## 26.3 Harness 中途崩溃

- 保存已产生的 stream/log；
- 将 `adapter_completed=false`，并按观测结果记录 `termination_kind=signal|crash|unknown`；
- 即使唯一 Outbox 已持久化，也不得提交其中的 Handoff / Complete / Block；
- Supervisor 已证明 Runner 进程组清空时，直接以 `phase=finalized, outcome=failed` 提交 Recovery Block，不设置 `recovery_required`；
- Supervisor 仍可能存活、Runner 身份未知或无法证明 Runner 进程组清空时，才进入 `recovery_required` 并提交 Recovery Block；
- 有恢复门禁时 Origin 可先运行 `recover`；门禁解除或原本没有门禁后，仍必须把 Block 展示给用户，只有用户明确授权才可用 `origin-resume --to <role-id>` 选择恢复目标，或取消 Run；
- External 角色仍使用既定 Session Policy，原 Harness Session 不可用时按 17.6 降级为 Fresh，Origin Binding 按 18.5 处理 Claim；
- 不自动 Handoff。

只有 `adapter_completed=true`、`termination_kind=normal`、退出码符合 Adapter 成功约定、`group_quiescent=true`、唯一 Outbox 有效，且 Before / After Facts 均存在并与 Turn Runtime 中的 Hash 一致时，Worker 崩溃后的恢复流程才允许补交 Outbox 中的正式动作。相同正常完成条件下没有 Outbox，则确定性补交 `block_reason=no_action`；Facts 缺失或不一致时不得重新采集当前 Workspace，其他情况进入 Recovery Block。

## 26.4 tmux Pane / Worker 退出

`agent-team recover`：

1. 获取 per-workspace 操作锁；对已有 Kickoff 且尚未满足终态释放条件的 Run，验证 Workspace Ownership 完整且仍属于本 Run。Owner 缺失、损坏或属于其他 Run 时，不重新获取、不追加 Event、不启动 Worker；只按完整身份做进程安全清理并返回 `TEAM_CORRUPTED`；
2. Journal 尚无 Kickoff 时拒绝恢复并提示执行 `start`，不得创建 Worker；
3. 直接扫描并校验不可变 Event Journal；
4. 使用 PID、PGID 和 Start ID 检查 Worker、Turn Runtime、`process/supervisor.json`、`runner.json`、`launch-authorized.json`、Runner 进程组和 Outbox；PID 复用时不得向新进程发信号，Start ID 无法查询时按未知结果处理；
5. 只有固定快照完整证明启动许可从未提交或消费，且 Supervisor / Runner 都已结束时，尚无终止 Event 的 Turn 才提交 Start Failure Block；许可缺失但已有身份引用或消费证据时按 22.3 进入 `CORRUPTED`，无论许可是否存在都绝不在同一 Turn 再次启动；
6. 许可已提交且 Supervisor 身份匹配并仍存活时只能继续观察，或让它按 Journal 终止 Runner 进程组；Supervisor 不响应时，仅在 Runner 身份三元组匹配后直接处理该进程组；
7. 对满足 26.3 正常完成条件但尚未 Finalize 的 Turn 补交已冻结 Outbox；对已知异常退出且 Runner 进程组已清空的 Turn 直接 Finalize 并提交 Recovery Block；
8. 只有 Supervisor / Runner 仍可能存活、身份未知或 Runner 进程组未证明清空时才创建 `recovery_required`；证明清空后只收口 Runtime，不自动 Resume；
9. Run 已 Blocked 时保持 Blocked；没有未解除的进程身份疑问时可以重建缺失 Window 和空闲 Worker，但不生成 Resume Event，也不启动新的业务 Turn；
10. Run 为 Running 时重建缺失 Window 并启动 Worker，确认 Worker 已启动后释放 Workspace 操作锁；
11. Worker 立即检查 Journal Tail，之后按固定周期继续检查。

任何补交 Event 后都必须重新扫描 Journal；若已进入终态，后续恢复步骤立即停止并转入安全清理。

`recover` 允许的 Journal 变化只限于从已持久化事实唯一推出的 Outbox 交付、No-action / Start Failure / Recovery Block 或既有终止 Event 的 Runtime 收口。它不创建 Resume、不另选角色，也不凭空创建业务 Turn；若确定性补交原 Handoff，后续目标 Claim 仍由正常 Worker / Origin Loop 完成。因此 `recover` 是技术收口，不是新的业务决策。

## 26.5 tmux Server 整体丢失

不可变 Event Journal 仍是 Token 和 Run 状态的事实来源。恢复时：

1. 优先请求 PID / Start ID 匹配的 Supervisor 清理 Runner 进程组；Supervisor 已退出或不响应时，只有 Runner PID / PGID / Start ID 全部匹配才直接结束该组。PID 已复用时不向新进程发信号，两种身份都无法验证时按 26.4 进入 Recovery Block 或保留终态 Ownership；
2. 获取 per-workspace 操作锁并验证持久化 Workspace Ownership；已有 Kickoff 且尚未满足终态安全释放条件的 Run 缺失、损坏或归属不符时按 26.4 进入 `CORRUPTED`，不得重新获取；
3. 重建 tmux Session 和 Worker；
4. 按 26.4 的确定性规则继续或 Block。

已提交 Event、协议、Handoff 和 Harness Session Ref 不会因为 tmux 丢失而丢失；正在运行但结果未知的 Turn 不承诺自动续跑。

## 26.6 Handoff 重复

同一 Turn 仅允许一个 `outbox.json`，并直接使用 `turn_id` 作为幂等键：

- Outbox 已存在且 `action`、`to_role`、`block_reason` 和 `payload_sha256` 相同时，返回已接受；
- Journal 中已有同一 `turn_id` 的终止 Event 时，返回原 Event；
- Outbox 已存在但上述任一字段不同时，明确拒绝，保留第一份 Outbox 及其 Payload 副本；
- Journal 已有同一 `turn_id` 但请求内容不同时，返回 `TURN_ALREADY_FINALIZED`，不再改变 Run；
- 不生成第二次 Token 转移。

## 26.7 目标 Role 不存在

CLI 立即返回错误，当前 Agent 必须修正；不得自动选择“最相近角色”。

## 26.8 Turn 无 Handoff

通知 Origin：

```text
Role <id> finished without handoff, complete, or block.
```

Origin 领取管理 Claim 后，可执行 `origin-resume --to <原 role-id> --file <resume.md>`，让同一角色按既定 Binding 再执行一个恢复 Turn；External 使用既定 Session Policy，Origin 使用新的业务 Claim。

## 26.9 Permission Prompt

非交互 Harness 若需要人工权限：

- 不自动使用危险绕过；
- Adapter 必须以结构化 `permission_required` Evidence 区分权限请求和普通崩溃；Supervisor 观察到后立即终止非交互 Runner，进程组清空后提交唯一 `block_reason=permission`，无论请求发生在启动期还是 Agent 执行期；
- 已有 Cancel / Limit Event 时仍服从现有终止 Event，不再追加 Permission Block；
- 返回 Origin；
- 若用户只需完成外部认证、审批或操作系统授权，完成后可用 `origin-resume` 明确选择恢复角色；
- 若必须改变 `launch_profile`，则取消旧 Run，并以新 Profile 创建新 Run。

## 26.10 达到安全上限

两种上限共用 13.3 的守卫，但触发语义不同：

- `max_turns` 限制“下一业务 Turn 的创建”，不提前终止当前第 `max_turns` 个 Turn；该 Turn 仍可 Complete、Agent Block 或被用户 Cancel；
- 第 `max_turns` 个 Turn 请求 Handoff 时不创建 Outbox，直接在当前 Turn 提交 `block_reason=limit, limit_reason=max_turns`；
- Blocked Run 已达到 `max_turns` 时，`origin-resume` 返回 `LIMIT_REACHED` 并保留原 Block，不追加第二个 Block Event；
- Wall Time 从 Kickoff 连续计算，Blocked 等待用户的时间也不暂停；用户较晚回复导致 Deadline 已过时只能取消旧 Run 并创建新 Run；
- Wall Time 对 External Turn 是受管 Runner 范围内的硬 Deadline：异步 Supervisor 按取消宽限期终止进程组；对 Origin Turn 是协作式 Deadline，只能在下一次 CLI / Skill 边界改为提交 Limit Block；
- 若 Wall Time 在 Token 转移后、目标领取前到期，目标领取方创建一个不启动 Harness 的 Turn 并立即提交 Limit Block，避免留下无法执行的 Running Token；
- 已提交的 Cancel Event 或 Limit Block 优先于 Outbox，所有 Limit 路径都禁止继续 Handoff。

Stage 1 不声称能强制中断宿主当前模型采样，也不把 Origin 越过 Wall Time 后到下一 CLI 边界之间的时间误报为已被系统终止。Limit Block 或因守卫失败而不可 Resume 的旧 Block 都返回 Origin；`team.json` 与安全上限不可在线修改，用户应取消旧 Run，并以新上限创建新 Run。

## 26.11 Journal 提交中断

恢复逻辑以 Event 文件是否完成原子 `rename` 为界：

- Event 不存在：Token 未转移，孤立 Payload 可清理；
- Event 存在且 Hash 有效：Token 已转移；
- Journal 已有当前 `turn_id` 的终止 Event、但 Turn Runtime 尚未写成 `finalized`：只补写 `terminal_event_id`、`phase` 和 `outcome`，不得再次提交 Event；
- Event 存在但 Payload 缺失或 Hash 不一致：直接推导为 `CORRUPTED`，禁止追加 Block 或其他 Event，不猜测或跳过；
- Event 提交后的 tmux 通知失败或丢失：不改变 Run 语义，由周期扫描发现。

---

## 27. 可观测性

观测层只投影现有 State Root、Owner、Journal、Turn / Process Runtime、Session 和 Workspace Facts，不写 Event、不创建 Claim、不刷新 Runtime，也不维护第二份健康状态。每次派生以 no-create 方式打开既有锁，先以共享模式取得 Workspace 操作锁，再以共享模式取得 Run 锁，在同一短临界区读取文件快照和必要的 PID / PGID / Start ID；锁顺序与运行时一致，写路径继续使用排他锁。显式 Run 查询发现 Workspace 锁缺失或无效时，不读取 Owner 或其他可变快照，只返回最小 `health=corrupted` 报告；省略 Run ID 时因无法安全解析 Owner 而返回接口错误。Workspace 锁有效但 Run 锁缺失或无效时，只在 Workspace 操作锁下返回最小 Corrupted 报告。锁在渲染、读取日志附件或等待下一次 Watch 轮询前全部释放，Watch 不长期占锁。`derive_observation()` 必须复用 13.2 / 13.3 的 canonical Run Status、Token、Runtime 和 Recovery Gate 推导，不能重写第二套转换判断；`status`、`diagnose` 和 `watch` 共用该函数，文本界面只是结构化结果的渲染。

## 27.1 结构化输出合同

`doctor --json`、`status --json` 和 `diagnose --json` 各输出一个 JSON 对象；`watch --jsonl` 每行输出一个完整 JSON 对象。结构化模式无论成功或失败，信封都是 stdout 的唯一内容，不混入进度、日志、ANSI 或更新提示；面向人的附加说明只写 stderr。共同信封为：

```json
{
  "schema_version": 1,
  "command": "status",
  "result": "ok",
  "observed_at": "2026-07-25T21:44:00.123Z",
  "data": {
    "run_id": "at-20260725-7f3a",
    "run_status": "RUNNING",
    "health": "ok",
    "journal_tail": {
      "event_id": "handoff-0004",
      "event_seq": 4,
      "event_type": "handoff",
      "from_role": "reviewer",
      "to_role": "developer",
      "turn_id": "turn-0004",
      "payload_path": "handoffs/0004-reviewer-to-developer.md",
      "created_at": "2026-07-25T21:43:23.000Z"
    },
    "current_role": "developer",
    "active_turn": {
      "turn_id": "turn-0005",
      "business_turn_seq": 5,
      "executor": "worker",
      "role_id": "developer",
      "phase": "running",
      "outcome": null,
      "input_event_id": "handoff-0004",
      "input_path": "turns/turn-0005/input.md",
      "age_seconds": 37,
      "managed_process_state": "running",
      "process_exit_code": null,
      "termination_kind": null,
      "agent_execution_started": true,
      "adapter_completed": false,
      "permission_required": false,
      "observed_session_ref": "550e...",
      "group_quiescent": false
    },
    "roles": [
      {
        "role_id": "developer",
        "binding": "external",
        "adapter": "claude-code",
        "session_policy": "resume",
        "state": "running",
        "worker_pid": 12345,
        "worker_start_id": "os-process-start-id",
        "tmux_session": "agent-team-at-20260725-7f3a",
        "tmux_pane_id": "%4",
        "session_status": "available",
        "session_generation": 3,
        "session_ref": "550e...",
        "session_unavailable_reason": null
      },
      {
        "role_id": "reviewer",
        "binding": "origin",
        "adapter": null,
        "session_policy": null,
        "state": "idle",
        "worker_pid": null,
        "worker_start_id": null,
        "tmux_session": null,
        "tmux_pane_id": null,
        "session_status": "not_applicable",
        "session_generation": null,
        "session_ref": null,
        "session_unavailable_reason": null
      }
    ],
    "workspace_owner": "this_run",
    "origin": {
      "state": "finalized"
    },
    "limits": {
      "turns_used": 5,
      "max_turns": 20,
      "elapsed_seconds": 1902,
      "deadline_at": "2026-07-25T23:12:18.000Z",
      "remaining_seconds": 5298
    },
    "block": null,
    "recovery_required": false,
    "recommended_action": "WAIT",
    "details": {
      "supervisor_pid": 12380,
      "supervisor_start_id": "os-process-start-id",
      "runner_pid": 12381,
      "runner_pgid": 12381,
      "runner_start_id": "os-process-start-id",
      "owner_run_id": "at-20260725-7f3a"
    },
    "evidence_paths": [
      "events/0004-handoff-0004.json",
      "handoffs/0004-reviewer-to-developer.md",
      "roles/developer.json",
      "sessions/developer.json",
      "turns/turn-0005/input.md",
      "turns/turn-0005/process/runner.json",
      "turns/turn-0005/process/supervisor.json",
      "turns/turn-0005/runtime.json"
    ]
  }
}
```

错误信封固定为同一组顶层字段加 `error`，且不含 `data`：

```json
{
  "schema_version": 1,
  "command": "status",
  "result": "error",
  "observed_at": "2026-07-25T21:44:00.123Z",
  "error": {
    "code": "RUN_NOT_FOUND",
    "message": "run not found",
    "evidence_paths": []
  }
}
```

`result=ok` 只表示报告成功构造，不表示 Run 健康；Blocked、Cancelled 或可确定的 Corrupted 都使用成功信封，并在 `data` 中表达。命令无法构造报告时使用 `result=error`，同时返回固定 `error.code`、人类可读 `message` 和已有的 `evidence_paths`。观察接口的错误码闭集为 `INVALID_ARGUMENT | RUN_NOT_FOUND | OBSERVATION_IO_ERROR | OBSERVATION_INTERNAL_ERROR`；完整性故障只要仍能构造确定报告，就属于 `data.health=corrupted` 而不是接口错误。

Run 范围内的 `evidence_paths` 一律是相对 Run Directory 的已校验普通文件路径；PID / PGID / Start ID 等操作系统观测放在结构化 `details` 中，不伪造成文件路径。错误信封不同时返回可能被误当作完整 Snapshot 的 `data`。

观察输出中的 `observed_at`、Event 时间、Deadline 和附件采集时间都规范化为 UTC RFC 3339；同一 Snapshot 的信封和派生字段复用同一个观测时间。排序仍以 Journal Sequence、Stream Sequence 或 Producer Sequence 为准，不依赖墙钟先后关系。

为保证结构化输出稳定，Role 按 `role_id`、诊断项按本节声明顺序、证据路径按字节序输出；JSON Object Key 顺序没有语义。

观察命令的进程退出码固定为：`0` 表示已输出完整报告，`2` 表示参数错误，`3` 表示目标 Run 不存在，`4` 表示 I/O 或内部错误导致报告无法构造，`130` 只表示 `watch` 被用户中断。一旦识别出 `--json` 或 `--jsonl`，参数错误也必须输出上述错误信封，Usage 只写 stderr。Skill 必须读取信封和状态字段决定下一步，不能把“非 RUNNING”简单等同于命令失败。

## 27.2 `agent-team status`

`status` 返回当前派生事实，不执行诊断修复。结构化 `data` 至少包含：

- `run_id`、`run_status` 和 `health`；`health` 固定为 `ok | attention | recovery_required | corrupted`；
- Journal Tail 的 Event ID、Sequence、类型、可空 From / To Role、可空来源 Turn、Payload 路径和提交时间；
- 当前 Token Role，以及唯一活跃业务或管理 Turn 的 ID、`business_turn_seq`、Executor、Role、Phase、Outcome、Input Event / `input.md` 路径、持续时间、Exit Code、Termination Kind、Agent Execution Started、Adapter Completion、Permission Evidence、Observed Session Ref 和 Group Quiescence；
- `active_turn.managed_process_state`，固定为 `not_applicable | not_started | starting | waiting_authorization | running | stopping | exited | identity_unknown`；
- 每个 Role 的 Binding、可空 Adapter / Session Policy、`state=not_started|idle|running|stopped|identity_unknown`、可空 Worker PID / Start ID / tmux Session / Pane、`session_status=not_applicable|not_created|available|unavailable`、`session_generation`、可空 `session_ref` 与 `session_unavailable_reason`；Origin Role 的 Worker / Session 字段都为不适用值，Claim / Wait 细节只在 `origin` 字段表达；
- `workspace_owner=not_acquired|this_run|other_run|missing|released|invalid`，以及 `origin.state=not_applicable|unclaimed|claimed|exited|finalized`；
- Turn 使用量、Wall Time Deadline、已用秒数，以及下限为 `0` 的剩余秒数；
- 当前 Block 的原因、Payload、`resume_policy=after_user_instruction|new_run_required` 和 Recovery Gate；
- `recommended_action`，固定为 `START | WAIT | CLAIM_ORIGIN_EVENT | FINALIZE_ORIGIN_EXIT | RETURN_BLOCK_TO_USER | RUN_RECOVER | READ_COMPLETION | MANUAL_DIAGNOSIS | NONE`；
- `details` 中唯一活跃 External Turn 的可空 Supervisor / Runner PID、PGID、Start ID，以及可空 `owner_run_id`；
- 支撑结论的 Journal、Runtime、Block 或 Completion 文件路径；Owner 与进程事实放在结构化字段中，不伪造成 Run 内文件。

`journal_tail` 在 UNSTARTED 时为 `null`；非空时固定包含 `{event_id, event_seq, event_type, from_role, to_role, turn_id, payload_path, created_at}`，不适用字段为 `null`。没有已领取的业务或管理 Turn 时 `active_turn=null`，否则对象中的上述字段全部存在。External Turn 的 Managed Process、Adapter Evidence、Exit 和 Group 字段优先来自当前有效 Supervisor Snapshot，Supervisor 已安全结束后才使用 Finalized Runtime；两者冲突按既有完整性规则处理，不能从 Stream 或普通日志补值。Origin 业务 / 管理 Turn 的 `managed_process_state=not_applicable`，Agent Execution、Adapter Completion、Permission、Observed Session Ref、Group 和所有 Process Detail 字段都为 `null`，不借用 External 的布尔语义。`active_turn.observed_session_ref` 是 External 当前 Turn 的候选 Evidence；Role 项中的 Session 字段只反映已提交的 `sessions/<role-id>.json`，提交窗口中仍为 `not_created` 或上一 Generation，不能用候选值提前覆盖。`block` 只在 BLOCKED 时为 `{event_id, block_reason, limit_reason, payload_path, resume_policy}`，其中 `limit_reason` 可空；`resume_policy` 在 `block_reason=limit|profile_changed` 或当前 `can_create_business_turn=false` 时为 `new_run_required`，其他原因只表示 `after_user_instruction`，并不代表 Recovery Gate 已解除。每个 Role 项固定保留自身可空的 Worker / tmux 身份；`details` 固定保留活跃 External Turn 的可空 Supervisor / Runner 身份和 `owner_run_id`。

Owner 文件不存在时，UNSTARTED 映射为 `not_acquired`，已满足安全释放条件的终态映射为 `released`，Kickoff 后尚未满足释放条件的 Run 才映射为 `missing` 并进入 Corrupted；有效的其他 Run Owner 始终映射为 `other_run`。历史终态 Run 在安全释放后看到新的 `other_run` 是正常状态，不追溯污染旧 Run。所有顶层键、非空对象的固定键和 Role 项字段在同一 Schema 版本中保持存在；Corrupted 状态下无法安全推导的值写 `null` 或对应的 `identity_unknown`，不能省略字段或从日志补猜。

`recommended_action` 只表达由状态唯一推出的技术下一步，不能选择 Handoff 目标、替用户授权 Resume、自动 Unlock 或判断业务是否完成。`CLAIM_ORIGIN_EVENT` 仍需正常调用 `wait-origin` 竞争 Claim，`FINALIZE_ORIGIN_EXIT` 仍要求原 Claim；观察结果不返回、转移或绕过 Claim。Stage 1 没有持久化 wait-origin 轮询进程身份，也没有 Worker / Supervisor Heartbeat；Health 证明的是持久化不变量和可验证进程身份，不证明一个仍存活的事件循环正在及时响应。持续时间较长只作为事实展示；没有 Deadline 或进程身份异常证据时不得把 Harness 或宿主 Agent 猜测为“卡死”，响应性问题保留给人工诊断，不为它增加第二份 Liveness 状态。

派生优先级固定为：

1. 完整性故障得到 `corrupted / MANUAL_DIAGNOSIS`；
2. 未解除的进程安全门得到 `recovery_required / RUN_RECOVER`；
3. `origin.state=exited` 得到 `attention / FINALIZE_ORIGIN_EXIT`；
4. 终止 Event 已提交且可验证的清理进程仍在工作时得到 `attention / WAIT`；
5. 终止 Event 已提交、受管执行已确认结束但 Owner 仍属于本 Run 时，说明自动释放未收口，得到 `attention / RUN_RECOVER`；
6. 当前 Event 或 Block 管理控制等待 Origin 领取且 `origin.state=unclaimed` 时，Running 保持 `ok`、Blocked 保持 `attention`，两者都建议 `CLAIM_ORIGIN_EVENT`；
7. Running / Blocked 中本应存在的 Worker 或 tmux 确定缺失时得到 `attention / RUN_RECOVER`；
8. 尚未启动的 Run 遇到 `workspace_owner=other_run` 时得到 `attention / WAIT`；
9. 其余 Blocked 得到 `attention / RETURN_BLOCK_TO_USER`；
10. 可启动的 UNSTARTED、正常 Running、已安全收口的 Completed、已安全收口的 Cancelled 都得到 `health=ok`，对应建议依次为 `START`、`WAIT`、`READ_COMPLETION`、`NONE`。

同一 Snapshot 在所有观察命令中必须得到相同结论。

文本输出示例：

```text
Run: at-20260725-7f3a
Status: RUNNING
Health: ok
Current role: developer
Journal tail: handoff-0004
Workspace owner: this run
Origin state: finalized
Turn: 5 / 20
Elapsed: 00:31:42
Remaining: 01:28:18
Active turn: turn-0005 phase=running age=00:00:37
Managed process: running
Recommended action: WAIT

Roles:
  developer  claude-code resume     running   session=550e...
  reviewer   origin                  idle

Current input:
  type=handoff reviewer -> developer
  source=handoffs/0004-reviewer-to-developer.md
  frozen=turns/turn-0005/input.md
```

`Turn: N / max_turns` 只统计业务 Turn Runtime，不统计 Origin 管理 Turn。Stage 1 没有同一 Turn 内启动重试。

Running 和 Blocked 的 `elapsed_seconds` 都从 Kickoff 计算到 `observed_at`；Completed / Cancelled 在终止 Event 时间停止增长，UNSTARTED 的 Deadline 与时间字段为 `null`。

`age_seconds` 和 `elapsed_seconds` 是时间差下限取 `0` 后向下取整的整数；`remaining_seconds` 下限取 `0` 后向上取整，保证尚未到期时至少显示 `1`。Deadline、Limit 和状态守卫仍使用原始时间戳比较，不使用展示用的舍入值。

## 27.3 `agent-team diagnose`

`diagnose` 在同一 Observation Snapshot 上运行固定、只读的检查，不执行 `recover`、Cancel、Unlock 或任何 Event 转换。`--role` 只缩小进程与日志附件范围，State Root、Journal、Owner 和当前 Turn 完整性仍按 Run 全局检查。

`diagnose.data` 固定包含 `observation`、`checks` 和 `attachments`：`observation` 是与同一时点 `status.data` 相同的完整对象，后两者只增加解释，不覆盖其中的 Health 或 Recommended Action。

每个检查结果固定包含 `check`、可空 `subject_role_id`、可空 `subject_path`、`status=pass|fail|unknown|not_applicable`、`code`、`summary`、`evidence_paths` 和可空 `recommended_action`；`subject_path` 只在目标是按 Schema 可确定的 Run 相对路径时填写，即使该文件缺失也能指出检查对象，`evidence_paths` 仍只列实际读到的普通文件。非空建议复用 Status 的同一闭集。检查名按 `workspace_lock | state_root | run_lock | config | journal | owner | active_turn | worker | supervisor | runner | runner_group | launch_authorization | session | workspace_facts | recovery_gate | tmux_runtime` 的固定顺序输出，同类 Role 检查再按 `role_id` 排序。`pass` 与 `not_applicable` 分别使用 `OK` 和 `NOT_APPLICABLE`；Stage 1 的其他诊断码闭集为：

```text
WORKSPACE_LOCK_INVALID
STATE_ROOT_INVALID
RUN_LOCK_INVALID
CONFIG_INTEGRITY_FAILED
JOURNAL_INTEGRITY_FAILED
OWNER_MISSING
OWNER_MISMATCH
OWNER_INVALID
TURN_SNAPSHOT_INVALID
PROCESS_RUNTIME_MISSING
PROCESS_IDENTITY_UNKNOWN
RUNNER_GROUP_NOT_QUIESCENT
LAUNCH_AUTHORIZATION_INVALID
SESSION_SNAPSHOT_INVALID
WORKSPACE_FACTS_INVALID
RECOVERY_REQUIRED
TMUX_RUNTIME_MISSING
```

检查集固定覆盖 State Root、不可变配置、Journal、Owner、活跃 Turn、Worker / Supervisor / Runner 身份与进程组、启动许可、Session、Workspace Facts、Recovery Gate 和适用时的 tmux Runtime。每项都复用对应生命周期的不变量：终态已安全释放 Owner、纯 Origin Run 没有 tmux、首次 Session 尚未产生，以及活跃 Supervisor 已观察到候选 Ref 但 Worker 尚未提交 Session Snapshot 等合法窗口，必须是 `pass` 或 `not_applicable`，不能误报故障。已验证 PID 不存在可以证明该 PID 已结束，但不能单独证明 Runner PGID 已清空、最终快照已提交或历史执行从未发生；无法验证完整进程身份时报告 `unknown`。Pane 文本或普通日志中的一句话不能升级为安全结论。当前 Harness / Adapter 能力与 Launch Profile Fingerprint 的主动探测仍属于 `doctor` 和 Turn 启动边界，普通 Status / Diagnose / Watch 不在持锁观察中重复执行外部 Probe。

`attachments` 固定为以下结构；已有 Worker 日志和原始 Stream 只作为已校验的 Run 相对路径返回。`capture-pane` 可在释放观察锁后读取最多 200 行且最多 64 KiB 的末尾内容，不写回 Run Store：

```json
{
  "paths": [
    "logs/developer.jsonl",
    "turns/turn-0005/process/stream.jsonl"
  ],
  "pane_excerpt": {
    "role_id": "developer",
    "captured_at": "2026-07-25T21:44:00.456Z",
    "truncated": false,
    "text": "last worker output"
  }
}
```

没有适用 Pane 时 `pane_excerpt=null`。Pane 摘录晚于 Observation Snapshot，日志与 Raw Stream 内容也都只是未受信任的人工线索；它们不进入 `observation.details`，检查逻辑只验证附件路径而不解析内容。除换行和制表外的终端控制字符必须转成可见转义，文本渲染不得原样输出 ESC / C0 / C1。附件和摘录都不得改变检查码、Health 或 Recommended Action。

## 27.4 `agent-team watch`

`watch` 是 `status` 的实时视图，不是事件总线：

- 启动后立即输出一份完整 Observation Snapshot；
- 每次采集完成后等待两秒再重新派生，不并发堆积观察任务；
- 文本模式每轮原地刷新，`--jsonl` 每轮输出一份完整 Snapshot，不维护变化比较或 Heartbeat 状态；
- 每条 JSONL 的 `data` 与该时点 `status.data` 相同，并在共同信封顶层增加从 `1` 开始的进程内单调 `watch_seq`，不提供跨进程 Cursor；
- 断线重启时从新的完整 Snapshot 开始，不补造漏掉的 Watch 行；历史仍从 Event Journal 读取；
- Watch 不 Claim Token、不发送 tmux 提示、不触发恢复；输出失败也不影响 Team Run；
- `COMPLETED | CANCELLED` 若仍处于自动清理的 `recommended_action=WAIT` 就继续观察；否则在发出最终 Snapshot 后正常退出。任何 `health=corrupted` 报告输出后都退出，Blocked 状态保持观察直到用户中断或 Run 继续。

Stage 1 不引入 Prometheus、OpenTelemetry、远程日志服务、告警规则或常驻观测进程；本地结构化投影、关联日志和原始审计文件已经覆盖 MVP 的操作与恢复需求。

Origin Binding 没有 Worker Pane、受管子进程或宿主采样日志；Observation 只能报告已持久化的 Claim、Turn Phase 和 Event，不能显示当前模型生成进度，也不能据持续时间推断宿主 Agent 已卡死。

## 27.5 人工介入

Stage 1 支持：

- 只读 Attach 查看 Pane；
- 通过 `diagnose` / `capture-pane` 收集末尾输出用于故障诊断；
- Cancel Run；
- 使用 `recover` 恢复 tmux / Worker 并执行确定性技术收口，但不 Resume、不选择新业务路由；
- 在可 Resume Blocked 状态下，通过 `origin-resume --to ... --file ...` 选择恢复角色并携带会成为下一 Turn 直接输入的 Resume Payload；
- 在确认无活进程后显式 Unlock。

Stage 1 不支持向任意下一 Turn 注入独立 Human Note，不支持在线修改 `REQUEST.md`、`PROTOCOL.md`、角色映射或安全上限，也不向正在运行的 Harness 子进程注入自由文本。需要改变这些配置时创建新 Run。

---

## 28. 技术实现建议

## 28.1 语言与依赖

Stage 1 推荐：

- Python 3.11+；
- 尽量只使用标准库；
- `argparse` 构建 CLI；
- `asyncio.create_subprocess_exec` 管理 Turn Supervisor，由组外 Supervisor 管理 Runner / Harness 进程组；
- `fcntl` 文件锁；
- JSON + Markdown；
- tmux CLI；
- macOS / Linux 上满足 22.1 本地原子文件语义的文件系统。

选择 Python 的原因：

- 第一阶段验证速度优先；
- Codex/Claude Code 可直接修改；
- 子进程、文件和 JSONL 处理简单；
- 后续可继续使用 SQLite 标准库；
- 无需过早引入服务端。

## 28.2 模块布局

```text
src/agent_team/
├── cli.py
├── commands/
│   ├── environment.py
│   ├── init.py
│   ├── start.py
│   ├── turn_actions.py
│   ├── cancel.py
│   ├── unlock.py
│   ├── origin_actions.py
│   ├── inspect.py
│   └── recover.py
├── bootstrap/
│   ├── protocol_templates.py
│   └── validator.py
├── runtime/
│   ├── run_store.py
│   ├── event_journal.py
│   ├── worker.py
│   ├── supervisor.py
│   ├── runner.py
│   ├── process_identity.py
│   ├── state_root.py
│   ├── workspace_owner.py
│   ├── workspace_facts.py
│   ├── observation.py
│   ├── recovery.py
│   ├── tmux.py
│   ├── turn.py
│   └── origin.py
├── adapters/
│   ├── base.py
│   ├── claude_code.py
│   └── codex.py
├── prompts/
│   ├── role_turn.py
│   └── origin_delivery.py
├── security/
│   ├── paths.py
│   └── integrity.py
└── util/
    ├── atomic.py
    ├── ids.py
    └── logging.py
```

布局按职责分组，不要求每个 CLI 子命令各占一个文件：`environment.py` 承载 Install / Doctor，`inspect.py` 只编排 Status / Watch / Attach / Diagnose 的调用、轮询与渲染，`observation.py` 提供它们共用的只读派生与固定诊断码，`origin_actions.py` 承载 `wait-origin`、`origin-context` 和所有 `origin-*` 动作；高风险的 Unlock 与 Start 保持独立用例。

模块依赖保持单向：

- `commands/` 只做参数解析后的用例编排，不直接实现锁、状态转换或进程控制；External 的 Context / Handoff / Complete / Block 共用 `turn_actions.py`，避免三份 Outbox 事务漂移；
- `bootstrap/` 只生成并校验不可变 Request / Protocol / Team 配置；
- `runtime/` 是 Event、Turn、Ownership、Workspace Facts、观测派生、进程与恢复规则的唯一实现层；PID / PGID / Start ID 的读取、匹配和安全信号只在 `process_identity.py` 实现，Supervisor、Observation、Recovery 和 Unlock 不各写一套；
- `adapters/` 只定义可序列化合同，并做 LaunchSpec 与结构化 Evidence 的纯转换，不写 Journal、Runtime 或 Ownership；`runtime/` 把 Supervisor 快照投影成 `ProcessResult` 后单向调用它；
- `prompts/role_turn.py` 同时处理 Kickoff、Handoff 和 Resume Input，不为 Recovery 建第二套业务 Turn；
- `security/` 只放路径和完整性校验；自然语言职责不在这里伪装成权限系统；
- `util/` 是无业务状态的叶子依赖。

跨模块共享的数据类型放在拥有该概念的模块中；若出现循环依赖，优先移动类型或合并小模块，不增加通用 Service Locator、Repository 层或事件总线。

## 28.3 关键实现约束

1. 所有外部命令使用 argv 数组，不使用 `shell=True`；
2. Role ID 限制为 `[a-z][a-z0-9_-]{0,31}`；
3. Agent 提供的 Handoff、Completion 等 Payload 路径必须解析到 Run Directory 内；用户提供的 Workspace 参数单独规范化校验，Stage 1 不接受状态目录参数；
4. tmux Session / Window 名称必须转义和限制长度；
5. Runner / Harness stdin 使用 PIPE，不继承 tmux PTY；
6. stdout/stderr 流式读取，避免死锁；
7. Session ID 单独持久化；
8. 不可变 Event 文件的原子 `rename` 是唯一状态转换提交点；
9. Event 类型、允许的前置状态和 Token 结果必须按 13.2 的闭集转换表校验；
10. Run Status、Token Owner 和 Inbox 必须直接从 Event Journal 与 Turn Runtime 推导；
11. State Root、Worker、Turn Runtime、Supervisor、Runner、Input、Before / After Facts、Outbox Payload、Outbox、Session 和 Owner 快照必须整文件原子替换；已存在但 Schema 损坏的 Session 快照直接推导为 `CORRUPTED`；
12. Event 先提交，再发送 Best-effort tmux 提示；Worker 即使没有提示也必须定时扫描；
13. Worker 与 Supervisor 必须监听 Journal、Deadline 和固定完整性守卫；Supervisor 必须留在 Runner 进程组之外并持续管理已记录 PGID，完整性失败只做进程安全清理而不追加 Event；
14. 只有 Adapter 明确正常完成、Supervisor 证明已记录 Runner PGID 清空且冻结的 Before / After Facts Hash 匹配时才允许正式交付 Outbox；`group_quiescent` 不得解释为完整后代进程证明；
15. Origin Handoff 和 Origin Resume 必须使用提交并等待的单一 CLI 调用，所有 Origin 动作必须校验 Claim；Stage 1 不提供 Claim Takeover；
16. `recover` 不得提交 Resume Event；可 Resume Blocked → Running 只能由 `origin-resume` 完成，Limit / Profile Changed Block 不允许 Resume；
17. 启动 Worker 前必须获取持久化 Workspace Ownership 并已提交唯一 Kickoff；Owner 是在稳定 per-workspace 操作锁内以原子 `rename` 提交的单文件；
18. 用户 Cancel 与 `deadline|max_turns` 直接提交各自的 Event；不得增加 Event 之前的第二套持久化控制状态；
19. Kickoff 后的 `REQUEST.md`、`PROTOCOL.md` 和 `team.json` 不可在线修改；Hash 不匹配直接推导为 `CORRUPTED`，不得尝试生成没有稳定生产者的 Recovery Block；
20. 自动控制不得使用 `send-keys`，`wait-for -L/-U` 不得替代文件级提交协议；
21. 不解析 Pane 文本决定工作流语义；
22. Outbox Candidate 创建时必须复制并哈希 Payload，之后不得重新读取原始文件；
23. 所有 Role 必须使用唯一受持久化 Ownership 保护的 Git Worktree 根目录；`.agent-team/` 必须具有绑定当前账号固定状态目录的有效 State Root 且不能被 Git 跟踪，Sparse Checkout 和 Gitlink 在 `init` 与每次边界 Snapshot 都拒绝；Git 可见 Snapshot 必须按 13.4 把 tracked 删除或文件变目录编码为 `missing`，并排除 ignored 路径、Run Store 与 Git 内部元数据；`init` 不修改 Git ignore 元数据；
24. 终止、恢复和 Unlock 不得仅凭 PID 操作，必须校验 PGID 与操作系统 Start ID；
25. External Adapter 必须用显式高优先级覆盖严格执行 `team.json` 中经 Probe 验证的 Launch Profile 与 Hash，不能依赖可变用户默认值、接受同名映射漂移或从 Role 语义推断权限；Fresh Role 只校验 Start，Resume Role 校验 Start / Resume 的等价技术权限；Probe 只验证 CLI 启动器及普通子进程的受管行为，不承诺完整进程 containment；
26. Adapter 只生成可序列化 LaunchSpec、纯解析结构化输出并判定持久化 Result；Supervisor 必须先提交符合状态不变量的 `starting` 快照，Runner 再自持久化稳定身份、获得唯一许可并原地 `exec` Harness；启动结果为 `started|start_failed|start_unknown`；
27. Handoff Candidate 接受、Handoff / Resume Event 提交和目标 Turn Claim 必须使用同一个 `can_create_business_turn` 计算；
28. Origin / External Binding 必须按 `binding` 严格区分，Origin 不进入 Adapter、Worker、Session 或 Launch Profile 路径；
29. Handoff Event 的提交不得依赖目标 Worker、tmux Window 或 Origin Turn 在线；
30. 已知异常退出且 Runner 进程组已清空时直接 Finalize；只有进程身份或清空状态不确定时使用 `recovery_required`；
31. 所有 Block 都必须返回用户；对可 Resume Block，只有新的明确用户指令可以授权 `origin-resume`；
32. `session_policy=resume` 的同一 External Launch Profile 必须在 Start 与 Resume 路径上具有经过 Probe 和集成测试验证的等价技术权限；Fresh-only Role 不增加无用的 Resume 要求；
33. Stage 1 每个业务 Turn 只允许一次 External 启动；任何 Start Failure 都 Block，不在同一 Turn 自动重试；
34. Kickoff、Handoff、Resume Payload 都必须冻结成下一业务 Turn 的 `input.md`；Resume 指令只可解除原 Block，改变不可变输入必须新建 Run；
35. 多个 Role 绑定同一 Origin Session 时必须明确共享宿主上下文；需要独立验证时使用不同 External Session。
36. 已有 Kickoff 且尚未满足终态释放条件的 Run 必须保有精确 Owner；缺失、损坏或归属改变直接进入 `CORRUPTED`，`start` / `recover` 不得自动重建 Ownership。
37. `origin-complete` / `origin-block` 返回后只能生成用户回复；旧 Origin Runtime 保持 `exited`，直到后续用户 Agent Turn 的 `wait-origin` 确认宿主执行窗口已经结束。
38. 所有命令只使用当前 OS 账号固定推导的 `state_dir_realpath`；环境变量、项目配置和 CLI 参数都不能为同一 Workspace 形成第二套锁和 Owner，State Root 只负责校验记录值一致。
39. 最终 Run 目录只能由完整临时目录的一次原子 `rename` 创建；单个初始化文件写完不代表 Run 已存在。
40. Status、Diagnose 和 Watch 必须以 no-create 方式打开既有锁，并调用同一个只读 Observation 派生函数；不得因输出格式、调用命令或重复观察而改变 Run，也不得持久化第二份 Health / Recommended Action。
41. 结构化观察输出必须遵循固定 Schema、Enum、错误码和退出码；stdout 不混入人类日志，文本输出只能由同一结构化对象渲染。
42. Worker 是 Role 日志的唯一写者，Supervisor 是 Turn Raw Stream 的唯一写者；Harness stdout / stderr 必须以带顺序、时间、来源和可逆字节编码的 `RawStreamChunk` 保存，同一确定性 Framer 只把完整 Harness 行交给 Adapter，Evidence Snapshot 不得先于所依据的 Raw Stream `fsync`。

---

## 29. 核心时序

## 29.1 外部角色之间 Handoff

```mermaid
sequenceDiagram
    participant W1 as Worker: Role A
    participant SV1 as Supervisor A
    participant R1 as Runner A
    participant A as Harness A
    participant S as Run Store
    participant T as tmux
    participant W2 as Worker: Role B
    participant SV2 as Supervisor B
    participant R2 as Runner B
    participant B as Harness B

    W1->>SV1: 启动组外 Supervisor
    SV1->>R1: start_new_session
    R1->>S: 自持久化身份并等待
    W1->>S: 验证身份并提交启动许可
    R1->>A: 原地 exec start/resume Turn
    A->>S: 写 Handoff Markdown
    A->>S: agent-team handoff --to B
    A-->>SV1: Harness 退出
    SV1-->>W1: Runner 进程组清空 + Adapter 结果
    W1->>S: 原子提交 Payload + Event
    W1-->>T: wait-for -S role-B:changed（Best-effort）
    T-->>W2: 解除等待
    W2->>S: 创建 Turn Runtime + 冻结 input.md
    W2->>SV2: 启动组外 Supervisor
    SV2->>R2: start_new_session
    R2->>S: 自持久化身份并等待
    W2->>S: 验证身份并提交启动许可
    R2->>B: 原地 exec start/resume Turn
```

## 29.2 外部角色交给 Origin Role

```mermaid
sequenceDiagram
    participant E as External Role
    participant S as Run Store
    participant O as Origin Codex

    E->>S: handoff --to reviewer(origin)
    S->>S: 提交 Event
    O->>S: wait-origin
    S-->>O: HANDOFF_TO_ORIGIN_ROLE + Turn + Claim
    O->>S: 读取 Protocol + 当前 input.md
    O->>O: 执行 Reviewer Turn
    alt Handoff 给外部角色
        O->>S: origin-handoff --claim（提交并等待）
        S->>S: 提交 Event
        S-->>O: 保持等待，不返回业务执行窗口
    else Complete / Block
        O->>S: origin-complete 或 origin-block --claim
    end
```

## 29.3 Completion 返回用户

```mermaid
sequenceDiagram
    participant C as Completion Role
    participant S as Run Store
    participant O as Origin Session
    participant U as User

    C->>S: complete --file completion.md
    S->>S: Run = COMPLETED
    O->>S: wait-origin
    S-->>O: TEAM_COMPLETED
    O->>S: 读取 Completion + Artifacts
    O->>U: 最终交付
```

## 29.4 初始角色绑定 Origin

```mermaid
sequenceDiagram
    participant B as Bootstrap Skill
    participant S as Run Store
    participant O as Origin Session

    B->>S: 提交 Kickoff Event(to origin-role)
    B->>S: wait-origin
    S-->>O: ORIGIN_KICKOFF + Turn + Claim
    O->>S: 读取 REQUEST / PROTOCOL / Kickoff input.md
    O->>O: 执行动态 Origin Role
```

## 29.5 可 Resume Blocked 后显式 Resume

```mermaid
sequenceDiagram
    participant R as Blocking Role
    participant S as Run Store
    participant O as Origin Session
    participant U as User
    participant W as Target Worker

    R->>S: 可 Resume block
    S->>S: 提交 Block Event，Token = null
    O->>S: wait-origin
    S-->>O: TEAM_BLOCKED + Management Turn + Claim
    O->>U: 展示 Block 与诊断结果
    U-->>O: 明确恢复指令 + 目标角色
    O->>S: origin-resume --claim --to target（提交并等待）
    S->>S: 提交 Resume Event，Token = target
    S-->>W: Best-effort 变更提示
    W->>S: Claim Turn + 冻结 Resume Payload 为 input.md
    O->>S: 同一调用内继续 wait-origin
```

---

## 30. MVP 验收标准

### 30.1 功能验收

1. 在 Origin Turn 持续存活时，用户只输入一次自然语言团队请求即可自动闭环；
2. 系统生成可读的 `REQUEST.md` 和 `PROTOCOL.md`；
3. 支持至少两个动态角色；
4. 支持 Origin Binding，以及 `claude-code`、`codex` 两类 External Binding；
5. 存在 External Binding 时自动创建 tmux Session；纯 Origin Run 不依赖 tmux；
6. 初始角色无论绑定外部 Harness 还是 Origin 都能自动启动；
7. 同一外部角色至少可 Resume 五轮；
8. Handoff 目标由 Agent 显式选择；
9. 支持 A ↔ B 循环以及 A → B → C；
10. 支持外部角色 Handoff 给 Origin 绑定角色；
11. 支持任意角色调用 Complete 并返回 Origin；
12. Origin 向用户输出最终结果；
13. 全流程无需用户手动切换 Agent 窗口或复制上下文；`embedded_poll` 的 Origin Turn 中断后只需在原 Session 继续；
14. 每轮都有可追溯的 Input Event 与日志；External Turn 记录 Session Ref；
15. tmux 重建后，确定状态的 Turn 可以继续，结果不确定的 Turn 明确 Block；
16. 持久化 Workspace Ownership 阻止同一规范化 Workspace 同时启动两个 Team Run；
17. 可 Resume Blocked Run 只能在新的明确用户指令下，由携带管理 Claim、Block 引用和显式 `to_role` 的 `origin-resume` 恢复；Resume Payload 成为下一 Turn 的直接输入，Limit / Profile Changed Block 或不可变输入变化必须新建 Run；
18. 同一个 Origin Turn 同时只有一个有效 Claim，第二个 Session 不能静默共同执行或替换 Claim；Claim 丢失时只能取消旧 Run 并安全创建新 Run。
19. Skill 可以通过 `status --json` 和 `diagnose --json` 取得稳定的 Run Health、活跃 Turn Phase、进程状态、Block Resume Policy、证据路径和唯一技术建议，不解析文本界面；
20. `watch --jsonl` 首行提供完整当前 Snapshot，断线重启仍能重新取得当前事实，不依赖丢失期间的 Watch 输出。

### 30.2 可靠性验收

1. 不从终端文本解析“通过/失败”；
2. 自动控制不使用 `send-keys`，完整 Handoff 只从 Run Store 读取；
3. tmux 提示丢失且 Worker 不重启时，Event 仍能通过周期扫描被发现；
4. 同一 Turn 不能提交两个 Handoff；
5. 不存在目标 Role 时明确失败；
6. Harness 退出但无正式动作时返回 Origin；
7. Kickoff 后 `REQUEST.md`、`PROTOCOL.md` 或 `team.json` 被修改时直接推导为 `CORRUPTED`，即使当前 Token 尚未被目标 Claim 也不伪造 Recovery Block；
8. 默认不启用危险全权限模式；
9. Run 取消后保留审计目录；
10. 不创建额外语义状态缓存；Run Status、Token Owner 和 Inbox 只由 Journal 与 Turn Runtime 推导，Ownership 只约束执行；
11. 在 Payload、Event 和 tmux 提示之间的任一崩溃点都不会产生两个 Token Owner；
12. 运行中的外部 Harness 可以被直接提交的 Cancel Event 或 Deadline Limit Block 终止；
13. Running、Blocked 和 Origin Turn 都可以由用户 Cancel，且终态 Event 只有一个；
14. Origin Handoff 或 Resume 提交后不会先返回可继续业务操作的窗口；
15. Origin Claim 只有匹配者可重复领取，Stage 1 不允许第二个 Session 替换活跃 Claim；
16. 非正常退出即使已有 Outbox 也不交付；只有可证明的正常完成且已记录 Runner PGID 清空才允许交付，`group_quiescent` 不被误报为完整后代进程证明；
17. State Root、Input、Before / After Workspace Facts、Supervisor、Runner、Session、Outbox Payload 和其他恢复快照都通过整文件原子替换写入；已存在但无法通过 Schema 校验的 Session 快照直接使 Run 进入 `CORRUPTED`；
18. Cancel、Deadline 和 Max-Turn 直接提交 Event，不存在“请求已持久化但 Event 未提交”的半状态；
19. Workspace Ownership 以当前 OS 账号固定状态目录中的单个原子 Owner 文件提交，在进程和 tmux Server 崩溃后仍保留；State Root 记录值必须与固定推导一致，已有 Kickoff 的 Run 丢失或错配 Owner 时进入 `CORRUPTED`，不会自动重建，过期记录只能经验证后显式解除；
20. `unlock` 与 `start` / `recover` 并发时仍不能删除活跃 Run 的 Ownership；
21. Outbox 创建后修改原始 `--file` 不会改变最终交付的 Payload；
22. PID 被复用时不会向无关进程发送信号，Start ID 无法查询时不会自动释放 Ownership；
23. 非 Git Worktree 根目录、Git 已跟踪或无法验证的 `.agent-team/`、Sparse Checkout、Gitlink 或 `team.json` 包含 per-role CWD 时在 `init` 阶段拒绝；运行中开始跟踪 `.agent-team/`、启用 Sparse Checkout、加入 Gitlink 或破坏 State Root 时在下一边界进入 `CORRUPTED`；`init` 不修改 `.gitignore`、`.git/info/exclude` 或其他 Git 元数据；
24. External Adapter Launch Profile 来自 Probe 闭集并冻结可执行版本与权限映射 Hash；Fresh Role 验证 Start，Resume Role 验证 Start / Resume 等价技术权限，且都不依赖同名 Profile 漂移、动态 Role 名称或自然语言 `read-only` 推断；
25. Stage 1 不提供删除审计目录的 `clean` 命令；
26. Handoff 判断不能覆盖宿主高优先级指令或对当前工作区的直接检查结果；
27. Origin Binding 不执行 Capability Probe，也不创建 Worker / Session Runtime 或伪 Launch Profile；
28. Kickoff 前崩溃不会留下 Worker，Kickoff 后崩溃可由同 Run `start` / `recover` 补建 Worker；
29. 每个业务 Turn 只有一次 External 启动；`start_failed` 直接 Block，`start_unknown` 也不会启动第二个 Harness；
30. Supervisor 先以 `state=starting` 和空 Runner 三元组自持久化，Runner 再在独立进程组中自持久化并等待许可；各 Supervisor 状态的不变量可校验，任一崩溃边界都不会产生身份未落盘却已执行的 Harness；
31. Worker 崩溃后的 Outbox 补交只使用已冻结并校验 Hash 的 Before / After Facts；
32. 第 `max_turns` 个业务 Turn 仍可 Complete，但不能把 Handoff 提交给第 `max_turns + 1` 个 Turn；
33. 目标 Worker 或 Origin Turn 不在线时仍可提交合法 Handoff，恢复后只能领取同一个 Event；
34. 已知异常退出且 Runner 进程组已清空时直接 Finalize 并 Block，不设置 `recovery_required`；
35. 任何 Block 都先返回用户，没有新的明确用户指令就不能 Resume；
36. 主 Harness 退出后仍有普通后台子进程时，组外 Supervisor 会先清空已记录 Runner 进程组；CLI 启动器自身会立即脱离时 Probe 拒绝，运行时观察到明确逃逸证据时不交付 Outbox，同时文档明确未观测逃逸超出 Stage 1 保证；
37. tracked 文件删除以及 tracked 文件被目录替换都使用稳定的 `missing` 记录参与 Fingerprint；目录下的新文件独立记录，Sparse Checkout 和 Gitlink 不会被误判为删除；Facts 明确标记 `snapshot_scope=git_visible`，不声称覆盖 ignored 文件；
38. Kickoff、Handoff 和 Resume 都冻结为 `input.md`，Resume 指令在限定范围内具有明确优先级，改变不可变输入时拒绝继续旧 Run；
39. Owner 临时文件崩溃不产生 Ownership，最终 Owner 文件不存在或完整存在，不会出现已提交空目录；
40. Supervisor 不属于 Runner PGID，强制 `killpg` Runner 进程组时仍能写入最终快照；
41. 多个 Origin Role 被明确视为共享同一个宿主上下文；要求独立 Review 或盲评时，Bootstrap 不会把共享 Origin Binding 误报为上下文隔离。
42. `origin-complete` / `origin-block` 返回后旧 Origin Runtime 保持 `exited` 且 Ownership 不会提前释放；后续用户 Agent Turn 的 `wait-origin` 才收口 Runtime。
43. Wall Time 对 External Runner 可异步强制，对 Origin 只在 CLI / Skill 边界协作式生效；状态和文案不会把后者误报为已被宿主强制中断。
44. 环境变量、项目配置或 CLI 参数都不能改变当前账号的固定状态目录；删除 Workspace State Root 后重新 `init` 仍会在同一操作锁下发现旧 Owner 并拒绝创建第二个 Run。
45. Adapter Probe 失败或 `init` 中途崩溃时不出现最终 Run 目录；包含 Request / Protocol / Team 与 `journal.lock` 的完整临时目录只能通过一次原子目录提交变成 UNSTARTED Run。
46. Workspace 或状态目录缺少所需的进程间 `flock`、同目录原子 `rename` 或 `fsync` 语义时，Kickoff 前明确拒绝。
47. 已存在但损坏的 Runner 身份或启动许可直接使 Run 进入 `CORRUPTED`，不会被当作文件缺失而创建第二个 Runner / Harness。
48. 首次 Git 可见 Snapshot 无法完整采集时，`start` 在 Kickoff 前失败并释放精确 Owner，不产生一个从未执行就 Corrupted 的 Run。
49. 同一底层 Snapshot 经 Status、Diagnose、Watch 或文本渲染得到相同的 Run Status、Health 与 Recommended Action；观察本身不写文件、不创建 Claim，也不更新任何业务快照或文件 mtime。
50. `diagnose` 只返回固定检查状态、诊断码、证据路径和技术建议；Pane、普通日志和运行时长不能被解析成完整性或进程安全结论。
51. Watch 每轮都输出完整 Snapshot；重启不要求 Cursor 或补写历史，Watch 输出丢失不影响 Journal 与 Runtime。
52. Worker 日志记录具有稳定关联字段；stdout / stderr 的有效 UTF-8 和任意非 UTF-8 字节都能从 `stream.jsonl` 无损恢复，并保持各来源内部字节顺序与 Supervisor 记录的观察顺序。
53. 结构化观察模式的 stdout 只含一个 JSON 或逐行 JSONL；可确定的 Blocked / Cancelled / Corrupted 报告使用成功信封，接口失败才使用固定错误码和非零退出码。

### 30.3 产品假设验收

通过真实任务评估：

- Agent 是否能正确理解动态团队协议；
- Handoff 时机是否合理；
- Handoff 目标是否正确；
- Handoff 内容是否足以接手；
- 多轮 Resume 是否显著优于每轮新建 Agent；
- 不同 Harness 是否能形成互补；
- 最终结果是否优于单 Agent 基线。

---

## 31. 测试方案

## 31.1 单元测试

- Run ID 和 Role ID；
- `team.json` 的 Run 目录、State Root Workspace、非空 Roles、Initial Role 和固定 Origin Mode 交叉校验；
- Protocol 模板区分宿主/Agent-Team 指令层级与事实证据层级；
- 不可变 Event 原子提交；
- Event 序号、Tail 链接和 Payload Hash；
- Journal 完整性失败直接推导 `CORRUPTED`，且不能再追加 Event；
- 六种 Event 的允许前置状态、必填字段、结果状态和 Token Owner；
- `profile_changed` Payload 的冻结 / 当前 Fingerprint 证据及其与 `limit` 一样不可 Resume 的转换守卫；
- 从 Journal / Turn Runtime 推导 Run Status、Token Owner 和 Inbox；
- Observation 从同一文件与进程快照唯一派生 Health、Managed Process State、Block Resume Policy 和 Recommended Action，Status / Diagnose / Watch 共享结果且不产生写入；Session 尚未创建、Owner 尚未获取和终态 Owner 遗留都有唯一映射；
- 观察 JSON 信封、字段闭集、诊断码、错误码与退出码；文本输出只是同一对象的渲染；
- Event 与所有权威 Run 技术快照写入都取得 `journal.lock` 排他锁，Observation 取得共享锁；日志、Raw Stream 和 `output.md` 不取该锁，也不参与状态推导；
- Turn Runtime 状态转换、不可变 `created_at` 与不依赖文件时间的 `active_turn.age_seconds`；
- `started|start_failed|start_unknown` 三态启动结果，失败和未知都不在同一 Turn 重试；
- `business_turn_seq` 连续且与 Event 输入链一致，`max_turns` 只统计业务 Turn，管理 Turn 不计数；
- `can_create_business_turn` 在 Handoff Candidate、Handoff / Resume Event 提交和目标 Claim 三处使用相同计算；
- Journal Tail Claim；
- 匹配 Origin Claim 的重复领取返回同一 Turn；
- 无 Claim 的第二个 Origin Session 被拒绝，Stage 1 不存在 Claim Takeover；Claim 丢失后只允许只读诊断、Cancel 和安全新建 Run；
- 业务 Claim 与 Blocked 管理 Claim 的权限隔离；
- 单 Turn 单终止动作；
- `turn_id` 重复提交与冲突内容拒绝；
- Outbox Payload 在 Candidate 创建时复制并固定 Hash，原始 `--file` 后续变化不影响交付；
- Event 已提交但 Turn Runtime 未收口时只补写 `finalized`；
- State Root、Worker / Turn Runtime、Supervisor、Runner、Input、启动许可、Before / After Facts、Outbox Payload、Outbox、Session 和 Owner 文件的原子替换与损坏处理；损坏 Session 快照总是推导为 `CORRUPTED`，只有有效快照才能被 Adapter 标记为 unavailable；
- Turn Runtime、Outbox 或 Facts 损坏时，活跃 Turn 可唯一推导才允许 Recovery Block，否则直接 `CORRUPTED`；
- Supervisor `starting` Schema 及状态不变量，以及 Supervisor 身份提交、Runner 创建、Runner 身份提交、启动许可提交和 `exec` 五个崩溃边界的恢复判定；
- 启动许可在所有证据均为初始值时缺失则 Harness 绝不创建；许可已被证据证明消费后缺失、有效存在后损坏，或 Runner 身份被引用后缺失都直接 `CORRUPTED`；恢复过程绝不再次授权或启动；
- External Runtime、LaunchSpec、启动许可、Team 配置和 Session 快照中的 Profile 名称 / Hash 必须一致，Origin 字段必须为 `null`；
- Workspace Ownership 单文件原子获取、提交前临时文件崩溃、同 Run 幂等恢复、跨 Run 冲突、安全释放和操作锁顺序；State Root 提交后 Workspace 锁及最终 Run 的 `journal.lock` 只能 no-create 打开，缺失时直接 `CORRUPTED`；已有 Kickoff 后 Owner 丢失或错配不能自动重建；
- 所有 Role 使用同一 Git Worktree 根目录；固定 Workspace 操作锁下的 State Root 创建、复用、固定状态目录校验、可配置路径拒绝和 Git tracked 拒绝；`init` 临时 Run 目录的整目录原子提交与崩溃清理；非 Git / 子目录 / Sparse Checkout / Gitlink / per-role CWD 被拒绝，且 Sparse / Gitlink / State Root 在每次边界重新验证；Git 可见 Snapshot 排除 ignored 路径、`.agent-team/` 与 Git 元数据，`init` 不改写 Git ignore 元数据；
- tracked 删除和 tracked 文件被目录替换都编码为 `missing`，目录下的新文件独立记录；untracked 消失和不支持的文件类型使 Snapshot 明确失败；ignored 文件变化不改变 Fingerprint，Facts 与交付文案必须明确其不在保证范围内；
- Kickoff 后 Before Facts 在 Runtime 提交前失败时直接 `CORRUPTED`，已有 Turn 的 After Facts 失败时生成 Recovery Block；Deadline-before-Claim 的技术 Turn 是唯一允许 Before Hash 为 `null` 的业务 Turn；
- Workspace Facts、Session、Owner、Runtime、Supervisor 与 Runner Schema 校验，Session Generation 只能由当前 Turn 前进；
- 同一业务文件状态重复采集得到相同 `workspace_state_sha256`，时间戳和 Run Store 变化不影响等价判断；
- Kickoff 前 Snapshot 可行性失败时没有 Event / Worker，且精确 Owner 被安全释放；
- 普通 Handoff 的来源 After 与目标 Before 必须相等；缺少可信 After 的技术恢复 Turn 标记 `WORKSPACE_CONTINUITY_UNKNOWN`；
- `unlock` 与同 Run `recover` / 其他 Run `start` 的竞态；
- State Root 损坏时 `unlock` 仍只从固定状态目录接受 Workspace Hash 与预期 Run 都匹配的既有 Owner，不能通过参数定位、创建或删除第二个 Namespace 中的 Owner；
- PID / PGID / Start ID 匹配、PID 复用、启动前能力拒绝和运行中 Start ID 无法查询；
- Running、Blocked、Origin Turn 的直接 Cancel Event 与单 Cancel Event；
- Cancel / Deadline 在启动许可提交前后与 Worker 崩溃并发时都没有控制半状态或第二个 Harness；
- Deadline 与 Max-Turn 同时成立时只提交 Deadline Limit Block，已提交 Limit 原因不可改写；
- `recover` 不生成 Resume Event；`origin-resume` 根据管理 Claim、当前 Block Tail 及其 Payload 路径/Hash、已校验目标角色和用户指令文件，唯一生成含 `continue_same_run` Scope 的 Resume Payload；
- Kickoff / Handoff / Resume Payload 都冻结为 `input.md`；Resume Input 的优先级正确，改变不可变目标或配置时不调用 `origin-resume`；
- 没有新的明确用户指令时，所有 `block_reason` 都拒绝 Resume；
- 未解除的 `recovery_required` 会拒绝 Resume、Origin 业务 Claim 和新 Harness 启动；
- 路径穿越防护；
- Workspace 与状态目录文件系统能力探测及不支持时的初始化拒绝；
- tmux `wait-for` / `capture-pane` 命令参数与通知失败降级；
- Watch 每轮完整 Snapshot、两秒非重叠轮询、进程内 Sequence、终态退出和无 Cursor 重启；
- 合法 Handoff 不依赖目标 Worker、Window 或 Origin Turn 在线；
- Adapter 通过同一确定性 Framer 把任意拆分或合并的 Raw Stream Chunk 还原为完整 `StreamRecord`，再由 `parse_stream_record()` 归一化固定技术证据，并通过 `classify_result()` 对持久化 Supervisor Result 分类；
- Worker 日志关联字段与 `producer_seq` 重启语义；Raw Stream 外层对 UTF-8 / 非 UTF-8 字节的可逆编码、来源、Supervisor 观察顺序和观测时间，以及 Evidence Snapshot 前的持久化顺序；
- Adapter 正常启动 / 完成证据、未知记录忽略与 `termination_kind` 分类；
- 启动期和执行期的结构化 `permission_required` 都映射为唯一 Permission Block；已有 Cancel / Limit 时不追加；
- Session Ref、Generation、`effective_launch_profile` 及其 SHA-256 持久化；
- Origin / External Binding 的互斥字段校验，Origin 不执行 Probe 或创建 Worker Runtime；多个 Origin Role 共享宿主上下文，独立验证要求不能错误映射到共享 Origin；
- External Adapter Probe 的 Launch Profile 闭集、可执行版本与规范化映射 Hash、权限相关键显式覆盖、用户默认值扰动，Fresh Role 的 Start 映射，以及 Resume Role 的 Start / Resume 确定性映射与权限等价；
- Supervisor 位于 Runner 进程组之外；主 Harness 退出后普通后台子进程的宽限、`killpg` 终止与 `group_quiescent` 判定，Probe 拒绝自身立即脱离的 CLI 启动器，并验证 `group_quiescent` 只表示已记录 PGID 清空；
- 已知异常退出直接 Finalize，只有活进程、未知身份或未清空进程组进入 `recovery_required`；
- Kickoff 后 Request / Protocol / Team 配置不可变，任一 Hash 不匹配在有无活跃 Turn 时都直接 `CORRUPTED`；
- 第 `max_turns` 个 Turn 的 Complete / Block 与 Handoff 边界、Wall Time Claim 竞态和 Limit Block 禁止 Resume。
- `origin-complete` / `origin-block` 后 Runtime 的 `exited → finalized` 跨用户 Agent Turn 收口，以及收口前 Ownership 保留；
- External Wall Time 的异步终止与 Origin Wall Time 的下一 CLI 边界生效语义。

## 31.2 Adapter 集成测试

### Claude Code

- 首次指定 Session ID；
- Resume 同一 Session；
- Stream JSON；
- 进程取消；
- Start / Resume Launch Profile 参数映射及有效权限等价；
- 改变用户默认权限配置后，显式 Launch Profile 的有效权限不漂移；
- 改变 Adapter / CLI 版本或规范化映射后，旧 `launch_profile_sha256` 被拒绝；
- 权限不足；
- Session 不存在时先 Start Failure Block，再由显式 Resume 降级为 Fresh。

### Codex

- `thread.started` 解析；
- `codex exec resume`；
- Start / Resume 分离的 Launch Profile 参数映射、有效权限等价与 Probe 拒绝未知 Profile；
- 改变用户默认权限配置后，显式 Launch Profile 的有效权限不漂移；
- 改变 Adapter / CLI 版本或规范化映射后，旧 `launch_profile_sha256` 被拒绝；
- Resume 路径不假定接受 Start 形式的 `--sandbox` 参数；
- 权限选择不依赖动态 Role 名称；
- `PROTOCOL.md` 的 `read-only` 不自动映射为 Harness 沙箱；
- JSONL；
- Turn failure；
- Git Worktree 根目录限制，且不传 `--skip-git-repo-check`。

## 31.3 端到端用例

1. Origin Reviewer + Claude Developer；
2. Claude Planner → Codex Developer → Claude Reviewer；
3. A ↔ B 循环五轮；
4. Reviewer 退回同一 Developer Session；
5. 外部 Completion Authority 返回 Origin；
6. 初始角色绑定 Origin；
7. tmux 变更提示丢失后由周期扫描启动；
8. 运行中 Cancel 与 Deadline；
9. Harness 正常完成后 Worker 崩溃可补做 Finalize；已知异常退出且 Runner 进程组已清空时直接 Finalize 为 Recovery Block，只有执行身份或清空状态不确定时进入 `recovery_required`；
10. Event 提交各崩溃点的故障注入；
11. tmux Server 重建；
12. Origin Handoff 和 Origin Resume 提交并等待；
13. 两个 Run 争用同一 Workspace；
14. `unlock` 与同 Run `recover`、其他 Run `start` 并发；
15. tmux 丢失后 Ownership 仍阻止其他 Run、同 Run 可恢复，并可在确认无活进程后显式 Unlock；
16. `embedded_poll` Origin Turn 中断后用原 Claim 恢复；
17. 两个 Origin Session 竞争同一 Turn 时第二个被拒绝；Claim 丢失后没有 Takeover 路径，只能 Cancel、确认旧 Turn 停止并创建新 Run；
18. Running、Blocked 和 Origin Turn 分别执行 Cancel；
19. Deadline 直接提交唯一 Limit Block，不生成额外持久化请求或待补交状态；
20. 无 Handoff 异常；
21. 错误目标角色；
22. 第 `max_turns` 个 Turn 可以 Complete；请求 Handoff 时改为产生不可在原 Run Resume 的 Limit Block，且不会创建第 `max_turns + 1` 个 Turn；
23. Handoff 后修改原始 Payload 文件，接收方仍读取 Outbox 创建时的副本；
24. `prepare_launch` 失败、Runner 许可超时和已知 `exec` 失败都一次产生 Start Failure Block；同一 Turn 不重试，`start_unknown` 一次即进入 Recovery Block，且仅在旧 Runner 进程组未证明清空时设置恢复门禁；
25. 恢复时构造已复用 PID，确认不会终止无关进程；
26. 非 Git 目录、Worktree 子目录、被 Git 跟踪或无法验证的 `.agent-team/`、Sparse Checkout、Gitlink 或 `team.json` 包含 per-role CWD 时拒绝启动；tracked 删除和 tracked 文件被目录替换都稳定编码为 `missing`，Run Store 与 ignored 文件变化不触发 Git 可见 Snapshot 偏差，Facts 明确披露范围，`init` 前后 Git ignore 元数据字节不变；
27. Runtime / Outbox / Facts 损坏但 Turn 身份唯一时产生 Recovery Block，Turn 身份不唯一时进入 `CORRUPTED`；Session 快照损坏无条件进入 `CORRUPTED`，有效 Session 的结构化 unavailable 才允许后续 Fresh 降级；
28. 分别在 Supervisor 身份提交、Runner 创建、Runner 身份提交、启动许可提交和 Runner `exec` 后注入崩溃，确认无许可时零 Harness、有许可时至多一个 Harness；
29. 未知 Profile、Profile Hash 漂移、Fresh Role 缺少 Start 映射，或 Resume Role 不能在 Start / Resume 保持等价权限时明确失败；Kickoff 后漂移生成不可 Resume 的 Profile Changed Block，`origin-resume` 拒绝，接受新 Profile 必须创建新 Run；
30. Origin Binding 不接受 External 字段，不执行 Probe，也不产生 `roles/<role>.json` 或 `sessions/<role>.json`；纯 Origin Run 不依赖 tmux；
31. 分别在 Owner 临时文件、Owner 原子提交、Kickoff 和 Worker 创建后注入崩溃：临时文件不占有 Workspace，Kickoff 前只能用重复 `start` 继续，Kickoff 后可用 `start` / `recover` 收口，且始终只有一个 Kickoff 和每个 External Role 一个 Worker；
32. 正常退出后、After Facts 提交前崩溃时只产生 Recovery Block；After Facts 已冻结且 Hash 匹配时允许补交 Outbox；
33. Wall Time 在 Kickoff / Handoff / Resume 后、目标 Claim 前到期时不启动 Harness，并由目标 Turn 立即提交 Limit Block；
34. 目标 External Worker、tmux Window 或 Origin Turn 不在线时提交 Handoff，重建或继续原 Session 后只领取一次；
35. Agent Block、结构化权限证据生成的 Permission Block、Start Failure Block 与 Recovery Block 都先展示给用户；没有新用户指令时 Origin 不会自动 Resume；
36. Harness 主进程退出但普通后台子进程仍存活时，组外 Supervisor 在清空已记录 Runner 进程组前不交付 Outbox；CLI 启动器自身立即脱离进程组的 Fake Adapter 在 Probe 阶段被拒绝，且测试不把 `group_quiescent` 解释为未观测逃逸进程的证明；
37. Cancel 与 Deadline 分别在启动许可前后触发，确认终止 Event 唯一、对 Runner PGID 的强制终止不会杀死 Supervisor、最终 `group_quiescent=true` 且没有重启 Harness；
38. Workspace Facts、Session Generation、Effective Launch Profile 及其 SHA-256、Runner Identity 和单文件 Owner Metadata 在崩溃恢复前后通过 Schema 与 Hash 校验；
39. Handoff 后、目标 Claim 前发生外部业务文件修改时不启动目标 Harness；缺少可信 After 的技术恢复经用户授权后以未知连续性新基线启动；
40. Resume 到 External 和 Origin Role 时都把完整 Resume Payload 冻结为 `input.md` 并注入 Prompt；要求改变目标、角色、Profile 或上限时取消旧 Run 而不是 Resume；
41. 两个逻辑 Role 绑定同一 Origin Session 时明确共享上下文；要求独立 Reviewer 时使用 External Session，不把逻辑角色切换误当作隔离。
42. Kickoff 后分别改写 Request / Protocol / Team、删除或替换 Owner，并在 Token 未 Claim 与 Turn 活跃两种窗口恢复：两者都只进入 `CORRUPTED`，不会伪造 Block、重建 Owner 或启动 Worker；活跃 Supervisor 只清理已验证进程组。
43. Run 中途开始跟踪 `.agent-team/`、启用 Sparse Checkout、加入 Gitlink 或破坏 `.agent-team/root.json` 时，下一边界不生成部分 Snapshot，直接进入 `CORRUPTED`。
44. Origin Role 调用 `origin-complete` / `origin-block` 后，确认旧 Runtime 保持 `exited`、同一 Agent Turn 不再调用工具且 Owner 保留；后续用户 Agent Turn 的 `wait-origin` 才 Finalize。
45. 同一短 Wall Time 下，External Runner 被异步终止；不再调用 CLI 的 Origin Turn 不被误报为已强制停止，并在下一 CLI 边界产生 Deadline Limit Block。
46. 环境变量、项目配置和 CLI 状态目录参数均被拒绝；删除 `.agent-team/` 但保留固定目录中的活跃 Owner 后再次 `init`，仍在同一 Workspace 操作锁下明确失败，不能创建第二个 Run 或 Owner。
47. 分别在 Adapter Probe、Request / Protocol / Team / `journal.lock` 写入和最终 Run 目录 rename 前后注入失败：提交点前没有最终 Run，提交点后恰好有一个完整且可解析的 UNSTARTED Run。
48. Fake Filesystem Probe 分别报告 `flock`、原子 rename 或 `fsync` 不可用时，`init` 不创建最终 Run，`start` 不提交 Kickoff。
49. 分别损坏已存在的 `runner.json` / `launch-authorized.json`，以及在身份被引用或许可消费证据出现后删除它们：恢复只进入 `CORRUPTED` 并清理可验证进程，不把它们视为首次缺失，也不产生第二次授权。
50. 在首次 Snapshot 中放入不支持的文件类型、消失的 untracked 路径或嵌套仓库目录：`start` 释放本 Run Owner 并拒绝 Kickoff，修正后可对同一 UNSTARTED Run 重试。
51. Kickoff 后分别让 Before 与 After Snapshot 失败：Before 不伪造 Turn / Block 而进入 `CORRUPTED`，After 使用既有 Turn 产生 Recovery Block；合法 Event 后 Deadline 先到则创建唯一的无 Before Facts 技术 Turn。
52. 损坏 `root.json` 后，`unlock` 仍能从固定状态目录定位 Owner；只有 `--expect-run` 匹配且所有身份清空时可删除精确 Owner，错误 Run 或活进程时均拒绝。
53. 对可启动 UNSTARTED、被其他 Owner 阻塞的 UNSTARTED、正常 Running、可恢复运行时缺失、Origin Unclaimed、Origin Exited、Blocked、Completed、Cancelled、终态清理仍活跃、终态进程已结束但 Owner 遗留、Recovery Required 和 Corrupted 分别调用 Status / Diagnose，确认 Health、Resume Policy 与 Recommended Action 的固定映射一致；终态 Owner 遗留建议 `RUN_RECOVER`，执行后只安全释放 Owner，不产生 Worker 或 Event。
54. 分别制造 Owner、Journal、Turn、Runner 身份、Session 和 Workspace Facts 故障：`diagnose --json` 返回固定码、检查对象与可用证据路径，不解析 Pane / 普通日志，也不调用 Recover。
55. 在 Running、Blocked 和 Completed 状态启动、断开并重启 `watch --jsonl`：每轮都是完整当前 Snapshot，Sequence 仅在当前 Watch 进程内递增；Completed 的正常清理阶段保持 `WAIT`，安全收口后的最后一行再正常退出。
56. Harness 以任意 Pipe Read 边界交错写入 stdout / stderr、多条或拆分的 JSONL、合法 UTF-8 和任意二进制字节后，`stream.jsonl` 每行仍是合法 JSON，可无损恢复各来源字节并按 Sequence 重放 Supervisor 的观察顺序；Framer 只把完整 Harness 行交给 Adapter，测试不声称恢复两个 Pipe 在 Harness 内的绝对发出顺序。运行中把 Stream 路径替换为符号链接不能重定向持有 FD 的写入，inode 复核失败时不交付 Outbox。
57. 在 Handoff、Cancel、Deadline 和终态 Owner 释放并发窗口持续执行观察命令：每份报告都对应一个锁内 Snapshot，观察不产生 Event、Claim、Runtime 更新、锁文件或第二份状态；删除既有 Workspace / Run 锁时不由观察命令补建，显式 Run 查询返回最小 Corrupted 报告，省略 Run ID 且无法安全解析 Owner 时返回 `OBSERVATION_IO_ERROR` / Exit `4`。

## 31.4 Handoff 质量评测

每次 Handoff 标注：

### 时机

- 正确；
- 过早；
- 过晚；
- 遗漏；
- 重复。

### 路由

- 正确目标；
- 错误目标；
- 本应完成却继续；
- 本应返回却跳过。

### 内容

- 目标和职责是否清楚；
- 修改/产物是否完整；
- 是否包含实际证据；
- 是否暴露不确定性；
- 下一步是否可执行；
- 下一个 Agent 是否需要额外追问。

### 偏见

- Reviewer 是否过度相信 Developer 自述；
- QA 是否被 Review 通过锚定；
- 接收方是否忽略原始请求；
- 多轮后是否出现语义漂移；
- 新 Session Reviewer 与 Resume Reviewer 的差异。

## 31.5 基线对比

- 单 Codex；
- 单 Claude Code；
- 同一 Harness 内 Subagent；
- Agent-Team 跨 Harness 团队。

指标：

- 最终验收通过率；
- 缺陷数量；
- 人工干预次数；
- 任务总耗时；
- Token / Cost；
- Handoff 循环数；
- 失败恢复能力。

---

## 32. Stage 2 升级设计

Stage 1 的真实失败会驱动 Stage 2，而不是提前一次性构建所有机制。

## 32.1 自然语言编译为 Workflow IR

```yaml
roles:
  developer:
    harness: claude-code
  reviewer:
    harness: codex

transitions:
  - from: developer
    event: implementation_ready
    to: reviewer
  - from: reviewer
    when: blocking_findings_exist
    to: developer
  - from: reviewer
    when: no_p0_to_p3_findings
    to: completed
```

## 32.2 Typed Handoff

增加：

- 业务 `handoff_kind`；
- `candidate_revision`；
- `claims`；
- `verified_evidence`；
- `open_findings`；
- `requested_action`；
- `idempotency_key`。

## 32.3 状态内核

由 Markdown / JSON 文件升级为 SQLite：

- Run；
- Role Runtime；
- Turn；
- Event；
- Lease；
- Handoff；
- Evidence；
- Session；
- Artifact；
- Budget。

## 32.4 Transition Guard

- 当前状态是否允许该转换；
- 当前角色是否有权提交；
- Candidate SHA 是否一致；
- Review 是否针对当前版本；
- QA 是否测试已批准版本；
- Completion Authority 是否正确；
- 必需证据是否存在。

## 32.5 Hook 强制

利用 Harness Hooks：

- 被协议限制为“只审查不修改”的动态 Role 禁止写业务文件；
- Handoff 后禁止继续修改；
- 自动采集测试命令；
- Agent Stop 时校验正式终止动作；
- 权限提示统一回 Origin。

## 32.6 并行拓扑

引入：

- 多 Token；
- Branch；
- Join；
- Worktree；
- Merge Role；
- 冲突处理；
- 部分失败和补偿。

## 32.7 结构化宿主集成

只有在真实产品需求证明短轮询不足后，才为具体 Harness 增加：

- Codex App Server、SDK 或等价结构化长连接；
- 明确的 Turn Start / Complete / Failed 事件；
- 宿主审批、Steer 和 Queue；
- 已结束 Origin Turn 的宿主唤醒能力；
- Capability Probe 和降级到 Stage 1 `embedded_poll` 的规则。

该能力不得通过 tmux `send-keys` 或 Pane 文本解析模拟。

---

## 33. 风险与权衡

## 33.1 自然语言协议可能被误解

这是 Stage 1 主动保留的风险，也是需要验证的核心假设。

缓解：

- 保留原始请求；
- 协议采用固定 Markdown 章节；
- 记录 Bootstrap 假设；
- Agent 必须显式写 Protocol Basis；
- 完整保存失败案例，为 Stage 2 IR 设计提供数据。

## 33.2 Agent 可能错误 Handoff

Stage 1 不做语义 Transition Guard。

缓解：

- 显式目标角色；
- 单执行 Token；
- 通用安全上限；
- Handoff 质量评测；
- Block 返回 Origin。

## 33.3 Agent 可能忘记调用 CLI

缓解：

- Coordination Skill 多次强调；
- 每 Turn Prompt 明确要求；
- Harness 退出无正式动作时不自动推进；
- 返回同一 Session 恢复。

## 33.4 Origin 会话可能无法长时间等待

缓解：

- 短超时 `wait-origin` 循环；
- Team Run Event 持久化；
- `origin-handoff` 将提交与等待合并，避免 Token 已转移后 Origin 继续工作；
- Origin Turn 使用无自动过期的显式 Claim，第二个 Session 默认拒绝；Claim 丢失时取消旧 Run 并安全新建 Run，不在 Stage 1 提供接管；
- 明确声明 Stage 1 只有 `embedded_poll`，不承诺跨 Turn 自动唤醒；
- 用户可中断并在原会话继续，所有待处理 Event 仍保存在 Journal；
- Stage 2 再通过结构化宿主 API 增加自动唤醒。

## 33.5 tmux 不是跨平台运行时

Stage 1 明确支持 macOS 和 Linux。Windows 可通过 WSL，原生 Windows 不在首版范围。

## 33.6 进程组不是容器

`group_quiescent=true` 只证明已记录的 Runner PGID 没有成员。Stage 1 无法证明角色执行的任意命令都不会主动创建逃逸 daemon，也不能可靠枚举已经脱离并重新托管的所有后代进程。

缓解：

- Coordination Skill 明确禁止角色创建逃逸进程；
- Probe 拒绝 CLI 启动器自身立即脱离的 Adapter；
- Supervisor 清理同一 PGID 内的普通后台子进程；
- 观察到明确逃逸证据时 Recovery Block 并保留 Ownership；
- Ownership 自动释放和 Unlock 都明确显示这一边界；
- 需要强进程 containment 时使用后续 cgroup、容器或宿主级作业对象，不在 Stage 1 伪造保证。

## 33.7 同一工作区并发修改

Stage 1 的串行 Token 只约束一个 Run。持久化 Workspace Ownership 进一步阻止两个 Agent-Team Run 并发，但不能阻止用户、IDE 或其他程序修改文件。

缓解：

- 在稳定操作锁内通过原子 `rename` 提交单文件 Workspace Ownership；
- 所有 Role 使用同一规范化 Workspace；
- `start`、`recover`、释放和 `unlock` 共用稳定的短期 per-workspace 操作锁；
- 不按超时自动抢占，过期记录只能在确认无活进程后显式解除；
- 每 Turn 前后采集排除 ignored 路径、Run Store 和 Git 内部元数据的 Git 可见 Snapshot，并在 Facts 中固定披露范围；
- ignored cache 和构建输出可以变化，但不能作为已经过跨 Turn 连续性验证的交付物；
- 无法归属的变化立即 Block；
- 明确要求运行期间不要手工并发编辑；
- Stage 2 Hooks 和 Worktree。

## 33.8 会话越长可能上下文漂移

缓解：

- 团队事实独立保存；
- 每 Turn 重新注入协议和当前 Input Event；
- 可配置 `fresh`；
- 后续引入角色记忆摘要和 Session Fork。

## 33.9 Origin 没有宿主级执行边界

Stage 1 无法强制停止当前宿主模型采样，也无法从 CLI 返回推断 Agent Turn 已结束。因此 Origin 的 Wall Time、终止后不再写入以及最终 Runtime 收口都是显式协作边界。

缓解：

- 所有 Origin 写动作都在 CLI 边界重新检查 Claim、Deadline、配置完整性和 Workspace Facts；
- Handoff / Resume 在同一调用中继续等待，不把失去 Token 后的执行窗口返回给 Agent；
- Complete / Block 必须是当前 Agent Turn 的最后一个工具调用，Runtime 先停在 `exited`；
- 下一次用户 Agent Turn 才 Finalize 旧 Runtime 并允许安全释放 Ownership；
- 需要强制执行时等待 Stage 2 宿主 API，不用 tmux 或自然语言伪装成技术保证。

---

## 34. 推荐实施顺序

### Milestone A：最小运行时

- Run Store；
- 不可变 Event Journal；
- Event 技术转换表；
- Workspace Ownership 与短期操作锁；
- External Binding 的 tmux Session；
- External Binding 的异步 Worker；
- 组外 Turn Supervisor、独立进程组 Runner 与单次启动许可；
- `handoff` / `complete` / `wait-origin`；
- 手工编写 `PROTOCOL.md`；
- Fake Harness Adapter。

### Milestone B：Claude Code + Codex Adapter

- Session start/resume；
- JSONL 日志；
- Start / Resume Launch Profile 等价映射与 Fingerprint；
- 取消与异常退出；
- Before / After System Facts。

### Milestone C：Bootstrap Skill

- Codex Skill 安装；
- 自然语言团队提取；
- 动态协议生成；
- Origin Kickoff 分流；
- Origin Claim；
- `origin-handoff` / `origin-resume` 提交并等待；
- Origin Event Loop；
- 最终交付。

### Milestone D：稳定性

- tmux 重建；
- Session 恢复；
- Journal 完整性扫描；
- 可变 JSON 快照原子替换；
- Before / After Facts 冻结与恢复；
- Outbox Payload 冻结与 Hash；
- Kickoff 前后崩溃恢复；
- Workspace Ownership 恢复与安全 Unlock；
- PID / PGID / Start ID 验证；
- Turn Runtime 恢复判定；
- Cancel / Deadline 直接 Event 与 Runner 进程组清理；
- Max-Turn 创建守卫；
- tmux 通知丢失降级与 `capture-pane` 诊断；
- 共享 Observation 派生、结构化 Status / Diagnose / Watch 与关联日志；
- 崩溃点故障注入；
- 限额；
- Doctor；
- E2E 测试。

### Milestone E：实验和评测

- Handoff 时机；
- Handoff 内容；
- 偏见；
- 单 Agent 对照；
- 真实项目任务。

---

## 35. 完整示例：用户给出的 Stage 2 场景

## 35.1 用户输入

```text
落地 Stage 2。你作为 Reviewer，一个 Claude Code 作为 Developer。
Developer 每轮的修改要交由 Reviewer 审查，Reviewer 只审查不修改。
审查意见给回 Developer 判断是否合理；合理就接受并进行下一轮修改，
不合理要提供理由。循环直到 Reviewer 没有 P3 以上问题。
最后仍然由当前 Codex 交付结果。
```

## 35.2 Bootstrap 生成的 `team.json`

```json
{
  "schema_version": 1,
  "run_id": "at-stage2-7f3a",
  "workspace": "/repo/project",
  "origin": {
    "harness": "codex",
    "session_mode": "embedded"
  },
  "roles": {
    "reviewer": {
      "binding": "origin"
    },
    "developer": {
      "binding": "external",
      "adapter": "claude-code",
      "session_policy": "resume",
      "launch_profile": "default",
      "launch_profile_sha256": "..."
    }
  },
  "initial_role": "developer",
  "limits": {
    "max_turns": 20,
    "max_wall_time_seconds": 7200
  }
}
```

## 35.3 Bootstrap 生成的 `PROTOCOL.md`

```markdown
# Agent Team Protocol

## Original objective

落地仓库现有规划中的 Stage 2。

## Source of truth

用户原始请求、仓库中的 Stage 2 规划、当前工作区、实际 Diff 与测试结果。

## Team roles

### developer

- Binding: external
- Harness: Claude Code
- Session policy: resume
- 可以修改代码和运行测试。
- 每轮完成修改后交给 reviewer。
- 收到审查意见后逐项独立判断。
- 合理意见应接受、修复并验证。
- 不合理意见应给出代码、测试或设计依据。
- 不得自行宣布整个任务完成。

### reviewer

- Binding: origin（当前 Codex Origin Session）
- 只审查，不修改业务代码。
- 每轮审查当前完整实现，而不只检查上一轮 Finding。
- 问题使用 P0、P1、P2、P3、P4 标注。
- 本协议采用：P0 至 P3 为阻塞，P4 为非阻塞。
- 有开放 P0 至 P3 时交回 developer。
- Developer 对 Finding 有质疑权，Reviewer 需要重新评估证据。
- Reviewer 对最终质量门有判断权。

## Initial role

developer

## Collaboration protocol

1. Developer 完成一轮修改并运行合理检查后 Handoff 给 Reviewer。
2. Reviewer 审查当前完整代码、Diff、测试和原始需求。
3. 存在 P0 至 P3 问题时，Reviewer 将 Finding 交回 Developer。
4. Developer 独立判断并处理后，再次交给 Reviewer。
5. Developer 产生任何新修改后都必须重新 Review。
6. 同一争议在没有新证据时连续两轮不能解决，应 Block 返回用户。

## Completion condition

Reviewer 确认当前实现没有开放 P0、P1、P2 或 P3 问题，并完成最终检查。

## Final delivery

Reviewer 即 Origin Codex，在完成后向用户交付实现结果、Review 轮次、验证结果和剩余 P4。

## Session continuity

- developer 恢复同一个 Claude Code Session。
- reviewer 使用当前 Origin Session，不创建外部 Session。

## Shared context policy

两个角色都读取原始请求、协议、当前工作区和当前 Input Event。该输入可能是 Handoff，也可能是用户解除 Block 的 Resume 指令。Developer 的自述与 Reviewer 的 Finding 都只是待独立核验的判断，不传递私有推理。

## Block and resume policy

任何 Block 都先展示给用户。Origin 可以运行只读诊断或 `recover`，但只有可 Resume Block 在收到新的明确用户指令后才能 Resume；Limit / Profile Changed Block 必须新建 Run。Resume Payload 记录 Block Event、用户指令、目标角色和 `continue_same_run` Scope，并成为下一 Turn 的直接输入。改变目标、协议、角色/Binding、Workspace、Profile 或上限时取消旧 Run 并新建 Run。

## Assumptions made during bootstrap

- “P3 以上”解释为 P0 至 P3 均阻塞，P4 不阻塞完成。
- 当前 Codex Session 同时承担 reviewer Role 和最终交付通道；这不是固定 Reviewer 实现。

## Safety limits

最多 20 个业务 Turn，Wall Time 为 7200 秒；External Runner 异步强制，Origin 在下一 CLI 边界生效。Workspace 必须是同一个 Git Worktree 根目录，运行期间不手工并发编辑。达到 Limit 后取消旧 Run，并以新配置创建 Run。
```

## 35.4 第一轮 Handoff

```markdown
# Handoff

## From

developer

## To

reviewer

## My responsibility in this turn

落地 Stage 2 的首轮实现。

## Work completed

- 实现了 ...
- 新增了 ...
- 调整了 ...

## Verified observations

- 运行 `...`，结果通过；
- 运行 `...`，结果通过。

## My judgment and claims

我认为 Stage 2 的主要验收路径已经覆盖。

## Uncertainties and disagreements

并发场景只进行了单元级验证，尚未运行完整集成环境。

## Requested next action

请 Reviewer 独立审查当前完整 Diff、并发边界和测试覆盖。

## Protocol basis

根据协议，Developer 每轮修改完成后必须交给 Reviewer。
```

## 35.5 最终交付

Origin Codex 不只转发 Completion 文件，而应输出：

- Stage 2 已落地的功能；
- 修改的关键模块；
- Developer / Reviewer 循环轮数；
- 各轮主要 Finding；
- 最终测试和检查；
- 尚存 P4 或风险；
- 是否完整满足原始请求。

---

## 36. 技术可行性依据

本设计依赖的现有 Harness 能力包括：

### Codex

- Skill 可由 `SKILL.md`、脚本和参考资料组成，并可显式或隐式触发；
- `codex exec` 支持非交互运行；
- `--json` 可输出包含 `thread.started`、`turn.started`、`turn.completed` 等事件的 JSONL；
- 非交互 Session 可通过 `codex exec resume <SESSION_ID>` 恢复；
- 交互 Session 可 Resume；
- App Server 提供 `thread/resume`，可作为后续更深集成路径。

### Claude Code

- 支持 `-p` 非交互运行；
- 支持 `--session-id` 指定 Session；
- 支持 `--resume` / `--continue`；
- 支持 `text`、`json`、`stream-json` 输出；
- 支持 Plugin、Skill、Hook 和权限模式；
- 可在后续阶段使用 Agent SDK 替代 CLI Wrapper。

### tmux

- Session 可 Detach 后继续运行；
- 可程序化创建 Session、Window 和 Pane；
- `wait-for` 可提供不带载荷的 Best-effort 变更提示；
- `capture-pane` 可用于人工诊断；
- Stage 1 不依赖 `send-keys`、Pane 文本解析或 tmux 锁完成工作流协调。

上述能力应由 `agent-team doctor` 在用户本地运行时再次探测；文档中的命令示例不是不可变协议。

---

## 37. 最终架构结论

Agent-Team Stage 1 的最小闭环是：

```text
用户自然语言请求
        ↓
Origin Bootstrap Skill
        ↓
REQUEST.md + PROTOCOL.md
        ↓
最小 Role Binding / Runtime 配置
        ↓
Workspace Ownership + Immutable Event Journal
        ↓
tmux Role Workers（外部角色）/ Embedded Origin
        ↓
组外 Turn Supervisor + 自持久化 Runner + 单次启动许可
        ↓
Harness 原生 Session Start/Resume
        ↓
Agent 主动自然语言 Handoff
        ↓
显式 CLI 路由 + 原子 Event 提交
        ↓
按用户本次协议循环
        ↓
Completion 返回 Origin Session
        ↓
原始 Agent 向用户最终交付
```

关键产品原则可以浓缩为：

> **用户用自然语言定义团队；Skill 规定协作行为；协议承载本次语义；单文件 Workspace Ownership 防止同一工作区出现多个 Team Run；不可变 Event Journal 定义执行权；tmux 只承载外部角色 Worker 并提供可丢失的提示；组外 Turn Supervisor 监控先自持久化、再获许可、最后原地 `exec` Harness 的独立 Runner 进程组；Harness Session 保持角色上下文；当前 Input Event 明确承载 Kickoff、Handoff 或 Resume；最终结果进入用户最初 Agent 会话的 Durable Event 视图，并在 Origin Turn 持续存活时自动交付。**
