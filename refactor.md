如果你的目标是把 ARC 真正设计成一个 **Requirement Compiler**，而不只是“多 Agent 工作流”，我建议项目结构也直接仿照 Clang/LLVM/MLIR 的思路：**核心是 IR、Pass、Analysis、Lowering、Codegen；LLM/Agent 只是某些 Pass 的实现机制。**

一个比较适合你当前 ARC 的整体结构可以是：

```text
arc/
├── compiler/
│   ├── frontend/              # Requirement Tree → canonical requirement model
│   ├── ir/                    # ARC IR definitions
│   ├── analysis/              # analyses over IR
│   ├── passes/                # IR construction / transformation passes
│   ├── validation/            # type / contract / consistency checking
│   ├── lowering/              # Design IR → Implementation IR
│   ├── codegen/               # Implementation IR → skeleton code
│   └── driver/                # compiler pipeline orchestration
│
├── synthesis/
│   ├── testgen/               # requirement-guided test generation
│   └── tdd/                   # RED → GREEN implementation
│
├── runtime/                   # build/test/runtime environment
├── targets/                   # React/FastAPI/etc. target definitions
├── agents/                    # LLM agents used by compiler passes
├── prompts/                   # prompt templates
├── tools/                     # LLM-accessible tools
└── cli/
```

其中最重要的是 `compiler/`。我会进一步设计成下面这样。

---

## 1. `frontend/`：ARC 的编译器前端

你的输入已经是 Requirement Tree，所以不需要传统意义上的 lexer/parser。

```text
compiler/frontend/
├── loader.py
├── canonicalize.py
├── requirement_graph.py
└── source_map.py
```

这里我建议明确一个 **Requirement IR / Source IR**。

例如：

```yaml
requirements:
  - id: REQ-1
    kind: feature
    text: Users can register an account.
    children:
      - REQ-1.1
      - REQ-1.2

  - id: REQ-1.1
    kind: data
    text: Email is required and unique.
```

因此你实际上不是两层 IR，而是：

```text
Requirement IR
      ↓
Design IR
      ↓
Implementation IR
```

---

# 2. `ir/`：整个 ARC 最核心的目录

建议不要让 Agent 自己维护随意的 JSON/YAML，而是定义正式的 IR schema。

```text
compiler/ir/
├── common/
│   ├── node.py
│   ├── types.py
│   ├── value.py
│   ├── reference.py
│   └── source.py
│
├── requirement/
│   ├── requirement.py
│   └── schema.py
│
├── design/
│   ├── database.py
│   ├── backend.py
│   ├── frontend.py
│   ├── contract.py
│   └── module.py
│
└── implementation/
    ├── file.py
    ├── symbol.py
    ├── type.py
    ├── function.py
    ├── component.py
    ├── import_.py
    └── project.py
```

形成三个 abstraction levels：

```text
IR Level 0
Requirement IR

        ↓ Design Passes

IR Level 1
Design IR
├── Database IR
├── Backend IR
└── Frontend IR

        ↓ Lowering

IR Level 2
Implementation IR
├── File
├── Symbol
├── Function
├── Type
├── Import
├── Route
└── Component

        ↓ Codegen

Source Code
```

这和 MLIR 的思路其实非常接近。

---

# 3. Design IR 内部不要物理割裂

逻辑上可以分 frontend/backend/database，但它们应该属于同一个 IR graph：

```text
DesignIR
│
├── Database
│   ├── Entity
│   ├── Field
│   └── Relation
│
├── Backend
│   ├── API
│   ├── FUNC
│   └── DB
│
└── Frontend
    ├── Page
    ├── Component
    ├── UI
    ├── Data
    ├── Event
    ├── Handler
    └── Effect
```

然后通过 reference 连接：

```text
Component
    │
 Handler
    │ invokes
    ▼
   API
    │ calls
    ▼
  FUNC
    │ calls
    ▼
   DB
    │ reads/writes
    ▼
Entity.Field
```

因此 `contract.py` 非常重要，例如定义：

```text
CallEdge
DataFlowEdge
ReadEdge
WriteEdge
InvokeEdge
ContainEdge
RequirementTrace
```

