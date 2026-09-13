# ARC 确定性编译器迁移基线

> 状态：guide 已完成通读，本文冻结当前系统的可观察行为，并把 guide 转译为第一版迁移边界。
> 本文不授权在兼容性门禁建立前直接删除现有实现。

## 1. 已确认的目标

ARC 的下一版应当是一个由确定性 compiler controller 主导的需求编译系统，而不是一个
按阶段提示代码智能体自行探索、设计并修改仓库的系统。LLM 仍可用于无法由传统规则完成的
语义转换，但只能作为 compiler pass 的执行器：controller 决定输入切片、response schema、
接受条件、重试、链接、冻结、调度和副作用。

同一份规范化输入、编译器版本和配置应产生稳定的 Requirement IR、图、符号、编译计划、
产物位置和 traceability 记录。只要仍使用生成模型，就不能默认承诺每次生成的实现正文
字节完全相同；第一版“确定性”的硬保证应集中在控制流、状态转换、命名、排序、权限、
结构、契约、验证和产物记录。需要字节级可重复时，应增加 pass 输入哈希、模型配置锁定和
已接受响应缓存。

第一版可以合并 guide 中仅为理论完整性而存在的重复扫描或 parser pass，但任何合并都必须：

1. 保留明确的输入、输出和错误语义；
2. 让每个转换结果可序列化、可比较、可追踪；
3. 不把语义决策重新藏进模型提示词或自由修改工作区的智能体中。

## 2. 当前系统的真实执行模型

当前入口链路是：

```text
arc compile
  -> main.CompilationConfig
  -> core.workflow.ARCWorkflowManager
  -> app_type_handler 初始化模板和依赖
  -> 为每个需求节点建立 DESIGN / IMPLEMENT 队列
  -> DESIGN: InterfaceDesigner + TestGenerator
  -> IMPLEMENT: TestDrivenDeveloper + 测试/修复循环
  -> .arc 状态、事件、traceability 和 Git checkpoint
```

确定性部分主要负责路径处理、模板复制、队列状态、JSON 持久化、命令执行、日志和事件。
真正决定接口、测试和实现内容的部分位于 `src/agents/` 及 `core/phases.py`，依赖模型、提示词、
工具调用和工作区探索。因此当前架构是“确定性调度器包裹代码智能体”，不是需求编译器。

## 2.1 guide 的架构不变量

guide 可以压缩成下面六条不可破坏的规则：

1. **Design 是 whole-program 的，Implementation 是 incremental 的。** 所有需求先完成全局
   发现、链接和设计冻结，之后才按 Requirement DAG 的 wave 实现 ATOMIC 节点。
2. **Skeleton 不是 IR。** Skeleton 是 Design IR 的 lowering/materialization 结果；设计事实
   不能只存在于生成代码里。
3. **LLM 不控制编译。** parser、DAG、artifact/version manager、schema validator、linker、
   freezer、scheduler、test runner 和 patch verifier 都由普通程序实现。
4. **先提取事实，再进行设计。** 大需求按节点提取带 provenance 的结构化 Fact IR，随后
   map/reduce 为 Global Symbol Table；不能用自然语言 summary-of-summary 代替事实。
5. **结构不可变，行为可变。** 全局结构和公共 contract 冻结；实现阶段只获得指定文件、
   指定 symbol 和 implementation region 的修改能力，并由 hash/AST/patch 检查强制执行。
6. **设计变化必须显式。** 实现遇到缺失 contract 时返回 `DESIGN_CHANGE_REQUEST`，由
   recompiler 计算影响范围、重新验证并冻结，不能让实现 worker 顺手修改设计。

guide 的一句话核心是：

> Requirements are globally compiled into a frozen structural program before behavior is incrementally synthesized.

## 2.2 三层 IR、五类核心产物和三张图

guide 实际描述了三层语义表示：

- **Requirement IR**：YAML 的规范化 AST、FOLDER/ATOMIC 语义、依赖和来源位置；
- **Fact/Symbol IR**：逐节点事实、provenance、六类 registry，以及可查询的 Global Symbol Table；
- **Design IR**：Page、Component、API、Service、Function、Repository、Entity、Schema、State、
  ownership、contract、dependency、side effect 和 data flow。

建议落盘的五类核心产物是：

1. `requirement_ir.json`；
2. `dependency_graph.json`；
3. `global_design.json`（guide 示例使用 YAML，第一版统一 JSON 更贴合现有 `.arc` 外观）；
4. `module_registry.json`；
5. `skeleton_manifest.json`。

此外还应保留可独立查询并互相映射的三张图：

