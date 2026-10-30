#!/usr/bin/env bash
# 链式执行：等 dedup 产物落地 → quality → mix → release → downstream（M5-M8 真实链路）
# 输出追加到 data/chain_log.txt，每步带时间戳与退出码。
set -u
cd "$(dirname "$0")/.."
LOG=data/chain_log.txt
say() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }

say "等待 dedup 产物（data/dedup/ + manifest 键）..."
for i in $(seq 1 240); do   # 最多再等 2 小时
  if [ -d data/dedup ] && grep -q '"dedup"' data/manifest.json 2>/dev/null; then
    say "dedup 产物已落地（等待了 $((i*30))s）"; break
  fi
  sleep 30
done
if ! grep -q '"dedup"' data/manifest.json 2>/dev/null; then
  say "超时：dedup 未在 2 小时内完成，链式中止"; exit 1
fi

for stage in quality mix release; do
  say "→ python -m $stage.run"
  if python -m "$stage.run" >>"$LOG" 2>&1; then say "✅ $stage 完成"; else say "❌ $stage 失败(见日志)"; exit 1; fi
done
say "→ python -m downstream.run"
if python -m downstream.run >>"$LOG" 2>&1; then say "✅ downstream 完成"; else say "❌ downstream 失败(见日志)"; fi
say "链式执行结束"
