# Uniform-Capacity Paper Pre-Data Revision Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite the Chinese LaTeX manuscript through Chapter 5 around a uniform stochastic bottleneck service rate and the H-I0/H-I1/HA-I0/HA-I1 experiment, while leaving data-dependent chapters as explicit reporting shells.

**Architecture:** Keep the current single-file `ctexbook` manuscript and BibTeX structure. Replace the obsolete Random-to-Markov and accident-risk argument with one consistent claim-evidence chain: stochastic service rate creates environmental uncertainty, ex-ante capacity information changes behavior, and Human-Agent participation can alter the aggregate welfare effect through strategic interaction and synchronized response.

**Tech Stack:** XeLaTeX, BibTeX, `ctexbook`, `natbib`, `amsmath`, `booktabs`, local reference PDFs, Poppler/PDF rendering tools where available.

---

### Task 1: Audit Sources and Bibliography

**Files:**
- Modify: `papers/dynamic_bottleneck_human_agent/references.bib`
- Reference: `papers/dynamic_bottleneck_human_agent/main.tex`
- Reference: `/Users/hybsmac/Desktop/相关文献/动态瓶颈与Human-Agent/`

- [ ] **Step 1: Inventory current citation keys**

Run:

```bash
rg -o '\\cite[a-z]*\{[^}]+\}' papers/dynamic_bottleneck_human_agent/main.tex
```

Expected: current Vickrey, Arnott, Liu, Tian, and related keys are visible; obsolete claims can be mapped to their citations.

- [ ] **Step 2: Verify added sources against primary metadata**

Verify author order, title, year, venue, volume, pages/article number, and DOI for stochastic-capacity reality, exact/partial pre-trip information, and the Liu experiment. Do not add a record when any core field remains uncertain.

- [ ] **Step 3: Add complete BibTeX records**

Add only records actually cited in the revised manuscript. Preserve existing keys when their metadata is correct.

- [ ] **Step 4: Check bibliography syntax**

Run:

```bash
python - <<'PY'
from pathlib import Path
text = Path('papers/dynamic_bottleneck_human_agent/references.bib').read_text()
assert text.count('{') == text.count('}')
assert '@article{liu2023departure' in text
print('bibliography braces and core Liu entry: OK')
PY
```

Expected: `bibliography braces and core Liu entry: OK`.

### Task 2: Rewrite Title, Abstract, and Introduction

**Files:**
- Modify: `papers/dynamic_bottleneck_human_agent/main.tex:28-99`

- [ ] **Step 1: Replace the title and pre-data abstract**

The title must name stochastic bottleneck service rate, pre-trip information, and Human-Agent participation. The abstract must state the research problem, 2 x 2 design, uniform distribution, planned outcomes, and pre-data status without claiming findings.

- [ ] **Step 2: Rebuild Chapter 1 argument flow**

Use the sequence: commute bottleneck problem; stochastic effective service rate; information value under congestion externalities; entry of LLM/RL actors; research questions; experiment; contributions; paper organization.

- [ ] **Step 3: Remove obsolete framing**

Run:

```bash
rg -n 'Markov|Random阶段|前20轮|后40轮|状态持续|转移矩阵' papers/dynamic_bottleneck_human_agent/main.tex
```

Expected after the full rewrite: no matches describing the current design.

### Task 3: Rewrite the Literature Review

**Files:**
- Modify: `papers/dynamic_bottleneck_human_agent/main.tex` Chapter 2
- Modify: `papers/dynamic_bottleneck_human_agent/references.bib`

- [ ] **Step 1: Synthesize deterministic and stochastic bottleneck research**

Connect Vickrey and Arnott to stochastic service-rate work. Distinguish empirical evidence that capacity is variable from the experimental choice to parameterize service rate with Liu's uniform distribution.

- [ ] **Step 2: Synthesize pre-trip information research**

Explain zero-information/distribution-only and current-capacity/full-information regimes. State that individual information value need not equal system welfare value because all commuters respond strategically.

- [ ] **Step 3: Synthesize experimental and Agent research**

Use Liu for repeated stochastic-capacity choice and RL behavioral modeling, and Tian for interactive traffic experiments and virtual participants. Avoid claiming that prior work has already tested the proposed Human/LLM/RL factorial design.

- [ ] **Step 4: End with a narrow research gap**

The gap must identify three separately estimable objects: information effect, mixed-actor-composition effect, and their interaction under the same stochastic capacity sequence.

### Task 4: Rewrite the Model and Hypotheses

**Files:**
- Modify: `papers/dynamic_bottleneck_human_agent/main.tex` Chapter 3

- [ ] **Step 1: Define the discrete point-queue model**

Define the action set, group-round flow, queue recursion, waiting time, workplace arrival, early/late schedule delay, and total cost. Keep the implemented batch rule explicit where it differs from a continuous-flow approximation.

- [ ] **Step 2: Define stochastic service rate and information sets**

Use:

```latex
\begin{equation}
  \mu_r \sim U(1.33,4.00).
\end{equation}
```

Define I0 as knowledge of the distribution and past public outcomes, and I1 as I0 plus the current round's announced service rate before departure choice.

- [ ] **Step 3: Define welfare and concentration**

Include system total/average cost, total queue delay, and normalized HHI. Explain why a common signal can improve state-contingent choice yet increase concentration.

- [ ] **Step 4: State testable hypotheses and research questions**

