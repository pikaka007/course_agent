# 当前验证记录

最新版本为 V4，验收指标、失败修复和边界见 [第四版迭代与验收](iteration-v4.md)。完整原始记录在本机 evidence/v4/。

# 第三版历史记录与平台纠正

2026-10-28 第三版完整回归为 48 项通过，输出在本机 evidence/v3/test-results.txt。测试使用临时目录和替身模型，不消耗用户模型额度。含 Rich/prompt-toolkit 实际输入事件、终端会话保存、配置优先级、跨平台密钥隔离、上传拒绝覆盖、回收站恢复、限定资料、上下文追问、取消后禁止写入及 HTTP 来源校验。

真实调用与自动化分开：本轮用户明确提供 https://api.deepseek.com，授权保存本机 .env。GET /models 成功返回 deepseek-flash、deepseek-v4-pro；官方文档说明 deepseek-flash 当前由 DeepSeek-V4.1-Flash-1017 提供服务。短文本连通性请求返回 OK、HTTP 200、16 tokens，completion ID 为 d6e6c990-f602-475a-ab77-4a8a63f28a15。

| 运行 | 任务 | 工具次数 | 模型请求 | 耗时 | tokens |
| --- | --- | --- | --- | --- | --- |
| e5b9ea6d144e | 解释 MCP 流程 | 4 | 4 | 25.70 秒 | 9208 |
| 2c3cc0ce0ca2 | 承接上轮压缩成三点并保存 | 4 | 4 | 24.19 秒 | 10259 |

两次均完成，connection.endpoint_host 为 api.deepseek.com。第二轮写出 outputs/mcp-official-v3.md，1163 字节，保存前引用校验通过。原始 JSON 包含每次 HTTP 响应的 completion ID、returned_model 和 usage；响应未提供所检查的 request-id 头，记录为 null。尚未登录用户控制台核对账单，不能声称已代用户核账。

第一、二版实际沿用了系统中的阿里云兼容网关和环境密钥。它们是该网关的真实网络调用，但不是用户提供的官方账号测试；原始记录保留，不能改标成官方证据。报告已改用本轮官方证据。

网页验收覆盖对话与追问、保存笔记预览、来源展开、接口回执和偏好保存。图片在 evidence/v3/screenshots。引用验证只核对标记和段落覆盖，不是语义事实核查；模型仍可能加入不必要解释。耗时为个案，不能当作稳定性能基准。
