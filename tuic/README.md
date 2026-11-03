# TUIC v5 增量安装（不覆盖现有 HY2）

本目录给**已安装** HY2 + Telegram 多设备机器人（`chb1991/hy2-deploy`）的 Debian 13 x86_64 VPS 增加 TUIC v5。

## 端口与隔离

| 项目 | 现有 HY2 | 新增 TUIC |
|---|---|---|
| 传输 | UDP 443 | UDP 51443 |
| 核心 | 官方 Hysteria | 独立 sing-box 1.14.4 |
| 服务 | hysteria-server.service | tuic-singbox.service |
| 配置 | /etc/hysteria/config.yaml | /etc/tuic-singbox/config.json |
| 凭据 | 原四个设备凭据原样保留 | 自动创建四个独立 UUID + 密码 |
| 证书 | 原有 Hysteria 内置 ACME | 独立 Certbot Cloudflare DNS-01 |

不重装 HY2，不覆盖 /etc/hysteria/config.yaml、HY2 用户数据、设备二维码。Telegram 机器人保留原 bot.py 文件，只修改服务入口为兼容两种协议的 dual_bot.py。HY2 没有被脚本显式重启；机器人本身会重启一次。

## 安装命令（仅用于已经正常运行 HY2 与机器人的 VPS）

```bash
curl -fsSL -o /root/install-tuic.sh https://raw.githubusercontent.com/chb1991/hy2-deploy/main/tuic/install.sh && bash /root/install-tuic.sh
```

安装器先检查原 HY2 与机器人是否正常、UDP 51443 是否空闲；若原 HY2 未使用 Cloudflare DNS-01、缺少原有多用户环境，安装器直接退出，不进行覆盖式转换。通过现有 Cloudflare DNS API Token 为原域名独立签发 TUIC 证书；密钥仍只存服务器。Cloudflare DNS 需保持灰云。不要求入站 TCP 80/443。 安装器会生成四台设备的节点文件和二维码，并逐一以 `hy2bot` 身份验证文件可读性；目录权限固定为 `0750`，节点和二维码文件为 `0640`，避免 `umask 077` 导致 Telegram 机器人无法读取二维码。

sing-box 从官方 GitHub v1.14.4 下载，并核验 GitHub release asset 的 SHA256。TUIC 配置写入前后均执行检查，失败后不会主动改动 HY2。服务器本机防火墙如果启用了 UFW/firewalld，只新增 UDP 51443 放行规则；没有自动管理的防火墙时，需自行检查服务商安全组、nftables 等是否允许 UDP 51443。

## Telegram

给原机器人发送：

- `/status`：并排显示 HY2、TUIC 的 systemd 状态、UDP 监听、用户数量；HY2 在线实例数据从官方统计 API 读取，TUIC 在线人数显示 N/A，**不是 0**。
- `/add_defaults`：选择 HY2、TUIC 或两种协议，获取四台设备独立链接和二维码；已有用户不重置密码。
- `/devices_tuic`：TUIC 设备列表。
- `/add_tuic IPAD`、`/get_tuic IPAD`、`/delete_tuic IPAD`：管理 TUIC 用户；删除需二次确认。
- `/restart_tuic`：二次确认，只重启 TUIC。
- 原有 HY2 的 `/add`、`/get`、`/qr`、`/devices`、`/traffic`、`/restart` 等命令仍由原 bot.py 处理。

`/status` 中监听成功 **不等于** 国内手机能连通，仍需使用蜂窝网络实测 TUIC，测试后才可确认可用。

## 双核心统一升级

TUIC 增量安装完成后，会同时安装统一升级器：

- SSH root：`update`，显示 HY2 / sing-box 当前版本和上游最新稳定版，输入 `y` 二次确认。
- SSH：`update --check` 只检查；`update --hy2` 只升级 HY2；`update --singbox` 只升级 sing-box。
- Telegram 私聊管理员：发送 `/update`，查看版本后点击确认或取消。

升级器只下载 Hysteria 与 sing-box 官方 GitHub Release 稳定正式版，校验 SHA256。升级前备份核心和相关配置；不重置 HY2/TUIC 设备凭据、节点链接、二维码、端口、域名和证书。两个核心分别升级、分别重启验证；失败时恢复旧二进制并尝试恢复服务。升级过程中相应协议会短暂断开。

升级完成后，仍应通过手机蜂窝网络实际测试节点。备份保存在 `/root/core-upgrade-backups/`。

## 诊断

```bash
systemctl status hysteria-server tuic-singbox hy2-telegram --no-pager
ss -lunp 'sport = :51443'
journalctl -u tuic-singbox -n 50 --no-pager
journalctl -u hy2-telegram -n 50 --no-pager
```

## 回滚（保留 HY2）

安装器若在机器人切换后遇到异常，会把原 Telegram 服务单元文件恢复，并停止新 TUIC；不会重启或修改 HY2。如果部署后想关闭 TUIC：

```bash
systemctl disable --now tuic-singbox
```

若只想恢复原机器人：在安装结果显示的 `/root/tuic-upgrade-backup-时间戳/` 中找到 `hy2-telegram.service` 备份文件，复制回 `/etc/systemd/system/hy2-telegram.service`，再执行 `systemctl daemon-reload && systemctl restart hy2-telegram`。这只会重启 Telegram，不会重启 HY2。

**不要重新运行仓库根目录的 install.sh 或 telegram-bot/upgrade-devices.sh 来安装 TUIC。** 这些脚本面向新 VPS 或 HY2 凭据迁移，会重置 HY2 用户和重启现有连接。

配置与 Token 不应上传公共仓库；这套 TUIC 安装器对重复安装会拒绝覆盖已有配置。证书续期由 Certbot 定时器管理，仅在 TUIC 证书更新时复制证书和重启 TUIC。
