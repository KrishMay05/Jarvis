from src.json_util import parse_llm_json
import pytest


@pytest.mark.parametrize(
    "text, expected",
    [
        ('{"action": "respond_to_user", "args": "hello"}', {"action": "respond_to_user", "args": "hello"}),
        (
            '```json\n{"action": "weather", "args": "London"}\n```',
            {"action": "weather", "args": "London"},
        ),
        (
            'Sure, here you go:\n{"action": "Time Tool", "args": "America/New_York"}\nDone.',
            {"action": "Time Tool", "args": "America/New_York"},
        ),
        ("[1, 2, 3]", [1, 2, 3]),
    ],
)
def test_parse_llm_json_accepts_common_shapes(text, expected):
    assert parse_llm_json(text) == expected


@pytest.mark.parametrize("text", ["", "   ", "not json", "```json\nnot json\n```", "42"])
def test_parse_llm_json_rejects_invalid_input(text):
    with pytest.raises(ValueError, match="Invalid JSON response"):
        parse_llm_json(text)
