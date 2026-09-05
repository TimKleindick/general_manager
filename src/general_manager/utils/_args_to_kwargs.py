from typing import Iterable, Mapping


class TooManyArgumentsError(TypeError):
    """Raised when more positional arguments are supplied than available keys."""

    def __init__(self) -> None:
        """
        Initialize an error for positional arguments that exceed available keys.

        The public message is
        ``"More positional arguments than keys provided."``.
        """
        super().__init__("More positional arguments than keys provided.")


class ConflictingKeywordError(TypeError):
    """Raised when generated keyword arguments conflict with existing kwargs."""

    def __init__(self) -> None:
        """
        Initialize an error for duplicate generated and existing keyword names.

        The public message is ``"Conflicts in existing kwargs."``.
        """
        super().__init__("Conflicts in existing kwargs.")


def args_to_kwargs(
    args: tuple[object, ...],
    keys: Iterable[str],
    existing_kwargs: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """
    Map positional arguments to the given keys and merge the result with an optional existing kwargs mapping.

    The ``keys`` iterable is materialized once, then paired with ``args`` in
    order. Supplying fewer positional values than keys leaves the remaining keys
    absent. ``existing_kwargs`` is merged after generated values, including
    falsey custom mapping objects; any overlapping key raises before merging.
    The returned dictionary is new: generated key order comes first, followed by
    the iteration order of ``existing_kwargs`` for non-conflicting keys.
    ``existing_kwargs`` itself is not mutated.

    Parameters:
        args (tuple[object, ...]): Positional values to assign to keys in order.
        keys (Iterable[str]): Keys to assign positional values to. Duplicate
            keys are allowed and follow normal dictionary overwrite semantics
            while generated positional values are built.
        existing_kwargs (Mapping[str, object] | None): Optional keyword mapping
            to merge into the result. ``None`` means no existing keywords;
            falsey mapping objects are still inspected and merged.

    Returns:
        dict[str, object]: A new dictionary containing the mapped keys for the provided positional arguments plus all entries from `existing_kwargs` (if given).

    Raises:
        TooManyArgumentsError: If more positional arguments are provided than keys.
        ConflictingKeywordError: If `existing_kwargs` contains a key that was already produced from `args` and `keys`.
        Exception: Exceptions raised while iterating ``keys``, iterating
            ``existing_kwargs``, or reading keys/values from
            ``existing_kwargs`` propagate unchanged.
    """
    keys = list(keys)
    if len(args) > len(keys):
        raise TooManyArgumentsError()

    kwargs: dict[str, object] = {
        key: value for key, value in zip(keys, args, strict=False)
    }
    if existing_kwargs is not None and any(key in kwargs for key in existing_kwargs):
        raise ConflictingKeywordError()
    if existing_kwargs is not None:
        kwargs.update(existing_kwargs)

    return kwargs