- Requirement Graph：`REQ -> REQ`；
- Symbol Graph：实体、状态、操作、页面和约束之间的关系；
- Module Graph：`Page -> API -> Service -> Repository -> DB`。

`REQ -> Symbol -> Module -> Artifact/Test` 的映射是 traceability 的主轴，而不再只是 agent
执行过程的旁路记录。

## 2.3 guide 对 pass 的完整语义

逻辑上需要覆盖以下转换；第一版允许合并实现扫描，但不能丢失中间语义或验证点：

```text
Requirement load/parse/normalize
  -> structural validation
  -> Requirement AST + provenance
  -> dependency resolution + cycle/missing-reference diagnostics
  -> per-ATOMIC-node fact extraction
  -> structured fact merge/reduce
  -> global registries + Global Symbol Table
  -> query-driven data/UI/API/service/module design
  -> symbol/module linking
  -> cross-node dataflow and consistency validation
  -> DISCOVERED -> PROPOSED -> RESOLVED -> FROZEN
  -> whole-program skeleton lowering
  -> build/type validation
  -> requirement acceptance tests + Design IR contract tests
  -> topological node implementation waves
  -> node tests + dependent regression + frozen-hash verification
  -> acceptance result and traceability projection
```

六类 global registry 是 Entity、State、Operation、Page、Interaction 和 Cross-cutting。检索应优先
依靠 requirement dependency、symbol reference 和 graph traversal；embedding 只能作为 fallback，
不能决定必需事实是否进入 Context Pack。

## 2.4 Module Registry 是执行控制面

Module Registry 不只是报告表，而是 linker、lowering、权限和验证共同使用的核心结构。至少要有：

- `module_id`、`kind`、`owner_requirement`、`file`、`symbol`；
- `exports`、`inputs`、`outputs`、`dependencies`；
- `side_effects`、`data_entities`；
- `visibility`、`editable_region`/capability；
- `contract_status`、`implementation_status`；
- `contract_hash`、`structure_hash`。

实现 worker 的 interface 不应是“实现 REQ-X 并自由编辑仓库”，而应接收 compiler 生成的
Context Pack 和 capability：目标 requirement、Design IR slice、可读 contract、可写 module/
region、测试与约束；输出只能是结构化 patch 或 design-change request。

## 2.5 guide 与当前实现的直接冲突

| guide 要求 | 当前实现 | 迁移动作 |
| --- | --- | --- |
| Whole-program Design 后再实现 | 每个节点依次 DESIGN/IMPLEMENT | 重写 queue/scheduler |
| FOLDER 只负责 scope/aggregation | FOLDER 也进入两个阶段任务 | 仅 ATOMIC 成为 implementation unit |
| Skeleton 由 Design IR lowering | agent 在 DESIGN 阶段直接写 workspace | 引入显式 IR 和 emitter |
| 事实提取与设计分离 | InterfaceDesigner 直接设计 | 新增 Fact IR / Symbol Table |
| 两类测试有独立来源 | TestGenerator 围绕节点和 interface 生成 | 分开 acceptance 与 contract lowering |
| machine-enforced writable region | 主要依赖 prompt/filesystem permission | patch/AST/hash verifier |
| 实现按 DAG wave | 当前是 requirement tree 的前序/后序队列 | 基于显式依赖做拓扑调度 |
| 全 TypeScript web target | 当前默认 Express backend 模板仍是 JavaScript | 升级默认 web backend/shared contracts |
| Context Pack 由 compiler 构造 | agent context 会探索项目并汇总 | 改为图查询产生闭合 slice |
| 显式 design-change request | agent 可直接调整接口/代码 | 引入诊断与 recompile 状态机 |

guide 中“全部 TypeScript”指生成的默认 web target，而不是要求把 ARC controller 从 Python
重写成 TypeScript：guide 后半部分仍以 Python controller/module 布局举例。现有 `--type` 外部
interface 可以保留，但 guide 的第一版完整 backend 应以 web TypeScript 为基准；CLI/Android
adapter 不应阻塞 compiler front end 和 IR 的落地。

## 3. 必须先冻结的外部接口

### 3.1 CLI

保留现有命令名和主要参数：

- `arc --version`
- `arc compile <requirement_path> -o <output_dir>`
- `--type {web,android,cli}`、`--port`、`--clean`
- `--resume`、`--retry-failed`、`--retry NODE_ID...`
- `arc doctor`
- 当前退出码语义：成功 `0`、编译失败 `1`、参数组合错误 `2`

当前 CLI 文案、banner、启动摘要、进度输出和最终摘要属于兼容性表面。迁移时可以改变其内部
事件来源，但不应无意改变用户可见格式。

