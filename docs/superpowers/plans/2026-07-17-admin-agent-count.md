# Admin Agent Count Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a prominent, validated `1-5` Agent-per-group control to the standard oTree Session creation page for Agent-enabled single-bottleneck configurations.

**Architecture:** Reuse oTree's existing custom Session-config serialization and websocket creation path instead of adding an endpoint. A project template override presents the existing `api_agent_count_per_group` field clearly, while a server helper validates the copied Session configuration before grouping and Persona initialization.

**Tech Stack:** Python 3, oTree, oTree/Jinja admin templates, Bootstrap 5, browser JavaScript, `unittest`, Playwright

**Version-control constraint:** Do not run `git add`, `git commit`, `git push`, or create tags. Leave all changes uncommitted for the user.

---

## File Structure

- Modify `single_bottleneck/__init__.py`: add authoritative count validation and call it before round-one initialization.
- Create `single_bottleneck/agents/test_admin_agent_count.py`: focused server-validation and template-contract tests without touching the existing dirty `single_bottleneck/tests.py`.
- Create `_templates/otree/CreateSession.html`: retain the standard oTree form and add the prominent Agent-count control using the existing editable config field.

### Task 1: Server-Side Agent Count Validation

**Files:**
- Modify: `single_bottleneck/__init__.py:351-421,1640-1685`
- Create: `single_bottleneck/agents/test_admin_agent_count.py`

- [ ] **Step 1: Write failing validator tests**

Create `single_bottleneck/agents/test_admin_agent_count.py`:

```python
import unittest
from types import SimpleNamespace

import single_bottleneck as app


class AdminAgentCountValidationTests(unittest.TestCase):
    def make_session(self, mode, count):
        return SimpleNamespace(
            config={
                "api_agent_mode": mode,
                "api_agent_count_per_group": count,
            }
        )

    def test_active_mode_accepts_boundary_values(self):
        for count in (1, 5):
            with self.subTest(count=count):
                self.assertEqual(
                    app.validate_api_agent_count(self.make_session("active", count)),
                    count,
                )

    def test_shadow_mode_uses_same_validation(self):
        self.assertEqual(
            app.validate_api_agent_count(self.make_session("shadow", 3)),
            3,
        )

    def test_active_mode_rejects_invalid_values(self):
        for count in (0, -1, 6, "2.5", "invalid"):
            with self.subTest(count=count):
                with self.assertRaisesRegex(ValueError, "1 到 5"):
                    app.validate_api_agent_count(
                        self.make_session("active", count)
                    )

    def test_off_mode_does_not_require_positive_agent_count(self):
        self.assertEqual(
            app.validate_api_agent_count(self.make_session("off", 0)),
            0,
        )
```

- [ ] **Step 2: Run the validator tests and verify RED**

Run:

```bash
conda run -n otree_env python -m unittest single_bottleneck.agents.test_admin_agent_count.AdminAgentCountValidationTests
```

Expected: FAIL because `single_bottleneck.validate_api_agent_count` does not exist.

- [ ] **Step 3: Implement the validator**

Add near `api_agent_count_per_group` in `single_bottleneck/__init__.py`:

```python
API_AGENT_COUNT_MIN = 1
API_AGENT_COUNT_MAX = 5


def validate_api_agent_count(session) -> int:
    count = api_agent_count_per_group(session)
    if api_agent_mode(session) == API_AGENT_MODE_OFF:
        return count
    raw_count = session.config.get("api_agent_count_per_group", count)
    if isinstance(raw_count, bool) or str(raw_count).strip() != str(count):
        raise ValueError("每组 Agent 数量必须是 1 到 5 之间的整数。")
    if not API_AGENT_COUNT_MIN <= count <= API_AGENT_COUNT_MAX:
        raise ValueError("每组 Agent 数量必须是 1 到 5 之间的整数。")
    return count
```

Use a parsing implementation that accepts integer values and integer strings such as `"3"`, but rejects decimal strings such as `"2.5"`, booleans, and non-numeric strings. A clearer equivalent implementation is acceptable if it preserves these exact behaviors.

