# HY2 Deploy：Hysteria 2 一键部署

面向个人 KVM VPS 的 HY2 + Telegram 机器人一体化部署脚本。目标系统为 **Debian 13 x86_64**，适合 512MB 内存的小型 VPS。

> 本项目只负责安装和配置 Hysteria 2 服务端，不提供代理节点、域名、证书或网络连通性保证。请仅在你拥有或获准管理的服务器上使用。

## 功能

- 使用 Hysteria 官方安装器 `https://get.hy2.sh/` 安装官方核心，默认固定版本 `v2.13.0`。
- 不安装 3x-ui、Docker、Nginx 或其他代理核心。
- 默认监听 **UDP 443**；可与同端口的 TCP 服务并存。
- 自有域名 + Cloudflare DNS-01 + Let's Encrypt 正式证书，或无域名 + 公网 IPv4 + 自签名证书 SHA256 指纹固定。
- 首次部署直接创建 WIN-PC、MACBOOK、IPHONE、FAMILY 四套独立随机凭据（Hysteria userpass）。
- 调用 Hysteria 官方 `share` 子命令生成 `hysteria2://` 分享链接和终端二维码。
- 使用 Debian 软件包 `qrencode` 额外生成 PNG 二维码。
- 检查 Cloudflare API Token、Zone 和 DNS A 记录。
- 无 Swap 且根分区空间充足时，尝试创建 512MB Swap。
- 节点信息保存在 `/root/hy2-output/`，敏感文件权限限制为 root。

## 官方来源

- Hysteria 官方源码：<https://github.com/HyNetworks/hysteria>
- 官方安装器：<https://get.hy2.sh/>
- 官方文档：<https://hysteria.network/docs/>
- 服务端配置：<https://hysteria.network/docs/getting-started/Server/>
- 安装器说明：<https://hysteria.network/docs/getting-started/Server-Installation-Script/>
- Cloudflare DNS-01：<https://hysteria.network/docs/advanced/ACME-DNS-Config/>
- 分享 URI 规范：<https://hysteria.network/docs/developers/URI-Scheme/>

本项目使用官方安装器下载官方发布的预编译核心，不在 512MB VPS 上编译 Go 源码。官方安装器只负责安装核心和 systemd 服务；本项目脚本负责写入服务端配置、启动服务和生成分享信息。

## 系统要求

- Debian 13 x86_64、root 权限和 systemd
- 公网 IPv4
- VPS 服务商允许入站 UDP 443
- 建议至少 512MB RAM；脚本会在无 Swap 且磁盘空间充足时尝试创建 512MB Swap

## GitHub 一键运行

将 `install.sh` 上传到自己的 GitHub 仓库根目录，例如 `https://github.com/chb1991/hy2-deploy`。然后在 VPS SSH 终端执行（直接使用下面命令即可）：

```bash
apt-get update && apt-get install -y curl
curl -fsSL -o /root/install-hy2.sh https://raw.githubusercontent.com/chb1991/hy2-deploy/main/install.sh
bash /root/install-hy2.sh
```

