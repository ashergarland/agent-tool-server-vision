"""String helpers aligned with JavaScript's UTF-16 length semantics."""

from __future__ import annotations


def utf16_length(value: str) -> int:
    return sum(2 if ord(character) > 0xFFFF else 1 for character in value)


def truncate_utf16(value: str, max_units: int) -> str:
    used = 0
    characters: list[str] = []
    for character in value:
        units = 2 if ord(character) > 0xFFFF else 1
        if used + units > max_units:
            break
        characters.append(character)
        used += units
    return "".join(characters)
