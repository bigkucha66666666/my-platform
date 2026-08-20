# Dynamic LLM Limited Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one-call, per-Agent, bounded external memory to DeepSeek participants in `dynamic_bottleneck_round` without exposing non-public experiment data.

**Architecture:** Extend the existing DeepSeek request/response contract with a short `limited_memory` input and `memory_summary` output. Persist only the latest valid summary per API Agent in a dedicated `participant.vars` store, and retain the old summary whenever memory is disabled, missing, invalid, or the API/fallback path fails.

**Tech Stack:** Python 3, oTree, `participant.vars`, `unittest`, existing DeepSeek Chat Completions adapter.

---

### Task 1: Define and test the bounded API protocol

**Files:**
- Modify: `dynamic_bottleneck_round/agents/deepseek_agent.py`
- Test: `dynamic_bottleneck_round/agents/test_dynamic_agent_integration.py`

- [ ] Add failing tests proving the prompt contains only the supplied prior memory, valid JSON returns a normalized `memory_summary`, overlong output is truncated, and old-format JSON remains accepted with no memory update.
- [ ] Run the targeted test module and verify failures are caused by the absent protocol fields.
- [ ] Add `limited_memory` to `AgentChoiceSet`, memory settings to `DeepSeekAgentConfig`, and optional `memory_summary` to `AgentChoice`.
- [ ] Normalize control characters and cap output at `api_agent_limited_memory_max_chars`; never reject an otherwise valid departure choice solely because memory is missing or malformed.
- [ ] Update the system prompt to request a concise subjective summary based only on supplied public context.
- [ ] Run targeted tests to green.

### Task 2: Add isolated persistent memory

**Files:**
- Modify: `dynamic_bottleneck_round/__init__.py`
- Test: `dynamic_bottleneck_round/agents/test_dynamic_agent_integration.py`

- [ ] Add failing tests for empty first-round memory, per-Agent isolation, disabled memory, valid replacement, and preservation after fallback or invalid output.
- [ ] Run tests and verify the intended failures.
- [ ] Add `API_AGENT_MEMORY_PARTICIPANT_VAR`, configuration helpers, read/write helpers, and memory injection in `api_agent_choice_set_for_group()`.
- [ ] Save valid output after Agent decision records are persisted; include input/output memory in each record for audit.
- [ ] Ensure missing previous public feedback yields no personal result or memory-derived backend reconstruction.
- [ ] Run targeted tests to green.

### Task 3: Configure and export the feature

**Files:**
- Modify: `settings.py`
- Modify: `dynamic_bottleneck_round/__init__.py`
- Modify: `dynamic_bottleneck_round/admin_report.html`
- Test: `dynamic_bottleneck_round/agents/test_dynamic_agent_integration.py`

- [ ] Add failing assertions for default config and exported memory audit fields.
- [ ] Add `api_agent_limited_memory_enabled=1` and `api_agent_limited_memory_max_chars=400` to dynamic demo/prod configuration bases.
- [ ] Add memory input/output fields to virtual Agent export rows and an enabled/max-length summary to the admin report.
- [ ] Run targeted tests to green.

### Task 4: Regression verification

**Files:**
- Verify: `dynamic_bottleneck_round/`
- Verify: `single_bottleneck/`

- [ ] Run `git diff --check` and Python compilation.
- [ ] Run all dynamic Agent unit tests.
- [ ] Run the combined dynamic oTree bot with API fallback and independent RL enabled.
- [ ] Run the original `single_bottleneck_demo` bot to prove isolation.
- [ ] Review the final diff for public-information parity, current-round leakage, and accidental changes outside the dynamic app/config/report scope.