- [ ] **Step 4: Call validation before Session initialization**

At the beginning of the round-one branch in `creating_session`:

```python
if subsession.round_number == 1:
    validate_api_agent_count(subsession.session)
    players = subsession.get_players()
```

This call must remain before grouping, calibration, and Persona initialization.

- [ ] **Step 5: Run the validator tests and verify GREEN**

Run:

```bash
conda run -n otree_env python -m unittest single_bottleneck.agents.test_admin_agent_count.AdminAgentCountValidationTests
```

Expected: `Ran 4 tests` and `OK`.

- [ ] **Step 6: Check the uncommitted server diff**

```bash
git diff --check -- single_bottleneck/__init__.py single_bottleneck/agents/test_admin_agent_count.py
```

Expected: no output and no Git commit.

### Task 2: Prominent Create-Session Control

**Files:**
- Modify: `single_bottleneck/agents/test_admin_agent_count.py`
- Create: `_templates/otree/CreateSession.html`

- [ ] **Step 1: Add a failing template contract test**

Append to `single_bottleneck/agents/test_admin_agent_count.py`:

```python
from pathlib import Path


class AdminAgentCountTemplateTests(unittest.TestCase):
    def setUp(self):
        self.template_path = Path("_templates/otree/CreateSession.html")

    def test_template_exposes_prominent_agent_count_control(self):
        template = self.template_path.read_text(encoding="utf-8")

        self.assertIn("每组 Agent 数量", template)
        self.assertIn('min="1"', template)
        self.assertIn('max="5"', template)
        self.assertIn('step="1"', template)
        self.assertIn("api_agent_count_per_group", template)

    def test_template_targets_only_active_agent_configs(self):
        template = self.template_path.read_text(encoding="utf-8")

        self.assertIn("single_bottleneck_prod_agent_active", template)
        self.assertIn("single_bottleneck_demo_agent_active", template)
        self.assertNotIn('"single_bottleneck_prod"', template)
        self.assertNotIn('"single_bottleneck_demo"', template)

    def test_template_keeps_standard_otree_form(self):
        template = self.template_path.read_text(encoding="utf-8")

        self.assertIn('otree/includes/CreateSessionForm.html', template)
        self.assertIn("agent-count-control", template)
        self.assertIn("reportValidity", template)
```

- [ ] **Step 2: Run the template tests and verify RED**

Run:

```bash
conda run -n otree_env python -m unittest single_bottleneck.agents.test_admin_agent_count.AdminAgentCountTemplateTests
```

Expected: ERROR with `FileNotFoundError` because the project template override does not exist.

- [ ] **Step 3: Create the project template override**

Create `_templates/otree/CreateSession.html` with:

```html
{% extends "otree/BaseAdminRegular.html" %}

{% block head_title %}Create session{% endblock %}
{% block title %}Create session{% endblock %}

{% block content %}
<div class="alert alert-info">
    <p><b>Tip:</b> consider creating your session in a
        <a href="{% url 'Rooms' %}">Room</a> for stable start links.</p>
</div>

{% include "otree/includes/CreateSessionForm.html" %}

<div id="agent-count-control" class="mb-3" hidden>
    <label class="col-form-label" for="agent-count-per-group">每组 Agent 数量</label>
    <div class="controls">
        <input id="agent-count-per-group" class="form-control w-auto"
               type="number" min="1" max="5" step="1" required>
        <p class="help-block">每个实验组加入的 Agent 数量；Agent 总数 = 实验组数 × 此数量。</p>
    </div>
</div>

<script>
document.addEventListener("DOMContentLoaded", () => {
    const activeAgentConfigs = new Set([
        "single_bottleneck_prod_agent_active",
        "single_bottleneck_demo_agent_active",
    ]);
    const suffix = ".api_agent_count_per_group";
    const dropdown = document.querySelector('[name="session_config"]');
    const participantBlock = document.querySelector('label[for="num_participants"]')?.closest(".mb-3");
    const control = document.getElementById("agent-count-control");
    const countInput = document.getElementById("agent-count-per-group");
    const createButton = document.getElementById("btn-create-session");
    const sourceInputs = new Map();

    document.querySelectorAll(`input[name$="${suffix}"]`).forEach((input) => {
        const configName = input.name.slice(0, -suffix.length);
        sourceInputs.set(configName, input);
        input.closest("tr")?.setAttribute("hidden", "hidden");
        input.removeAttribute("name");
    });

    if (!dropdown || !participantBlock || !control || !countInput || !createButton) return;
    participantBlock.insertAdjacentElement("afterend", control);

    let currentConfig = "";
    function syncControl() {
        if (currentConfig && sourceInputs.has(currentConfig)) {
            sourceInputs.get(currentConfig).value = countInput.value;
        }
        currentConfig = dropdown.value;
        const source = sourceInputs.get(currentConfig);
        const visible = activeAgentConfigs.has(currentConfig) && Boolean(source);
        control.hidden = !visible;
        countInput.removeAttribute("name");
        if (!visible) return;
        countInput.name = `${currentConfig}${suffix}`;
        countInput.value = source.value;
    }

    dropdown.addEventListener("change", syncControl);
    createButton.addEventListener("click", (event) => {
        if (control.hidden) return;
        countInput.setCustomValidity("");
        if (!countInput.checkValidity() || !Number.isInteger(Number(countInput.value))) {
            countInput.setCustomValidity("请输入 1 到 5 之间的整数");
            countInput.reportValidity();
            event.preventDefault();
            event.stopImmediatePropagation();
        }
    }, true);
    syncControl();
});
</script>
{% endblock %}
```

- [ ] **Step 4: Run all admin-count tests and verify GREEN**

Run:

```bash
conda run -n otree_env python -m unittest single_bottleneck.agents.test_admin_agent_count
```

Expected: `Ran 7 tests` and `OK`.

- [ ] **Step 5: Check template resolution and syntax**

Run:

```bash
conda run -n otree_env python -c "from otree.templating import get_template_name_if_exists; print(get_template_name_if_exists(['otree/CreateSession.html']))"
conda run -n otree_env python -m compileall settings.py single_bottleneck
```

Expected: the first command resolves `otree/CreateSession.html`; compilation exits with status 0.

### Task 3: Regression and Browser Verification

**Files:**
- Verify only; no planned source changes unless browser testing exposes a defect.

- [ ] **Step 1: Run focused regression tests**

```bash
conda run -n otree_env python single_bottleneck/agents/test_personas.py
conda run -n otree_env python single_bottleneck/agents/test_deepseek_shadow_agent.py
conda run -n otree_env python -m unittest single_bottleneck.agents.test_persona_integration
conda run -n otree_env python -m unittest single_bottleneck.tests.AgentResultCostTests
```

Expected: every command reports `OK`.

- [ ] **Step 2: Start oTree on an available local port**

```bash
conda run -n otree_env otree devserver 8000
```

If port 8000 is occupied, use 8001. Keep the process running until browser verification is complete.

- [ ] **Step 3: Verify the administrator workflow in a real browser**

Use Playwright to open `/create_session`, authenticate with the local administrator credentials if prompted, and verify:

1. The Agent count control is hidden for `single_bottleneck_prod`.
2. It appears below participant count for `single_bottleneck_prod_agent_active`.
3. Values 1 and 5 are accepted by browser validation.
4. Values 0, 6, and 2.5 are rejected before websocket submission.
5. Switching away from the Agent config hides the control.
6. No duplicate raw `api_agent_count_per_group` row remains visible.

Capture one desktop screenshot for the selected Agent-enabled configuration.

- [ ] **Step 4: Run final checks and stop the server**

```bash
git diff --check
git status --short
```

Expected: no whitespace errors. All changes remain uncommitted, including the user's pre-existing modifications.
