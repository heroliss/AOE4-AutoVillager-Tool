"""
网页编辑器宿主的无头测试：节点定义导出、Graph<->载荷 往返一致、参数类型回解析。
不需要 pywebview/界面（只测可单元测试的转换核心）。
运行：python check_web.py
"""
from __future__ import annotations

from flow.core import registry
import flow.nodes  # noqa: F401
from flow.editor.webhost import node_defs, graph_to_payload, payload_to_graph
from flow.flows.villager import build_villager_graph
from flow.flows.jin import build_jin_graph
from flow.flows.trade_cart import build_trade_cart_graph


def check(name, cond):
    print(("  ok  " if cond else "  FAIL") + f"  {name}")
    return cond


def edge_set(g):
    ex = {(e.src_id, e.src_port, e.dst_id, e.dst_port) for e in g.exec_edges}
    da = {(e.src_id, e.src_port, e.dst_id, e.dst_port) for e in g.data_edges}
    return ex, da


def main():
    ok = True

    defs = node_defs()
    ok &= check(f"导出 {len(defs)} 个节点定义 (==注册数 {len(registry())})", len(defs) == len(registry()))
    sample = next(d for d in defs if d["type"] == "sense.template_match")
    ok &= check("模板匹配定义含 输入/输出/参数",
                sample["inputs"] and sample["outputs"] and any(p["key"] == "templates" for p in sample["params"]))
    ok &= check("端口带 kind(exec/data)",
                all(p["kind"] in ("exec", "data") for d in defs for p in d["inputs"] + d["outputs"]))

    for name, build in (("出农", build_villager_graph), ("出商队", build_trade_cart_graph), ("金朝", build_jin_graph)):
        g = build()
        payload = graph_to_payload(g)
        g2 = payload_to_graph(payload)
        ok &= check(f"{name}: 节点数往返一致 ({len(g.nodes)})", len(g2.nodes) == len(g.nodes))
        ex1, da1 = edge_set(g)
        ex2, da2 = edge_set(g2)
        ok &= check(f"{name}: 执行连线往返一致 ({len(ex1)})", ex1 == ex2)
        ok &= check(f"{name}: 数据连线往返一致 ({len(da1)})", da1 == da2)
        # 参数类型保真：region 仍是列表、float 仍是数值、bool 仍是布尔
        v = g.nodes["vill"].values if "vill" in g.nodes else None
        if v is not None:
            v2 = g2.nodes["vill"].values
            ok &= check(f"{name}: 区域参数仍为列表", isinstance(v2["region"], list) and v2["region"] == v["region"])
            ok &= check(f"{name}: 阈值参数仍为数值", abs(float(v2["threshold"]) - float(v["threshold"])) < 1e-9)
            ok &= check(f"{name}: 多模板参数仍为列表", isinstance(v2["templates"], list) and v2["templates"] == v["templates"])

    print("\n" + ("网页宿主转换全部通过 OK" if ok else "存在失败 FAIL"))
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
