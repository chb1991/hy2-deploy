# Telegram 机器人（HY2 私有监控）

本模块为现有 Hysteria 2 服务增加一个私有 Telegram 机器人。

## 功能

- \`/start\`：服务器状态、节点分享链接、二维码图片
- \`/status\`：实时服务器状态
- \`/node\`：HY2 分享链接
- \`/qr\`：HY2 二维码图片
- \`/traffic\`：Hysteria 累计上传/下载流量及在线设备
- \`/restart\`：二次确认后重启 Hysteria
- \`/help\`：命令帮助

机器人只接受安装时指定的 Telegram 数字 User ID，并且只响应私聊。未授权用户的消息会被忽略。

## 安全设计

- Telegram 使用官方 Bot API Long Polling，不需要公网 Webhook，也不需要额外开放端口。
- Hysteria 官方 Traffic Stats API 只监听 \`127.0.0.1:9999\`，设置随机 secret，不对公网开放。
- Bot Token、管理员 ID、统计 API secret 存放在 VPS 的 \`/etc/hy2-telegram/bot.env\`，权限为 root:hy2bot 0640，不进入 Git。
- 节点链接和二维码复制到 \`/var/lib/hy2-telegram/\`，只允许机器人账户读取。
- 机器人以独立的 \`hy2bot\` 系统用户运行。
- \`/restart\` 通过 sudoers 只允许执行固定的 HY2 重启 helper，并需要 Telegram 二次确认。

## 安装前准备

1. 在 Telegram 搜索官方 \`@BotFather\`。
2. 发送 \`/newbot\`，按照提示设置机器人名称和 username。
3. 复制 BotFather 返回的 Token。
4. 获取自己的 Telegram 数字 User ID（可使用可信的 ID 查询机器人，例如 \`@userinfobot\`；核对是数字 ID，不是用户名）。
5. 不要把 Bot Token 发给别人或提交到 GitHub。

## 安装

确保已完成 HY2 部署，且以下文件存在：

- \`/etc/hysteria/config.yaml\`
- \`/root/hy2-output/node.txt\`
- \`/root/hy2-output/node-qr.png\`

在 Debian 13 VPS 上以 root 执行：

\`\`\`bash
curl -fsSL -o /root/install-hy2-telegram.sh https://raw.githubusercontent.com/chb1991/hy2-deploy/main/telegram-bot/install.sh
bash /root/install-hy2-telegram.sh
\`\`\`

安装器会提示输入 Bot Token 和 Telegram 数字 User ID。Token 输入时不会显示。

**注意：安装器会给 Hysteria 配置增加本机统计 API，并重启一次 Hysteria，现有代理连接会短暂中断。** 它会在修改前备份配置。如果检测到已有 \`trafficStats\` 配置，会停止并提示人工检查，不覆盖现有设置。

## 使用

安装完成后，打开自己的机器人私聊并发送 \`/start\`。机器人会发送：

1. 主机、时间、运行时间、负载、RAM/Swap、TCP/UDP socket 数量。
2. Hysteria 在线设备数和累计流量。
3. HY2 节点分享链接。
4. 节点二维码 PNG。

日期时间默认使用 \`Asia/Shanghai\`。IPv4/IPv6 显示的是节点域名 DNS 解析结果。TCP/UDP 数量是系统 socket 统计，不是 HY2 在线设备数。HY2 在线设备和流量来自官方 Traffic Stats API。

## 运维

\`\`\`bash
systemctl status hy2-telegram --no-pager
journalctl -u hy2-telegram -f
systemctl restart hy2-telegram
systemctl stop hy2-telegram
systemctl status hysteria-server --no-pager
\`\`\`

如果机器人无响应，先检查 VPS 是否能访问 \`https://api.telegram.org\`，再检查：

\`\`\`bash
journalctl -u hy2-telegram -n 80 --no-pager
\`\`\`

## 官方参考

- Telegram Bot API：<https://core.telegram.org/bots/api>
- Telegram Long Polling：<https://core.telegram.org/bots/faq>
- Hysteria Traffic Stats API：<https://v2.hysteria.network/docs/advanced/Traffic-Stats-API/>
- Hysteria 完整服务端配置：<https://hysteria.network/docs/advanced/Full-Server-Config/>
