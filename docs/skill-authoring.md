# 编写与安装技能

本项目支持版本1的 Python JSON 技能包。现成示例位于 `examples/focus_minutes/`，计算轮数乘以每轮分钟数；它不依赖主循环的硬编码分支。

## 包结构

```text
focus_minutes/
  skill.json
  handler.py
```

`skill.json` 示例：

```json
{
  "version": 1,
  "id": "focus_minutes",
  "kind": "python",
  "title": "专注时长换算",
  "description": "将专注轮数和每轮分钟数换算为总时长。",
  "permissions": ["本机Python执行"],
  "requires": [],
  "parameters": {
    "type": "object",
    "properties": {
      "rounds": {"type": "integer", "minimum": 1, "maximum": 20},
      "minutes": {"type": "integer", "minimum": 5, "maximum": 120}
    },
    "required": ["rounds", "minutes"],
    "additionalProperties": false
  }
}
```

`handler.py` 从标准输入读取一个 JSON 对象，标准输出只能是一个 JSON 结果。使用当前 Python 的独立进程执行，工作目录是技能目录，超时20秒；报错详情不直接回传模型。

```python
import json, sys
args = json.load(sys.stdin)
print(json.dumps({"total_minutes": args["rounds"] * args["minutes"]}))
```

支持的参数类型为 object、array、string、integer、boolean 及 enum、长度/数值范围；不是完整 JSON Schema 实现。`requires` 填 Python 发行包名，系统检查是否安装，不自动执行 pip，也不自动运行安装脚本。依赖需要用户在项目虚拟环境中安装。权限字段只是说明，并非操作系统权限限制。

## 使用

1. 网页“技能管理”填写包含上述两个文件的文件夹绝对路径，点击安装；或终端 `/install 路径`。
2. 查看源码和清单，核对依赖；安装后默认停用。
3. 网页启用，或终端 `/enable focus_minutes`，按照提示明确确认信任。
4. 在真实模型模式提问“用专注时长换算技能计算3轮、每轮25分钟”。模型可选择 `skill_focus_minutes`，结果应为75分钟。
5. `/skills` 查看状态，`/disable focus_minutes` 停用。修改安装目录代码后指纹改变，必须重新审阅启用。升级安装用不同 id，当前没有覆盖更新功能。

包限100个文件、2MB，仅接受 .py/.json/.md/.txt，不接受符号链接。说明文档 SKILL.md 可随包附带，但单独下载一份 SKILL.md 不会变成可执行技能。依赖 Codex 专有工具或其他运行环境的技能，需要先实现适配入口。

插件以当前用户权限运行，不能把“参数校验”“停用”“独立进程”误认为安全沙箱。只安装并启用自己信任的代码。
