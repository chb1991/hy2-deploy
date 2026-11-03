#!/usr/bin/env bash
# Transactional updater for the official Hysteria 2 and sing-box binaries.
# SSH: update | update --check | update --hy2 | update --singbox
# Telegram: core-update --check --json, then core-update --all --yes --json
set -Eeuo pipefail
umask 077

API="https://api.github.com"
HY2_REPO="HyNetworks/hysteria"
SB_REPO="SagerNet/sing-box"
HY2_BIN="/usr/local/bin/hysteria"
HY2_CONFIG="/etc/hysteria/config.yaml"
HY2_UNIT="hysteria-server.service"
SB_BIN="/opt/tuic-singbox/sing-box"
SB_CONFIG="/etc/tuic-singbox/config.json"
SB_UNIT="tuic-singbox.service"
LOCK="/run/lock/hy2-singbox-core-update.lock"
TMP=""
BACKUP=""
MUTATED=0
DO_HY2=0
DO_SB=0
JSON=0
YES=0
CHECK=0

log() { printf '[UPDATE] %s\n' "$*" >&2; }
fail() { printf '[UPDATE] ERROR: %s\n' "$*" >&2; exit 1; }

need() { command -v "$1" >/dev/null 2>&1 || fail "缺少命令：$1"; }

api_get() {
  curl -fsSL --retry 3 --connect-timeout 10 --max-time 90 \
    -H 'Accept: application/vnd.github+json' \
    -H 'X-GitHub-Api-Version: 2022-11-28' \
    "$1"
}

digest_ok() {
  [[ "$1" =~ ^sha256:[a-fA-F0-9]{64}$ ]] || fail "GitHub Release 未提供有效 SHA256 摘要，拒绝安装。"
}

version_hy2() {
  local output
  output="$("$HY2_BIN" version 2>&1)" || return 1
  printf '%s\n' "$output" | sed -nE 's/^[[:space:]]*Version:[[:space:]]*(v?[0-9]+\.[0-9]+\.[0-9]+([+-][A-Za-z0-9.-]+)?)[[:space:]]*$/\1/p' | head -n 1
}

version_sb() {
  local output
  output="$("$SB_BIN" version 2>&1)" || return 1
  printf '%s\n' "$output" | grep -Eo '[0-9]+\.[0-9]+\.[0-9]+([+-][A-Za-z0-9.-]+)?' | head -n 1
}

installed_hy2() {
  [[ -x "$HY2_BIN" && -f "$HY2_CONFIG" ]] && systemctl cat "$HY2_UNIT" >/dev/null 2>&1
}
installed_sb() {
  [[ -x "$SB_BIN" && -f "$SB_CONFIG" ]] && systemctl cat "$SB_UNIT" >/dev/null 2>&1
}

