# HY2 Telegram 多设备独立节点管理

为已经运行的 Hysteria 2 和 Telegram 机器人增加独立设备账号、独立分享链接、二维码和流量统计。

## 默认设备

- WIN-PC：Windows 电脑
- MACBOOK：MacBook
- IPHONE：iPhone
- FAMILY：家人

每个设备使用独立随机密码。所有设备共用 VPS、域名、UDP 443 和 TLS 证书，但凭据与流量统计相互独立。

## Telegram 命令

- /start：服务器状态和设备概览
- /devices：列出设备在线情况、上传和下载流量
- /all：发送四个默认设备当前有效的链接和二维码（推荐，最好记）
- /add_defaults：/all 的兼容别名
- /add IPAD：创建新设备并发送链接、二维码
- /get WIN-PC：获取指定设备链接和二维码
- /node WIN-PC：只获取指定设备链接
- /qr IPHONE：只获取指定设备二维码
- /delete IPAD：二次确认后删除设备
- /traffic：查看服务器总流量
- /restart：二次确认后重启 HY2
- /help：显示帮助

设备名称只允许 1-32 位英文字母、数字、下划线和连字符。机器人仅接受绑定的 Telegram 数字 User ID，且仅响应私聊。

## 升级已安装的机器人

前提：原 Hysteria 服务和 Telegram 机器人均正常运行；原部署目录中存在 /root/hy2-output/client.yaml。

在 VPS 的 root SSH 终端执行：

~~~bash
curl -fsSL -o /root/upgrade-hy2-devices.sh https://raw.githubusercontent.com/chb1991/hy2-deploy/main/telegram-bot/upgrade-devices.sh
bash /root/upgrade-hy2-devices.sh
~~~

升级程序会：

1. 备份原 Hysteria 配置和机器人程序。
2. 撤销旧的单密码认证，不保留旧节点。
3. 自动生成 WIN-PC、MACBOOK、IPHONE、FAMILY 四个全新随机密码账号。
4. 为四个设备分别生成 Hysteria 官方分享链接和 PNG 二维码。
5. 更新 Telegram 机器人和受限的设备管理权限。
6. 保留域名、UDP 443、TLS/ACME 证书、Cloudflare DNS-01 和统计 API 配置。
7. 重启 HY2 和 Telegram 机器人。

**重要：升级会重启 HY2 一次，现有连接会短暂中断。所有旧分享链接都会失效，请使用新生成的四个设备链接。**

## 发送四个设备的链接和二维码

升级成功后，在 Telegram 私聊机器人发送：

~~~text
/all
~~~

机器人会依次发送 WIN-PC、MACBOOK、IPHONE、FAMILY 的独立链接和二维码。重复发送此命令不会重置密码，只会重新发送当前有效凭据。旧命令 /add_defaults 仍然可用。

## 添加、查询和删除设备

~~~text
/add IPAD
/add FAMILY-PC
/get IPAD
/node IPAD
/qr IPAD
/delete IPAD
~~~

删除操作需要在 Telegram 中点击确认。删除设备后该设备凭据失效；HY2 会重启一次，因此其他设备的现有连接会短暂中断，但其他账号不会被删除。

## 服务器文件与安全

- 服务端用户名和密码保存在 /etc/hysteria/config.yaml。
- 客户端配置模板保存在 /var/lib/hy2-telegram/base-client.yaml。
- 每个设备的链接和二维码保存在 /var/lib/hy2-telegram/clients/设备名/。
- 设备登记保存在 /etc/hy2-telegram/clients.json。
- Bot Token 和统计 API 密钥仍保存在 /etc/hy2-telegram/bot.env，不上传 GitHub。
- 统计 API 只监听 127.0.0.1:9999。
- Telegram 机器人通过受限 sudo helper 管理设备，不获得通用 root shell。

## 官方技术依据

- Hysteria 服务端 userpass 配置：https://v2.hysteria.network/zh/docs/advanced/Full-Server-Config/
- Hysteria 分享 URI：https://v2.hysteria.network/zh/docs/developers/URI-Scheme/
- Hysteria 流量统计 API：https://v2.hysteria.network/zh/docs/advanced/Traffic-Stats-API/
