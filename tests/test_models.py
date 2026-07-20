import pytest

from meshpi.models import (
    MAX_MESSAGE_BYTES,
    node_num_to_id,
    normalize_node_id,
    short_node_id,
    validate_message_text,
)


def test_full_node_id_to_short_id():
    assert short_node_id("!710365c8") == "65c8"
    assert node_num_to_id(0x710365C8) == "!710365c8"


@pytest.mark.parametrize("value", ["!710365c8", "710365C8", "  !710365c8  "])
def test_normalize_node_id(value):
    assert normalize_node_id(value) == "!710365c8"


@pytest.mark.parametrize("value", ["", "!65c8", "!710365cg", "!ffffffff"])
def test_reject_invalid_dm_node_id(value):
    with pytest.raises(ValueError):
        normalize_node_id(value)


def test_validate_message_uses_utf8_byte_limit():
    assert validate_message_text(" hei ") == "hei"
    assert validate_message_text("æ" * (MAX_MESSAGE_BYTES // 2))
    with pytest.raises(ValueError):
        validate_message_text("æ" * (MAX_MESSAGE_BYTES // 2 + 1))
    with pytest.raises(ValueError):
        validate_message_text(" \n ")

