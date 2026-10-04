"""Exact, single-name commands only. Business edits stay with LLM/Clarify."""
import re
from .models import ProcessDefinition


def rename_command(process: ProcessDefinition, command: str):
    if process.ambiguities:
        return None
    match = re.fullmatch(r'\s*(?:Переименуй|Переименовать)\s+(роль|участника|дорожку|задачу)\s+(.+?)\s+в\s+(.+?)\s*', command, re.I)
    if match is None:
        match = re.fullmatch(r'\s*Измени название\s+(задачи|роли|дорожки)\s+(.+?)\s+на\s+(.+?)\s*', command, re.I)
    if match is None:
        return None
    category, old, new = match.groups()
    def unquote(value):
        for left, right in [('«', '»'), ('"', '"')]:
            if value.startswith(left) and value.endswith(right):
                return value[1:-1]
        return value
    old, new = unquote(old), unquote(new)
    # No compound instructions, placeholders, punctuation, or fuzzy name matching.
    if not re.fullmatch(r'[\wА-Яа-яЁё ()-]{1,200}', new) or re.search(r'\b(?:и|затем|после|если|удали|добавь|измени|переименуй)\b', new, re.I):
        return None
    tasks = category.casefold() in ('задачу', 'задачи')
    collection = [n for n in process.nodes if n.type.endswith('task')] if tasks else process.participants
    matches = [item for item in collection if item.name == old]
    if len(matches) != 1 or any(item.name == new and item.id != matches[0].id for item in collection):
        return None
    if tasks and len(new) > 90:
        return None  # Readable labels still use the standard semantic checks.
    result = process.model_copy(deep=True)
    target = next(item for item in (result.nodes if tasks else result.participants) if item.id == matches[0].id)
    target.name = new
    return ProcessDefinition.model_validate(result.model_dump())
