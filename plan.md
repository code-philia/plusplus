## 接下来的推荐步骤

### 第一步：完成 Backend / Frontend Pass 的真实拆分

目前这两个阶段只是“新入口 + 旧 monolith”。

建议先把：

design_stage.py
frontend_generation.py

中的实际逻辑拆成真正的 pass：

Backend:
BuildAPIPass
DecomposeAPIPass
DecomposeFUNCPass
ResolveCallsPass

Frontend:
DiscoverPagesPass
DecomposeComponentsPass
InferDataPass
InferBehaviorPass

每个 pass 应该具备：

class Pass:
    input_level = ...
    output_level = ...

    def run(self, module, context):
        ...

每个 pass 只负责一个阶段，不能继续由一个旧类隐式完成全部流程。

———

### 第二步：建立真正的 Design IR 图

目前 BackendDesignIR 和 FrontendDesignIR 仍然是对旧字典的 typed view。

下一步应该让它们成为真实的 canonical representation：

DesignModule
├── database nodes
├── backend nodes
├── frontend nodes
└── typed edges

所有跨层关系统一通过：

CallEdge
DataFlowEdge
ReadEdge
WriteEdge
InvokeEdge
ContainEdge
RequirementTrace

表示，不再依赖各处的：

"id"
"parent_id"
"component_id"
"api_ids"
"callee"
"owner_requirement"

隐式连接。

———

### 第三步：实现 Linking Pass

这是连接三类 Design IR 的关键阶段：

Requirement → Backend API
Requirement → Frontend Page
Frontend Event → Backend API
Backend API → FUNC
FUNC → DB Operation
DB Operation → Entity / Field

建议创建：

compiler/passes/linking/
├── link_requirements.py
├── link_frontend_backend.py
└── link_backend_database.py

并将现在分散在：

- design_stage.py
- frontend_generation.py
- code_binding.py
- design_projection.py

里的连接逻辑收敛进去。

———

### 第四步：实现 Validation / Verifier

建议顺序：

1. reference_checker.py
2. contract_checker.py
3. database_checker.py
4. frontend_checker.py
5. coverage_checker.py
6. 总入口 verifier.py

每个 pass 结束后执行：

Pass
↓
IR
↓
Verifier
↓
下一 Pass

例如：

pass_manager.run(
    pass_=DecomposeAPIPass(),
    verify_after=True,
)

———

### 第五步：把 lowering 真正改成 Design IR → Implementation IR

现在的 lowering 还直接吃字典。

目标应变成：

BackendDesignIR
    ↓
BackendLowering
    ↓
ImplementationIR

以及：

FrontendDesignIR
    ↓
FrontendLowering
    ↓
ImplementationIR

数据库 lowering 也同理：

DatabaseDesignIR
    ↓
DatabaseLowering
    ↓
ImplementationIR

这一步完成后，旧的：

- GlobalSymbolPlanner
- GlobalFilePlanner
- TypeLowerer
- ModuleSkeletonLowerer
- BackendGlueLowerer

应该逐渐变成 Implementation IR 构建器，而不是直接生成最终源码。

———

### 第六步：实现 deterministic Codegen

Codegen 不应该再承担语义判断。

应建立：

compiler/codegen/
├── generator.py
├── python/
├── typescript/
└── templates/

目标：

同一个 ImplementationIR
    ↓
永远生成相同源码

Codegen 只负责：

- 文件生成
- import 输出
- 类型输出
- 函数签名输出
- component 输出
- route 输出
- template rendering

不调用 LLM，也不决定系统设计。

———

### 第七步：完善 Target Profile

建立：

targets/react_fastapi/
├── target.yaml
├── types.py
├── conventions.py
└── templates/

把当前散落的 React、FastAPI、TypeScript、Drizzle 约定移动到 target profile。

之后 driver 应支持：

ARCCompiler(target="react-fastapi")

并把 target profile 传入 lowering 和 codegen。

———

### 第八步：最后清理旧模块

只有在新 pass、IR、lowering、codegen 全部稳定后，才适合删除：

- design_stage.py
- frontend_generation.py
- database_stage.py
- backend_lowering.py
- frontend_react_lowering.py
- 各种旧兼容入口

当前还不建议删除，因为它们仍然承载主要行为逻辑。

———