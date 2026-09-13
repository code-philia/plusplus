你的想法非常适合用“**编译器**”而不是“Agent 写代码”来建模。核心原则应该是：

> **Agent 的自由度主要集中在前期 Design Compile；一旦设计被编译成结构化 IR，后面的 Skeleton、Test、Implementation 都尽可能变成受约束的机械过程。**

你上传的需求本身已经很适合这么做。例如 `REQ-1.2` 显式依赖 `REQ-1.1`，`REQ-2.2` 依赖 `REQ-2.1`，而 `REQ-3.1` 同时依赖登录与选车能力，之后 `REQ-3.2` 再依赖 `REQ-3.1`。   

系统正式定义成下面这条流水线：

```text
Requirement YAML
      ↓
Requirement AST
      ↓
Requirement DAG
      ↓
Global Analysis
      ↓
Design IR              ← 最重要
      ↓
Whole-program Skeleton
      ↓
Test IR / Test Code
      ↓
Node-by-node TDD Implementation
      ↓
Regression + Acceptance
```

---

## 1. 技术栈：全部 TypeScript

优先选择一个**尽量简单、静态约束强、前后端共享类型方便**的技术栈。

我建议：

| 层                 | 推荐                                 |
| ----------------- | ---------------------------------- |
| Frontend          | React 18 + TypeScript + Vite       |
| Routing           | React Router                       |
| Backend           | Node.js + TypeScript + Express     |
| DB                | SQLite                             |
| DB access         | Drizzle ORM 或非常薄的 repository layer |
| Schema validation | Zod                                |
| API               | REST                               |
| Unit test         | Vitest                             |
| Backend API test  | Supertest                          |
| E2E               | Playwright                         |
| Package layout    | npm workspace / monorepo           |

也就是：

```text
project/
├── frontend/
├── backend/
├── shared/
└── tests/
```

其中我尤其建议：

```text
shared/
├── contracts/
├── schemas/
└── types/
```

前后端不要各自“理解”API，而是共同依赖编译出来的 contract：

```ts
export const RegisterRequestSchema = z.object({
  username: z.string(),
  email: z.string(),
  password: z.string(),
  ...
});

export type RegisterRequest =
  z.infer<typeof RegisterRequestSchema>;
```

这是降低 Agent 自由度非常有效的一步。

> ** TypeScript + Schema + 显式 Contract。**

因为你的目的不是追求开发灵活性，而是追求**可编译性和可约束性**。

SQLite 也特别适合 ARC / benchmark 场景，因为部署简单、状态容易重置。

---

# 2. 最重要的问题：每个节点 Design → Test → Implement，还是整体 Skeleton 后再实现？

我的结论非常明确：

> **不要采用纯粹的 Node-by-Node Design → Test → Implement。**
>
> 应该采用：
>
> **Whole-program Design → Whole-program Skeleton → Node-by-node Test & Implementation。**

也就是说

```text
不是：

REQ1
 Design
 Skeleton
 Test
 Implement

REQ2
 Design
 Skeleton
 Test
 Implement

REQ3
 ...


而是：

ALL REQUIREMENTS
      ↓
Global Design
      ↓
ALL NODE Designs
      ↓
Whole-program Skeleton
      ↓

REQ1 tests → implementation
REQ2 tests → implementation
REQ3 tests → implementation
...
```

这是整个方案最关键的一点。

---

## 为什么不能边设计边实现？

你这个需求文件就是很好的例子。

注册需求一开始看起来只是“创建账号”，但是实际需求里它还要求创建持久 Session。

后面的登录又依赖这个 Account 和 Session。

更后面的 Booking 页面同时依赖：

```text
Authentication
+
Selected Journey
```



而最终 Booking 又要求：

```text
Account
Journey
Passenger
Booking
Idempotent confirmation
Persistence
```



如果 Agent 在 REQ-1 时完全不知道 REQ-3，可能会做出：

```text
User
id
username
password
```

后面才发现还需要：

```text
name
nationality
passport_number
passport_expiration
birth_date
gender
email
```

然后又开始改数据库。

这就是传统 Agent Coding 最容易产生的：

> **局部正确，全局不断重构。**

而你的 Requirement Compilation 想解决的恰恰应该是这个问题。

---

# 3. Skeleton 不应该本身就是 IR

这里我建议你稍微修正一个非常重要的概念。

你提到：

> 是否将 skeleton 类似真实编译过程中的 AST / IR，一旦生成就不能再改变？

我的答案是：

> **不要让 Skeleton 本身成为唯一的 IR。**
>
> 应该有一个更加抽象的、结构化的 **Design IR**。

关系应该是：

```text
Requirement AST
       ↓
Design IR
       ↓
Code Skeleton
```

Skeleton 是 Design IR 的 **materialization / lowering result**。

就像真正的编译器：

```text
Source
 ↓
AST
 ↓
HIR
 ↓
MIR
 ↓
Machine Code
```

不是把机器码当 IR。

