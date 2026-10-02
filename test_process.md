已添加独立配置：/D:/project/agentic-requirement-compiler/workspace/bookstack-1-from-frontend-ir-80d410fc/validation/playwright.config.ts。已验证可以发现 34 个验收测试，尚未实际
执行浏览器测试。

配置已设置：

- 仅执行 validation/REQ-*.spec.ts。
- Chromium，1 个 worker，不重试。
- 每条测试、断言、操作、导航超时均为 10 秒。
- 失败时保存截图和 trace，并生成 HTML 报告。

下面是完整的 Windows PowerShell 流程。每一步成功后再执行下一步；使用 npm.cmd、npx.cmd 可以避免 PowerShell 的脚本执行策略问题。

1．安装依赖和浏览器

在项目根目录执行。项目要求 Node.js ≥ 22.12.0。

cd D:\project\agentic-requirement-compiler\workspace\bookstack-1-from-frontend-ir-80d410fc

npm.cmd ci
npx.cmd playwright install chromium

Playwright 已在项目依赖中，无需另外安装最新版。

2．构建前端

后端会托管 frontend/dist，而 backend 的 start 不会构建前端，所以先执行：

npm.cmd run build

3．在终端 A 初始化数据并启动后端

cd D:\project\agentic-requirement-compiler\workspace\bookstack-1-from-frontend-ir-80d410fc\backend

$env:PORT = "33003"
$env:NODE_ENV = "test"

# 每轮使用新的验收数据库，避免上次增删改的数据影响结果
$runId = [guid]::NewGuid().ToString("N")
$env:DATABASE_URL = "./data/validation-$runId.db"

node ./init-db.mjs
npm.cmd run start

保持该终端运行。初始化脚本负责导入现有 fixture 数据，后端启动时只初始化表结构，因此不要省略初始化步骤。

这里使用文件数据库，不要改成 :memory:：初始化脚本和后端是两个进程，内存数据库无法共享。

项目没有自动加载 .env 的代码，因此上述环境变量需要显式设置。

4．在终端 B 检查服务并执行验收

cd D:\project\agentic-requirement-compiler\workspace\bookstack-1-from-frontend-ir-80d410fc

$env:ARC_TEST_BASE_URL = "http://127.0.0.1:33003"


# 检查前端页面，应返回 200
(Invoke-WebRequest -UseBasicParsing "$env:ARC_TEST_BASE_URL/").StatusCode

# 执行全部验收测试
npx.cmd playwright test --config validation/playwright.config.ts --workers=1 --timeout=10000

前端页面与 API 都由 33003 端口提供，无需另外启动 Vite。新配置不自动启动服务，完全使用终端 A 中的 npm run start。

注意：这里的 10 秒是每条测试的总时间预算，不是每个步骤都能额外获得 10 秒；依赖安装、构建和部署不受这个限制。

5．查看结果

npx.cmd playwright show-report validation/playwright-report

失败截图和 trace 位于 validation/test-results/。

仅运行某一个验收文件：

npx.cmd playwright test --config validation/playwright.config.ts REQ-2.2.spec.ts --workers=1 --timeout=10000

更换端口或重新测试

若 33003 被占用，例如改为 33004，需要同时修改终端 A 的 PORT 和终端 B 的 ARC_TEST_BASE_URL。配置中 ARC_TEST_BASE_URL 优先于 ARC_E2E_PORT。

重新执行完整验收前，在终端 A 按 Ctrl+C 停止后端，再重复第 3 步生成新的数据库、初始化并启动。单 worker 可以避免并发写入，但不会自动恢复被测试修改的数据。