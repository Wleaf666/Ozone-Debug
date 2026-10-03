# Ozone Debug

通过 Ozone Automation Socket 操作目标板内存的 Windows 桌面调试面板。用按钮执行设零、使能、升降、翻转和夹爪开合，不再逐项手动修改 Watch 变量。

界面使用 **PySide6 / Qt Widgets**，通信使用 Python 标准库。当前默认配置对应 **STM32F407 + 两台 M3508/C620 + 两个气动夹爪**；变量表达式、类型和写入权限可自行配置。

> 本工具不是硬件急停。关闭窗口、最小化或断开连接不会停止机构。命令被接受不代表机械动作已经完成。

## 功能

- 连接本机 Ozone，读取版本并进行只读能力诊断。
- 解析 ELF 符号和对象成员地址，不硬编码堆对象地址。
- 升降：设零、使能、失能、正负相对位移、停止保持。
- 翻转：设零、使能、失能、到 180°、回 0°、停止保持。
- 两个夹爪独立开合。
- 显示命令结果、操作记录、协议详情及手动刷新变量快照。
- 编辑变量绑定，支持 `u32`、`i32`、`float`、`bool`、`u8`。
- 写入参数后读回确认，最后提交命令；错误和超时不会自动重发运动。

本工具**不直接占用 J-Link，也不自动烧录、复位、暂停或继续运行目标**。

## 环境要求

| 项目 | 要求 |
| --- | --- |
| 桌面系统 | Windows x64；exe 需在 Windows 上构建 |
| 开发环境 | Python 3.10+；本机使用 Python 3.14 验证 |
| Ozone | 外部控制接口由 V3.40 引入；当前已在 V3.50 完成真实只读诊断 |
| 接口 | `127.0.0.1:19200`，原始 TCP 文本协议 |
| 目标 | 当前内存地址校验针对 STM32F407 SRAM/CCM |

Ozone 必须加载**与板上固件匹配的 ELF**。目标程序需要初始化设备并运行命令处理任务。

## 从源码启动

在 PowerShell 中执行：