fetch_singbox_latest() {
  local json_file="$1" release tag version asset_name asset url digest
  release="$(api_get "$API/repos/$SB_REPO/releases/latest")" || fail "无法获取 sing-box 最新稳定 Release。"
  jq -e '.draft == false and .prerelease == false and (.tag_name|type=="string")' >/dev/null <<< "$release" || fail "sing-box 最新版本不是稳定正式版。"
  tag="$(jq -r '.tag_name' <<< "$release")"
  version="${tag#v}"
  asset_name="sing-box-$version-linux-amd64.tar.gz"
  asset="$(jq -c --arg name "$asset_name" '[.assets[] | select(.name==$name)][0] // empty' <<< "$release")"
  [[ -n "$asset" && "$asset" != "null" ]] || fail "sing-box Release 缺少资产：$asset_name"
  url="$(jq -r '.browser_download_url // empty' <<< "$asset")"
  digest="$(jq -r '.digest // empty' <<< "$asset")"
  [[ "$url" == https://github.com/"$SB_REPO"/releases/download/* ]] || fail "sing-box 下载地址不符合官方 GitHub Release 地址。"
  digest_ok "$digest"
  jq -n --arg tag "$tag" --arg version "$version" --arg asset "$asset_name" --arg url "$url" --arg digest "$digest" \
    '{tag:$tag,version:$version,asset:$asset,url:$url,digest:$digest}' > "$json_file"
}

fetch_latest() {
  local repo="$1" asset_name="$2" json_file="$3"
  local release tag asset url digest
  release="$(api_get "$API/repos/$repo/releases/latest")" || fail "无法获取 $repo 最新稳定 Release。"
  jq -e '.draft == false and .prerelease == false and (.tag_name|type=="string")' >/dev/null <<< "$release" || fail "$repo 最新版本不是稳定正式版。"
  tag="$(jq -r '.tag_name' <<< "$release")"
  asset="$(jq -c --arg name "$asset_name" '[.assets[] | select(.name==$name)][0] // empty' <<< "$release")"
  [[ -n "$asset" && "$asset" != "null" ]] || fail "$repo Release 缺少资产：$asset_name"
  url="$(jq -r '.browser_download_url // empty' <<< "$asset")"
  digest="$(jq -r '.digest // empty' <<< "$asset")"
  [[ "$url" == https://github.com/"$repo"/releases/download/* ]] || fail "$repo 下载地址不符合官方 GitHub Release 地址。"
  digest_ok "$digest"
  jq -n --arg tag "$tag" --arg asset "$asset_name" --arg url "$url" --arg digest "$digest" \
    '{tag:$tag,asset:$asset,url:$url,digest:$digest}' > "$json_file"
}

collect_status() {
  local h_inst=0 s_inst=0 h_cur="" h_tag="" s_cur="" s_tag=""
  if installed_hy2 && (( DO_HY2 )); then
    h_inst=1
    h_cur="$(version_hy2 || true)"
    [[ -n "$h_cur" ]] || h_cur="unknown"
    fetch_latest "$HY2_REPO" "hysteria-linux-amd64" "$TMP/hy2-release.json"
    h_tag="$(jq -r '.tag' "$TMP/hy2-release.json")"
    h_tag="${h_tag##*/}"
    [[ "$h_tag" == v* ]] || h_tag="v$h_tag"
  fi
  if installed_sb && (( DO_SB )); then
    s_inst=1
    s_cur="$(version_sb || true)"
    [[ -n "$s_cur" ]] || s_cur="unknown"
    fetch_singbox_latest "$TMP/sb-release.json"
    s_tag="$(jq -r '.version' "$TMP/sb-release.json")"
  fi

  if (( JSON )); then
    jq -n \
      --argjson hi "$h_inst" --arg hc "$h_cur" --arg ht "$h_tag" \
      --argjson si "$s_inst" --arg sc "$s_cur" --arg st "$s_tag" \
      '{
        hy2:{installed:($hi==1),current:(if $hi==1 then $hc else null end),latest:(if $hi==1 then $ht else null end),update_available:($hi==1 and $hc!=$ht)},
        singbox:{installed:($si==1),current:(if $si==1 then $sc else null end),latest:(if $si==1 then $st else null end),update_available:($si==1 and $sc!=$st)}
      }'
  else
    printf 'HY2: %s\n' "$([[ $h_inst == 1 ]] && printf '%s -> %s' "$h_cur" "$h_tag" || printf '未安装')"
    printf 'sing-box: %s\n' "$([[ $s_inst == 1 ]] && printf '%s -> %s' "$s_cur" "$s_tag" || printf '未安装')"
  fi
}

rollback() {
  local rc=$?
  if (( rc != 0 && MUTATED == 1 )); then
    log "升级未完成，正在恢复升级前的核心二进制……"
    if [[ -n "$BACKUP" ]]; then
      [[ ! -f "$BACKUP/hysteria" ]] || install -m 0755 "$BACKUP/hysteria" "$HY2_BIN"
      [[ ! -f "$BACKUP/sing-box" ]] || install -m 0755 "$BACKUP/sing-box" "$SB_BIN"
      systemctl daemon-reload || true
      if [[ -f "$BACKUP/hysteria" ]]; then systemctl restart "$HY2_UNIT" || true; fi
      if [[ -f "$BACKUP/sing-box" ]]; then systemctl restart "$SB_UNIT" || true; fi
      log "已尝试恢复原版本。备份目录：$BACKUP"
    fi
  fi
  [[ -z "$TMP" ]] || rm -rf "$TMP"
  return "$rc"
}

upgrade() {
  local h_old="" s_old="" h_new="" s_new="" h_url="" h_digest="" s_url="" s_digest=""
  local h_asset="" s_asset="" extracted=""
  TMP="$(mktemp -d /tmp/core-update.XXXXXXXX)"
  trap rollback EXIT

  if installed_hy2 && (( DO_HY2 )); then
    h_old="$(version_hy2 || true)"
    fetch_latest "$HY2_REPO" "hysteria-linux-amd64" "$TMP/hy2-release.json"
    h_new="$(jq -r '.tag' "$TMP/hy2-release.json")"
    h_new="${h_new##*/}"
    h_new="v${h_new#v}"
    h_url="$(jq -r '.url' "$TMP/hy2-release.json")"
    h_digest="$(jq -r '.digest' "$TMP/hy2-release.json")"
  fi
  if installed_sb && (( DO_SB )); then
    s_old="$(version_sb || true)"
    fetch_singbox_latest "$TMP/sb-release.json"
    s_new="$(jq -r '.version' "$TMP/sb-release.json")"
    s_asset="$(jq -r '.asset' "$TMP/sb-release.json")"
    s_url="$(jq -r '.url' "$TMP/sb-release.json")"
    s_digest="$(jq -r '.digest' "$TMP/sb-release.json")"
  fi

  local need_h=0 need_s=0
  if [[ -n "$h_new" && "$h_old" != "$h_new" ]]; then need_h=1; fi
  if [[ -n "$s_new" && "$s_old" != "$s_new" ]]; then need_s=1; fi
  if (( need_h == 0 && need_s == 0 )); then
    log "已安装的核心均为上游最新稳定版，无需升级。"
    if (( JSON )); then
      jq -n --arg h "${h_old:-not-installed}" --arg s "${s_old:-not-installed}" '{ok:true,changed:false,hy2:$h,singbox:$s}'
    else
      echo "无需升级。"
    fi
    return 0
  fi

  log "下载并校验官方核心文件……"
  if (( need_h )); then
    curl -fL --retry 3 --connect-timeout 10 "$h_url" -o "$TMP/hysteria"
    printf '%s  %s\n' "${h_digest#sha256:}" "$TMP/hysteria" | sha256sum -c - >/dev/null || fail "Hysteria SHA256 校验失败。"
    chmod 0755 "$TMP/hysteria"
    "$TMP/hysteria" version >/dev/null 2>&1 || fail "新 Hysteria 二进制无法执行。"
    # Hysteria does not expose a standalone config-check command; verify YAML parses before restart.
    python3 - "$HY2_CONFIG" <<'PY'
import sys, yaml
with open(sys.argv[1], encoding="utf-8") as f:
    value = yaml.safe_load(f)
if not isinstance(value, dict):
    raise SystemExit("HY2 config is not a YAML mapping")
PY
  fi
  if (( need_s )); then
    curl -fL --retry 3 --connect-timeout 10 "$s_url" -o "$TMP/$s_asset"
    printf '%s  %s\n' "${s_digest#sha256:}" "$TMP/$s_asset" | sha256sum -c - >/dev/null || fail "sing-box SHA256 校验失败。"
    mkdir "$TMP/singbox-extract"
    tar -xzf "$TMP/$s_asset" -C "$TMP/singbox-extract"
    extracted="$(find "$TMP/singbox-extract" -type f -name sing-box -print -quit)"
    [[ -n "$extracted" ]] || fail "sing-box 压缩包中没有 sing-box 二进制。"
    install -m 0755 "$extracted" "$TMP/sing-box"
    "$TMP/sing-box" version >/dev/null 2>&1 || fail "新 sing-box 二进制无法执行。"
    "$TMP/sing-box" check -c "$SB_CONFIG" >/dev/null || fail "现有 TUIC 配置不兼容新 sing-box，取消升级。"
  fi

  install -d -m 0700 /root/core-upgrade-backups
  BACKUP="$(mktemp -d "/root/core-upgrade-backups/$(date +%Y%m%d-%H%M%S)-XXXXXX")"
  chmod 0700 "$BACKUP"
  if (( need_h )); then
    cp -a "$HY2_BIN" "$BACKUP/hysteria"
    cp -a "$HY2_CONFIG" "$BACKUP/hysteria-config.yaml"
    local h_unit_path
    h_unit_path="$(systemctl show -p FragmentPath --value "$HY2_UNIT")"
    [[ ! -f "$h_unit_path" ]] || cp -a "$h_unit_path" "$BACKUP/hysteria.service"
  fi
  if (( need_s )); then
    cp -a "$SB_BIN" "$BACKUP/sing-box"
    cp -a "$SB_CONFIG" "$BACKUP/tuic-config.json"
    local s_unit_path
    s_unit_path="$(systemctl show -p FragmentPath --value "$SB_UNIT")"
    [[ ! -f "$s_unit_path" ]] || cp -a "$s_unit_path" "$BACKUP/tuic.service"
    [[ ! -d /etc/tuic-singbox ]] || cp -a /etc/tuic-singbox "$BACKUP/tuic-singbox"
  fi
  if [[ -d /var/lib/hy2-telegram ]]; then
    tar -czf "$BACKUP/hy2-telegram-state.tar.gz" -C /var/lib hy2-telegram
  fi
  chmod -R go-rwx "$BACKUP"

  log "备份完成：$BACKUP"
  MUTATED=1
  if (( need_h )); then
    install -m 0755 "$TMP/hysteria" "$HY2_BIN.new"
    mv -f "$HY2_BIN.new" "$HY2_BIN"
    systemctl restart "$HY2_UNIT"
    sleep 2
    systemctl is-active --quiet "$HY2_UNIT" || fail "HY2 升级后未保持 active。"
    [[ "$(version_hy2 || true)" == "$h_new" ]] || fail "HY2 升级后版本核验不匹配。"
    [[ -n "$(ss -H -lun 'sport = :443' 2>/dev/null)" ]] || fail "HY2 升级后未检测到 UDP 443 监听。"
    log "HY2 已升级：$h_old -> $(version_hy2 || echo unknown)"
  fi
  if (( need_s )); then
    install -m 0755 "$TMP/sing-box" "$SB_BIN.new"
    mv -f "$SB_BIN.new" "$SB_BIN"
    systemctl restart "$SB_UNIT"
    sleep 2
    systemctl is-active --quiet "$SB_UNIT" || fail "TUIC 升级后未保持 active。"
    [[ "$(version_sb || true)" == "$s_new" ]] || fail "sing-box 升级后版本核验不匹配。"
    [[ -n "$(ss -H -lun 'sport = :51443' 2>/dev/null)" ]] || fail "TUIC 升级后 UDP 51443 未监听。"
    log "sing-box 已升级：$s_old -> $(version_sb || echo unknown)"
  fi

  if (( JSON )); then
    jq -n --arg h_old "${h_old:-not-installed}" --arg h_new "${h_new:-not-installed}" \
      --arg s_old "${s_old:-not-installed}" --arg s_new "${s_new:-not-installed}" \
      --arg backup "$BACKUP" '{ok:true,changed:true,hy2:{from:$h_old,to:$h_new},singbox:{from:$s_old,to:$s_new},backup:$backup}'
  else
    echo "核心升级完成。"
    [[ -z "$h_new" ]] || echo "HY2：$h_old -> $h_new"
    [[ -z "$s_new" ]] || echo "sing-box：$s_old -> $s_new"
    echo "备份：$BACKUP"
  fi
  MUTATED=0
}

main() {
  [[ $EUID -eq 0 ]] || fail "请使用 root 执行。"
  need curl; need jq; need sha256sum; need tar; need systemctl; need ss; need flock; need python3
  local mode="all"
  while (($#)); do
    case "$1" in
      --check) CHECK=1 ;;
      --json) JSON=1 ;;
      --yes) YES=1 ;;
      --all) mode="all" ;;
      --hy2) mode="hy2" ;;
      --singbox) mode="singbox" ;;
      -h|--help)
        cat <<'HELP'
