#!/usr/bin/env python3
"""
Model Router
===========

Routes requests to the appropriate model based on task type.

Routing logic:
- Tool calling / execution -> MiniCPM5 LoRA
- Planning / reasoning -> VibeThinker
- Complex tasks -> Both (synergistic mode)
"""

import json
import urllib.request
from typing import Dict, List

VIBETHINKER = "http://localhost:1234"
MINICPM5 = "http://localhost:1235"

# Keywords that indicate tool-calling tasks
TOOL_KEYWORDS = [
    "list", "read", "write", "create", "delete", "run", "execute",
    "find", "search", "check", "show", "get", "set", "install",
    "copy", "move", "rename", "chmod", "grep", "cat", "ls"
]

# Keywords that indicate reasoning tasks
REASON_KEYWORDS = [
    "explain", "why", "how", "analyze", "compare", "evaluate",
    "plan", "design", "architect", "strategy", "approach",
    "debug", "troubleshoot", "diagnose"
]

def classify_task(task: str) -> str:
    """Classify a task as 'tool', 'reason', or 'both'."""
    task_lower = task.lower()
    
    has_tool = any(kw in task_lower for kw in TOOL_KEYWORDS)
    has_reason = any(kw in task_lower for kw in REASON_KEYWORDS)
    
    if has_tool and has_reason:
        return "both"
    elif has_tool:
        return "tool"
    elif has_reason:
        return "reason"
    else:
        return "tool"  # Default to tool execution

def route(task: str) -> Dict:
    """Route a task to the appropriate model(s)."""
    task_type = classify_task(task)
    
    return {
        "task": task,
        "type": task_type,
        "model": {
            "tool": "MiniCPM5-LoRA",
            "reason": "VibeThinker",
            "both": "Both (synergistic)"
        }[task_type],
        "endpoint": {
            "tool": MINICPM5,
            "reason": VIBETHINKER,
            "both": f"{VIBETHINKER} + {MINICPM5}"
        }[task_type]
    }

def health_check() -> Dict:
    """Check health of all model endpoints."""
    endpoints = {
        "VibeThinker": VIBETHINKER,
        "MiniCPM5": MINICPM5
    }
    
    status = {}
    for name, url in endpoints.items():
        try:
            urllib.request.urlopen(f"{url}/v1/models", timeout=5)
            status[name] = "ONLINE"
        except:
            status[name] = "OFFLINE"
    
    return status

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "health":
        print("Health Check:")
        for name, status in health_check().items():
            print(f"  {name}: {status}")
    elif len(sys.argv) > 1:
        task = " ".join(sys.argv[1:])
        result = route(task)
        print(f"Task: {result['task']}")
        print(f"Type: {result['type']}")
        print(f"Model: {result['model']}")
        print(f"Endpoint: {result['endpoint']}")
    else:
        # Demo routing
        tasks = [
            "List all Python files",
            "Explain how the system works",
            "Analyze and fix the bug",
            "Check disk usage",
            "Plan the architecture"
        ]
        
        print("Routing Demo:")
        for task in tasks:
            result = route(task)
            print(f"\n  '{task}'")
            print(f"    -> {result['type']} ({result['model']})")