整个 Design IR 实际是：

> **typed heterogeneous graph**

而不是几份孤立 JSON。

---

# 4. `analysis/`：只分析，不修改 IR

这一点建议严格学习 LLVM。

```text
compiler/analysis/
├── requirement_coverage.py
├── call_graph.py
├── data_flow.py
├── dependency_graph.py
├── type_analysis.py
├── effect_analysis.py
└── reachability.py
```

例如：

```python
CallGraphAnalysis(design_ir)
DataFlowAnalysis(design_ir)
RequirementCoverageAnalysis(design_ir)
```

得到：

```text
REQ-1
 ├── page.register
 ├── api.register
 ├── func.register
 └── db.create_user
```

以及：

```text
UI.email
   ↓
Form.email
   ↓
API.email
   ↓
FUNC.email
   ↓
DB.email
   ↓
User.email
```

**Analysis 不应该修改 IR。**

---

# 5. `passes/`：ARC 真正的编译过程

这是我认为你目前架构最值得重构的地方。

不要让：

```text
Manager
 → InterfaceDesigner
 → TestGenerator
 → Developer
```

成为最高层抽象。

应该改成：

```text
Compiler
   ↓
PassManager
   ↓
Pass
   ↓
Pass
   ↓
Pass
```

例如：

```text
compiler/passes/
├── database/
│   ├── discover_entities.py
│   ├── infer_fields.py
│   └── infer_relations.py
│
├── backend/
│   ├── build_api.py
│   ├── decompose_api.py
│   ├── decompose_func.py
│   └── resolve_calls.py
│
├── frontend/
│   ├── discover_pages.py
│   ├── decompose_components.py
│   ├── infer_data.py
│   └── infer_behavior.py
│
└── linking/
    ├── link_requirements.py
    ├── link_frontend_backend.py
    └── link_backend_database.py
```

于是你的瀑布式设计就非常自然：

```text
RequirementIR
     │
     ▼
DiscoverEntitiesPass
     ↓
InferFieldsPass
     ↓
InferRelationsPass
     ↓
BuildAPIPass
     ↓
DecomposeAPIPass
     ↓
DecomposeFUNC Pass
     ↓
FrontendStructurePass
     ↓
FrontendBehaviorPass
     ↓
CrossLayerLinkPass
     ↓
DesignIR
```

某个 Pass 内部是不是调用 LLM，是 implementation detail。

比如：

```python
class DecomposeAPIPass(Pass):

    def run(self, module, context):
        result = llm.generate(...)
        ...
```

因此：

> **Agent 不再控制 compiler，Compiler 控制 Agent。**

我认为这个变化对 ARC 很关键。

---

# 6. `validation/`：类似 Compiler Verifier

每一个 Pass 后面都可以运行 verifier。

```text
compiler/validation/
├── verifier.py
├── type_checker.py
├── reference_checker.py
├── contract_checker.py
├── database_checker.py
├── frontend_checker.py
└── coverage_checker.py
```

例如：

```text
DecomposeAPIPass
       ↓
Design IR
       ↓
Verifier
       ├── reference valid?
       ├── input/output valid?
       ├── type compatible?
       ├── calls exist?
       └── requirement mapped?
```

这样你就能定义：

```python
pass_manager.run(
    pass_=DecomposeAPIPass(),
    verify_after=True,
)
```

非常接近真正的 compiler infrastructure。

---

# 7. `lowering/`：这是你第二个核心目录

这里专门负责：

> Design IR → Implementation IR

```text
compiler/lowering/
├── context.py
├── rules/
│   ├── type_rules.py
│   ├── naming_rules.py
│   ├── file_rules.py
│   └── symbol_rules.py
│
├── database/
│   └── lower_database.py
│
├── backend/
│   ├── lower_api.py
│   ├── lower_func.py
│   └── lower_db.py
│
├── frontend/
│   ├── lower_page.py
│   └── lower_component.py
│
└── linker.py
```

例如：

