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

## 增加 TUIC v5（保留现有 HY2）

在 HY2 + Telegram 部署成功后，若需要增加 TUIC v5，执行独立增量安装器；它使用 UDP 51443 和独立 sing-box 服务，不覆盖现有 HY2 UDP 443：

~~~bash
curl -fsSL -o /root/install-tuic.sh https://raw.githubusercontent.com/chb1991/hy2-deploy/main/tuic/install.sh
bash /root/install-tuic.sh
~~~

TUIC 安装器会创建独立证书、四个 TUIC 设备账号，并将 Telegram 机器人切换为双协议管理。安装结束前会以 `hy2bot` 身份检查每个设备的节点文件和二维码均可读取。二维码目录权限为 `0750`，文件权限为 `0640`，避免严格 `umask 077` 下目录权限过窄。

完整步骤、隔离方式和维护命令见 [TUIC 增量安装说明](tuic/README.md)。

## 核心升级（HY2 + sing-box）

机器人安装器会部署统一核心升级工具。完成 HY2 + Telegram 部署后，或完成 TUIC 增量安装后，可在 root SSH 终端直接执行：

~~~bash
update
~~~

命令先检查上游版本并显示当前版本与最新稳定版，输入 `y` 才开始升级。支持：

| 命令 | 作用 |
|---|---|
| `update` | 检查并交互确认升级所有已安装核心 |
| `update --check` | 只检查版本，不修改服务 |
| `update --hy2` | 只升级 Hysteria 2 |
| `update --singbox` | 只升级 sing-box（TUIC） |

完成 TUIC 增量安装、切换到双协议机器人后，Telegram 私聊管理员机器人发送 `/update`，机器人先显示两个核心的当前版本和上游最新稳定版，再点击 **确认升级** 或 **取消**。升级在后台执行，不会阻塞机器人继续响应其他命令。

升级策略：

- 只读取官方 GitHub Releases 的最新稳定正式版，不安装 Draft 或 Pre-release。
- 下载官方发布资产，并校验 GitHub Release 提供的 SHA256；缺少摘要或校验不匹配时立即中止。
- 升级前备份旧核心、服务单元、配置和必要状态到 `/root/core-upgrade-backups/`。
- 不重写 HY2/TUIC 配置，不重置设备 UUID、密码、分享链接、二维码、端口、域名或证书。
- HY2 与 sing-box 分别重启并检查服务状态、版本和 UDP 监听。
- 任一升级步骤失败时，自动恢复备份的旧核心并尝试恢复服务。

升级期间对应协议会短暂断开。自动回滚是故障恢复措施，不替代升级后使用手机蜂窝网络进行实际连接测试。

## 版本与安全说明

首次安装脚本当前固定安装 Hysteria `v2.13.0`；部署后通过上面的 `update` 命令跟进上游最新稳定版。

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

## 新增 TCP：VLESS + XHTTP + REALITY（可选）

已经安装 HY2 与 Telegram 机器人的 Debian 13 x86_64 VPS，可额外安装官方 Xray-core（与 HY2 共存）。

**严格前提：** HY2 UDP 443 继续使用；Xray 使用独立 TCP 443。如果现有 TCP 443 已被其他程序占用，脚本会拒绝安装，不会自动停止服务。若已安装 Xray/3x-ui，脚本也会拒绝覆盖。

一行部署命令（root SSH 终端，**不要使用 curl | bash**，交互式输入需要正常终端 stdin）：

~~~bash
curl -fsSL -o /root/install-xhttp.sh https://raw.githubusercontent.com/chb1991/hy2-deploy/main/xray-xhttp/install.sh && bash /root/install-xhttp.sh
~~~

安装器依次询问：

1. 节点连接地址：现有灰云域名或 VPS IP。
2. REALITY 目标域名：必须支持 TLS 1.3 与 HTTP/2；脚本使用 openssl 验证。此域名是 REALITY 握手目标，不必是你的自有域名。
3. 确认安装官方 Xray-core v26.10.31。

REALITY 目标不要使用自己的节点连接域名，也应避开官方核心明确警告的高风险目标（例如 Apple、Microsoft、iCloud 等）。脚本使用 Xray 官方配置检查，在检测到相关风险警告时停止安装并提示更换目标。若安装中断，脚本可识别自己上一次安装留下的受限标记并恢复，不会覆盖无关的已有 Xray 服务。

使用 XTLS/Xray-install 官方安装器和 XTLS/Xray-examples 官方示例格式。自动生成 WIN-PC、MACBOOK、IPHONE、FAMILY 四套独立 UUID、随机 XHTTP path、REALITY 公私钥和 Short ID；通过 Xray 官方 run -test 检查配置后再启动。不会修改现有 Hysteria 配置、密码、UDP 端口或 Cloudflare DNS。

安装成功后，复用原 Telegram Bot；新增命令：