Use directional hypotheses only for information responsiveness. Treat the sign of welfare and Agent interaction effects as empirical questions unless the theoretical mechanism gives an unambiguous sign.

### Task 5: Rewrite the Experimental Design

**Files:**
- Modify: `papers/dynamic_bottleneck_human_agent/main.tex` Chapter 4

- [ ] **Step 1: Specify the factorial design and experimental unit**

Write the four cells H-I0, H-I1, HA-I0, and HA-I1, with 30 total actors in each system and 10/10/10 composition in HA. State that the independent treatment unit is the session/group traffic system.

- [ ] **Step 2: Specify timing and information parity**

Write the exact per-round sequence: public information; departure choice; lock choices; apply matched service rate; settle one common bottleneck; reveal results. Human, LLM, and RL must receive the same authorized fields within an information condition.

- [ ] **Step 3: Specify capacity sequences and rounds**

Document 5 practice plus 30 formal rounds, frozen matched sequences, two-decimal official service rates, and randomized equal-probability stratification for finite-round coverage. Describe this as randomized stratification rather than strict i.i.d. if used.

- [ ] **Step 4: Specify Agent boundaries and implementation status**

Describe LLM and Liu-REL RL mechanisms at the level required for replication, but label details that remain subject to final code freeze. Do not imply that LLM and RL type effects are separately randomized when both appear jointly in HA.

- [ ] **Step 5: Specify recruitment, payment, Pilot, ethics, and manipulation checks**

State approved values and procedures. When the value is not yet approved, write a `\draftnote{}` describing the decision that must be frozen before preregistration rather than inventing a number.

### Task 6: Rewrite Variables and the Pre-Analysis Plan

**Files:**
- Modify: `papers/dynamic_bottleneck_human_agent/main.tex` Chapter 5

- [ ] **Step 1: Define analysis levels and outcomes**

Separate actor-round, human-round, group-round, and session-level data. Pre-specify one primary individual outcome family and one primary system outcome family.

- [ ] **Step 2: Specify the 2 x 2 estimands**

Model information, HA composition, and their interaction. Add current service-rate interactions to measure state-contingent responsiveness.

- [ ] **Step 3: Align inference with assignment**

Use session/group-level clustering or randomization inference. State that repeated rounds are not independent experimental units and that matched capacity sequence is a block/fixed effect.

- [ ] **Step 4: Pre-specify exclusions and robustness checks**

Cover dropout, timeout, LLM API fallback, RL fallback, incomplete sessions, sequence effects, learning trends, alternative concentration measures, and multiple-testing control.

### Task 7: Replace Data-Dependent Chapters with Reporting Shells

**Files:**
- Modify: `papers/dynamic_bottleneck_human_agent/main.tex` Chapters 6-8

- [ ] **Step 1: Build the result-reporting order**

Order results as implementation and sample; manipulation checks; information effect; HA effect; interaction; individual/system separation; mechanism and robustness.

- [ ] **Step 2: Add table and figure shells**

List each required table and figure with its estimand and unit of analysis. Do not insert fabricated cells, coefficients, standard errors, sample sizes, or p-values.

- [ ] **Step 3: Make discussion conditional**

Use explicit branches for welfare improvement, null effect, or deterioration. Discuss external validity, exact-information upper-bound interpretation, uniform-distribution limitation, and inability to causally separate LLM from RL within HA.

- [ ] **Step 4: Leave a conclusion completion template**

Require the final conclusion to report sample, primary estimates with uncertainty, mechanism evidence, limitations, and policy implication after data collection.

### Task 8: Citation, Compile, and Visual QA

**Files:**
- Verify: `papers/dynamic_bottleneck_human_agent/main.tex`
- Verify: `papers/dynamic_bottleneck_human_agent/references.bib`
- Generate: `papers/dynamic_bottleneck_human_agent/main.pdf`

- [ ] **Step 1: Check obsolete concepts and citation-key coverage**

Run:

```bash
rg -n 'Markov|Random阶段|事故概率|Beta损失|转移矩阵' papers/dynamic_bottleneck_human_agent/main.tex
```

Expected: no matches except an explicit statement that the revised study does not use those mechanisms, if such a contrast is retained.

- [ ] **Step 2: Compile through the complete citation cycle**

Run from the paper directory:

```bash
xelatex -interaction=nonstopmode -halt-on-error main.tex
bibtex main
xelatex -interaction=nonstopmode -halt-on-error main.tex
xelatex -interaction=nonstopmode -halt-on-error main.tex
```

Expected: exit code 0 for all commands; no undefined citations or references.

- [ ] **Step 3: Audit the log**

Run:

```bash
rg -n 'Undefined|Citation.*undefined|Reference.*undefined|Overfull|Underfull|LaTeX Error' main.log
```

Expected: no undefined citations/references or LaTeX errors. Review any remaining box warnings and fix materially visible overflow.

- [ ] **Step 4: Render and inspect representative pages**

Render the PDF to PNG and inspect the title/abstract, table of contents, first page of each rewritten chapter, treatment table, long equations, result-shell table, and bibliography. Confirm that Chinese glyphs, equations, tables, headings, page numbers, and citation text are legible and do not overlap.

- [ ] **Step 5: Report changes without claiming results**

The final handoff must identify revised files, compiled PDF, major scope choices, remaining data-dependent notes, and any unresolved design values that must be frozen before formal data collection.