```powershell
git clone https://github.com/Wleaf666/Ozone-Debug.git
cd Ozone-Debug
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

不需要激活虚拟环境，不需要单独的 Shell 启动脚本。

## 构建 Windows exe

```powershell
.\.venv\Scripts\python.exe build.py
```

当前产物：

```text
dist/v0.4.2/
├─ OzonePanel.exe
├─ 操作手册.md
├─ usage.txt
└─ THIRD_PARTY_LICENSES.txt
```

`OzonePanel.exe` 是单文件程序，包含 Python、Qt 和默认配置，可以复制到其他可写目录双击运行，无需安装 Python。首次启动会在临时目录展开运行资源。

**仓库不包含预编译 exe、`build/` 或 `dist/`。** `build.py` 保留用于可复现构建，依赖版本锁定在 `requirements.txt`。

## 首次操作

1. 启动支持 Automation Socket 的 Ozone，连接目标并加载匹配 ELF。
2. 确认目标程序正常运行，没有 HardFault；首次测试不带工件。
3. 启动面板，点击 **连接诊断**。
4. 诊断通过后，确认 CPU 在运行，再勾选 **确认目标运行，允许写入**。
5. 机构静止且失能时设零，确认成功，再使能。
6. 先做小位移测试，确认方向和反馈，再测试完整动作。
7. 用 **刷新状态** 查看位置、`busy`、`zeroed` 和 `result`。这些是快照，不是实时采样。

详细步骤、故障处理、命令含义和停止区别见 [操作手册](操作手册.md)。简要启动说明见 [usage.txt](usage.txt)。

### 默认控制方式

| 操作 | 固件接口 |
| --- | --- |
| 升降 | 先写 `lift->distance_mm`，再写 `lift->command` |
| 翻转 | 先写 `flip->target_deg`，再写 `flip->command` |
| 夹爪 | 先写对应开合布尔变量，再写 `gpioOutputflag` |

默认命令编号：`0` 空闲、`1` 设零、`2` 使能、`3` 失能、`4` 运动、`5` 停止保持。

这些是**当前固件约定**，不是 Ozone 通用命令。若目标固件不同，应先调整绑定和对应动作逻辑。

## 变量配置

在“变量与配置”页编辑 JSON，点击保存后重新连接。

```json
{
  "host": "127.0.0.1",
  "port": 19200,
  "timeout": 3.0,
  "variables": {
    "lift_distance": {
      "expression": "lift->distance_mm",
      "type": "float",
      "writable": true
    }
  }
}
```

上面仅展示配置格式，**不是完整机构配置**；当前按钮依赖的完整绑定见 [config.json](config.json)，不要只保留这一项。

配置优先级：

1. 程序目录的 `.local.json`（个人设置，Git 忽略）。
2. 程序目录的 `config.json`。
3. exe 内置默认配置。

表达式支持变量、成员和数字数组下标，不接受任意脚本。通用读写仍限制在 STM32F407 RAM/CCM；这是当前产品边界，尚不是任意 MCU 的通用面板。

**通用写入只检查类型、权限、地址和读回，不执行机构动作顺序检查。** 自动清零的命令标志应优先使用专用按钮。

## 常见问题

### 拒绝连接 / WinError 10061

检查 Ozone 是否运行、是否为 V3.40+、19200 端口是否监听。旧 V3.24 没有此接口。不要改成 Ozone 的其他监听端口，也不需要增加 STM32 串口。

### 连接成功但解析失败

使用最新面板。Ozone 控制台不是通用 C 表达式解释器，不能把 API 嵌套在 `Util.LogHex` 中调用。读取回复的 `returns` 是字节数，内存值位于 `data is`；成功写入可能只有命令回显。

### 参数写入了，但运动没执行

当前流程会在参数读回确认后才提交 Move。失败时先确认命令、结果和实际位置，不要不断重试相对移动。

### 目标暂停，命令一直没有消费

面板不会自动继续运行目标。先确认机构状态和 Ozone 的 CPU 状态，再由操作者决定是否恢复运行。已写入的命令可能在恢复后执行。

### 最小化后恢复

当前 UI 使用 Qt backing store 双缓冲，替代了旧 Tk/CustomTkinter 版本的逐块黑屏重绘。恢复窗口不会重连或提交动作。

## 项目结构

```text
app.py                    Qt界面、后台工作线程与操作组织
ozone_client.py           Socket协议、地址解析、类型编码与命令确认
config.json               当前机构默认变量绑定
build.py                  Windows exe构建脚本
requirements.txt          UI及打包依赖版本
test_protocol.py          真实Ozone回复格式的回归测试
操作手册.md                操作步骤与故障处理
THIRD_PARTY_LICENSES.txt   Qt/Python等第三方声明
.agents/                  开发时使用的设计skill，不是运行依赖
skills-lock.json          skill来源及内容哈希
```

`build/`、`dist/`、`.venv/`、缓存、日志和 `.local.json` 通过 `.gitignore` 排除，不上传构建产物或个人配置。

## 验证

```powershell
.\.venv\Scripts\python.exe -m unittest test_protocol -v
```

当前覆盖读取字节数与数据值区分、成功写入纯回显、失败写入、无关回显以及错误回复。已在真实 Ozone V3.50 验证 Socket、符号及目标变量的**只读访问**，并验证打包启动和最小化恢复。

**这些验证不等于机械动作、PID参数或气缸夹紧已经验收。**

## 依赖与参考

- [Ozone Automation Socket](https://kb.segger.com/Connecting_to_Ozone%27s_Automation_Socket_Interface)
- [Ozone版本记录](https://www.segger.com/downloads/jlink/ReleaseNotes_Ozone.html)
- [Qt for Python](https://doc.qt.io/qtforpython-6/)
- [Qt for Python许可](https://doc.qt.io/qtforpython-6/licenses.html)
- [taste-skill](https://github.com/Leonxlnx/taste-skill)：开发时设计指导，保留来源和原许可证。

第三方许可证见 [THIRD_PARTY_LICENSES.txt](THIRD_PARTY_LICENSES.txt)。本项目没有将 Ozone、J-Link软件或固件一起上传。
