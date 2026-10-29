"""Harbor 0.25.0 Pi adapter with an extra local tool-recovery safety hook.

Uses only public Pi extension events; does not inspect tasks, judge rewards,
or change Harbor verifier behavior. Pin Harbor and Pi before upgrading this.
"""
from __future__ import annotations

from harbor.agents.installed.pi import Pi


_GUARD_HOOK = r'''
  // One trial, one state. Never persist task content or tool arguments.
  let lastFailedSignature: string | null = null;
  const pending = new Map<string, string>();
  const knownDirectories = new Set<string>();
  pi.on("agent_start", () => {
    lastFailedSignature = null;
    pending.clear();
    knownDirectories.clear();
  });
  pi.on("tool_call", (event: any) => {
    if (!["bash", "read", "write", "edit"].includes(event.toolName)) return;
    const signature = event.toolName + ":" + JSON.stringify(event.input ?? {});
    if (signature === lastFailedSignature) {
      return { block: true, reason: "The identical tool call just failed. Read its error, inspect usage/help or the relevant files, and try corrected arguments. Do not blindly retry." };
    }
    pending.set(event.toolCallId, signature);
    if (event.toolName === "bash") {
      const command = String(event.input?.command ?? "").trim();
      // In a noninteractive agent loop these commands read EOF or truncate files.
      // Require a heredoc, pipe, or the native write tool instead.
      if (command === "cat" || /^cat\s*>\s*[^\s<>|;&]+\s*$/.test(command)) {
        lastFailedSignature = signature;
        return { block: true, reason: "A bare cat or cat > file has no input in this noninteractive shell and can create empty files. Use the write tool or a complete heredoc with content. Never retry this command unchanged." };
      }
    }
    if (event.toolName === "read") {
      const path = String(event.input?.path ?? "");
      if (path.endsWith("/") || knownDirectories.has(path)) {
        lastFailedSignature = signature;
        return { block: true, reason: "Cannot read a directory with the read tool. Use bash: ls -la <directory> or find to inspect filenames." };
      }
    }
  });
  pi.on("tool_result", (event: any) => {
    const signature = pending.get(event.toolCallId);
    if (!signature) return;
    pending.delete(event.toolCallId);
    if (event.isError) {
      lastFailedSignature = signature;
      if (event.toolName === "read") {
        const text = (event.content ?? []).map((part: any) => String(part.text ?? "")).join("\n");
        const path = String(event.input?.path ?? "");
        if (text.includes("EISDIR") && path) knownDirectories.add(path);
      }
    } else lastFailedSignature = null;
  });
'''


class GuardedPi(Pi):
    """Drop-in Pi installed agent for diagnostic benchmark runs."""

    @staticmethod
    def _build_max_turns_extension(max_turns: int) -> str:
        source = Pi._build_max_turns_extension(max_turns)
        if not source.rstrip().endswith("}"):
            raise RuntimeError("Unexpected Harbor Pi extension API shape")
        closing = source.rfind("}")
        return source[:closing] + _GUARD_HOOK + source[closing:]
