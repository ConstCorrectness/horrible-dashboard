"""`load_tools` must understand however a small model spelled its `groups` argument.

A regression test for a measured failure, not a hypothetical one. Against
llama-3.2-3b through LM Studio, the model asked for the right group and the
orchestrator loaded nothing, because an *array* parameter came back as a string:

    {"groups": "[\\"github\\"]"}          valid JSON, wrong type
    {"groups": "['files', 'editor']"}   not even valid JSON

The old code did `if isinstance(requested, str): requested = [requested]`, which
made the group name literally `["github"]`, matched no group, and returned
`loaded: []`. The capability never appeared and it read as the model being unable
to use progressive disclosure — when what happened is that we did not read its
answer.
"""

from __future__ import annotations

import pytest

from backend.modules.agent.orchestrator import _coerce_group_list


@pytest.mark.parametrize(
    "raw,expected",
    [
        # What the model actually sent, both times.
        ('["github"]', ["github"]),
        ("['files', 'editor']", ["files", "editor"]),
        # The shape the schema asks for.
        (["github"], ["github"]),
        (["files", "editor"], ["files", "editor"]),
        # A bare name is a group name, not a list.
        ("github", ["github"]),
        # Not-quite-JSON shapes worth recovering rather than failing the call over.
        ('["files","editor"]', ["files", "editor"]),
        ("[files, editor]", ["files", "editor"]),
        ("files, editor", ["files", "editor"]),
        # Nothing asked for.
        ("", []),
        ("[]", []),
        (None, []),
        ({}, []),
        # Whitespace is not part of a group name.
        (["a ", " b"], ["a", "b"]),
    ],
)
def test_group_lists_are_read_however_they_are_spelled(raw, expected):
    assert _coerce_group_list(raw) == expected


def test_a_bare_name_is_never_run_through_a_list_parser():
    """`github` is a group name. Sending it near `literal_eval` would be a way to
    turn a name into something else entirely."""
    assert _coerce_group_list("github") == ["github"]
    assert _coerce_group_list("None") == ["None"]