`build_config_parser()` 由 `build_doctor_parser()` 间接注册；这个不直观的调用关系应由 CLI
契约测试覆盖，后续可以整理实现，但继续保留 `arc config` 外部命令。

另一个已验证的问题是 `src/main.py` 在模块导入阶段就加载 `core.workflow`，继而强制导入
`deepagents`。在没有安装 agent 依赖时，连 `arc --version`、`arc --help` 和
`arc compile --help` 都会以 `ModuleNotFoundError` 失败。新 seam 应让纯 CLI 查询不依赖任何
模型或编译 backend；迁移的第一个兼容性测试需要固定这一点。

### 3.2 输出目录与运行状态

需要保留或明确迁移的 `.arc` 外观：

```text
.arc/
  debug.log
  processing_queue.json
  runner-events.jsonl
  traceability/
    requirements.json
    scenarios.json
    interfaces.json
    tests.json
    call_edges.json
    node_states.json
    node_contracts.json
```

环境变量 `ARCBENCH_TRACEABILITY_DIR` 可以覆盖 traceability 目录；运行事件还可通过
`ARCBENCH_RUNNER_EVENTS_PATH` 输出 JSONL。文件写入目前采用临时文件替换，适合保留为
确定性编译器的持久化基础。

### 3.3 Traceability 表

现有 `TraceabilityStore` 是最值得复用的深模块之一：调用方只需要按稳定 id 读写记录，
排序、原子写入和事件通知隐藏在实现内。

现有核心记录形状如下：

- requirement：`req_id`、名称、描述、父子关系、依赖、视觉引用；
- scenario：`scenario_id`、`req_id`、名称、步骤；
- interface：`interface_id`、关联 `req_ids`、类型、内容、文件定位、实现状态、调用关系；
- test：`test_id`、`req_id`、类型、文件定位、关联接口、通过状态、场景 id；
- call edge：需求与接口之间的有向调用边；
- node state：需求节点编译状态；
- node contract：节点阶段产物摘要。

迁移时应保留表名和已发布字段，并通过增加 `schema_version`、编译器版本、输入摘要、pass id、
产物摘要等字段来扩展，不应把内部 Python 对象直接泄漏为新的外部格式。

### 3.4 应用类型适配

`AppTypeHandler` 的模板选择、先决条件检查、workspace 初始化、build/test 执行是可复用的 seam。
第一版编译器可以继续支持 `web`、`android`、`cli` 三个 adapter，同时把代码生成从当前
agent 写文件改成确定性的 backend/emitter。`web.py` 和 `android.py` 体积很大，迁移前应先用
契约测试覆盖其命令、路径和输出解析行为，再拆分实现。

## 4. 代码处置边界

### 4.1 优先保留并加契约测试

- `src/main.py` 中的参数和路径接口；
- `src/core/cli.py`、`src/core/logging.py` 的用户可见输出；
- `src/arcbench_agent_runtime/jsonio.py`、`events.py`、`traceability.py`；
- `src/app_type_handler/` 中模板初始化、构建、测试及输出解析能力；
- `src/arc-template/` 模板仓库及其 catalog；
- canonical example 的需求、视觉引用与端到端场景；
- Git checkpoint 能力（作为可选副作用，不作为语义正确性的来源）。

### 4.2 需要以编译器实现替换

- `src/agents/interface_designer.py`；
- `src/agents/test_generator.py`；
- `src/agents/test_driven_developer.py`；
- `src/agents/context/`、`src/agents/model/`、`src/agents/runtime/`、`src/agents/skills/`；
- `src/core/phases.py` 中围绕 agent 响应、重试和修复循环的阶段逻辑；
- `src/core/workflow.py` 中直接构造三个 agent 的部分。

这些文件现在不能直接删除：它们仍然定义了阶段结果形状、日志事件和若干隐含输出契约。
应先提取 golden fixtures 和兼容性测试，再以新模块替换调用链，最后删除。

### 4.3 需要重新判断

- `processing_queue.json` 当前以每个节点的 DESIGN/IMPLEMENT 为任务。新编译器更适合记录
  全局 pass 和目标产物；为兼容 resume，可保留文件名并升级内部 schema。
- `node_sessions/` 保存的是旧 agent 恢复上下文。第一轮清理已停止生成并删除对应实现；
  pass 状态统一进入 schema v2 的 `processing_queue.json`。
- `interfaces` 当前混合“Design IR contract”与“生成代码位置”。应在内部拆成 Symbol、Module、
  Contract 和 Artifact Mapping，再由现有 `interfaces.json` 提供兼容投影。