---

# 4. 我建议定义三个不同层次的 IR

这个会让你的论文/系统设计非常漂亮。

### 第一层：Requirement IR

来自 YAML。

例如：

```yaml
id: REQ-3.1
dependencies:
  - REQ-1.2
  - REQ-2.2
```

编译成：

```json
{
  "id": "REQ-3.1",
  "type": "ATOMIC",
  "dependsOn": [
    "REQ-1.2",
    "REQ-2.2"
  ]
}
```

形成：

```text
REQ-1.1
   ↓
REQ-1.2 ─────┐
             ↓
           REQ-3.1 → REQ-3.2
             ↑
REQ-2.1       │
   ↓          │
REQ-2.2 ──────┘
```

而 `FOLDER` 节点我不建议作为实际 compilation unit。

例如：

```text
REQ-1
REQ-2
REQ-3
```

主要承担：

```text
scope
constraints
aggregation
dependency propagation
```

真正进入实现队列的应该主要是：

```text
ATOMIC node
```

---

# 5. 第二层，也是最核心：Design IR

这是整个 ARC Requirement Compiler 的核心资产。

它应该记录的不是自然语言，而是：

```text
Page
Component
API
Function
Service
Repository
Entity
Schema
State
Data flow
Dependency
Ownership
Contract
```

比如你的需求经过 Design Compile 后，可以得到：

```yaml
modules:

  - id: DB.User
    kind: ENTITY
    owner: REQ-1.1
    file: backend/db/schema/user.ts

  - id: DB.Session
    kind: ENTITY
    owner: REQ-1.1
    file: backend/db/schema/session.ts

  - id: AUTH.RegisterService
    kind: SERVICE
    owner: REQ-1.1
    input: RegisterRequest
    output: AuthSession

  - id: API.Register
    kind: API
    owner: REQ-1.1
    method: POST
    path: /api/auth/register
    calls:
      - AUTH.RegisterService

  - id: PAGE.Register
    kind: PAGE
    owner: REQ-1.1
    route: /register
    calls:
      - API.Register
```

现在关键来了：

REQ-1.2 不允许重新设计 User。

它只能：

```yaml
- id: AUTH.LoginService
  owner: REQ-1.2
  depends_on:
    - DB.User
    - DB.Session
```

这样就变成真正的：

```text
compile against existing symbols
```

而不是：

```text
Agent 看代码然后自己猜怎么改。
```

---

# 6. Module Table 非常值得做，而且我认为它应该成为核心数据结构

你提到“模块表”，这个方向我非常赞同。

我建议字段至少包含：

| Field                 | 含义                                 |
| --------------------- | ---------------------------------- |
| module_id             | 全局唯一 ID                            |
| kind                  | PAGE/API/SERVICE/FUNC/DB/COMPONENT |
| owner_requirement     | 哪个需求创建                             |
| file                  | 生成位置                               |
| exports               | 对外暴露接口                             |
| inputs                | 输入                                 |
| outputs               | 输出                                 |
| dependencies          | 可调用模块                              |
| side_effects          | DB / Session / File 等              |
| data_entities         | 使用哪些数据                             |
| editable_region       | Agent 可以修改哪里                       |
| contract_status       | OPEN/FROZEN                        |
| implementation_status | TODO/DONE                          |
| hash                  | Skeleton/Contract fingerprint      |

例如：

| Module           | Owner   | Type    | Input       | Output       | Depends       |
| ---------------- | ------- | ------- | ----------- | ------------ | ------------- |
| `DB.User`        | REQ-1.1 | DB      | —           | User         | —             |
| `Auth.Register`  | REQ-1.1 | Service | RegisterDTO | Session      | UserRepo      |
| `API.Register`   | REQ-1.1 | API     | HTTP        | AuthResponse | Auth.Register |
| `Auth.Login`     | REQ-1.2 | Service | LoginDTO    | Session      | UserRepo      |
| `Train.Search`   | REQ-2.1 | Service | SearchDTO   | Journey[]    | TrainRepo     |
| `Booking.Create` | REQ-3.2 | Service | BookingDTO  | Booking      | User, Journey |

这样 Agent 后面不是：

> “请实现 REQ-3.2。”

而是：

> 实现 `Booking.Create`。
>
> 输入只能使用 `CreateBookingInput`。
>
> 输出必须是 `BookingResult`。
>
> 可以调用：
>
> `BookingRepository`
>
> `JourneyRepository`
>
> `CurrentUser`
>
> 禁止修改其他模块 Contract。

这种 prompt 的确定性会高非常多。

---

# 7. 所以我建议 Design 阶段不是“一次 Agent 调用”，而是多 Pass Compile

不要：

```text
requirements
      ↓
LLM
      ↓
design.yaml
```

而应该设计成 Compiler Pass：

