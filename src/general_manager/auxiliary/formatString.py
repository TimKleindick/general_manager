def snake_to_pascal(s: str) -> str:
    return "".join(p.title() for p in s.split("_"))


def snake_to_camel(s: str) -> str:
    parts = s.split("_")
    return parts[0] + "".join(p.title() for p in parts[1:])


def pascal_to_snake(s: str) -> str:
    return "".join(["_" + c.lower() if c.isupper() else c for c in s]).lstrip("_")


def camel_to_snake(s: str) -> str:
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
