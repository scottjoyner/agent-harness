#!/usr/bin/env python3
"""
Agent Harness Benchmark Suite
=============================

Standardized benchmarks for testing agent harness performance.
"""

import json
import time
import urllib.request
import subprocess
import os
from typing import Dict, List, Callable
from dataclasses import dataclass, asdict
from datetime import datetime

@dataclass
class BenchmarkResult:
    name: str
    category: str
    success: bool
    tokens: int
    prompt_tokens: int
    gen_speed: float
    prompt_speed: float
    time: float
    output: str
    error: str = ""

@dataclass
class BenchmarkSuite:
    version: str
    timestamp: str
    model: str
    results: List[BenchmarkResult]
    summary: Dict

# Test categories and tasks
BENCHMARK_TASKS = {
    "tool_calling": [
        {"name": "bash_simple", "prompt": "Run 'echo hello'", "expect": "function"},
        {"name": "bash_complex", "prompt": "List all Python files and count them", "expect": "function"},
        {"name": "file_read", "prompt": "Read /etc/hostname", "expect": "function"},
        {"name": "file_write", "prompt": "Create test.txt with content 'hello'", "expect": "function"},
        {"name": "file_search", "prompt": "Find files matching *.py", "expect": "function"},
    ],
    "reasoning": [
        {"name": "explain_simple", "prompt": "Explain what a variable is in Python", "expect": "text"},
        {"name": "explain_complex", "prompt": "How does garbage collection work?", "expect": "text"},
        {"name": "compare", "prompt": "Compare lists and tuples in Python", "expect": "text"},
        {"name": "debug", "prompt": "Why might 'list.append' fail?", "expect": "text"},
        {"name": "plan", "prompt": "Plan a simple web scraper", "expect": "text"},
    ],
    "multi_step": [
        {"name": "sequence", "prompt": "First list files, then count them", "expect": "function"},
        {"name": "conditional", "prompt": "If python exists, show its version", "expect": "function"},
        {"name": "loop", "prompt": "Show disk usage 3 times", "expect": "function"},
    ],
    "edge_cases": [
        {"name": "empty", "prompt": "", "expect": "text"},
        {"name": "long", "prompt": "x" * 1000, "expect": "text"},
        {"name": "special", "prompt": "Test <script>alert(1)</script>", "expect": "text"},
        {"name": "unicode", "prompt": "Hello 世界", "expect": "text"},
    ]
}