```text
Pass 1
Requirement Graph Analysis

Pass 2
Domain/Data Model Analysis

Pass 3
UI/Page Analysis

Pass 4
API Analysis

Pass 5
Service/Function Analysis

Pass 6
Cross-node Dataflow Analysis

Pass 7
Module Linking

Pass 8
Design Validation

Pass 9
Freeze
```

尤其是：

### Pass 2：Domain Model

你的示例需求全局扫描后，很容易识别出四个核心 Entity：

```text
User
Session
Train/Journey
Booking
```

需求明确要求账户、Session、Booking 持久化。

那么 DB schema 应该在这里统一做掉，而不是等 REQ-3.2 才突然：

```text
CREATE TABLE booking
```

---

# 8. 一个比较理想的完整设计流程

我会把你的 compiler 设计成：

```text
             requirements.yaml
                    │
                    ▼
          ┌──────────────────┐
          │ Requirement Parse │
          └────────┬─────────┘
                   ▼
              Req AST
                   │
                   ▼
          Dependency Analysis
                   │
                   ▼
              Req DAG
                   │
         ┌─────────┴─────────┐
         │                   │
         ▼                   ▼
 Global Data Analysis    UI Analysis
         │                   │
         └─────────┬─────────┘
                   ▼
               API Design
                   │
                   ▼
            Service Design
                   │
                   ▼
              Module Graph
                   │
                   ▼
              Design IR
                   │
              VALIDATION
                   │
                   ▼
                FREEZE
                   │
                   ▼
       Whole-program Skeleton
                   │
              npm run build
                   │
                   ▼
            Test Generation
                   │
                   ▼
       Topological TDD Queue
```

这比：

```text
REQ → code → test → fix
```

更接近真正的 Requirement Compilation。

---

# 9. Skeleton 到底冻结什么？

这里我认为不能简单说：

> Skeleton 一旦生成，一行都不能修改。

这样会太死。

应该区分：

## Contract Frozen

不能改：

```text
DB schema
API path
API request/response
function signature
module ownership
routing
module dependencies
public interface
shared types
```

例如：

```ts
export interface BookingService {
  createBooking(
    input: CreateBookingInput
  ): Promise<CreateBookingResult>;
}
```

冻结以后实现 Agent 不能改成：

```ts
createBooking(userId, trainId, passenger)
```

---

## Structure Frozen

比如：

```text
backend/
  modules/
    booking/
      booking.service.ts
      booking.repository.ts
      booking.routes.ts
```

后续 Agent 不允许随便：

```text
新增 booking2.ts
删除 service
绕过 repository
直接在 route 写 SQL
```

---

## Implementation Mutable

例如：

```ts
export async function createBooking(
  input: CreateBookingInput
): Promise<CreateBookingResult> {

  // <IMPLEMENTATION>
  throw new Error("TODO");
  // </IMPLEMENTATION>
}
```

Agent 只能修改：

```text
IMPLEMENTATION region
```

这其实就是：

> **Structural immutability + behavioral mutability**

我认为这是你的方法中非常值得强调的概念。

---

# 10. 最好不要让 Agent“直接编辑 repository”

这一点非常关键。

你现在的想法里：

> 严格限定修改范围

我建议真正实现成机器约束，而不是 Prompt 约束。

例如 Agent 返回：

```diff
--- booking.service.ts
+++ booking.service.ts
@@ IMPLEMENTATION:BookingService.createBooking
...
```

Compiler Controller 检查：

```text
modified_files ⊆ allowed_files
modified_regions ⊆ implementation_regions
```

否则：

```text
PATCH REJECTED
```

而不是告诉 Agent：

> 请不要修改其他文件。

---

# 11. 甚至可以给 Frozen Skeleton 做 Hash

例如：

```json
{
  "module": "BookingService",
  "contractHash": "sha256:...",
  "structureHash": "sha256:..."
}
```

实现前：

```text
verify skeleton
```

实现后：

```text
verify frozen regions
```

如果：

```text
before.contractHash != after.contractHash
```

直接拒绝。

于是：

```text
Agent 想自由发挥
```

也没有机会。

这个机制比 Prompt 强得多。

---

# 12. 更进一步：让 Agent 根本看不到不该改的模块

比如处理：

```text
REQ-3.2
```

上下文不要把整个 repo 全给它。

给：

```text
Requirement:
REQ-3.2

Writable:
BookingService implementation
BookingRepository implementation

Readable contracts:
User
Session
Journey
Booking

Tests:
REQ-3.2 tests
```

而不是：

```text
Here is the whole repository.
Please implement REQ-3.2.
```

那么搜索空间会明显下降。

这正好符合你的目标：

> **不是让 Agent 变聪明，而是让 Agent 可选的行为越来越少。**

---

# 13. 后续 requirement 如何不修改前面的设计？

这个可以通过一个很像 linker 的机制解决。

假设现在编译：

```text
REQ-3.2
```

它需要：

```text
User
Journey
Booking
```

其中：

```text
User       已存在
Journey    已存在
Booking    全局 Design Pass 已预声明
```

那么 REQ-3.2 Design Agent 做的不是重新设计，而是：