```text
Design IR

Entity(User)
   ↓

LowerDatabasePass
   ↓

Implementation IR

File:
 backend/models/user.py

Class:
 User

Fields:
 id: UUID
 email: str
```

注意 lowering 最好不要再做：

> “这个系统应该有没有 User？”

这种 semantic decision。

只做：

> “已经存在的 User 在 FastAPI + SQLAlchemy target 下如何 materialize？”

---

# 8. `targets/`：非常值得单独建立

如果你要真正强调“编译”，target 最好独立出来：

```text
targets/
├── react_fastapi/
│   ├── target.yaml
│   ├── types.py
│   ├── conventions.py
│   └── templates/
│
├── vue_fastapi/
└── react_express/
```

例如：

```yaml
frontend:
  framework: react
  language: typescript

backend:
  framework: fastapi
  language: python

database:
  orm: sqlalchemy

mapping:
  semantic_string: str
  semantic_integer: int
  semantic_datetime: datetime
```

所以编译过程成为：

```text
Design IR
   +
Target Description
   │
   ▼
Lowering
   │
   ▼
Implementation IR
```

这和：

```text
LLVM IR
   +
x86 target
   ↓
Machine IR
```

概念非常一致。

---

# 9. `codegen/`：必须尽可能 dumb

Code Generator 不应该调用 LLM。

```text
compiler/codegen/
├── generator.py
├── python/
│   ├── module.py
│   ├── class_.py
│   └── function.py
│
├── typescript/
│   ├── module.py
│   ├── interface.py
│   └── function.py
│
└── templates/
```

输入：

```yaml
file: backend/services/auth.py

symbols:
  - kind: function
    name: register_user
    parameters:
      - request: RegisterRequest
    return: RegisterResponse
```

确定性输出：

```python
def register_user(
    request: RegisterRequest,
) -> RegisterResponse:
    raise NotImplementedError()
```

所以：

```text
Implementation IR
       ↓
pretty printer / template / AST generator
       ↓
Source Files
```

Codegen 阶段最好做到：

**同一个 Implementation IR → 永远产生相同 Skeleton。**

---

# 10. `driver/`：把所有东西串起来

```text
compiler/driver/
├── compiler.py
├── pipeline.py
├── pass_manager.py
├── context.py
└── artifact_manager.py
```

顶层可能就是：

```python
compiler = ARCCompiler(target="react-fastapi")

compiler.compile(
    requirements="requirements.yaml",
    output="./generated",
)
```

内部：

```text
load requirements

→ build Requirement IR

→ run semantic passes

→ verify Design IR

→ lower Design IR

→ verify Implementation IR

→ codegen

→ build check
```

---

# 11. Agent 应该降级成 Compiler Service

你原来的 agents 不需要删除，但角色要发生变化。

```text
agents/
├── llm.py
├── structured_generation.py
├── repair.py
└── context_builder.py
```

然后：

```text
Compiler Pass
      │
      ▼
StructuredGenerationService
      │
      ▼
LLM
```

而不是：

```text
Agent
 ↓
决定下一步
 ↓
Agent
 ↓
决定下一步
```

这样 ARC 的 deterministic 部分和 probabilistic 部分就被明确隔离：

```text
Compiler
│
├── Deterministic
│   ├── Pass scheduling
│   ├── validation
│   ├── graph construction
│   ├── linking
│   ├── lowering
│   └── codegen
│
└── Probabilistic
    └── Semantic inference
            ↓
           LLM
```

---

# 12. Test/TDD 不建议塞进 Compiler Codegen

我会明确分开：

```text
arc/
├── compiler/
└── synthesis/
```

Compiler 到这里结束：

```text
Requirement
    ↓
Design IR
    ↓
Implementation IR
    ↓
Compile-ready Skeleton
```

然后：

```text
synthesis/
├── testgen/
│   ├── planner.py
│   ├── generator.py
│   └── traceability.py
│
└── tdd/
    ├── scheduler.py
    ├── developer.py
    ├── feedback.py
    └── repair.py
```

形成：

