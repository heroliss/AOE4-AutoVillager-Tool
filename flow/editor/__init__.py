"""flow.editor —— 节点编辑器（LiteGraph.js + pywebview）。

入口：`flow.editor.webhost.launch`。可单元测试的转换核心也在 webhost
（`node_defs` / `graph_to_payload` / `payload_to_graph`，见 check_web.py）。
采集覆盖层（框选/取点/吸色/截模板/捕获按键）在 `flow.editor.capture`。
"""