```text
resolve symbol User
resolve symbol Journey
resolve symbol Booking
```

如果发现需求需要一个系统里不存在的接口：

```text
PaymentService
```

不能自己创建。

返回：

```text
UNRESOLVED_SYMBOL
```

由 Design Compiler 决定：

```text
是否新增模块
```

这和：

```text
compiler/linker
```

非常像。

---

# 14. 我还建议加入“设计权限”

Module 可以有：

```yaml
owner: REQ-1.1
visibility: PUBLIC
contract: FROZEN
```

REQ-3.2：

```text
READ User
CALL UserRepository
WRITE Booking
```

但不能：

```text
ALTER User
```

这就形成：

```text
Requirement ownership
+
Module ownership
+
Modification capability
```

---

# 15. 测试生成应该放在哪里？

你的原方案：

```text
Skeleton
→ tests
→ implementation
```

这个方向是对的。

不过要区分两类测试。

### Requirement tests

来源：

```text
requirement scenarios
```

比如你 YAML 已经有：

```text
GIVEN
WHEN
THEN
```

注册成功要求刷新后仍然保持登录状态。

这些适合：

```text
Playwright acceptance tests
```

---

### Contract tests

来自 Design IR。

例如：

```text
POST /api/auth/register
```

就自动生成：

```text
request schema test
response schema test
status code test
DB side effect test
```

所以应该是：

```text
Requirement
     ↓
Acceptance Tests

Design IR
     ↓
Contract / Unit Tests
```

两个来源不同。

这一点也非常重要，因为：

> **测试不能只根据 Skeleton 生成。**

否则很容易产生“代码设计什么，测试就验证什么”的自证循环。

---

# 16. 最终实施阶段才真正 Node-by-Node

到了这个阶段，就非常适合按照 dependency DAG：

```text
REQ-1.1
REQ-2.1
    ↓
REQ-1.2
REQ-2.2
    ↓
REQ-3.1
    ↓
REQ-3.2
```

分 wave：

```text
Wave 1
REQ-1.1
REQ-2.1

Wave 2
REQ-1.2
REQ-2.2

Wave 3
REQ-3.1

Wave 4
REQ-3.2
```

同一 Wave 甚至可以并行。

每个 Node：

```text
Load requirement
       ↓
Load Design IR slice
       ↓
Load writable modules
       ↓
Run tests
       ↓
Implement
       ↓
Run node tests
       ↓
Run dependent regression tests
       ↓
Verify frozen hashes
       ↓
Commit
```

---

# 17. 因此我不建议“全部代码 skeleton 完全不可修改”

而建议三个 Freeze Level：

```text
Level 1 — Global Freeze

DB schema
global config
routing conventions
folder structure


Level 2 — Contract Freeze

API
function signatures
module boundaries
shared types


Level 3 — Implementation

function body
SQL/query implementation
UI rendering details
business logic
```

其中 Level 1/2：

> 默认绝对禁止 Implementation Agent 修改。

Level 3：

> TDD Agent 可以迭代。

---

# 18. Design 如果真的错了怎么办？

现实中不可能保证 Design 100% 正确。

所以不要偷偷允许 Agent 修改。

而是设计一个显式机制：

```text
DESIGN_CHANGE_REQUEST
```

比如实现 REQ-3.2 时发现：

```text
BookingRepository 缺少 findByIdempotencyKey()
```

Agent 不能直接加。

它只能提交：

```yaml
type: DESIGN_CHANGE_REQUEST

requirement: REQ-3.2

module:
  BookingRepository

problem:
  Duplicate confirmation cannot be implemented
  with current contract.

proposed_change:
  add:
    findByIdempotencyKey(key): Booking | null
```

然后进入：

```text
Design Recompiler
```

分析：

```text
affected modules
affected requirements
affected tests
```

批准后重新冻结。

于是：

> **修改设计不是禁止，而是必须显式发生。**

这一点很像数据库 migration 或 API change management。

---

# 19. 这样，你的方法和普通 Agent Coding 就形成了很清楚的区别

普通 Agent：

```text
Requirement
     ↓
Reason
     ↓
Search repository
     ↓
Edit anything
     ↓
Run tests
     ↓
Fix anything
```

你的方法：

```text
Requirement
     ↓
Compile Design
     ↓
Freeze Contracts
     ↓
Generate Skeleton
     ↓
Generate Tests
     ↓
Restricted Implementation
     ↓
Verification
```

搜索空间从：

```text
整个 repository × 所有可能设计
```

变成：

```text
当前 module × 当前 implementation body
```

这个其实就是你所谓：

> **Deterministic Agentic Programming**

最核心的思想。

---

# 20. 如果让我给这个系统设计核心中间产物，我会定成 5 个

```text
requirements.yaml
        ↓
1. requirement_ir.json

        ↓
2. dependency_graph.json

        ↓
3. global_design.yaml

        ↓
4. module_registry.yaml

        ↓
5. skeleton_manifest.json
```

