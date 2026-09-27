# Liu-REL Independent-Market Pretraining Implementation Plan

> **For agentic workers:** Follow test-driven development task-by-task. This task runs inline because no subagent execution was requested. Do not commit; the user manages version control.

**Goal:** Build a frozen, validated offline experience bank for matched HA-I0/HA-I1 RL seats without using formal capacity sequences.

**Architecture:** A pure simulator generates independent 30-actor market observations and a holdout report. Liu-REL state keeps pretraining separate from formal observations; the oTree integration loads the checked bank by RL seat index before decisions.

**Tech Stack:** Python 3, unittest, oTree, JSON/SHA-256.

---

### Task 1: Define state and decision behavior

**Files:** Modify `dynamic_bottleneck_round/agents/liu_rel_agent.py`; test `dynamic_bottleneck_round/agents/test_liu_rel_agent.py`.

- [ ] Add failing tests: pretraining observations survive validation, do not increase `rounds_observed`, and inform formal round 1; I0 probabilities are unchanged by a supplied current capacity.
- [ ] Run `python -m unittest dynamic_bottleneck_round.agents.test_liu_rel_agent -v` and confirm the new tests fail for missing behavior.
- [ ] Add a pretraining list and manifest ID to state, validate its records, combine it with formal history only during inference, and bypass forced initial uniform choices when pretraining exists.
- [ ] Rerun the same command and ensure all tests pass.

### Task 2: Generate and validate independent simulation data

**Files:** Create `dynamic_bottleneck_round/agents/liu_rel_pretraining.py`, `dynamic_bottleneck_round/agents/test_liu_rel_pretraining.py`, and frozen JSON artifact in `dynamic_bottleneck_round/agents/`.

- [ ] Add failing tests for 30-actor market queue/cost parity, different capacity draws, mixed opponent strategies, distinct train/holdout seeds, deterministic artifact generation, and matched seat loading.
- [ ] Run `python -m unittest dynamic_bottleneck_round.agents.test_liu_rel_pretraining -v` and confirm failure on missing simulator functions.
- [ ] Implement the fixed-seed market generator, 10 seat-specific training histories, 30-round holdout markets, validation summary, and integrity-checked bank reader. Do not import formal sequence-bank generation/loading.
- [ ] Generate artifact with `python -m dynamic_bottleneck_round.agents.liu_rel_pretraining --write`; run its `--verify` mode and inspect report.
- [ ] Rerun test module and confirm pass.

### Task 3: Connect frozen bank to oTree HA RL decisions

**Files:** Modify `dynamic_bottleneck_round/__init__.py`, `settings.py`, integration tests.

- [ ] Add failing integration tests: HA-I0 and HA-I1 same-index RL load identical experience; I0 is not passed current service rate; formal round 1 is pretrained; regular settlement adds only formal history; a missing/corrupt bank fails closed.
- [ ] Run the targeted integration tests and confirm expected failures.
- [ ] Initialize each missing RL state from checked artifact by index, retain states between rounds, update policy/config version, and add bank metadata to decision audit.
- [ ] Run targeted unit/integration tests and resolve regressions.

### Task 4: Final verification

**Files:** Frozen artifact and documentation only.

- [ ] Run `--verify` and the relevant full unittest suite under `/opt/anaconda3/envs/otree_env/bin/python`.
- [ ] Confirm `git diff` has no accidental changes to existing dirty files or formal S01–S05 sequence data.
- [ ] Report training/holdout market counts, validation outcomes, version/hash, and any limits. Do not commit.
