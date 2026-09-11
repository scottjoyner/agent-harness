# Agent Harness Version History

## v1.0.0 - Initial Release (2026-09-11)

### Models
- MiniCPM5-2B LoRA: 10.5 tok/s, 8/8 tool calling
- VibeThinker-3B: 5.7 tok/s, reasoning only

### Features
- Basic dual-model harness
- Simple task routing
- systemd services

### Performance
- Tool calling: 100% accuracy
- Average speed: 10.5 tok/s

---

## v1.1.0 - Terminal Bench Focus (2026-09-11)

### New Features
- **Terminal Bench Harness**: Specialized for terminal tasks
- **Precision Tool Calling**: Enhanced XML parsing with validation
- **Multi-step Planning**: VibeThinker creates plans, MiniCPM5 executes
- **Error Recovery**: Automatic retry and error handling
- **State Verification**: Final state checking against criteria

### Improvements
- Better system prompts for tool calling
- Parameter validation for tool calls
- Context tracking between steps
- Confidence scoring for tool calls

### Benchmark Suite
- 5 Terminal Bench-style tasks
- Metrics: success rate, tokens, time
- Comparison with SOTA models

### Performance
- Tool calling accuracy: Expected >80%
- Planning accuracy: TBD
- Error recovery: TBD

---

## Roadmap

### v1.2.0 (Planned)
- [ ] Multi-turn conversation support
- [ ] Conversation history sharing
- [ ] Dynamic load balancing
- [ ] Memory optimization

### v1.3.0 (Planned)
- [ ] Task-specific LoRAs
- [ ] Automatic fine-tuning from traces
- [ ] Fleet distribution
- [ ] Production error handling

### v2.0.0 (Future)
- [ ] Multi-node execution
- [ ] Self-improvement loop
- [ ] Specialized model routing
- [ ] Real-time adaptation

---

## Benchmark Results

### Terminal Bench v1.0 (2026-09-11)

| Task | Status | Tokens | Time |
|------|--------|--------|------|
| tb-001 | Pending | - | - |
| tb-002 | Pending | - | - |
| tb-003 | Pending | - | - |
| tb-004 | Pending | - | - |
| tb-005 | Pending | - | - |

### Comparison with SOTA

| Model | Score | Notes |
|-------|-------|-------|
| GPT-5.5 (Codex) | 82% | SOTA |
| Claude Opus 4.6 | 79.8% | |
| Small models | ~15% | |
| **Our harness** | **TBD** | |

---

## Testing Protocol

### Benchmark Categories
1. **File Operations**: Create, read, edit, delete
2. **System Administration**: Process management, monitoring
3. **Development**: Scripting, testing, debugging
4. **Data Processing**: CSV, JSON, text manipulation
5. **Networking**: HTTP servers, API calls

### Success Criteria
- **Pass**: Task completed, all criteria met
- **Fail**: Task incomplete or criteria not met
- **Error**: Execution error or timeout

### Metrics
- **Success Rate**: % of tasks passed
- **Token Efficiency**: Tokens per task
- **Time Efficiency**: Seconds per task
- **Error Rate**: % of tasks with errors

---

## Hardware Requirements

### Minimum
- CPU: 4 cores
- RAM: 4GB
- Storage: 10GB free

### Recommended
- CPU: 8 cores
- RAM: 8GB
- Storage: 50GB free

### Optimal
- GPU: 8GB+ VRAM
- RAM: 16GB
- Storage: 100GB SSD

---

## Known Issues

### v1.0.0
- VibeThinker doesn't output tool calls directly
- Ling3.0-tiny too slow for CPU
- iGPU doesn't support 16-bit storage

### v1.1.0
- Planning accuracy needs improvement
- Error recovery is basic
- No multi-turn support yet

---

## Contributing

### How to Improve
1. Run benchmarks
2. Identify failure points
3. Improve prompts or logic
4. Test changes
5. Submit updates

### Code Style
- Python 3.8+
- Type hints
- Docstrings
- Unit tests

### Testing
```bash
# Run quick benchmark
python3 quick_benchmark.py 1235 MiniCPM5-LoRA

# Run Terminal Bench
python3 run_terminal_bench.py

# Compare models
python3 compare_models.py
```
