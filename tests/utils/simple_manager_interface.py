from typing import ClassVar

from general_manager.interface.base_interface import InterfaceBase
from general_manager.bucket.base_bucket import Bucket


class SingleItemRequiredError(ValueError):
    """Raised when a bucket operation expects exactly one item but the bucket contains a different amount."""

    def __init__(self) -> None:
        """
        Initialize the SingleItemRequiredError with the default message "get() requires exactly one item.".
        """
        super().__init__("get() requires exactly one item.")


class SimpleBucket(Bucket):
    def __init__(self, manager_class, items=None):
        """
        Initialize the SimpleBucket with a manager class and optional initial items.

        Parameters:
            manager_class: Class used to manage or instantiate items stored in the bucket.
            items (iterable, optional): Iterable of items to populate the bucket; defaults to an empty list.
        """
        super().__init__(manager_class)
        self._data = list(items or [])

    def __or__(self, other):
        """
        Combine this bucket with another bucket or a manager instance.

        If `other` is a SimpleBucket, returns a new SimpleBucket containing items from this bucket followed by items from `other`. If `other` is an instance of this bucket's manager class, returns a new SimpleBucket with `other` appended. If `other` is neither, returns a shallow copy of this bucket.

        Parameters:
            other: The value to combine with this bucket — either a SimpleBucket, an instance of this bucket's manager class, or any other object.

        Returns:
            SimpleBucket: A new bucket containing the combined or copied items.
        """
        if isinstance(other, SimpleBucket):
            return SimpleBucket(self._manager_class, [*self._data, *other._data])
        if isinstance(other, self._manager_class):
            return SimpleBucket(self._manager_class, [*self._data, other])
        return SimpleBucket(self._manager_class, list(self._data))

    def __iter__(self):  # type: ignore
        """
        Iterate over the items in the bucket.

        Returns:
            iterator: An iterator that yields each item stored in the bucket.
        """
        return iter(self._data)

    def filter(self, **kwargs):
        """
        Return a new SimpleBucket containing manager instances for each ID specified in the 'id__in' filter.

        Parameters:
                id__in (list, optional): List of IDs to generate manager instances for.

        Returns:
                SimpleBucket: A bucket containing one manager instance per provided ID.
        """
        ids = kwargs.get("id__in", [])
        return SimpleBucket(self._manager_class, [self._manager_class() for _ in ids])

    def exclude(self, **kwargs):
        """
        Return an empty SimpleBucket of the same manager class, effectively excluding all items.
        """
        return SimpleBucket(self._manager_class, [])

    def first(self):
        """
        Return the first item in the bucket, or None if the bucket is empty.
        """
        return self._data[0] if self._data else None

    def last(self):
        """
        Return the last item in the bucket, or None if the bucket is empty.
        """
        return self._data[-1] if self._data else None

    def count(self):
        """
        Return the number of items contained in the bucket.
        """
        return len(self._data)

    def all(self):
        """
        Return a SimpleBucket containing all items managed by this instance.

        Returns:
                SimpleBucket: A bucket with all current items.
        """
        return SimpleBucket(self._manager_class, self._data)

    def get(self, **kwargs):
        """
        Retrieve the single item in the bucket when the bucket contains exactly one element.

        Returns:
            The single item contained in the bucket.

        Raises:
            SingleItemRequiredError: If the bucket does not contain exactly one item.
        """
        if len(self._data) == 1:
            return self._data[0]
        raise SingleItemRequiredError()

    def __getitem__(self, item):
        """
        Retrieve a single item or a sliced bucket from this SimpleBucket.

        Parameters:
            item (int | slice): An index to select a single element or a slice to select a range.

        Returns:
            SimpleBucket | object: A new SimpleBucket containing the sliced items if `item` is a slice, otherwise the element at the given index.
        """
        if isinstance(item, slice):
            return SimpleBucket(self._manager_class, self._data[item])
        return self._data[item]

    def __len__(self):
        """
        Return the number of items contained in the bucket.
        """
        return len(self._data)

    def __contains__(self, item):
        """
        Return True if the specified item exists in the bucket; otherwise, return False.
        """
        return item in self._data

    def sort(self, key, reverse: bool = False):
        """
        Return a new SimpleBucket with items sorted by the specified key function.

        Parameters:
            key: A function that extracts a comparison key from each item.
            reverse (bool): If True, sort in descending order. Defaults to False.

        Returns:
            SimpleBucket: A new bucket containing the sorted items.
        """
        sorted_data = sorted(self._data, key=key, reverse=reverse)  # type: ignore
        return SimpleBucket(self._manager_class, sorted_data)


class BaseTestInterface(InterfaceBase):
    input_fields: ClassVar[dict[str, object]] = {}

    @classmethod
    def create(cls, *args, **kwargs):
        """
        Declare the interface for creating an instance; subclasses must implement this method.

        Raises:
            NotImplementedError: Always raised to indicate subclasses must provide an implementation.
        """
        raise NotImplementedError

    def update(self, *args, **kwargs):
        """
        Raises NotImplementedError to indicate that the update operation is not implemented.
        """
        raise NotImplementedError

    def deactivate(self, *args, **kwargs):
        """
        Raises NotImplementedError to indicate that deactivation is not implemented for this interface.
        """
        raise NotImplementedError

    def getData(self, search_date=None):
        """
        Raises NotImplementedError to indicate that data retrieval must be implemented by subclasses.

        Parameters:
            search_date: Optional parameter for filtering data by date.
        """
        raise NotImplementedError

    @classmethod
    def getAttributeTypes(cls):
        """
        Return a dictionary mapping attribute names to their types for the class.

        Returns:
            dict: An empty dictionary, indicating no attribute types are defined by default.
        """
        return {}

    @classmethod
    def getAttributes(cls):
        """
        Return a dictionary of attribute definitions for the class.

        Returns:
            dict: An empty dictionary, indicating no attributes are defined by default.
        """
        return {}

    @classmethod
    def filter(cls, **kwargs):
        """
        Return a SimpleBucket containing instances of the parent class for each provided ID.

        Parameters:
            id__in (list, optional): List of IDs to generate instances for. If not provided, returns an empty bucket.

        Returns:
            SimpleBucket: A bucket containing one instance of the parent class for each ID in `id__in`.
        """
        ids = kwargs.get("id__in", [])
        return SimpleBucket(cls._parent_class, [cls._parent_class() for _ in ids])

    @classmethod
    def exclude(cls, **kwargs):
        """
        Return an empty SimpleBucket for the parent class, effectively excluding all items.
        """
        return SimpleBucket(cls._parent_class, [])

    @classmethod
    def getFieldType(cls, field_name: str) -> type:
        """
        Return the type of the specified field, always as `str`.

        Parameters:
            field_name (str): The name of the field to query.

        Returns:
            type: The type of the field, which is always `str`.
        """
        return str

    @classmethod
    def handleInterface(cls):
        """
        Return pre- and post-processing hooks for interface class construction.

        The returned `pre` function injects the interface into the class attributes before class creation. The `post` function sets the `_parent_class` attribute on the interface class after class creation.

        Returns:
            tuple: A pair of functions `(pre, post)` for use in class construction workflows.
        """

        def pre(name, attrs, interface):
            attrs["Interface"] = interface
            return attrs, interface, None

        def post(new_cls, interface_cls, model):
            """
            Sets the `_parent_class` attribute of the interface class to the provided class.

            This links the interface class to its parent class in the hierarchy.
            """
            interface_cls._parent_class = new_cls

        return pre, post
