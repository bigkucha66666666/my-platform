# Dynamic Bottleneck Post Survey Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a two-page, treatment-aware post-experiment questionnaire for the dynamic bottleneck experiment.

**Architecture:** The dynamic app records stable treatment metadata in `participant.vars`; a separate one-round survey app reads that metadata, conditionally exposes Agent questions, and stores survey answers in its own Player model. Templates use native oTree fields with scoped CSS for accessibility and validation.

**Tech Stack:** Python, oTree, HTML/CSS, unittest, oTree bots.

---

### Task 1: Tests and Treatment Metadata

- [ ] Add failing tests for H/HA classification, conditional form fields, question validation, app sequence, templates, and export headers.
- [ ] Run focused tests and confirm the new app is missing.
- [ ] Add treatment metadata assignment to `dynamic_bottleneck_round` and verify its tests.

### Task 2: Survey Model and Flow

- [ ] Create `dynamic_bottleneck_survey/__init__.py` with 7 common fields, 2 HA fields, two pages, completion page, metadata persistence, and custom export.
- [ ] Add the app to dynamic demo and production session sequences before payment.
- [ ] Add oTree bot submissions for both common pages.

### Task 3: Participant Interface

- [ ] Create restrained, responsive templates for recognition, strategy, and completion pages.
- [ ] Ensure Q1/Q2/Q7 precede questions that could cue the hidden pattern.
- [ ] Verify selection controls and mobile layout in a real browser.

### Task 4: Verification

- [ ] Run Python compilation and `git diff --check`.
- [ ] Run survey, dynamic app, Agent, and single-bottleneck regression tests.
- [ ] Run a complete dynamic demo bot flow with the new survey app.
- [ ] Capture desktop and mobile screenshots for user review.