其中真正的核心不是 Skeleton，而是：

```text
global_design.yaml
+
module_registry.yaml
```

比如：

```text
module_registry.yaml
```

可能长这样：

```yaml
modules:

  - id: auth.register
    owner: REQ-1.1
    type: SERVICE

    implementation:
      file: backend/src/auth/register.ts
      symbol: register

    contract:
      input: RegisterInput
      output: AuthSession

    dependencies:
      - user.repository
      - session.repository

    writable:
      - function_body

    status:
      design: FROZEN
      implementation: TODO
```

这会是整个系统控制 Agent 行为的基础。

---

# 21. 对你三个问题，我最后压缩成三个结论

**第一，技术栈。** React + Node.js 非常合适，但建议使用 **React + TypeScript + Vite / Node + TypeScript + Express / SQLite / Zod / Vitest / Supertest / Playwright**。TypeScript、Schema 和 Contract 对“确定性编程”比框架选择本身更重要。

**第二，流水线顺序。** 不要完全按照 `一个需求 → Design → Test → Implement → 下一个需求`。更合理的是：

```text
全需求扫描
→ Requirement DAG
→ Global Design
→ 全节点 Design
→ Design IR Freeze
→ Whole-program Skeleton
→ 编译检查
→ Tests
→ 按 DAG Node-by-node TDD
```

也就是：**Design 是 whole-program 的，Implementation 是 incremental 的。**

**第三，如何保证设计不被破坏。** 不要单纯依赖 Prompt；应该建立：

```text
Design IR
Module Registry
Module ownership
Frozen contracts
Writable regions
File allowlist
AST/patch validation
Hash verification
Explicit design-change request
```

做到：

> **Agent 不是“被要求不要改”，而是在系统机制上“没有权限改”。**

我认为这一版架构已经可以把你的想法从“Agent 编程策略”提升成一个真正的 **Requirement Compiler Architecture**。尤其值得强调的一句话是：

> **Requirements are globally compiled into a frozen structural program before behavior is incrementally synthesized.**

这句话基本可以作为你整个方法的核心抽象。


---


> **外层是你自己实现的 deterministic compiler controller；LLM/Agent 只是 Compiler Pass 的执行器，而不是整个系统的控制者。**

这会直接决定后面系统是不是还能保持“需求编译”的味道。

## 1. DeepAgent 还是自己写 LLM 交互？

我的建议不是二选一，而是分层：

```text
Requirement Compiler
│
├── Compiler Controller          ← 自己写，绝对控制
│   ├── DAG scheduler
│   ├── artifact/version manager
│   ├── schema validator
│   ├── dependency resolver
│   ├── freeze checker
│   └── test runner
│
└── LLM Workers                  ← 可以用 DeepAgent / 原生 API
    ├── Requirement Analyzer
    ├── Data Model Designer
    ├── UI Designer
    ├── API Designer
    ├── Test Generator
    └── Implementation Agent
```

也就是说：

> **不要基于 DeepAgent 构建整个 ARC Compiler；可以在 Compiler 内部使用 DeepAgent。**

### 为什么？

Deep Agents 目前本质上仍然是建立在 LangChain agent loop 之上的一个 agent harness，它帮你提供 planning、filesystem、subagent delegation、summarization、memory、permissions 等能力。([GitHub][1])

这些能力对于“Coding Agent”非常好。

特别是 implementation 阶段：

```text
读取任务
→ 查看允许访问的文件
→ 编写代码
→ 执行测试
→ 查看错误
→ 再修改
```

这是一个天然的 Agent Loop。

而且 Deep Agents 已经支持 filesystem permission、subagents、structured response 等机制，这和你希望限制 Agent 的行为有一定契合。([GitHub][2])

但是你真正的核心流程：

```text
Parse Requirement
→ Build DAG
→ Global Analysis
→ Build Symbol Table
→ Freeze Design
→ Schedule Nodes
→ Verify modifications
```

**绝对不应该交给 Agent 自己决定。**

因为如果你这样写：

```python
agent = create_deep_agent(...)

agent.invoke("""
Read requirements.yaml.
Analyze the requirements.
Design the system.
Implement it requirement by requirement.
""")
```

你其实又退回到了：

> autonomous coding agent

只是 prompt 写得比较复杂。

---

## 我建议三个层次使用不同方式

| 阶段                        | 推荐方式                     |      Agent 自由度 |
| ------------------------- | ------------------------ | -------------: |
| Requirement / Design Pass | 原生 LLM structured output |             很低 |
| Test generation           | 原生 LLM / 简单 Agent        |             中低 |
| TDD Implementation        | DeepAgent / Coding Agent | 中高，但严格 sandbox |

例如 Design 阶段，我甚至**不建议使用 Agent loop**。

而是：

```python
response = llm.generate(
    prompt=...,
    response_schema=ModuleDesignSchema
)
```

强制它输出：

