from typing import Any, Iterable


def args_to_kwargs(
    args: tuple[Any, ...], keys: Iterable[Any], existing_kwargs: dict | None = None
):
    """
    Converts *args into **kwargs and combines them with existing **kwargs.

    :param args: Tuple of positional arguments (e.g., *args).
    :param keys: List of keys to associate with the arguments.
    :param existing_kwargs: Optional dictionary of already existing key-value mappings.
    :return: Dictionary of combined **kwargs.
    """
    keys = list(keys)
    if len(args) > len(keys):
        raise TypeError("More positional arguments than keys provided.")

    kwargs = {key: value for key, value in zip(keys, args)}
    if existing_kwargs and any(key in kwargs for key in existing_kwargs):
        raise TypeError("Conflicts in existing kwargs.")
    if existing_kwargs:
        kwargs.update(existing_kwargs)

    return kwargs
