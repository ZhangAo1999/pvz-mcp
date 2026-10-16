# 开发说明

## 源码结构

| 文件 / 目录 | 用途 |
| --- | --- |
| `app/mcp-server.mjs` | MCP 接口、参数校验和后端通信 |
| `app/pvz.py` | Win32 控制后端、场景与动作逻辑 |
| `app/runtime_bridge.py` | EXE 识别、进程启动与运行时桥接加载 |
| `app/bridge_asm.py` | 当前构建的桥接汇编、地址和调用逻辑 |
| `app/state.py` | 游戏对象与 JSON 状态解码 |
| `app/collect_extension.py` | 原生拾取扩展 |
| `app/selection.py` | 临时选关及进度保留 |
| `app/presentation.py`、`app/spectator.py` | 观战设置、计时和提示窗口 |
| `packaging/Installer.cs` | EXE 安装器界面、兼容性校验、解包及安装流程 |
| `packaging/build_installer.py` | 使用 Windows .NET 编译器生成 EXE |
| `packaging/` 其他文件 | 离线打包、安装、稳定 MCP 入口和版本回退 |
| `tests/` | 回归测试 |

## 从源码运行

已验证的环境为 Windows x64、Node.js 24 和包含 tkinter 的 Python 3.8。Node.js 至少需要 20.3，更新检查的代理功能需要 24.5 或更新版本；其他运行时版本需要自行验证。

在仓库根目录执行，替换 Python 和游戏路径：

```powershell
npm install --ignore-scripts
$env:PVZ_DATA_DIR = 'D:\PvZ-MCP-Dev\data'
$env:PVZ_PYTHON = 'D:\Programs\Python\python.exe'
$env:PYTHONPYCACHEPREFIX = 'D:\PvZ-MCP-Dev\cache\python'
& $env:PVZ_PYTHON .\app\configure.py 'D:\Games\PvZ\PlantsVsZombies.exe'
```

配置脚本会检查游戏兼容性，并在 `PVZ_DATA_DIR` 写入工具配置，不改写游戏。

让 MCP 客户端运行 `node`，参数为仓库中 `app/mcp-server.mjs` 的绝对路径，同时传入上面的环境变量。依赖默认从仓库的 `node_modules` 加载，也可通过 `PVZ_SDK_ROOT` 指定其他依赖目录。

配置示例中的路径需替换为实际安装位置：

```json
{
  "mcpServers": {
    "pvz": {
      "command": "node",
      "args": ["D:\\Projects\\PvZ-MCP\\app\\mcp-server.mjs"],
      "env": {
        "PVZ_DATA_DIR": "D:\\PvZ-MCP-Dev\\data",
        "PVZ_PYTHON": "D:\\Programs\\Python\\python.exe",
        "PYTHONPYCACHEPREFIX": "D:\\PvZ-MCP-Dev\\cache\\python"
      }
    }
  }
}
```

## 测试

配置好环境后，可运行只读 MCP 联调：

```powershell
node .\app\smoke-mcp.mjs
```

它检查工具注册、状态、存档信息、模式目录和参数校验，不启动关卡或修改存档。

回归测试：

```powershell
$env:PYTHONPATH = Join-Path $PWD 'app'
& $env:PVZ_PYTHON -m unittest discover -s tests -p 'test_*.py'
```

原始 EXE 的静态交叉验证需要设置 `PVZ_TEST_GAME`；可选本地化检查需要 `PVZ_TEST_LOCALIZATION`。缺少输入时对应检查会跳过。测试生成物集中保存在 D 盘临时目录，供手动清理。

## 更新检查的测试与发布

更新模块的离线回归测试：

```powershell
node --test .\tests\test_updates.mjs
```

`app/updates.mjs` 直接查询本仓库的公开 Release 列表，不依赖 GitHub 登录。MCP 和 `Manage.ps1 -Action CheckUpdate` 共用该实现。缓存和设置位于数据目录，不属于源码或构建产物；发布预览版时，须勾选 GitHub 的 Pre-release，并上传名称与版本匹配的 EXE。使用新版本号构建与发布，保留旧版本供回退。

## 添加游戏版本适配

游戏版本之间可能存在函数地址、调用约定、对象字段、菜单流程和存档格式差异。新增适配主要涉及 `runtime_bridge.py`、`bridge_asm.py`、`pvz.py`、`state.py` 和 `collect_extension.py`。

保留严格的版本识别，为新增构建补充静态校验和实机测试。分别验证读取、暂停推进、种植、拾取、菜单与存档等能力，只开放已验证的功能，并更新 README 的兼容性表。

不要仅删除哈希检查或添加白名单来适配版本。仓库中不要加入游戏 EXE、资源、用户存档或本机运行日志。

## 构建 EXE 和离线包

打包脚本从构建环境收集运行时和已安装的依赖。可通过以下变量指定位置：

- `PVZ_BUILD_SDK`：包含 MCP SDK 与 Zod 的 `node_modules` 目录。
- `PVZ_BUILD_PYTHON_DIR`：完整 Python 安装目录，默认使用执行脚本的 Python 环境。
- `PVZ_BUILD_NODE_DIR`：包含 `node.exe` 与许可证文件的 Node.js 目录。

```powershell
$env:PVZ_BUILD_SDK = Join-Path $PWD 'node_modules'
& $env:PVZ_PYTHON .\packaging\build_release.py 'D:\Builds\PvZ-MCP'
```

当前 Python 运行库文件清单按 3.8 布局编写，换用其他版本时需要调整清单并重新验证。输出目录需为空或尚不存在，已生成的同版本目录不会被覆盖。

产物包括 EXE 安装器、ZIP 离线包、源码包和 SHA-256 清单。EXE 嵌入同一份 ZIP 包，复用 `Install.ps1` 的安装与更新逻辑。源码包排除 Git 内部文件、依赖、运行数据和备份；游戏文件不属于任何构建产物。


### EXE 构建要求

`build_release.py` 从 `package.json` 读取版本号，同时构建安装器。更新版本时修改该版本号，再运行构建命令；输出目录应在源码树之外或仓库的 `dist` 下。

安装器由仓库中的 C# WinForms 源码编译，不依赖下载的打包器。默认使用 Windows .NET Framework 的 `csc.exe`，也可设置 `PVZ_CSC` 指定编译器。构建产物未做代码签名。

`app/compatibility.json` 提供安装器显示所需的构建哈希，后端仍保留严格的运行时校验。新增支持项必须同时实现对应适配，不能只修改这个 JSON。

可单独重新编译已有离线包的安装器：

```powershell
& $env:PVZ_PYTHON .\packaging\build_installer.py 'D:\Builds\PvZ-MCP\离线包目录' 'D:\Builds\PvZ-MCP\离线包.zip' 'D:\Builds\PvZ-MCP\Setup.exe'
```

EXE 提供 `--self-test 报告路径`，检查嵌入资源与兼容性分类，并输出界面预览。安装回归测试可使用 `--test-install 目标目录 游戏EXE或none 报告路径`；该测试入口只允许写入 `D:\Temp\Codex` 下的测试目录。测试不启动游戏。
