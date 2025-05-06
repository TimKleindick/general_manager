from typing import Any, Iterable


def args_to_kwargs(
    args: tuple[Any, ...], keys: Iterable[Any], existing_kwargs: dict | None = None
):
    """
    Wandelt *args in **kwargs um und kombiniert sie mit bestehenden **kwargs.

    :param args: Tuple der positional arguments (z. B. *args).
    :param keys: Liste der Schlüssel, die den Argumenten zugeordnet werden.
    :param existing_kwargs: Optionales Dictionary mit bereits existierenden Schlüssel-Wert-Zuordnungen.
    :return: Dictionary mit kombinierten **kwargs.
    """
    keys = list(keys)
    if len(args) > len(keys):
        raise ValueError("Mehr args als keys vorhanden.")

    kwargs = {key: value for key, value in zip(keys, args)}
    if existing_kwargs and any(key in kwargs for key in existing_kwargs):
        raise ValueError("Konflikte in bestehenden kwargs.")
    if existing_kwargs:
        kwargs.update(existing_kwargs)

    return kwargs