```text
                   ARC
                    │
          ┌─────────┴─────────┐
          ▼                   ▼
    ARC Compiler        ARC Synthesizer
          │                   │
Requirement Tree              │
     ↓                        │
Requirement IR                │
     ↓                        │
Design IR ────────────────────┤
     ↓                        │
Implementation IR ────────────┤
     ↓                        │
Skeleton ─────────────────────┤
                              ↓
                           Tests
                              ↓
                         RED → GREEN
                              ↓
                      Runnable Software
```

---

# 13. 最终我推荐的完整目录

如果现在让我直接给 ARC 开一个新 repo，我会采用：

```text
arc/
│
├── compiler/
│   │
│   ├── frontend/
│   │   ├── loader.py
│   │   ├── canonicalize.py
│   │   └── source_map.py
│   │
│   ├── ir/
│   │   ├── common/
│   │   ├── requirement/
│   │   ├── design/
│   │   │   ├── database.py
│   │   │   ├── backend.py
│   │   │   ├── frontend.py
│   │   │   └── contract.py
│   │   └── implementation/
│   │       ├── project.py
│   │       ├── file.py
│   │       ├── symbol.py
│   │       └── type.py
│   │
│   ├── analysis/
│   │   ├── call_graph.py
│   │   ├── data_flow.py
│   │   ├── dependency.py
│   │   └── coverage.py
│   │
│   ├── passes/
│   │   ├── database/
│   │   ├── backend/
│   │   ├── frontend/
│   │   └── linking/
│   │
│   ├── validation/
│   │   ├── verifier.py
│   │   ├── type_checker.py
│   │   └── contract_checker.py
│   │
│   ├── lowering/
│   │   ├── database/
│   │   ├── backend/
│   │   ├── frontend/
│   │   └── rules/
│   │
│   ├── codegen/
│   │   ├── python/
│   │   ├── typescript/
│   │   └── templates/
│   │
│   └── driver/
│       ├── compiler.py
│       ├── pipeline.py
│       └── pass_manager.py
│
├── targets/
│   ├── react_fastapi/
│   └── ...
│
├── synthesis/
│   ├── testgen/
│   └── tdd/
│
├── agents/
│   ├── llm.py
│   └── structured_generation.py
│
├── runtime/
│   ├── builder/
│   ├── tester/
│   └── sandbox/
│
├── prompts/
│
├── tools/
│
├── schemas/
│
├── tests/
│   ├── compiler/
│   ├── lowering/
│   └── codegen/
│
└── cli/
```

我认为其中最重要的架构变化可以概括成下面这张图：

```text
                    ARC Compiler

 Requirement Tree
       │
       ▼
┌──────────────────┐
│ Requirement IR   │       ← source-level IR
└────────┬─────────┘
         │
         │ Semantic Passes
         │   └── LLM allowed here
         ▼
┌──────────────────────────────┐
│       Design IR              │       ← High-level IR
│                              │
│ Database Backend Frontend    │
│    ╲      │      ╱           │
│     typed contracts          │
└────────────┬─────────────────┘
             │
             │ Analysis + Verification
             ▼
          Lowering
             │
             │ Target Profile
             ▼
┌──────────────────────────────┐
│     Implementation IR        │       ← Low-level IR
│                              │
│ File Symbol Type Signature   │
│ Import Export Route Binding  │
└────────────┬─────────────────┘
             │
             │ Deterministic Codegen
             ▼
┌──────────────────────────────┐
│   Compile-ready Skeleton     │
└────────────┬─────────────────┘
             │
─────────────┼──── Compiler boundary ─────
             │
             ▼
      Requirement Tests
             │
             ▼
        TDD Synthesis
             │
             ▼
      Runnable Software
```

**如果你准备真正重构 ARC，我最建议先做的不是 Agent，而是先固定 `Requirement IR → Design IR → Implementation IR` 三层数据模型，以及 `PassManager + Verifier + Lowering` 四个基础设施。** 一旦这几个东西固定下来，数据库设计、前端设计、后端设计都只是不同的 compiler passes；LLM 也只是 Pass 中用于补充 semantic information 的工具，而整个 ARC 的架构就真正从“Agent Workflow”转变成了“Agentic Compiler”。