class BenchmarkRunner:
    def __init__(self, port: int, model_name: str):
        self.port = port
        self.model_name = model_name
        self.url = f"http://localhost:{port}"
    
    def _call(self, messages: List[Dict], max_tokens: int = 300, tools: bool = False) -> Dict:
        body = {
            "model": self.model_name,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.1
        }
        if tools:
            body["tools"] = [
                {"type": "function", "function": {"name": "bash", "parameters": {"type": "object", "properties": {"command": {"type": "string"}}}}}
            ]
        
        data = json.dumps(body).encode()
        req = urllib.request.Request(f"{self.url}/v1/chat/completions", data=data, headers={"Content-Type": "application/json"})
        
        start = time.time()
        try:
            resp = urllib.request.urlopen(req, timeout=60)
            result = json.loads(resp.read())
            msg = result["choices"][0]["message"]
            return {
                "content": msg.get("content", ""),
                "reasoning": msg.get("reasoning_content", ""),
                "tool_calls": msg.get("tool_calls", []),
                "tokens": result["usage"]["completion_tokens"],
                "prompt_tokens": result["usage"]["prompt_tokens"],
                "speed": result["timings"]["predicted_per_second"],
                "prompt_speed": result["timings"]["prompt_per_second"],
                "time": time.time() - start
            }
        except Exception as e:
            return {
                "content": "",
                "reasoning": "",
                "tool_calls": [],
                "tokens": 0,
                "prompt_tokens": 0,
                "speed": 0,
                "prompt_speed": 0,
                "time": time.time() - start,
                "error": str(e)
            }
    
    def _check_expect(self, content: str, tool_calls: List, expect: str) -> bool:
        if expect == "function":
            return "<function" in content or len(tool_calls) > 0
        elif expect == "text":
            return len(content) > 10
        return True
    
    def run_task(self, task: Dict, category: str) -> BenchmarkResult:
        messages = [
            {"role": "system", "content": "You are a helpful assistant. Always use tools when needed."},
            {"role": "user", "content": task["prompt"]}
        ]
        
        result = self._call(messages, tools=(task["expect"] == "function"))
        
        success = self._check_expect(result["content"], result["tool_calls"], task["expect"])
        
        return BenchmarkResult(
            name=task["name"],
            category=category,
            success=success,
            tokens=result["tokens"],
            prompt_tokens=result["prompt_tokens"],
            gen_speed=result["speed"],
            prompt_speed=result["prompt_speed"],
            time=result["time"],
            output=result["content"][:200],
            error=result.get("error", "")
        )
    
    def run_category(self, category: str) -> List[BenchmarkResult]:
        tasks = BENCHMARK_TASKS.get(category, [])
        return [self.run_task(task, category) for task in tasks]
    
    def run_all(self) -> BenchmarkSuite:
        all_results = []
        for category in BENCHMARK_TASKS:
            print(f"Running {category}...")
            results = self.run_category(category)
            all_results.extend(results)
        
        # Calculate summary
        total = len(all_results)
        passed = sum(1 for r in all_results if r.success)
        avg_gen = sum(r.gen_speed for r in all_results) / total if total > 0 else 0
        avg_prompt = sum(r.prompt_speed for r in all_results) / total if total > 0 else 0
        avg_tokens = sum(r.tokens for r in all_results) / total if total > 0 else 0
        
        summary = {
            "total_tasks": total,
            "passed": passed,
            "success_rate": f"{(passed/total*100):.1f}%",
            "avg_gen_speed": f"{avg_gen:.1f} tok/s",
            "avg_prompt_speed": f"{avg_prompt:.1f} tok/s",
            "avg_tokens": f"{avg_tokens:.0f}",
            "categories": {}
        }
        
        for category in BENCHMARK_TASKS:
            cat_results = [r for r in all_results if r.category == category]
            cat_passed = sum(1 for r in cat_results if r.success)
            summary["categories"][category] = {
                "total": len(cat_results),
                "passed": cat_passed,
                "rate": f"{(cat_passed/len(cat_results)*100):.1f}%"
            }
        
        return BenchmarkSuite(
            version="1.0.0",
            timestamp=datetime.now().isoformat(),
            model=self.model_name,
            results=all_results,
            summary=summary
        )

def print_results(suite: BenchmarkSuite):
    print(f"\n{'='*70}")
    print(f"BENCHMARK RESULTS: {suite.model}")
    print(f"{'='*70}")
    print(f"Version: {suite.version}")
    print(f"Timestamp: {suite.timestamp}")
    print(f"\n{'='*70}")
    print("SUMMARY")
    print(f"{'='*70}")
    print(f"Total tasks: {suite.summary['total_tasks']}")
    print(f"Passed: {suite.summary['passed']}")
    print(f"Success rate: {suite.summary['success_rate']}")
    print(f"Avg generation speed: {suite.summary['avg_gen_speed']}")
    print(f"Avg prompt speed: {suite.summary['avg_prompt_speed']}")
    
    print(f"\n{'='*70}")
    print("CATEGORY BREAKDOWN")
    print(f"{'='*70}")
    for cat, stats in suite.summary['categories'].items():
        print(f"{cat:<20} {stats['passed']}/{stats['total']} ({stats['rate']})")
    
    print(f"\n{'='*70}")
    print("DETAILED RESULTS")
    print(f"{'='*70}")
    for result in suite.results:
        status = "PASS" if result.success else "FAIL"
        print(f"{result.category:<15} {result.name:<20} {status} | {result.gen_speed:.1f} tok/s | {result.tokens} tokens")
    
    print(f"\n{'='*70}")

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python3 benchmark.py <port> [model_name]")
        print("Example: python3 benchmark.py 1235 MiniCPM5-LoRA")
        sys.exit(1)
    
    port = int(sys.argv[1])
    model_name = sys.argv[2] if len(sys.argv) > 2 else f"model-{port}"
    
    runner = BenchmarkRunner(port, model_name)
    suite = runner.run_all()
    print_results(suite)
    
    # Save results
    filename = f"benchmark_{model_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(filename, 'w') as f:
        json.dump(asdict(suite), f, indent=2)
    print(f"Results saved to {filename}")
