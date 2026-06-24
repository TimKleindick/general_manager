"""Utility helpers for converting between common string casing styles."""


def snake_to_pascal(s: str) -> str:
    """
    Convert a snake_case string to PascalCase.

    The function splits only on underscores and title-cases each segment.
    Empty segments from leading, trailing, or repeated underscores disappear.
    Digits and non-underscore punctuation are preserved by ``str.title()``.
    Acronyms are not preserved; each segment follows Python's title-casing
    rules.

    Parameters:
        s: Input string to convert.

    Returns:
        The converted string. Empty input returns an empty string.
    """
    return "".join(p.title() for p in s.split("_"))


def snake_to_camel(s: str) -> str:
    """
    Convert a snake_case string to camelCase.

    The first underscore-delimited segment is returned unchanged. Later
    segments are title-cased and concatenated. Empty later segments disappear,
    so repeated or trailing underscores are collapsed. A leading underscore
    makes the first segment empty, so the first non-empty segment is title-cased.

    Parameters:
        s: Input string to convert.

    Returns:
        The converted string. Empty input returns an empty string.
    """
    parts = s.split("_")
    return parts[0] + "".join(p.title() for p in parts[1:])


def pascal_to_snake(s: str) -> str:
    """
    Convert a PascalCase string to snake_case.

    Every uppercase character is lower-cased and prefixed with an underscore,
    then one leading underscore is stripped. Consecutive uppercase characters
    are therefore split one character at a time (``"ABC"`` becomes
    ``"a_b_c"``). Existing underscores, digits, lowercase characters, and other
    punctuation are preserved.

    Parameters:
        s: Input string to convert.

    Returns:
        The converted string. Empty input returns an empty string.
    """
    return "".join(["_" + c.lower() if c.isupper() else c for c in s]).lstrip("_")


def camel_to_snake(s: str) -> str:
    """
    Convert a camelCase string to snake_case.

    The first character is lower-cased. Every later uppercase character is
    lower-cased and prefixed with an underscore. Consecutive uppercase
    characters are split one character at a time, and existing underscores,
    digits, lowercase characters, and punctuation are preserved.

    Parameters:
        s: Input string to convert.

    Returns:
        The converted string. Empty input returns an empty string.
    """
    if not s:
        return ""
    parts = [s[0].lower()]
    for c in s[1:]:
        if c.isupper():
            parts.append("_")
            parts.append(c.lower())
        else:
            parts.append(c)
    return "".join(parts)
