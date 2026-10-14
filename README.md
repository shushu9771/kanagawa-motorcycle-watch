# 神奈川大型二轮预约空位监控

云端每5分钟尝试检查指定日期范围的「大型自動二輪」空位；实际执行可能延迟。空位日期合并列出，分别通知两个邮件地址。当前每次独立检查发现空位都会提醒；重跑同一运行编号不会重复发送。微信通过pushplus可选，需配置密钥；LINE目前关闭。程序不会提交预约或勾选同意条款。

当前已启用邮件监控，范围为2026-10-19至2026-10-25（含首尾）。微信接口已准备，尚需个人账号绑定与PUSHPLUS_TOKEN。

## 一次性设置

1. 使用公开GitHub仓库和标准Ubuntu运行器，运行代码免费。代码中不填写个人邮箱或凭证；仓库运行记录可被公开查看。
2. 在GitHub仓库 Settings → Secrets and variables → Actions → Repository secrets 设置以下私密项目，不能写进源代码或聊天：

| 名称 | 内容 |
|---|---|
| `AGENTMAIL_API_KEY` | AgentMail的程序访问密钥，需在AgentMail控制台创建；ChatGPT的OAuth连接不能代替GitHub运行器的密钥 |
| `AGENTMAIL_INBOX_ID` | 专用通知邮箱的inbox ID |
| `NOTIFY_EMAILS` | 两个收件邮箱构成的JSON数组，例如 `["first@example.com", "second@example.com"]` |
| `PUSHPLUS_TOKEN` | pushplus消息token；需在自己的账号完成微信绑定及服务要求的实名认证，并保存到GitHub Secrets |
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE官方账号Messaging API的访问令牌 |
| `LINE_USER_ID` | 你的LINE开发者用户ID；不是昵称、手机号或公开LINE ID |

`GITHUB_TOKEN`由GitHub自动提供，不需要手动创建。工作流只申请Contents写权限，用于保存设置和通知历史。

3. 若以后需要LINE通知，先设置settings.json中的line_enabled为true；LINE官方账号应启用Messaging API，并由个人LINE账号加好友。你的用户ID可在同一频道的Basic settings → Your user ID查看。
4. 打开仓库 Actions → Kanagawa reservation watch → Run workflow，选择`test`。确认两个邮箱收到测试通知；仅在启用LINE时检查LINE。
5. 测试完成后，Run workflow选择`enable`。之后不需电脑开机或保持网页打开。

## 用手机修改范围

打开仓库settings.json并编辑start_date、end_date，可以同时更改范围。也可以在Actions页面Run workflow选择`set_range`，只更改end_date（新的截止日期须不早于start_date）。固定起始日期在settings.json的start_date中修改，截止日期在end_date中修改。每轮从固定起始日期和日本时间当天中较晚的一天开始；官网最早显示日期更晚且向前翻页禁用时，按官网最早日期开始。修改日期不会自动启用已暂停的任务；需要时另选`enable`。选择`pause`可暂停。选择`check`立即检查一轮。

## 微信设置和重复提醒

1. 在 https://www.pushplus.plus/ 登录，关注并绑定自己的微信，按服务要求完成实名认证。
2. 从个人账号获取消息token，保存到仓库 Settings → Secrets and variables → Actions → Repository secrets，名称为PUSHPLUS_TOKEN。不要放入公开文件或聊天。
3. settings.json中的wechat_enabled目前为true；缺少密钥时跳过微信，邮件继续。保存密钥后运行test并确认手机收到微信消息，才能确认送达。
4. pushplus微信免费渠道每日少于200次请求；达到限额后无法继续推送，次日恢复。接口code=200仅代表排队接受，不保证手机已收到。

repeat_mode为every_check时，每轮检查到空位均发送。改为once可恢复每日期每渠道只提醒一次。旧通知历史保留，重复模式只保存最近一轮记录，避免文件无限增长。

## 通知状态

`state.json`只记录日期、通道目标的SHA-256指纹、请求重试ID和通知状态，不包含邮箱地址、LINE用户ID或密钥。状态会提交至仓库，不能删除，否则可能重复通知。修改收件目标会生成新的通道指纹。

每次先保存待发送记录，再发送，最后保存服务已接受的记录。LINE重试使用同一`X-Line-Retry-Key`和内容；超过23小时仍无法确认时停止自动重试。邮件超时、5xx或进程中断可能无法确认是否已被接受，此时记录为`uncertain`，同一运行编号不会自动重复发信。once模式先在AgentMail核实；every_check模式下一次独立检查仍会按用户要求重新提醒。必要时人工恢复未实际发送的记录。通知服务接受请求不等于最终送达，尤其是收件人屏蔽机器人或公司邮件过滤时。

## 限制与验证

GitHub定时运行可能延迟或被跳过，不能保证严格每5分钟执行，公共仓库长时间无活动也可能自动暂停定时任务。日期到期后程序自动暂停；恢复日期后可重新启用。网页获取失败不会被判成没有空位。页面结构改变或要求验证码时需要人工处理。

完整云端日历扫描、邮件API发送测试和通知逻辑测试已通过。LINE通知目前关闭。具体运行状态以GitHub运行记录为准。

参考：[GitHub定时事件](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)、[AgentMail发送接口](https://docs.agentmail.to/api-reference/inboxes/messages/send)、[LINE设置](https://developers.line.biz/en/docs/messaging-api/getting-started/)、[LINE用户ID](https://developers.line.biz/en/docs/messaging-api/getting-user-ids/)、[LINE安全重试](https://developers.line.biz/en/docs/messaging-api/retrying-api-request/)。