```json
{
  "modules": [],
  "entities": [],
  "apis": [],
  "dependencies": []
}
```

然后：

```text
LLM output
     ↓
JSON Schema Validation
     ↓
Semantic Validation
     ↓
Compiler accepts/rejects
```

如果不合法：

```text
REJECT
→ 把 validation error 给模型
→ regenerate
```

这比：

```text
Agent 自己想
Agent 自己写文件
Agent 自己决定完成
```

稳定很多。

---

# 一个比较好的实现架构

甚至可以完全这样设计：

```text
                 ┌──────────────────────┐
                 │  Compiler Controller │
                 │   Python code        │
                 └──────────┬───────────┘
                            │
           ┌────────────────┼─────────────────┐
           │                │                 │
           ▼                ▼                 ▼
     LLM Call          LLM Call          Deep Agent
     Design Pass       Test Pass         Coding Pass
           │                │                 │
           ▼                ▼                 ▼
     Structured IR       Tests           Code Patch
           │                │                 │
           └────────────────┼─────────────────┘
                            ▼
                       Validator
```

甚至我会建议：

```text
Compiler Controller
```

本身完全**不使用 LLM 做决策**。

它只执行：

```python
parse()
analyze_dependencies()
schedule()
invoke_pass()
validate()
freeze()
run_tests()
commit()
```

LLM 只负责：

```text
semantic transformation
```

这一点对于论文也很重要。

你可以说：

> LLMs are used as semantic transformers within deterministic compiler passes rather than as autonomous workflow controllers.

我认为这个表述非常准确。

---

# 2. 更困难的问题：需求太大，不可能一次全部放进上下文怎么办？

这里有一个很重要的认识：

> **Whole-program design ≠ 把 whole program 一次性塞给 LLM。**

真正的编译器也从来不是：

```text
把整个 Linux kernel 源代码
一次塞进一个函数
然后输出设计
```

而是依赖：

```text
局部解析
+
符号表
+
索引
+
多遍扫描
+
链接
```

你的 Requirement Compiler 也应该一样。

所以你之前说的：

> “先扫一遍”

这个方向其实是对的。

但我建议把“扫一遍确定数据库”扩大成：

> **Global Discovery Pass**

而不是只发现 Database。

---

# 3. 大需求下，第一遍不要“设计”，只“提取事实”

假设有：

```text
5 MB requirement.yaml
500 个 requirement nodes
100,000 tokens
```

不要让模型：

> 请阅读以下全部需求并设计数据库。

而是每个节点独立处理。

例如：

```text
REQ-001
        ↓
Requirement Fact Extractor
        ↓
facts-001.json

REQ-002
        ↓
Requirement Fact Extractor
        ↓
facts-002.json
```

这里不设计系统。

只抽取这个节点明确需要什么。

比如注册节点：

```yaml
requirement: REQ-1.1

entities:
  - User
  - Session

data_fields:
  - entity: User
    field: username
    constraints:
      - unique
      - length: 3..32

  - entity: User
    field: email
    constraints:
      - unique_case_insensitive

operations:
  - create_account
  - create_session

ui:
  pages:
    - Register

state:
  persistent:
    - User
    - Session

security:
  - password_authentication

invariants:
  - invalid_registration_creates_no_account

evidence:
  - requirement: REQ-1.1
```

注意：

> **这个阶段不允许模型决定表叫什么、API 是什么、文件叫什么。**

只提取 semantic facts。

这样模型处理上下文：

```text
2000 tokens
```

而不是：

```text
100000 tokens
```

---

# 4. 然后进行 Map → Reduce

这实际上非常适合 MapReduce。

第一层：

```text
500 Requirements
      │
      ├── R1 → Facts
      ├── R2 → Facts
      ├── R3 → Facts
      │
      ...
      └── R500 → Facts
```

然后按照 Requirement Tree 本身进行 reduce。

比如：

```text
REQ-1.*
   ↓
Account Domain Summary

REQ-2.*
   ↓
Train Domain Summary

REQ-3.*
   ↓
Booking Domain Summary
```

于是：

```text
500 nodes
↓
50 subtree summaries
↓
10 domain summaries
↓
global summary
```

这时候 Global Designer 看的是：

```text
几十个结构化 summary
```

而不是原始几十万 token。

---

# 5. 但是这里有一个危险：Summary 会丢信息

这一点必须特别注意。

不能：

```text
Requirement
    ↓
natural-language summary
    ↓
summary of summary
    ↓
summary of summary
```

否则三层以后细节全没了。

应该是：

```text
Requirement
    ↓
Structured Fact IR
```

然后 merge 的是**结构化事实**。

例如原始：

```yaml
REQ-1.1:
  username unique

REQ-1.2:
  login accepts username

REQ-4.7:
  username displayed in profile
```

最终：

```yaml
symbols:
  User.username:

    constraints:
      - type: unique
        source: REQ-1.1

    used_by:
      - REQ-1.1
      - REQ-1.2
      - REQ-4.7
```

