# 神奈川大型二轮预约空位监控

云端每5分钟尝试检查当天到指定截止日期的「大型自動二輪」空位，分别发送两封邮件和一条LINE消息。多个新空位日期合并列出；每个日期在每个渠道只通知一次，日期调整不清除历史。程序不会提交预约或勾选同意条款。

初始状态是暂停；部署成功、三个收件通道测试通过后，再启用。

## 一次性设置

1. 使用公开GitHub仓库和标准Ubuntu运行器，运行代码免费。代码中不填写个人邮箱或凭证；仓库运行记录可被公开查看。
2. 在GitHub仓库 Settings → Secrets and variables → Actions → Repository secrets 设置以下私密项目，不能写进源代码或聊天：

| 名称 | 内容 |
|---|---|
| `AGENTMAIL_API_KEY` | AgentMail的程序访问密钥，需在AgentMail控制台创建；ChatGPT的OAuth连接不能代替GitHub运行器的密钥 |
| `AGENTMAIL_INBOX_ID` | 专用通知邮箱的inbox ID |
| `NOTIFY_EMAILS` | 两个收件邮箱构成的JSON数组，例如 `["first@example.com", "second@example.com"]` |
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE官方账号Messaging API的访问令牌 |
| `LINE_USER_ID` | 你的LINE开发者用户ID；不是昵称、手机号或公开LINE ID |

`GITHUB_TOKEN`由GitHub自动提供，不需要手动创建。工作流只申请Contents写权限，用于保存设置和通知历史。

3. LINE官方账号应启用Messaging API，并由个人LINE账号加好友。你的用户ID可在同一频道的Basic settings → Your user ID查看。
4. 打开仓库 Actions → Kanagawa reservation watch → Run workflow，选择`test`。确认两个邮箱与LINE都收到测试通知。
5. 测试完成后，Run workflow选择`enable`。之后不需电脑开机或保持网页打开。

## 用手机修改范围

打开同一Actions页面，Run workflow选择`set_range`，在end_date填写新的日期（如`2026-11-16`）。每轮开始日期始终是日本时间当天。修改日期不会自动启用已暂停的任务；需要时另选`enable`。选择`pause`可暂停。选择`check`立即检查一轮。

## 通知状态

`state.json`只记录日期、通道目标的SHA-256指纹、请求重试ID和通知状态，不包含邮箱地址、LINE用户ID或密钥。状态会提交至仓库，不能删除，否则可能重复通知。修改收件目标会生成新的通道指纹。

每次先保存待发送记录，再发送，最后保存服务已接受的记录。LINE重试使用同一`X-Line-Retry-Key`和内容；超过23小时仍无法确认时停止自动重试。邮件超时、5xx或进程中断可能无法确认是否已被接受，此时记录为`uncertain`，先在AgentMail核实，避免自动重复发信。必要时人工恢复未实际发送的记录。通知服务接受请求不等于最终送达，尤其是收件人屏蔽机器人或公司邮件过滤时。

## 限制与验证

GitHub定时运行可能延迟或被跳过，不能保证严格每5分钟执行，公共仓库长时间无活动也可能自动暂停定时任务。日期到期后程序自动暂停；恢复日期后可重新启用。网页获取失败不会被判成没有空位。页面结构改变或要求验证码时需要人工处理。

目前源码已准备，官方日历结构已通过浏览器检查；本地解析和通知逻辑可用单元测试验证。云端实际扫描、邮件API凭证和LINE凭证仍需完成运行测试。具体上线状态以GitHub运行记录和三个通道的实际测试通知为准。

参考：[GitHub定时事件](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)、[AgentMail发送接口](https://docs.agentmail.to/api-reference/inboxes/messages/send)、[LINE设置](https://developers.line.biz/en/docs/messaging-api/getting-started/)、[LINE用户ID](https://developers.line.biz/en/docs/messaging-api/getting-user-ids/)、[LINE安全重试](https://developers.line.biz/en/docs/messaging-api/retrying-api-request/)。
