"""摘要一份 claude -p --output-format stream-json 紀錄：工具呼叫、被拒絕的指令、最終回覆。"""
import json
import sys

sys.stdout.reconfigure(encoding="utf-8")
path = sys.argv[1]
calls, results, init, final = {}, {}, None, None
order = []
for line in open(path, encoding="utf-8", errors="replace"):
    line = line.strip()
    if not line.startswith("{"):
        continue
    try:
        m = json.loads(line)
    except json.JSONDecodeError:
        continue
    t = m.get("type")
    if t == "system" and m.get("subtype") == "init":
        init = m
    elif t == "assistant":
        for c in m.get("message", {}).get("content", []):
            if c.get("type") == "tool_use":
                calls[c["id"]] = c
                order.append(c["id"])
    elif t == "user":
        content = m.get("message", {}).get("content", [])
        if isinstance(content, list):
            for c in content:
                if c.get("type") == "tool_result":
                    txt = c.get("content")
                    if isinstance(txt, list):
                        txt = " ".join(x.get("text", "") for x in txt if isinstance(x, dict))
                    results[c["tool_use_id"]] = (bool(c.get("is_error")), str(txt))
    elif t == "result":
        final = m

if init:
    print(f"session={init.get('session_id')} model={init.get('model')} cwd={init.get('cwd')} "
          f"permissionMode={init.get('permissionMode')}")
print(f"\n== 工具呼叫 {len(order)} 次 ==")
for i, cid in enumerate(order, 1):
    c = calls[cid]
    inp = c.get("input", {})
    desc = inp.get("command") or inp.get("file_path") or inp.get("pattern") or inp.get("url") or json.dumps(inp, ensure_ascii=False)
    err, txt = results.get(cid, (None, ""))
    flag = "ERR" if err else "ok "
    if err and ("permission" in txt.lower() or "denied" in txt.lower() or "approval" in txt.lower()):
        flag = "DENY"
    print(f"{i:3d} [{flag}] {c['name']}: {str(desc)[:220]!s}".replace("\n", "⏎"))
    if err:
        print(f"        → {txt[:200]}".replace("\n", "⏎"))
if final:
    print(f"\n== 結果 ==\nsubtype={final.get('subtype')} turns={final.get('num_turns')} "
          f"duration={final.get('duration_ms', 0) / 1000:.0f}s cost=${final.get('total_cost_usd', 0):.2f}")
    den = final.get("permission_denials") or []
    print(f"permission_denials={len(den)}")
    for d in den:
        print("   DENIED:", d.get("tool_name"), json.dumps(d.get("tool_input"), ensure_ascii=False)[:250])
    print("\n" + str(final.get("result", ""))[:6000])
