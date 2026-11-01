# 安装说明

CourseMate 需要 Python 3.10+，并支持 SQLite FTS5。在项目目录安装依赖、检查环境并启动：

```sh
python -m pip install -r requirements-lock.txt
python app.py check
python app.py serve --port 8765
```

使用终端聊天时运行 `python app.py chat`。如果系统提供的是 `python3`，将上述命令中的 `python` 统一换成 `python3`。安装与运行应使用同一个 Python，避免依赖装在一个环境、程序却从另一个环境启动。

Windows 也可以运行 `安装终端依赖.ps1` 安装依赖，再双击 `启动.bat` 或 `终端聊天.bat`。这些脚本兼容项目目录已有的 `.venv`：存在时使用其中的 Python，否则使用系统的 `python`；安装脚本不会自动创建新环境。

## 可选：隔离项目依赖

如果电脑上有多个 Python 项目，可以使用虚拟环境隔离依赖。它不是使用 CourseMate 的必要条件。

```sh
python -m venv .venv
```

Windows PowerShell 激活：

```powershell
.\.venv\Scripts\Activate.ps1
```

macOS / Linux 激活：

```sh
source .venv/bin/activate
```

激活后仍使用本文开头的 `python -m pip install` 和 `python app.py` 命令。结束后执行 `deactivate` 退出环境。若系统限制直接安装依赖，也可采用这种方式。