用法：
  update                  检查并交互确认后升级所有已安装核心
  update --check          仅检查上游最新稳定版本
  update --hy2            仅升级 Hysteria 2
  update --singbox        仅升级 sing-box
  update --yes            非交互执行（仅供受控自动化调用）
HELP
        return 0 ;;
      *) fail "未知参数：$1" ;;
    esac
    shift
  done

  installed_hy2 && [[ "$mode" != "singbox" ]] && DO_HY2=1
  installed_sb && [[ "$mode" != "hy2" ]] && DO_SB=1
  [[ "$mode" != "hy2" || $DO_HY2 == 1 ]] || fail "HY2 未安装。"
  [[ "$mode" != "singbox" || $DO_SB == 1 ]] || fail "sing-box/TUIC 未安装。"
  (( DO_HY2 || DO_SB )) || fail "未检测到可升级的 HY2 或 sing-box 服务。"

  TMP="$(mktemp -d /tmp/core-update-check.XXXXXXXX)"
  if (( CHECK )); then
    collect_status
    rm -rf "$TMP"
    TMP=""
    return 0
  fi

  exec 9>"$LOCK"
  flock -n 9 || fail "已有核心升级任务正在执行，请稍后再试。"

  # Show versions before prompting; Telegram supplies --yes only after button confirmation.
  if (( ! YES )); then
    collect_status
    printf '\n将升级已安装的核心，服务连接会短暂中断。配置、用户和节点凭据保持不变。\n'
    read -r -p "确认执行升级？[y/N] " answer
    [[ "$answer" == "y" || "$answer" == "Y" || "$answer" == "yes" || "$answer" == "YES" ]] || {
      echo "已取消。"
      rm -rf "$TMP"; TMP=""
      return 0
    }
  fi

  rm -rf "$TMP"; TMP=""
  upgrade
}

main "$@"
