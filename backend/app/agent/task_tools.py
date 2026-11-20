# Ported from Open WebUI v0.13.4, backend/open_webui/tools/builtin.py
# (TaskItem, VALID_TASK_STATUSES, _task_summary, create_tasks, update_task).
# Copyright (c) 2023- Open WebUI Inc. [Created by Timothy Jaeryang Baek]
# All rights reserved. Used under the Open WebUI License, with its conditions
# and disclaimer: see third_party/open-webui/LICENSE.
"""A visible task checklist the model keeps during a research run.

As in Open WebUI: ``create_tasks`` writes the list, ``update_task`` changes
one item's status, and each call returns the whole list with counts. Here the
list lives in the run (not in a chat row); every change is sent to the page
(``on_change``) and the final list is saved with the answer. Task calls are not
course tool calls: they do not use the research budget, but a run may make at
most MAX_CALLS of them.
"""
from __future__ import annotations

import json
from typing import Callable, Literal, Optional

from pydantic import BaseModel, Field, ValidationError

VALID_TASK_STATUSES = {'pending', 'in_progress', 'completed', 'cancelled'}
MAX_TASKS = 12
MAX_CONTENT_CHARS = 200
MAX_CALLS = 30


class TaskItem(BaseModel):
    id: Optional[str] = Field(None, description='Unique identifier for the task. Auto-generated if omitted.')
    content: str = Field(..., description='Task description.')
    status: Literal['pending', 'in_progress', 'completed', 'cancelled'] = Field('pending', description='Task status.')


class CreateTasksArgs(BaseModel):
    tasks: list[TaskItem] = Field(..., description='List of task items.')


class UpdateTaskArgs(BaseModel):
    id: str = Field(..., description='The task ID to update')
    status: str = Field('completed', description='New status: completed, in_progress, pending, or cancelled')


TASK_TOOLS = [
    {'type': 'function', 'function': {
        'name': 'create_tasks',
        'description': ('Create a visible task checklist for multi-step work so progress can be shown '
                        'in chat. Returns JSON with the full task list and summary counts.'),
        'parameters': CreateTasksArgs.model_json_schema()}},
    {'type': 'function', 'function': {
        'name': 'update_task',
        'description': ('Mark a single visible task item as completed, in_progress, pending, or '
                        'cancelled. Returns JSON with the updated task list and summary counts.'),
        'parameters': UpdateTaskArgs.model_json_schema()}},
]
TASK_TOOL_NAMES = {t['function']['name'] for t in TASK_TOOLS}


def _task_summary(all_tasks: list[dict]) -> dict:
    """Build summary counts for a task list."""
    return {
        'total': len(all_tasks),
        'pending': sum(1 for t in all_tasks if t['status'] == 'pending'),
        'in_progress': sum(1 for t in all_tasks if t['status'] == 'in_progress'),
        'completed': sum(1 for t in all_tasks if t['status'] == 'completed'),
        'cancelled': sum(1 for t in all_tasks if t['status'] == 'cancelled'),
    }


class TaskList:
    """The checklist of one run."""

    def __init__(self, on_change: Callable[[list[dict]], None] | None = None):
        self.tasks: list[dict] = []
        self.calls = 0
        self.on_change = on_change or (lambda _tasks: None)

    def _reply(self) -> str:
        self.on_change([dict(t) for t in self.tasks])
        return json.dumps({'tasks': self.tasks, 'summary': _task_summary(self.tasks)}, ensure_ascii=False)

    def call(self, name: str, arguments: str | None) -> str:
        """The tool result of a create_tasks or update_task call."""
        self.calls += 1
        if self.calls > MAX_CALLS:
            return json.dumps({'error': f'At most {MAX_CALLS} task calls in one answer'})
        try:
            if name == 'create_tasks':
                return self._create(CreateTasksArgs.model_validate_json(arguments or '{}'))
            return self._update(UpdateTaskArgs.model_validate_json(arguments or '{}'))
        except ValidationError as exc:
            return json.dumps({'error': '; '.join(e['msg'] for e in exc.errors(include_url=False))[:200]})

    def _create(self, args: CreateTasksArgs) -> str:
        all_tasks = []
        for idx, task in enumerate(args.tasks[:MAX_TASKS]):
            content = task.content.strip()[:MAX_CONTENT_CHARS]
            if not content:
                continue
            item_id = (task.id or '').strip() or str(idx + 1)
            all_tasks.append({'id': item_id, 'content': content, 'status': task.status})
        self.tasks = all_tasks
        return self._reply()

    def _update(self, args: UpdateTaskArgs) -> str:
        status = args.status.strip().lower()
        if status not in VALID_TASK_STATUSES:
            return json.dumps({'error': f'Invalid status: {status}. Must be one of: '
                                        f'{", ".join(sorted(VALID_TASK_STATUSES))}'})
        for task in self.tasks:
            if task['id'] == args.id:
                task['status'] = status
                return self._reply()
        return json.dumps({'error': f'Task with id "{args.id}" not found'})
