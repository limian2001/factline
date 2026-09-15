#!/usr/bin/env bash
# One command that answers "what is this box doing right now".
#
# Written because the self-deploy is silent when idle, which makes "nothing to
# do" and "the timer is dead" look identical in the journal. Every section here
# answers one specific question, so there is never a reason to sit watching
# `journalctl -f` and guessing.
#
#   /opt/factline/deploy/status.sh
set -uo pipefail

APP=/opt/factline
REPO_SLUG="limian2001/factline"
BRANCH=main

bold() { printf '\n\033[1m%s\033[0m\n' "$1"; }
ok()   { printf '  \033[32m%s\033[0m\n' "$1"; }
bad()  { printf '  \033[31m%s\033[0m\n' "$1"; }
dim()  { printf '  \033[2m%s\033[0m\n' "$1"; }

# ---------------------------------------------------------------- timers
bold "定时器"
for unit in factline-deploy.timer factline.timer; do
  if systemctl is-active --quiet "$unit" 2>/dev/null; then
    next=$(systemctl show "$unit" -p NextElapseUSecRealtime --value 2>/dev/null)
    ok "$unit 活着"
    [ -n "$next" ] && dim "下次: $next"
  else
    bad "$unit 没在跑  ->  sudo systemctl enable --now $unit"
  fi
done

# ------------------------------------------------------------- last checks
bold "自部署最近一次检查"
last_check_file="$APP/data/.deploy-last-check"
if [ -r "$last_check_file" ]; then
  last=$(cat "$last_check_file")
  age=$(( $(date -u +%s) - $(date -u -d "$last" +%s 2>/dev/null || echo 0) ))
  if [ "$age" -lt 600 ]; then
    ok "$last($age 秒前)"
  else
    bad "$last($((age / 60)) 分钟前 —— 超过 10 分钟就不正常了)"
  fi
else
  dim "还没有心跳文件(这个版本刚部署上来?)"
fi

result=$(systemctl show factline-deploy.service -p Result --value 2>/dev/null)
[ "$result" = "success" ] && ok "上次退出: success" || bad "上次退出: ${result:-未知}"

# ----------------------------------------------------------------- commits
bold "代码版本"
local_sha=$(git -C "$APP" rev-parse --short HEAD 2>/dev/null || echo "?")
git -C "$APP" fetch --quiet origin "$BRANCH" 2>/dev/null
remote_sha=$(git -C "$APP" rev-parse --short "origin/$BRANCH" 2>/dev/null || echo "?")

dim "本地 : $local_sha  $(git -C "$APP" log -1 --format=%s 2>/dev/null | cut -c1-48)"
dim "远端 : $remote_sha"

if [ "$local_sha" = "?" ] || [ "$remote_sha" = "?" ]; then
  # Never report "all good" from state we could not actually read. A status tool
  # that says fine when it is blind is worse than no status tool at all.
  bad "读不到版本信息 —— /opt/factline 不是 git 仓库,或者取不到远端"
elif [ "$local_sha" = "$remote_sha" ]; then
  ok "已是最新"
else
  bad "落后了,等下一次自部署(≤3 分钟)"

  full=$(git -C "$APP" rev-parse "origin/$BRANCH" 2>/dev/null)
  ci=$("$APP/deploy/ci_state.py" "$full" 2>/dev/null || echo unreachable)
  case "$ci" in
    success)  ok  "远端 CI: 绿灯,下一轮自部署就会上线" ;;
    pending)  dim "远端 CI: 还在跑,它会等" ;;
    none)     bad "远端 CI: 这个 commit 没有任何 check —— workflow 没触发?" ;;
    failed:*) bad "远端 CI: 红灯 -> ${ci#failed:}" ;;
    *)        bad "远端 CI: 读不到($ci)" ;;
  esac
fi

# -------------------------------------------------------------- pipeline
bold "数据流水线"
p_result=$(systemctl show factline.service -p Result --value 2>/dev/null)
[ "$p_result" = "success" ] && ok "上次退出: success" || bad "上次退出: ${p_result:-还没跑过}"

health="$APP/data/site/health.json"
if [ -r "$health" ]; then
  finished=$(python3 -c "import json;print(json.load(open('$health'))['last_run_finished'])" 2>/dev/null)
  age=$(( $(date -u +%s) - $(date -u -d "$finished" +%s 2>/dev/null || echo 0) ))
  hours=$(( age / 3600 ))
  if [ "$hours" -lt 36 ]; then
    ok "数据新鲜度: $finished($hours 小时前)"
  else
    bad "数据新鲜度: $finished($hours 小时前 —— 陈旧)"
  fi
else
  dim "还没有 health.json(流水线一次都没成功跑完)"
fi

rows=$(ls -1 "$APP/data/clean/facts/"*.parquet 2>/dev/null | wc -l | tr -d ' ')
[ "$rows" -gt 0 ] && ok "数据湖: $rows 个 ticker 的分区" || dim "数据湖: 空的"

# ------------------------------------------------------------------ hints
bold "想看更多"
dim "自部署日志(含静默的 idle 心跳): sudo journalctl -u factline-deploy.service -p debug -n 30 --no-pager"
dim "流水线日志:                     sudo journalctl -u factline.service -n 50 --no-pager"
dim "立刻手动跑一次自部署:           sudo -u factline $APP/deploy/pull-deploy.sh"
dim "立刻手动跑一次流水线:           sudo systemctl start factline.service"
echo
