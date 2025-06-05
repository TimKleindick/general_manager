# Group Manager

`group_by()` allows you to aggregate multiple `GeneralManager` objects by one or more attributes. It returns a `GroupBucket` containing `GroupedManager` instances representing each group.

## Basic usage

Call `group_by()` on any bucket to build groups.

```python
projects = Project.all()
project_groups = projects.group_by("start_date")
```

Each entry of `project_groups` is a `GroupedManager`. It exposes the grouping key (here `start_date`) and combines the remaining attributes from all objects in the group.

## Accessing aggregated values

Accessing an attribute on a `GroupedManager` merges the values of all contained objects. The merge strategy depends on the attribute type:

- `int`, `float` and `Measurement` values are summed.
- `datetime`, `date` and `time` return the latest value.
- `bool` evaluates to `any`.
- lists and dictionaries are concatenated.
- buckets or `GeneralManager` instances are combined using `|`.

```python
first_group = project_groups.first()
print(first_group.start_date)  # grouping value
print(first_group.total_capex) # summed Measurement
```

If every value of an attribute is `None`, the result is `None`.

## Filtering, slicing and sorting

A `GroupBucket` supports the same operations as a regular bucket:

- `filter()` and `exclude()` apply conditions before grouping.
- `sort(key, reverse=False)` sorts the groups by one or more attributes.
- slicing (`bucket[1:3]`) returns a new `GroupBucket` with the selected groups.

Groups can be combined with `|` to merge their underlying data.

## Nested grouping

You can chain `group_by()` to create hierarchical groups:

```python
volume_groups = DerivativeVolume.all().group_by("date").group_by("derivative")
```

This returns a bucket where each group represents a derivative on a specific date.