也就是说每个事实必须带：

```text
provenance
```

我非常建议你的 IR 从一开始就保留：

```yaml
source:
  requirement_id: REQ-1.1
  location: description
```

于是最终模型即使看到：

```text
User.email
```

也可以按需把原需求拉回来。

---

# 6. 真正的核心应该是 Global Symbol Table

这跟编译器越来越像了。

第一遍扫描的最终结果不是“数据库 schema”，而是：

```text
Global Symbol Table
```

例如：

```yaml
entities:

  User:
    discovered_from:
      - REQ-1.1
      - REQ-1.2
      - REQ-3.1

  Session:
    discovered_from:
      - REQ-1.1
      - REQ-1.2
      - REQ-3.1

  Train:
    discovered_from:
      - REQ-2.1
      - REQ-2.2
      - REQ-3.1

  Booking:
    discovered_from:
      - REQ-3.2
```

同时有：

```yaml
operations:

  RegisterUser:
    requirements:
      - REQ-1.1

  Login:
    requirements:
      - REQ-1.2

  SearchTrain:
    requirements:
      - REQ-2.1

  CreateBooking:
    requirements:
      - REQ-3.2
```

还有：

```yaml
pages:
apis:
states:
permissions:
cross_cutting_constraints:
```

这张表本身可以非常大。

没关系。

因为：

> LLM 每次不需要读取整个 Symbol Table。

Compiler 可以 query：

```text
give me symbols related to Booking
```

得到：

```text
Booking
User
Session
Journey
CreateBooking
```

---

# 7. 所以“全局数据库设计”可以做成 Query-driven

例如准备设计：

```text
User
```

系统先查：

```text
Symbol Table:
Who touches User?
```

得到：

```text
REQ-1.1
REQ-1.2
REQ-3.1
REQ-3.2
REQ-7.4
```

然后只加载：

```text
这些 Requirement 的 Fact IR
+
必要时原始 Requirement
```

于是：

```text
User Schema Designer
```

看到的是：

```text
所有和 User 真正相关的需求
```

而不是整个 Requirement document。

例如：

```text
500 requirements
```

里面只有 12 个和 User 有关。

那数据库设计 Agent 实际上下文可能只有：

```text
12 requirements
+
symbol metadata
```

这就解决了 context window 问题。

---

# 8. 不要过度依赖 embedding RAG

这里我会特别谨慎。

普通 RAG：

```text
"设计 Booking 数据库"
      ↓
embedding search
      ↓
Top 10 similar chunks
```

很容易漏掉：

```text
REQ-217:
Repeated confirmation must not create another record.
```

它可能语义相似度并不高，但这是极其关键的 DB constraint。

你的系统应该优先使用：

```text
显式 requirement dependency
+
symbol reference
+
entity/operation index
```

也就是：

```text
graph-based retrieval
```

而不是：

```text
semantic similarity only
```

Embedding 可以作为 fallback。

---

# 9. 我建议第一轮实际上扫出六类 Global Facts

不是只扫 DB。

你的“扫描阶段”最好得到：

| Global Registry        | 解决什么                  |
| ---------------------- | --------------------- |
| Entity Registry        | 数据库                   |
| State Registry         | session / persistence |
| Operation Registry     | service/function      |
| Page Registry          | UI 页面                 |
| Interaction Registry   | 页面之间导航                |
| Cross-cutting Registry | auth、错误处理、配置、权限等      |

例如你的 Demo：

```text
Entity:
User
Session
Train
Booking

Page:
Home
Register
Login
SearchResults
Booking

Operation:
Register
Login
SearchTrain
SelectTrain
CreateBooking

Persistent state:
User
Session
Booking
```

这样后面才是真正的 Whole-program Design。

---

# 10. 第二遍才做 Global Design

所以真正的 pipeline 可以变成：

```text
                    requirements.yaml
                           │
                           ▼
                  Requirement Parser
                           │
                           ▼
                 Requirement AST/DAG
                           │
                           ▼
            ┌──── Global Discovery ────┐
            │                          │
      Node Fact Extraction             │
            │                          │
            ▼                          │
       Requirement Facts               │
            │                          │
            ▼                          │
       Symbol Resolution               │
            │                          │
            ▼                          │
       Global Registries ──────────────┘
            │
            ▼
        Global Design Pass
            │
            ├── Data Design
            ├── UI Design
            ├── API Design
            └── Module Design
            │
            ▼
          Design IR
```

所以你最初的：

> 扫一遍确定数据库结构

我会改成：

> **Pass 1: Global Requirement Discovery**

然后：

> **Pass 2: Global Architecture Synthesis**

---

# 11. Global Architecture Synthesis 也不应该只有一次 LLM

这一点同样重要。

例如 Data Design：

```text
Entity Registry
     ↓
User Designer
Booking Designer
Journey Designer
...
```

先分别设计。

然后进行：

```text
Data Linker
```

检测：

```text
Booking.user_id → User.id
Booking.journey_id → Journey.id
```

