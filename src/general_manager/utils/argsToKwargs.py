from typing import Iterable, Mapping


class TooManyArgumentsError(TypeError):
    """Raised when more positional arguments are supplied than available keys."""

    def __init__(self) -> None:
        """
        Initialize the TooManyArgumentsError instance.

        Sets the exception message to "More positional arguments than keys provided."
        """
        super().__init__("More positional arguments than keys provided.")


class ConflictingKeywordError(TypeError):
    """Raised when generated keyword arguments conflict with existing kwargs."""

    def __init__(self) -> None:
        """
        Initialize ConflictingKeywordError with the standard message "Conflicts in existing kwargs."
        """
        super().__init__("Conflicts in existing kwargs.")


def args_to_kwargs(
    args: tuple[object, ...],
    keys: Iterable[str],
    existing_kwargs: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """
    Map positional arguments to the given keys and merge the result with an optional existing kwargs mapping.

    Parameters:
        args (tuple[object, ...]): Positional values to assign to keys in order.
        keys (Iterable[str]): Keys to assign the positional values to.
        existing_kwargs (Mapping[str, object] | None): Optional mapping of keyword arguments to merge into the result.

    Returns:
        dict[str, object]: A dictionary containing the mapped keys for the provided positional arguments plus all entries from `existing_kwargs` (if given).

    Raises:
        TooManyArgumentsError: If more positional arguments are provided than keys.
        ConflictingKeywordError: If `existing_kwargs` contains a key that was already produced from `args` and `keys`.
    """
    keys = list(keys)
    if len(args) > len(keys):
        raise TooManyArgumentsError()

    kwargs: dict[str, object] = {
        key: value for key, value in zip(keys, args, strict=False)
    }
    if existing_kwargs and any(key in kwargs for key in existing_kwargs):
        raise ConflictingKeywordError()
    if existing_kwargs:
        kwargs.update(existing_kwargs)

    return kwargs
