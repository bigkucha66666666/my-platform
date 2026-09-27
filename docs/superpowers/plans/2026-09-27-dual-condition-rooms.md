# Dual-Condition Rooms Implementation Plan

> **For agentic workers:** Execute inline using test-driven development. Do not commit; the user manages version control.

**Goal:** Add independent I0/I1 formal rooms while reusing the existing 40-Human paired treatment presets and sequential labels.

**Architecture:** Extend `settings.ROOMS`; map room names to existing frontend paired presets. Keep backend grouping, labels and Room-to-Session storage unchanged.

**Tech Stack:** Python/oTree, unittest, browser JavaScript, Node.js syntax/behavior checks.

---

### Task 1: Room registration

Files: `settings.py`, `dynamic_bottleneck_round/test_room_label_admin.py`.

- [ ] Assert `prod_room_i0` and `prod_room_i1` exist, point to `_rooms/econ101.txt`, retain old rooms, and share security settings.
- [ ] Run `/opt/anaconda3/envs/otree_env/bin/python -m unittest dynamic_bottleneck_round.test_room_label_admin -v`; expect missing room assertion failure.
- [ ] Add the two dictionaries to `ROOMS` with condition-specific Chinese display names.
- [ ] Rerun the same tests; expect pass.

### Task 2: Existing preset defaults

Files: `_templates/otree/includes/DynamicSessionControls.html`, `dynamic_bottleneck_round/test_room_label_admin.py`.

- [ ] Add executable tests for `defaultPresetForRoom` returning `PAIR-I0`/`PAIR-I1`, with legacy rooms falling back to `PAIR-I0`; assert initialization hooks and one-time guard are present.
- [ ] Run targeted tests; expect missing helper failure.
- [ ] Implement pure room-name mapping, derive current hidden `room_name`, initialize paired preset once after scene selection, and use the room default when exiting preview/pilot mode.
- [ ] Run tests and JavaScript syntax validation; expect pass.

### Task 3: Label isolation and final verification

Files: `dynamic_bottleneck_round/test_room_label_integration.py` and design document.

- [ ] Verify two separately created 40-player label plans each contain P001–P040 with 30/10 grouping and independent participant objects.
- [ ] Run Room-label, strict-label and administrator-template compatibility tests.
- [ ] Import installed oTree `ROOM_DICT` and inspect new rooms without creating sessions.
- [ ] Check scoped whitespace diff; report restart requirement and exact room/session setup. Do not restart a potentially active experiment.

## 执行结果

2026-09-27：三项任务已完成。Room/标签/管理员针对性检查 29 项通过；动态场景回归 165 项通过；参与者链接导出、管理员模板、oTree 标签兼容检查通过。JavaScript 全脚本语法检查与房间默认方案执行检查通过。未创建实际 Session，未重启服务，未提交 Git。
