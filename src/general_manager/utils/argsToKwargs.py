from typing import Iterable, Mapping


def args_to_kwargs(
    args: tuple[object, ...],
    keys: Iterable[str],
    existing_kwargs: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """
    Convert positional arguments to keyword arguments and merge them into an existing mapping.

    Parameters:
        args (tuple[Any, ...]): Positional arguments that should be mapped to keyword arguments.
        keys (Iterable[Any]): Keys used to map each positional argument within `args`.
        existing_kwargs (dict | None): Optional keyword argument mapping to merge with the generated values.

    Returns:
        dict[Any, Any]: A dictionary containing the merged keyword arguments.

    Raises:
        TypeError: If the number of positional arguments exceeds the number of provided keys, or if any generated keyword collides with `existing_kwargs`.
    """
    keys = list(keys)
    if len(args) > len(keys):
        raise TypeError("More positional arguments than keys provided.")

    kwargs: dict[str, object] = {key: value for key, value in zip(keys, args)}
    if existing_kwargs and any(key in kwargs for key in existing_kwargs):
        raise TypeError("Conflicts in existing kwargs.")
    if existing_kwargs:
        kwargs.update(existing_kwargs)

    return kwargs
