"""Deterministic Room-label assignment for formal bottleneck sessions."""

from pathlib import Path


class RoomLabelAssignmentError(ValueError):
    """Raised when a Room label file cannot define the requested groups."""


def _validate_unique_labels(labels):
    if len(labels) != len(set(labels)):
        raise RoomLabelAssignmentError('Room 标签文件中存在重复标签。')


def load_room_labels(path):
    """Load nonblank Room labels in their file order."""

    label_path = Path(path)
    try:
        labels = label_path.read_text(encoding='utf-8-sig').split()
    except OSError as exc:
        raise RoomLabelAssignmentError(
            f'无法读取 Room 标签文件：{label_path}'
        ) from exc
    if not labels:
        raise RoomLabelAssignmentError('Room 标签文件为空。')
    _validate_unique_labels(labels)
    return labels


def build_sequential_label_plan(labels, treatments):
    """Slice ordered labels into treatment groups by Human count."""

    ordered_labels = [str(label).strip() for label in labels]
    if not ordered_labels or any(not label for label in ordered_labels):
        raise RoomLabelAssignmentError('Room 标签不能为空。')
    _validate_unique_labels(ordered_labels)
    if not treatments:
        raise RoomLabelAssignmentError('至少需要一个实验组。')

    plan = {}
    start = 0
    for group_label, treatment in treatments.items():
        try:
            human_count = int(treatment['human'])
        except (KeyError, TypeError, ValueError) as exc:
            raise RoomLabelAssignmentError(
                f'{group_label} 的 Human 数量必须为正整数。'
            ) from exc
        if human_count <= 0:
            raise RoomLabelAssignmentError(
                f'{group_label} 的 Human 数量必须为正整数。'
            )
        end = start + human_count
        if end > len(ordered_labels):
            raise RoomLabelAssignmentError(
                f'Room 标签数量不足：{group_label} 需要 {human_count} 个，'
                f'累计需要 {end} 个，但文件仅有 {len(ordered_labels)} 个。'
            )
        plan[str(group_label)] = ordered_labels[start:end]
        start = end
    return plan


def flatten_label_plan(plan):
    """Return a label plan as one ordered sequence."""

    return [label for labels in plan.values() for label in labels]
