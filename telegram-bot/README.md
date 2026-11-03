# HY2 Telegram 多设备管理

本模块为已经运行的 Hysteria 2 + Telegram 机器人增加独立设备账号管理。

## Telegram 命令

- \`/start\`：服务器状态 + 设备在线和流量概览
- \`/devices\`：列出所有设备，显示在线实例数、上传、下载
- \`/add_defaults\`：一键创建 WIN-PC、MACBOOK、IPHONE、FAMILY 四个独立账号
- \`/add WIN-PC\`：创建一个新账号，自动生成独立密码、分享链接和二维码
- \`/get WIN-PC\`：发送指定设备的链接和二维码
- \`/node WIN-PC\`：只发送指定设备链接
- \`/qr WIN-PC\`：只发送指定设备二维码
- \`/delete WIN-PC\`：二次确认后删除设备账号
- \`/traffic\`：查看服务器总流量
- \`/restart\`：二次确认后重启 HY2
- \`/help\`：帮助

设备名称只允许 1-32 位英文字母、数字、下划线和连字符。机器人只接受安装时绑定的 Telegram 数字 User ID，并且只响应私聊。

## 工作方式

- 使用 Hysteria 2 官方 \`auth.type: userpass\` 用户名-密码映射。
- 每台设备使用不同用户名和随机密码。
- 使用 Hysteria 官方 \`hysteria -c client.yaml share\` 生成分享 URI。
- URI fragment 使用设备名称，便于客户端识别节点。
- 使用 \`qrencode\` 为每台设备生成独立 PNG 二维码。
- 在线实例数、上传和下载统计来自 Hysteria 官方 Traffic Stats API，按 userpass 用户名对应的客户端 ID 显示。

## 升级已经安装的机器人

确认原 HY2 与 Telegram 机器人均正常运行，然后在 VPS root SSH 终端执行：

\`\`\`bash
curl -fsSL -o /root/upgrade-hy2-devices.sh https://raw.githubusercontent.com/chb1991/hy2-deploy/main/telegram-bot/upgrade-devices.sh
bash /root/upgrade-hy2-devices.sh
\`\`\`

升级程序会安装 YAML 解析器和二维码工具、备份现有服务端配置、迁移认证方式、更新机器人程序与受限 sudo helper，并重启服务。

### 认证迁移提醒

当前一键部署脚本使用的是单密码 \`auth.type: password\`。多设备功能使用官方 \`auth.type: userpass\`。迁移时会：

1. 将原来的共享密码保留为 \`OLD-SHARED\` 账号。
2. 生成新的 \`OLD-SHARED\` 节点链接和二维码。
3. 保留域名、UDP 443、TLS/ACME 证书、Cloudflare DNS-01 和统计 API 配置。
4. 备份旧配置到 \`/var/lib/hy2-telegram/backups/\`。

**迁移会重启 HY2 一次，现有连接短暂中断。原来不带用户名的旧分享链接需要替换为机器人重新发送的 \`OLD-SHARED\` 链接。**

## 创建四个默认设备

升级完成后，在 Telegram 私聊机器人发送：

\`\`\`text
/add_defaults
\`\`\`

机器人会批量创建 WIN-PC、MACBOOK、IPHONE、FAMILY 四个账号，只重启 HY2 一次，然后分别发送四条独立链接和四张二维码。

## 添加其他设备

\`\`\`text
/add IPAD
/add FAMILY-PC
/get IPAD
/delete IPAD
\`\`\`

删除操作需要在 Telegram 中点击确认。删除账号会重启 HY2，因此现有连接会短暂中断，但其他设备的账号不会被删除。

## 文件与安全

- 设备密码保存在 \`/etc/hysteria/config.yaml\`。
- 设备链接与二维码保存在 \`/var/lib/hy2-telegram/clients/\`。
- 设备登记保存在 \`/etc/hy2-telegram/clients.json\`。
- Bot Token 与统计 API secret 仍保存在原来的 \`/etc/hy2-telegram/bot.env\`。
- 敏感文件不上传 GitHub。
- 统计 API 只监听 \`127.0.0.1:9999\`。
- 机器人通过受限 sudo helper 管理账号，不获得通用 root shell。

## 官方文档

- Hysteria 服务端 userpass：<https://v2.hysteria.network/zh/docs/advanced/Full-Server-Config/>
- Hysteria URI 格式：<https://v2.hysteria.network/zh/docs/developers/URI-Scheme/>
- Hysteria 流量统计 API：<https://v2.hysteria.network/zh/docs/advanced/Traffic-Stats-API/>
