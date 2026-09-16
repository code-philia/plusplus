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

其中 Stage 1 负责把 Requirement IR 编译为 Database Schema IR。当前四个 Pass、容错边界、
静态校验和输出产物见 [Stage 1 数据库 Schema 编译实现指导](stage1_data_schema.md)。

Stage 2 先生成精简的 Requirement Contract，再进行 Requirement→API→FUNC→DB 的逐层拆解。模型在每次
拆解前自行思考当前模块的完成路径，但只返回直接子模块的 `kind/name/spec/inputs/outputs/effects` 固定模板。
Compiler 根据列表顺序和接口自顶向下物化子模块。最终在 `.arc/design/` 输出四张 JSON 列表符号表：
`requirement_contracts.json`、`api_modules.json`、`function_modules.json` 和 `db_modules.json`。调用及其 binding
在内部表示数据流，落盘模块只记录 `callers` 与 `callees`；需求到设计符号的简洁关联写入
`traceability/requirements.json`。当前不设计 UI。当前实现约束见
[Stage 2 Design IR](stage_2_design_ir.md)，设计原则见 [Stage 2 指导](stage_2_guide.md)。

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