- /xstatus：查看 Xray 版本、运行状态。
- /xall：获取四个 XHTTP REALITY 设备链接及二维码。
- /xget IPHONE：获取指定设备的链接和二维码。

原命令 /add_defaults、/get IPHONE 仍然管理 HY2，互不混淆。Xray 节点链接、二维码保存在 VPS /var/lib/hy2-telegram/xray/clients/（只有 root 和机器人组可读），不要提交 GitHub。安装过程会备份并重启 Telegram 机器人，但不会重启 HY2。

**兼容性提示：** VLESS XHTTP REALITY 并非所有 iOS 客户端都能正确导入。Loon 曾存在将 XHTTP 链接错误解析为普通 TCP 的报告，二维码成功发送不代表客户端已经支持该传输。应在导入后检查实际传输类型并测试连接。Cloudflare 仍使用 DNS only（灰云），普通橙云不直接转发 REALITY TCP。

## 新增 Loon 兼容 TCP：VLESS + REALITY + Vision

**适用对象：** 已在同一台 Debian 13 x86_64 VPS 上运行 HY2、官方 Xray + XHTTP REALITY 与原 Telegram 机器人。

该方案直接复用已经安装的官方 Xray-core，不安装 3x-ui、不替换 XHTTP 配置、不更改 HY2/TUIC。新增独立进程 xray-vision.service，默认监听 TCP 8443。服务端使用官方 VLESS TCP REALITY XTLS-Vision 配置（Flow 为 xtls-rprx-vision）。

root SSH 一行安装（交互命令必须以 bash 方式执行，**不要使用 curl | bash**）：

~~~bash
curl -fsSL -o /root/install-vision.sh https://raw.githubusercontent.com/chb1991/hy2-deploy/main/xray-vision/install.sh && bash /root/install-vision.sh
~~~

交互设置：

1. 连接域名/IP：默认自动复用已有 XHTTP 节点的连接地址；也可以手动选择另一个灰云 A 记录。
2. REALITY SNI：默认从现有 XHTTP 配置读取已验证目标。必须支持 TLS 1.3、H2，且 TLS 证书验证通过；并以官方 Xray 配置检查结果为准。
3. TCP 端口：默认 8443；若已占用可选另一个 1024–65535 的端口。
4. 输入 y 确认创建服务、四套独立 UUID 及更新原 Telegram 机器人。

**网络：** Cloudflare 原 A 记录继续 DNS only（灰云），不必新建域名或申请证书；需要放行入站 **TCP 8443**（或你填写的端口）。TCP 443（XHTTP）、UDP 443（HY2）和 UDP 51443（TUIC，如有）不变。新服务采用独立 systemd，启动/停止不重启其他代理核心。安装器检测已有 Vision 时拒绝覆盖；安装中失败会尝试移除新 Vision 服务并恢复原 Telegram 文件。 安装器在修改机器人程序前将现有代码备份至 VPS 的 /root/vision-backups/，即使机器重启也保留备份供人工恢复。

**Telegram：**

- /vstatus：Vision 服务状态。
- /vall：发送四个 Vision 设备链接、对应 Loon 专用配置行和二维码。
- /vget IPHONE：仅发送 iPhone 的 Vision 设备节点、Loon 配置和二维码。

原有 HY2 /get、/add_defaults，XHTTP /xget、/xall 和 TUIC 扩展命令保持不变。Vision 服务端配置仅保存在 VPS 的 /usr/local/etc/xray/vision.json；四套私有客户端节点位于 /var/lib/hy2-telegram/vision/clients/。不在 GitHub 提交 UUID、私钥、节点或真实 IP。

**Telegram 安全与验证：** 机器人原管理员数字 ID + 私聊校验不变，新增 /vstatus、/vall、/vget IPHONE；Vision 节点及二维码仅授权 root 与 hy2bot 用户组读取。Telegram Bot 私聊不是端到端加密，收到的节点链接属于凭据，请勿转发到群聊或公开分享。CI 已包含 Python 的 7 项离线机器人测试（单设备、四设备、非法设备、越权拒绝、TUIC 双协议转发和状态命令），但还未在实际 Telegram API 上进行端到端测试。

**客户端兼容性：** Loon 官方文档支持 transport=tcp、flow=xtls-rprx-vision、public-key、short-id、over-tls=true、sni。实际能否连接还取决于 Loon 客户端是否兼容当前 Xray REALITY 的握手要求。二维码或链接导入成功不等于连接成功，应使用 iPhone 蜂窝网络实测。

官方资料：
- https://github.com/XTLS/Xray-examples/tree/main/VLESS-TCP-XTLS-Vision-REALITY
- https://nsloon.app/docs/Node/
- https://xtls.github.io/en/config/transports/reality.html

### Loon TLS 指纹自动刷新

