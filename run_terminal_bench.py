#!/usr/bin/env python3
"""
Terminal Bench Benchmark Runner
===============================

Run Terminal Bench-style tasks and measure performance.
"""

import json
import time
import os
from datetime import datetime
from typing import List, Dict
from terminal_bench_harness import TerminalBenchHarness, TERMINAL_BENCH_TASKS

class BenchmarkRunner:
    def __init__(self, harness: TerminalBenchHarness):
        self.harness = harness
        self.results = []
    
    def run_benchmark(self, tasks: List[Dict] = None) -> Dict:
        """Run a set of tasks and collect results."""
        if tasks is None:
            tasks = TERMINAL_BENCH_TASKS
        
        print(f"\n{'='*70}")
        print(f"TERMINAL BENCH BENCHMARK")
        print(f"{'='*70}")
        print(f"Tasks: {len(tasks)}")
        print(f"Start: {datetime.now().isoformat()}")
        print(f"{'='*70}\n")
        
        start_time = time.time()
        
        for i, task in enumerate(tasks):
            print(f"\n[{i+1}/{len(tasks)}] Running {task['id']}...")
            
            result = self.harness.run(task["id"], task["instruction"])
            self.results.append(result)
            
            # Brief pause between tasks
            time.sleep(1)
        
        total_time = time.time() - start_time
        
        # Calculate summary
        passed = sum(1 for r in self.results if r.success)
        total = len(self.results)
        avg_tokens = sum(r.total_tokens for r in self.results) / total if total > 0 else 0
        avg_time = sum(r.total_time for r in self.results) / total if total > 0 else 0
        
        summary = {
            "timestamp": datetime.now().isoformat(),
            "total_tasks": total,
            "passed": passed,
            "success_rate": f"{(passed/total*100):.1f}%",
            "avg_tokens": f"{avg_tokens:.0f}",
            "avg_time": f"{avg_time:.1f}s",
            "total_time": f"{total_time:.1f}s",
            "tasks": []
        }
        
        for result in self.results:
            summary["tasks"].append({
                "id": result.task_id,
                "success": result.success,
                "tokens": result.total_tokens,
                "time": f"{result.total_time:.1f}s"
            })
        
        print(f"\n{'='*70}")
        print(f"BENCHMARK SUMMARY")
        print(f"{'='*70}")
        print(f"Passed: {passed}/{total} ({(passed/total*100):.1f}%)")
        print(f"Avg tokens: {avg_tokens:.0f}")
        print(f"Avg time: {avg_time:.1f}s")
        print(f"Total time: {total_time:.1f}s")
        print(f"{'='*70}\n")
        
        return summary

def main():
    """Run the benchmark."""
    import sys
    
    # Check if models are running
    try:
        import urllib.request
        urllib.request.urlopen("http://localhost:1235/v1/models", timeout=5)
        urllib.request.urlopen("http://localhost:1234/v1/models", timeout=5)
        print("Models are running.")
    except:
        print("ERROR: Models not running. Start MiniCPM5 LoRA (1235) and VibeThinker (1234)")
        sys.exit(1)
    
    # Create harness and runner
    harness = TerminalBenchHarness(executor_port=1235, reasoner_port=1234)
    runner = BenchmarkRunner(harness)
    
    # Run benchmark
    summary = runner.run_benchmark()
    
    # Save results
    filename = f"terminal_bench_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(filename, 'w') as f:
        json.dump(summary, f, indent=2)
    
    print(f"Results saved to {filename}")
    
    # Compare with SOTA
    print(f"\n{'='*70}")
    print(f"COMPARISON WITH SOTA (Terminal-Bench 2.0)")
    print(f"{'='*70}")
    print(f"Your score: {summary['success_rate']}")
    print(f"SOTA (GPT-5.5): 82%")
    print(f"Small models: ~15%")
    print(f"{'='*70}")

if __name__ == "__main__":
    main()
