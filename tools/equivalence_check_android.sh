#!/usr/bin/env bash
# Compile and run tools/equivalence_check_android.kt against both trees.
#
# Both trees declare the same packages, so each side is compiled into its own
# output directory and run as its own JVM; the driver then compares the two
# JSON files. Nothing here touches the network, Gradle or the Android SDK — the
# files under test are pure Kotlin plus org.json, and the three Android classes
# they do touch (Rect, Log, Context/SharedPreferences) come from the hand-written
# stubs that ship with the repository under tools/equivalence_stub/android.
#
# Usage:
#   tools/equivalence_check_android.sh --upstream <path to jev-chat-jarvis>
#                                      [--ours <path, default android/>]
#                                      [--probe <path to the kotlinc/jdk probe>]
#                                      [--negative-control [keys|clamp]]
#
# --negative-control [keys|clamp] copies our tree, breaks it deliberately and
# runs the suite against the copy, which must go red. Our own tree is never
# touched. The two breaks:
#   keys  (default) reverses the reply keys, so the per-candidate scores land on
#         the wrong candidates — the ranking control.
#   clamp makes the adapter clamp the tension score into 0..9, i.e. re-validates
#         what the consumers already handle — the "translation only" control.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
OURS="$ROOT/android"
UPSTREAM="${UPSTREAM_TREE:-}"
PROBE="${ANDROID_PROBE:-}"
NEGATIVE=""

while [ $# -gt 0 ]; do
  case "$1" in
    --upstream) UPSTREAM="$2"; shift 2;;
    --ours) OURS="$2"; shift 2;;
    --probe) PROBE="$2"; shift 2;;
    --negative-control)
      NEGATIVE="keys"
      case "${2:-}" in keys|clamp) NEGATIVE="$2"; shift;; esac
      shift;;
    *) echo "unknown argument $1" >&2; exit 2;;
  esac
done

[ -n "$UPSTREAM" ] || { echo "要给 --upstream <上游那棵树> 或设 UPSTREAM_TREE" >&2; exit 2; }
[ -n "$PROBE" ] || { echo "要给 --probe <带 jdk/kotlinc/org.json.jar 的目录> 或设 ANDROID_PROBE" >&2; exit 2; }

JDK="$(echo "$PROBE"/jdk/jdk-*)"
KOTLINC="$PROBE/kotlinc/kotlinc/bin/kotlinc"
JSONJAR="$PROBE/org.json.jar"
STDLIB="$PROBE/kotlinc/kotlinc/lib/kotlin-stdlib.jar"
# 三个 Android 类型的桩(Rect / Log / Context)是**源码**,跟仓库走,不跟工具链走:
# 本地和 CI 用同一份,改一处两边都变。--probe 只提供 JDK、kotlinc 和 org.json。
ANDROID_STUBS="$HERE/equivalence_stub/android"
export JAVA_HOME="$JDK"
export PATH="$JAVA_HOME/bin:$PATH"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

if [ -n "$NEGATIVE" ]; then
  cp -r "$OURS" "$WORK/broken-ours"
  OURS="$WORK/broken-ours"
  MA="$OURS/app/src/main/java/com/jev/probe/jev/MergedAnswer.kt"
  JQ="$OURS/app/src/main/java/com/jev/probe/jev/JevQuestions.kt"
  case "$NEGATIVE" in
    keys)
      sed -i 's/listOf("reply_a", "reply_b", "reply_c")/listOf("reply_c", "reply_b", "reply_a")/' "$JQ"
      echo "*** 负控 keys：把 reply 键顺序改反了（reply_a <-> reply_c），这一轮应当变红 ***";;
    clamp)
      sed -i 's/o.optDouble("score", 0.0), o.optDouble("confidence", 0.0), max)/o.optDouble("score", 0.0).coerceIn(0.0, max.toDouble()), o.optDouble("confidence", 0.0), max)/' "$MA"
      echo "*** 负控 clamp：让适配层把紧张度夹回 0..9（上游不夹），这一轮应当变红 ***";;
  esac
  echo
fi

# The set of sources each side needs. Everything else in either tree is UI or
# Android-only and is not part of what happens after the API.
common_src() {
  local tree="$1" pkg="$1/app/src/main/java/com/jev/probe"
  echo "$pkg/core/ChatModels.kt $pkg/core/Prefs.kt $pkg/core/kb/KbModels.kt"
  echo "$pkg/jev/JevQuestions.kt $pkg/jev/JevClient.kt $pkg/jev/ReplyClient.kt"
}

build_side() {
  local name="$1" tree="$2" extra="$3"
  local out="$WORK/$name"
  mkdir -p "$out"
  echo "--- 编译 $name（$tree）"
  # shellcheck disable=SC2046
  "$KOTLINC" -nowarn -cp "$JSONJAR" -d "$out" \
    "$ANDROID_STUBS/graphics/Rect.kt" "$ANDROID_STUBS/util/Log.kt" "$ANDROID_STUBS/content/Context.kt" \
    $(common_src "$tree") $extra \
    "$HERE/equivalence_stub/HttpJson.kt" "$HERE/equivalence_check_android.kt"
}

PKG_UP="$UPSTREAM/app/src/main/java/com/jev/probe"
PKG_OURS="$OURS/app/src/main/java/com/jev/probe"
build_side upstream "$UPSTREAM" "$PKG_UP/jev/JudgeClient.kt"
build_side ours "$OURS" "$PKG_OURS/jev/MergedAnswer.kt"

echo
echo "--- 跑两边（各自一个 JVM）"
java -cp "$JSONJAR:$STDLIB:$WORK/upstream" Equivalence_check_androidKt --side upstream --out "$WORK/up.json"
java -cp "$JSONJAR:$STDLIB:$WORK/ours" Equivalence_check_androidKt --side ours --out "$WORK/ours.json"

echo
java -cp "$JSONJAR:$STDLIB:$WORK/ours" Equivalence_check_androidKt --compare "$WORK/up.json" "$WORK/ours.json"