现有 Vision 节点在 Loon 中需要指定 TLS 指纹时，生成器现已为二维码、VLESS URI 设置 fp=chrome147 与 Loon 专用提示 tls-profile=chrome147，并在 loon.txt 中使用官方支持的 tls-profile=chrome147。

注意：Loon 官方文档列出的名称是 chrome 和 chrome147，没有独立列出 chrome-14。chrome147 并不一定等价于客户端 UI 中的 X25519 握手标注。VLESS URI 额外提示可能被第三方客户端忽略，因此扫码导入后仍需在 Loon 检查 TLS 指纹是否正确。

对于已安装的 Vision，绝对不要再次运行安装器覆盖现有 UUID；执行这一条命令更新四个设备的节点链接及二维码：

~~~bash
curl -fsSL -o /root/refresh-vision-loon.py https://raw.githubusercontent.com/chb1991/hy2-deploy/main/xray-vision/refresh-loon.py && python3 /root/refresh-vision-loon.py
~~~

刷新脚本会先核对 server JSON 中的四个 UUID 和现有节点链接，按原权限保留身份信息，仅更新 node.txt、loon.txt、node-qr.png，自动备份至 /root/vision-backups。不会修改 Xray/HY2/TUIC 配置，不重启任何服务。Telegram 重新执行 /vget IPHONE 就能获取更新后的二维码和链接。

## CHBVPS Bot V2.0 · 免费极简界面（仅升级 Telegram 机器人）

已部署 HY2/TUIC/XHTTP/Vision 的 Debian 13 VPS 可以使用 V2 安全升级器。该升级器只更新 `/opt/hy2-telegram` 中的机器人代码与精确限定的 sudo 权限，不修改代理服务、密钥、UUID、端口、域名或 TLS 证书，也不重启任何代理核心。复用已有 Telegram Bot Token 和管理员数字 ID。

一行 SSH 安装命令（root，交互式确认；不要使用 curl | bash）：

~~~bash
curl -fsSL -o /root/upgrade-chbvps-v2.sh https://raw.githubusercontent.com/chb1991/hy2-deploy/main/telegram-bot/upgrade-v2.sh && bash /root/upgrade-chbvps-v2.sh
~~~

可见命令共 9 个：`/start`、`/help`、`/status`、`/devices [协议]`、`/get [设备] [协议]`、`/add <设备> [协议]`、`/del <设备> [协议]`、`/restart [协议]`、`/update`。菜单采用免费 Unicode 线条符号、固定状态指示和 Telegram 原生 `primary/success/danger` 按钮风格，无需 Telegram Premium，自定义 Premium 图标不使用。

操作示例：

~~~text
/get IPHONE            # 四协议现有链接，二维码按钮按需发送
/get IPHONE vision     # Vision 单协议链接
/devices hy2           # HY2 在线与当前服务周期流量
/devices tuic          # TUIC 账号列表，不虚报在线数量
/add IPAD              # 选择 HY2/TUIC/两者
/add IPAD hy2          # 只新增 HY2
/del IPAD hy2          # 二次确认删除
/restart all           # 二次确认，顺序重启四协议
/update                # 检查与选择更新 HY2/sing-box，不自动升级 Xray
~~~

原有 `/vget`、`/xget`、`/get_tuic`、`/qr`、`/delete`、`/add_defaults` 等命令依然兼容，但从 `/help` 及 Telegram 命令下拉菜单隐藏。**旧消息里的删除、重启、升级确认按钮不再允许直接执行**，必须使用新的命令重新发起并通过一次性确认。

安全特性：管理员私聊校验、`callback_data` 64 字节限制、短时且单次有效的随机确认令牌（绑定管理员聊天及确切消息 ID）、危险操作明确反馈、按协议只允许 HY2/TUIC 动态增删、四协议状态及本机端口检测、按需发送现有节点/二维码。HY2 流量严格标注为当前服务统计周期；TUIC/Vision/XHTTP 不虚构在线数据。升级器为原机器人与 sudo 规则在 `/root/chbvps-bot-backups/` 生成备份，失败时尽力自动恢复。Telegram 私聊不是端到端加密，切勿公开分享节点链接。

升级前要求：`hy2-telegram.service` 已在运行，现有 `/usr/local/sbin/core-update` 与 `/usr/local/sbin/hy2-restart` 存在，并保留原有管理员 Bot Token 配置。新脚本会更新机器人服务所使用的 `bot.py`、`dual_bot.py`（若存在）和 `v2_ui.py`，仅重启 `hy2-telegram.service`，不会重启 HY2/TUIC/Vision/XHTTP。安装后在 Telegram 发送 `/start` 验证按钮。

源代码：`telegram-bot/v2_ui.py`；回归测试：`telegram-bot/test_v2_ui.py`。GitHub Actions 通过不等于真实 Telegram API / VPS 端到端测试。首次实际升级后应检查 `systemctl is-active hy2-telegram` 与 Telegram 私聊按钮功能。
