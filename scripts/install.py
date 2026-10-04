#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""install.py - 把 Laya 决策中枢 + 专家团部署到 WorkBuddy 的 skills 目录。

默认把本 skill 及 experts/ 下每个专家软链（symlink）到 ~/.codebuddy/skills/，
使 WorkBuddy 能识别并触发它们（专家团成员即一组并行可用的 skill）。

用法：
  python3 install.py                 # 软链到 ~/.codebuddy/skills（默认）
  python3 install.py --mode copy    # 复制而非软链（适合只读/便携）
  python3 install.py --target /path/to/skills --force
  python3 install.py --core-only    # 只部署主 skill，不部署专家

部署后请重启 WorkBuddy 以加载新 skill。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_ROOT = os.path.dirname(HERE)            # .../laya-decision
EXPERTS_DIR = os.path.join(SKILL_ROOT, "experts")
REGISTRY = os.path.join(EXPERTS_DIR, "_registry.json")
DEFAULT_TARGET = os.path.join(os.path.expanduser("~"), ".codebuddy", "skills")


def link_or_copy(src, dst, mode, force):
    if os.path.islink(dst) or os.path.exists(dst):
        if not force:
            return "skip(已存在)"
        if os.path.islink(dst):
            os.unlink(dst)
        elif os.path.isdir(dst):
            import shutil
            shutil.rmtree(dst)
        else:
            os.remove(dst)
    if mode == "copy":
        import shutil
        shutil.copytree(src, dst)
        return "copied"
    os.symlink(src, dst)
    return "linked"


def main():
    ap = argparse.ArgumentParser(description="部署 Laya 专家团到 WorkBuddy")
    ap.add_argument("--target", default=DEFAULT_TARGET)
    ap.add_argument("--mode", choices=["link", "copy"], default="link")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的部署")
    ap.add_argument("--core-only", action="store_true", help="只部署主 skill")
    args = ap.parse_args()

    target = os.path.abspath(os.path.expanduser(args.target))
    os.makedirs(target, exist_ok=True)

    print("部署目标: %s  (模式: %s)\n" % (target, args.mode))

    # 1) 主 skill
    core_dst = os.path.join(target, "laya-decision")
    st = link_or_copy(SKILL_ROOT, core_dst, args.mode, args.force)
    print("  [core] laya-decision -> %s  (%s)" % (core_dst, st))

    if args.core_only:
        print("\n完成（仅主 skill）。重启 WorkBuddy 以加载。")
        return 0

    # 2) 专家团
    if not os.path.exists(REGISTRY):
        print("  [warn] 找不到注册表 %s，跳过专家部署" % REGISTRY, file=sys.stderr)
        return 2
    with open(REGISTRY, encoding="utf-8") as fh:
        reg = json.load(fh)

    ok = 0
    for eid, edef in reg.get("experts", {}).items():
        src = os.path.join(SKILL_ROOT, edef["dir"])
        if not os.path.isdir(src):
            print("  [warn] 专家目录缺失: %s" % src, file=sys.stderr)
            continue
        dst = os.path.join(target, edef["skill_name"])
        st = link_or_copy(src, dst, args.mode, args.force)
        print("  [expert] %s -> %s  (%s)" % (edef["skill_name"], dst, st))
        ok += 1

    print("\n部署完成：1 个中枢 + %d 个专家。" % ok)
    print("重启 WorkBuddy 后，中枢(laya-decision)与各专家(expert-*)将被识别。")
    print("中枢 SKILL.md 描述「专家团编排协议」；各专家可被单独触发，也可由中枢编排。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