再跑：

```text
Consistency Check
```

这样：

```text
局部设计
+
全局链接
```

就产生了 Global Design。

和编译器其实非常像：

```text
translation unit compilation
+
linking
```

---

# 12. 可以有“设计收敛”而不是一次冻结

我不建议：

```text
第一次扫描
→ DB Schema
→ 永远冻结
```

因为第一遍一定可能遗漏。

可以设计三个阶段：

```text
DISCOVERED
    ↓
PROPOSED
    ↓
RESOLVED
    ↓
FROZEN
```

例如：

```yaml
User:
  status: PROPOSED
```

Global pass 完成后：

```yaml
User:
  status: RESOLVED
```

等 Design Validator 检查所有 Requirement：

```text
所有字段都有支持
所有操作都有数据
所有 reference 都可解析
没有 unresolved symbol
```

之后：

```yaml
User:
  status: FROZEN
```

这就比：

> 第一遍 Agent 觉得数据库应该这么设计，然后永久不准改

稳很多。

---

# 13. 对超大需求，我会引入“Context Pack”

这是后面实现上非常重要的概念。

不要让每个 Agent 自己决定读哪些需求。

Compiler 负责产生：

```text
ContextPack(REQ-3.2)
```

例如：

```yaml
target:
  REQ-3.2

requirement:
  ...

ancestors:
  - REQ-3

dependencies:
  - REQ-3.1

transitive_contracts:
  - REQ-1.2
  - REQ-2.2

symbols:
  - User
  - Session
  - Journey
  - Booking

modules:
  - BookingService
  - BookingRepository

constraints:
  ...

allowed_changes:
  ...
```

Agent 每次只看到这个。

这非常像：

```text
compiler environment
```

---

# 14. ContextPack 还有一个非常好的性质

你以后可以比较：

```text
Agent + whole repository context
```

和：

```text
Agent + compiled context pack
```

然后证明：

```text
Token ↓
Hallucination ↓
Unrelated modifications ↓
Success rate ↑
```

这很可能成为非常好的实验点。

---

# 15. 整个系统最后其实会出现三种“图”

之前我们谈的是 Requirement DAG。

实际上最终最好有三个 Graph：

```text
Requirement Graph
REQ → REQ

Symbol Graph
User → Session → Booking → Journey

Module Graph
Page → API → Service → Repository → DB
```

它们之间通过 mapping 联系：

```text
REQ-3.2
   │
   ├── Booking
   │
   └── CreateBooking
            │
            ▼
     BookingPage
            ↓
     BookingAPI
            ↓
     BookingService
            ↓
     BookingRepository
```

这样以后处理 `REQ-3.2`：

不需要全文搜索项目。

直接沿图走。

---

# 16. 关于 DeepAgent，我最终会这样用

我会让你的系统大致变成：

```text
arc-compiler/
│
├── compiler/
│   ├── parser.py
│   ├── graph.py
│   ├── scheduler.py
│   ├── symbol_table.py
│   ├── linker.py
│   ├── validator.py
│   └── freezer.py
│
├── passes/
│   ├── requirement_fact_pass.py
│   ├── entity_design_pass.py
│   ├── api_design_pass.py
│   ├── ui_design_pass.py
│   └── module_design_pass.py
│
├── agents/
│   ├── llm_client.py
│   ├── test_agent.py
│   └── implementation_agent.py
│
└── artifacts/
    ├── requirement_ir/
    ├── facts/
    ├── symbols/
    ├── design/
    ├── skeleton/
    └── tests/
```

其中：

```text
requirement_fact_pass
entity_design_pass
api_design_pass
```

主要就是：

```text
LLM structured call
```

而：

```text
implementation_agent
```

则可以使用 Deep Agents。

因为 Deep Agents 本身就是面向长任务、filesystem、subagent、sandbox 等场景设计的 harness；官方的 `deepagents-code` 也是把它作为 coding-agent harness，而不是一个特定领域的 compiler controller。([GitHub][1])

---

## 因此，我对两个问题的答案可以压缩成两句话

**第一：不要“基于 DeepAgent 做需求编译器”，而是“自己实现需求编译器，并在需要自主迭代的阶段嵌入 DeepAgent”。** 特别是 Requirement Analysis / Design 阶段，我更推荐普通的 structured LLM calls；Implementation/TDD 阶段才适合 Agent loop。

**第二：全局设计不是依赖大 Context，而应该依赖 `local extraction → global symbol table → targeted retrieval → linking → validation → freeze`。** 换句话说，你真正要模拟的不是“一个架构师把所有需求记在脑子里”，而是**编译器通过多遍扫描和中间表示逐渐建立全局知识**。

我甚至认为这里可以形成你方法里最核心的两个结构：

```text
Requirement Fact IR
        ↓
Global Symbol Table
        ↓
Design IR
```

其中 **Global Symbol Table 正好是解决“大规模需求无法一次输入 LLM”问题的关键中间表示**。