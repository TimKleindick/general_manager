def snake_to_pascal(s: str) -> str:
    """
    Convert a snake_case string to PascalCase.

    Parameters:
        s (str): The input string in snake_case format.

    Returns:
        str: The converted string in PascalCase format.
    """
    return "".join(p.title() for p in s.split("_"))


def snake_to_camel(s: str) -> str:
    """
    Convert a snake_case string to camelCase.

    Parameters:
        s (str): The snake_case string to convert.

    Returns:
        str: The input string converted to camelCase.
    """
    parts = s.split("_")
    return parts[0] + "".join(p.title() for p in parts[1:])


def pascal_to_snake(s: str) -> str:
    """
    Convert a PascalCase string to snake_case.

    Parameters:
        s (str): The PascalCase string to convert.

    Returns:
        str: The converted snake_case string.
    """
    return "".join(["_" + c.lower() if c.isupper() else c for c in s]).lstrip("_")


def camel_to_snake(s: str) -> str:
    """
    Convert a camelCase string to snake_case.

    Parameters:
        s (str): The camelCase string to convert.

    Returns:
        str: The converted snake_case string. Returns an empty string if the input is empty.
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