也可以直接执行：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/chb1991/hy2-deploy/main/install.sh)
```

## 模式 1：Cloudflare 正式证书（推荐）

部署前，在 Cloudflare DNS 添加 A 记录：

| 字段 | 示例 |
|---|---|
| 类型 | A |
| 名称 | `hy2` |
| 内容 | VPS 公网 IPv4 |
| 代理状态 | **DNS only（灰云）** |
| TTL | Auto |

例如 HY2 域名为 `hy2.example.com`，Cloudflare Zone 主域名填写 `example.com`。

创建 Cloudflare API Token，最小权限：

- Zone → DNS → Edit
- Zone → Zone → Read
- Zone 资源范围仅选择该域名所在 Zone

运行脚本选择 `1`，输入 HY2 域名、Zone 主域名、邮箱和 API Token。Token 输入时不会显示。脚本会验证 Token，并检查 A 记录指向当前 VPS 公网 IPv4 且 `proxied=false`。

DNS-01 不需要入站 TCP 80/443；VPS 仍须能主动访问 Cloudflare API 和 ACME 服务。Cloudflare 普通橙云 CDN 不会代理 Hysteria 2 的 UDP 流量，因此节点域名必须保持 DNS only。如果 VPS 没有配置 IPv6，请删除该节点域名上指向其他地址的 AAAA 记录。

## 模式 2：无域名自签名证书

运行脚本选择 `2`。脚本自动获取公网 IPv4，生成带 IP SAN 的自签名证书，并将 SHA256 指纹写入客户端配置。官方 `hysteria share` 会把 `insecure=1` 和 `pinSHA256` 写入分享 URI。

客户端必须支持 Hysteria 2 URI 的 `insecure` 与 `pinSHA256` 参数。不要删除分享链接中的指纹参数。

## 部署后生成的文件

| 路径 | 内容 |
|---|---|
| `/root/hy2-output/node.txt` | 完整节点分享链接 |
| `/root/hy2-output/node-qr.png` | 节点二维码 PNG |
| `/root/hy2-output/share-qr.txt` | 官方 CLI 终端二维码 |
| `/root/hy2-output/client.yaml` | 客户端配置 |
| `/root/hy2-output/server-info.txt` | 节点信息 |
| `/etc/hysteria/config.yaml` | 服务端配置 |

这些文件包含密码、证书指纹或 Cloudflare API Token，只能保存在 VPS 上，**不要提交到 GitHub**。Cloudflare Token 保存在服务端配置中供证书续期使用；除非先更新服务端配置，否则不要撤销该 Token。

## 防火墙与维护

- 必须在 VPS 本机防火墙和服务商安全组放行入站 **UDP 443**。
- 已有 VLESS 使用 TCP 443 时，HY2 使用 UDP 443，二者可以共用端口号。
- 本机显示服务 `active` 和 UDP 443 正在监听，不代表机房上游网络一定放行 UDP；必须使用手机蜂窝网络实际导入节点测试。

常用命令：

```bash
systemctl status hysteria-server --no-pager
journalctl -u hysteria-server -f
systemctl restart hysteria-server
ss -lunp 'sport = :443'
/usr/local/bin/hysteria -c /root/hy2-output/client.yaml share --qr
```

## 版本与安全说明

当前脚本固定安装 `v2.13.0`。需要升级时，先查看 Hysteria 官方 Releases，再修改 `install.sh` 顶部的 `HY2_VERSION`。

- 不要将节点链接、密码、Cloudflare Token、服务端配置或输出目录提交到公开仓库。
- 删除 GitHub 当前分支中的旧文件不会自动删除 Git 历史。若旧文件曾包含订阅链接或凭据，应撤销/轮换；需要彻底清除历史时，应另建干净仓库或单独重写 Git 历史。
- 脚本只做本机部署和监听检查，不能代替公网 UDP 连通性测试。
- 默认不设置 Brutal 固定带宽，使用 Hysteria 默认拥塞控制，避免把套餐标称带宽误当成真实可用 UDP 带宽。


## HY2 + Telegram 机器人一体化部署

主安装器在同一次交互流程中安装官方 Hysteria 2、TLS 证书、Telegram 机器人、官方流量统计 API，以及四套独立设备账号。新服务器不再需要先部署 HY2、再单独安装机器人、最后升级多用户。

全新 Debian 13 x86_64 VPS 执行：

~~~bash
apt-get update && apt-get install -y curl
curl -fsSL -o /root/install-hy2.sh https://raw.githubusercontent.com/chb1991/hy2-deploy/main/install.sh
bash /root/install-hy2.sh
~~~

### 安装时按顺序提供的信息

1. TLS 模式：推荐选择 1（Cloudflare DNS-01 + Let's Encrypt）。
2. HY2 域名，例如 hy2.example.com。
3. Cloudflare Zone 主域名，例如 example.com。
4. 证书联系邮箱。
5. Cloudflare API Token（隐藏输入；需要 Zone / DNS / Edit 与 Zone / Zone / Read，范围限制到该 Zone）。
6. Telegram Bot Token（隐藏输入；先通过 @BotFather 创建）。
7. Telegram 管理员数字 User ID（不是用户名）。


### Telegram 信息如何获取

- Bot Token：在 Telegram 搜索 @BotFather，发送 /newbot，按提示设置机器人名称和用户名，复制 BotFather 返回的 Token。输入安装器时不会回显。
- 管理员数字 User ID：在 Telegram 搜索 @userinfobot，发送 /start，复制返回的纯数字 ID。不要填写 @用户名。
- 不要把 Bot Token、Cloudflare API Token 或节点链接发到聊天、截图或 GitHub 仓库。


注意：一个 Telegram Bot Token 同一时间只能由一个长轮询实例使用。如果新 VPS 复用当前服务器正在运行的 Bot Token，必须先停止旧服务器上的 hy2-telegram 服务，或为新 VPS 创建另一个 Bot；否则两个实例会争抢 getUpdates。

脚本会先检查 Cloudflare Token、Zone、A 记录及 DNS only 状态，再安装 HY2。HY2 服务启动后，脚本继续提示输入 Telegram Bot Token 和管理员 User ID，验证 Bot Token 后自动安装机器人及多用户功能。无域名模式选择 2，会跳过 Cloudflare 信息，但仍需要 Telegram Bot Token 和管理员 ID。

### 自动完成的内容

- 安装 Hysteria 官方发布核心（当前固定版本 v2.13.0）。
- 配置 UDP 443、TLS 和 systemd。
- 启用只监听 127.0.0.1:9999 的 Hysteria 官方 Traffic Stats API。
- 安装 Telegram Bot，并绑定管理员数字 User ID。
- 配置 Hysteria userpass，自动生成 WIN-PC、MACBOOK、IPHONE、FAMILY 四个不同密码。
- 为每个设备生成官方 hysteria2:// 分享链接、PNG 二维码和客户端 YAML。
- 配置开机自启，并检查 HY2、机器人服务和四套设备文件。

安装结束后，在 Telegram 私聊机器人发送 /start，再发送 /add_defaults，机器人会发送四台设备各自的链接和二维码。

### 安全与适用范围

- 主安装器只用于全新 VPS。检测到现有 HY2 或机器人配置时会停止，不覆盖已有服务。
- 已有 HY2/Telegram 部署请勿重新运行主安装器；已有环境的多设备升级请参阅 telegram-bot/README.md。
- Bot Token、Cloudflare API Token、设备密码和节点链接只保存在 VPS，不提交 GitHub。
- Cloudflare 模式必须 DNS only（灰云），并在服务商安全组放行入站 UDP 443。
- 服务 active 和本机 UDP 监听不代表公网 UDP 一定可达，仍需用手机蜂窝网络实测。