- `openai`、`deepagents`、`langchain-openai` 不属于 compiler controller。普通 OpenAI structured
  call 可作为 semantic pass adapter；DeepAgent 最多保留给受 capability 限制的实现 worker。
  如果第一版选择不启用某类 worker，再删除对应依赖，避免半迁移状态无法运行。

## 5. 目标模块骨架

新系统应把复杂度放到少量深模块后面，CLI 所依赖的外部 seam 为：

```text
Compiler.compile(CompilationRequest) -> CompilationResult
```

其实现内部可按第一版需要合并 pass，但逻辑职责至少要可区分：

```text
source loader
  -> parser + structural validation
  -> Requirement IR + DAG
  -> fact discovery + symbol table
  -> global design + linking + freeze
  -> skeleton/test lowering
  -> topological implementation plan
  -> capability-restricted workers
  -> build/test/hash verifier
  -> traceability projection + persisted result
```

建议的新代码放在 `src/compiler/`，不要继续扩张 `core/`。`core` 保留进程级基础设施，
`compiler` 拥有语言语义和转换规则，`app_type_handler` 逐步收敛为 backend 所依赖的运行 adapter。
每个 pass 接受不可变或按约定只读的 IR，并返回新结果与 diagnostics；不要让 pass 自由扫描和
编辑整个 workspace。

内部只在确有不同实现时建立 seam：

- `SemanticTransformer`：至少可以有 LLM structured-output adapter 和 fixture/cached adapter；
- `Backend`：web/cli/android 是三个真实 adapter；
- `ArtifactStore`：第一版只需要 JSON filesystem implementation，不预设数据库 abstraction；
- `ImplementationWorker`：如果只有 DeepAgent 一种实现，先保持内部模块，不制造假想 interface。

## 5.1 第一版允许的 pass 合并

为了尽快形成可运行闭环，第一版不必机械实现九个独立 Design pass。建议合并为四段，同时
保留每段的结构化输入输出和 diagnostics：

1. **Front end**：parse、normalize、结构校验、provenance、Requirement DAG；
2. **Discovery**：逐 ATOMIC 节点 Fact IR，加确定性的 registry merge 和 symbol resolution；
3. **Design**：按 symbol 查询相关事实，分 data/UI/API/module 设计，再统一 link/validate/freeze；
4. **Lowering & execution**：skeleton、两类 tests、DAG wave、受限实现、回归和 frozen verification。

可以减少扫描次数，但不能省略以下结果：provenance、unresolved symbol、ownership、dependency、
freeze 状态、writable capability 和 contract/structure hash。

## 5.2 确定性保证分级

- **必须保证**：输入规范化、id、排序、DAG、状态机、context selection、schema validation、
  linker、权限、冻结检查、产物路径和 traceability 投影；
- **通过约束收敛**：LLM 产生的 Fact/Design/Test IR，只能在 schema 和语义 validator 接受后进入
  编译状态；无效结果不产生工作区副作用；
- **默认不承诺**：模型生成的实现正文逐字节相同；如实验要求可复现，使用内容寻址缓存把
  已接受的 pass 结果固定下来。

## 6. 删除前门禁

在“清空大部分代码”之前，至少完成以下准备：

1. 版本化 `guide.md`，并在实现任务中把每条算法规则标注到对应 pass、IR 字段和 diagnostic；
2. 记录 `arc --help`、`arc compile --help`、`arc --version` 的 golden 输出；
3. 用固定 requirement fixture 记录 `.arc` 目录、七张 traceability 表和 queue 的 schema；
4. 为 requirement 加载、拓扑/树顺序、原子 JSON、resume 状态转换编写聚焦测试；
5. 定义确定性口径：排序、id 生成、路径规范化、时间戳隔离、配置摘要和输出覆盖策略；
6. 让 CLI 先只依赖新的 `Compiler` interface，再删除旧 agent 调用链；
7. 最后移除模型依赖和旧 prompt/skill，不做“新旧两套长期并存”。

## 7. 下一阶段产物

理解阶段完成后，实施前还需要把上述语义固化为可执行规格：

- 第一版 Requirement IR、Fact IR、Symbol Table、Design IR 和 Module Registry schema；
- compiler diagnostic taxonomy，包括 parse、dependency、link、validation、freeze 和 capability 错误；
- 现有文件到新模块的逐文件保留/迁移/删除清单；
- 小步提交顺序及每步验证方式；
- 首个可运行的 parser/DAG/artifact-store/compiler skeleton；
- ticketbooking demo 的首套 golden IR 与 traceability projection。
