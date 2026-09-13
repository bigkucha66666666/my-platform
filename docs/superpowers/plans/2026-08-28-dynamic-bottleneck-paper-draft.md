# Dynamic Bottleneck Paper Draft Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a Chinese LaTeX thesis-style preliminary manuscript for the 20+40 dynamic bottleneck Human-Agent experiment without inventing empirical results.

**Architecture:** Keep the manuscript in one compilable `main.tex` for convenient early revision, with verified metadata in a separate BibTeX database. A short README records the compiler and the unresolved design decisions that must be fixed before preregistration.

**Tech Stack:** XeLaTeX/ctexbook, BibTeX, natbib, amsmath, booktabs, longtable.

---

### Task 1: Create the reference database

**Files:**
- Create: `papers/dynamic_bottleneck_human_agent/references.bib`

- [ ] Add verified entries for Liu et al. (2023), Tian et al. (2022), Vickrey (1969), Small (1982), Arnott et al. (1990), and Arnott et al. (1999).
- [ ] Check every DOI and bibliographic field against the supplied PDFs.

### Task 2: Draft the data-independent manuscript

**Files:**
- Create: `papers/dynamic_bottleneck_human_agent/main.tex`

- [ ] Add the Chinese thesis preamble, title page, provisional abstract, table of contents, equations, tables, and bibliography configuration.
- [ ] Draft the introduction and literature review with explicit research gaps and contributions.
- [ ] Draft the bottleneck model, temporal-learning mechanism, welfare mechanism, and falsifiable hypotheses.
- [ ] Draft the 20+40 experiment, Human-Agent information boundary, Agent reproducibility requirements, variables, and analysis strategy.
- [ ] Add a results-shell chapter that contains no fabricated numbers and a discussion chapter that uses conditional language.

### Task 3: Add operating notes

**Files:**
- Create: `papers/dynamic_bottleneck_human_agent/README.md`

- [ ] Record the XeLaTeX/BibTeX compilation commands.
- [ ] Record the design choices that must be fixed before data collection.

### Task 4: Validate the artifact

**Files:**
- Inspect: `papers/dynamic_bottleneck_human_agent/main.tex`
- Inspect: `papers/dynamic_bottleneck_human_agent/references.bib`

- [ ] Parse the LaTeX with Pandoc to catch structural syntax errors.
- [ ] Verify that all citation keys used in `main.tex` exist in `references.bib`.
- [ ] Search for unsupported empirical claims, fake p-values, and inconsistent 30+30 round descriptions.
- [ ] Review the Git diff to ensure no existing experiment code is modified.
