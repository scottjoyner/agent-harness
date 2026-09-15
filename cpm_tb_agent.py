#!/usr/bin/env python3
"""CPM TB bridge agent: drives official terminal-bench tasks with local MiniCPM5 :1235.
Usage: tb runs create ... --agent-import-path cpm_tb_agent:CpmTbAgent -k endpoint=http://localhost:1235 -k max_steps=15
Requires PYTHONPATH to include this file's directory."""

import json
import re
import time
import urllib.request
from pathlib import Path

from terminal_bench.agents.base_agent import AgentResult, BaseAgent

BASH_SYS = """You are a tool-calling assistant working inside a Docker container. Execute tasks using bash.
RULES:
1. Output ONLY the function call, no thinking
2. Format: <function name="bash"><param name="command">COMMAND</param></function>
3. Never explain, just execute
4. Chain with &&, write files with cat << 'EOF'
5. Working directory persists between steps
Example:
<function name="bash"><param name="command">echo hello</param></function>"""


class CpmTbAgent(BaseAgent):
    @staticmethod
    def name() -> str:
        return "cpm-local"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._endpoint = kwargs.get("endpoint", "http://localhost:1235").rstrip("/")
        self._max_steps = int(kwargs.get("max_steps", 15))
        self._in_tokens = 0
        self._out_tokens = 0

    def _call(self, messages, max_tokens=400):
        body = {"model": "cpm", "messages": messages,
                "max_tokens": max_tokens, "temperature": 0.1}
        req = urllib.request.Request(
            f"{self._endpoint}/v1/chat/completions",
            data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        try:
            r = json.loads(urllib.request.urlopen(req, timeout=150).read())
            m = r["choices"][0]["message"]
            content = m.get("content", "") or ""
            reason = m.get("reasoning_content", "") or ""
            text = content if "<function" in content else reason
            self._in_tokens += r["usage"].get("prompt_tokens", 0)
            self._out_tokens += r["usage"].get("completion_tokens", 0)
            return text
        except Exception as e:
            return f"Error: {e}"

    def _extract(self, text):
        if "<function" not in text:
            return None
        m = re.search(r'<param\s+name="command">(.*?)</param>', text, re.DOTALL)
        if not m:
            return None
        cmd = m.group(1).strip()
        if cmd.startswith("<![CDATA["):
            cmd = cmd[9:]
        if cmd.endswith("]]>"):
            cmd = cmd[:-3]
        return cmd.strip() or None

    def perform_task(self, instruction, session, logging_dir=None):
        self._in_tokens = 0
        self._out_tokens = 0
        base = [{"role": "system", "content": BASH_SYS},
                {"role": "user", "content": instruction}]
        fb = ""
        for _ in range(self._max_steps):
            msgs = base + ([{"role": "user", "content": fb}] if fb else [])
            text = self._call(msgs)
            cmd = self._extract(text)
            if not cmd:
                fb = "No tool call found. Output ONE <function name=\"bash\"> tool call now."
                continue
            try:
                session.send_keys([cmd, "Enter"], block=True, max_timeout_sec=120)
                time.sleep(1)
                out = session.capture_pane() if hasattr(session, "capture_pane") else ""
            except Exception as e:
                out = f"exec error: {e}"
            out = str(out)[-1500:]
            fb = f"Output:\n{out}\nContinue with the next tool call, or output DONE if the task is complete."
            if text.strip().endswith("DONE") and not cmd:
                break
        return AgentResult(total_input_tokens=self._in_tokens,
                           total_output_tokens=self._out_tokens)
