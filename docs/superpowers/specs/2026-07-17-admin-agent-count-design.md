# Admin Agent Count Design

## Objective

Allow an administrator to choose the number of DeepSeek Agents per group while creating an Agent-enabled single-bottleneck Session. The control must be prominent, validated, and stored in the new Session's existing `api_agent_count_per_group` configuration value.

This change does not add database fields, change participant-facing pages, or alter existing Sessions.

## Existing Capability

oTree already treats scalar custom Session configuration values as editable. The standard create-session websocket reads a field named `<session_config_name>.api_agent_count_per_group` and passes the changed value to `create_session` through `modified_session_config_fields`.

The current project already defines `api_agent_count_per_group` as an integer in `SINGLE_BOTTLENECK_COMMON`. Therefore, the backend persistence path exists. The current control is only available under the generic `Configure session` panel, uses the raw field name, and has no project-specific range validation.

## User Interface

Create a project-level override at `_templates/otree/CreateSession.html`. It retains the standard oTree page structure and includes the standard `otree/includes/CreateSessionForm.html`, then adds a focused Agent-count control and enhancement script.

The control appears directly below the participant-count field with:

- Label: `每组 Agent 数量`
- Numeric input with integer step
- Minimum: `1`
- Maximum: `5`
- Help text: `每个实验组加入的 Agent 数量；Agent 总数 = 实验组数 × 此数量。`

It is visible only for:

- `single_bottleneck_prod_agent_active`
- `single_bottleneck_demo_agent_active`

It remains hidden for Agent-off single-bottleneck configurations and all route-choice configurations.

The default value comes from the selected Session configuration, which is currently initialized from `SINGLE_BOTTLENECK_API_AGENT_COUNT_PER_GROUP` in `.env`. Switching between Session configurations restores the selected configuration's default value.

The enhancement script hides the raw `api_agent_count_per_group` row in the generic configuration table to avoid duplicate controls. The visible input uses the exact standard oTree field name expected by the create-session websocket.

## Validation

Client-side validation blocks submission when the visible value is missing, non-integral, below 1, or above 5. The browser displays the native numeric-field validation message.

Server-side validation remains authoritative. Add a helper in `single_bottleneck/__init__.py` that:

1. Returns immediately for Agent mode `off`.
2. Reads `api_agent_count_per_group` from the Session configuration using the existing parser.
3. Raises a clear `ValueError` unless the value is between 1 and 5 inclusive.

Call the helper at the start of round-one `creating_session`, before grouping calibration and Persona initialization. This catches invalid values supplied through modified Session configuration fields, REST calls, or manually constructed Sessions.

## Data Flow

```text
Administrator selects an Agent-enabled configuration
    -> prominent count input appears
    -> administrator enters 1-5
    -> standard oTree websocket serializes the field
    -> oTree stores it in session.config
    -> creating_session validates it
    -> Persona initialization creates that many Agent profiles per group
    -> each round creates that many Agent decisions per group
```

The meaning is explicitly per group. For example, three groups with a value of two create six Agents in total.

## Compatibility

- Existing Sessions keep their copied Session configuration and are not changed.
- Existing `.env` configuration remains the default for newly opened create-session forms.
- Agent mode `off` does not require a positive Agent count and does not initialize Personas.
- Active and shadow modes use the same validated count if a shadow configuration is added later.
- No database reset or migration is required.
- Existing Agent result settlement and display logic are unchanged.

## Error Handling

Invalid input is rejected before Session setup proceeds. The server error states that the Agent count must be an integer between 1 and 5. No partial Persona initialization should occur before validation succeeds.

If the project template cannot locate the standard Agent-count input for a selected active configuration, the prominent control remains unavailable and the standard configuration panel remains the fallback. The script must not remove the original field name until the replacement control is initialized successfully.

## Tests

Tests are written before implementation and cover:

1. The project-level create-session template contains the prominent Chinese label and `1-5` constraints.
2. The template identifies both active single-bottleneck Session configurations and excludes off configurations from the visible-control list.
3. The server validator accepts 1 and 5 for active mode.
4. The server validator rejects 0, negative values, values above 5, and non-integer values for active mode.
5. The server validator ignores Agent count in off mode.
6. Existing Persona initialization and Agent integration tests continue to pass.
7. Python compilation and template rendering complete without errors.

## Out of Scope

- Changing Agent count after a Session has been created.
- Participant-facing Agent controls or disclosure.
- Per-group counts that differ within one Session.
- Agent counts above five.
- Database schema changes.
- Changes to DeepSeek model, Persona definitions, or settlement formulas.
