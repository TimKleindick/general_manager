"""GraphQL warmup helpers."""

from __future__ import annotations

from typing import Iterable, Type

from django.conf import settings

from general_manager.logging import get_logger
from general_manager.manager.general_manager import GeneralManager

logger = get_logger("api.warmup")


def warmup_enabled() -> bool:
    """
    Return whether GraphQL warmup is globally enabled.

    Uses GENERAL_MANAGER["GRAPHQL_WARMUP_ENABLED"] with fallback to
    GRAPHQL_WARMUP_ENABLED. Defaults to True.
    """
    config = getattr(settings, "GENERAL_MANAGER", {})
    if isinstance(config, dict) and "GRAPHQL_WARMUP_ENABLED" in config:
        return bool(config.get("GRAPHQL_WARMUP_ENABLED"))
    return bool(getattr(settings, "GRAPHQL_WARMUP_ENABLED", True))


def warm_up_graphql_properties(
    manager_classes: Iterable[Type[GeneralManager]],
) -> None:
    """
    Warm up GraphQL property caches for properties declared with ``warm_up=True``.

    Parameters:
        manager_classes (Iterable[type[GeneralManager]]): Manager classes to inspect.
    """
    if not warmup_enabled():
        return
    for manager_class in manager_classes:
        interface_cls = getattr(manager_class, "Interface", None)
        if interface_cls is None:
            continue
        properties = interface_cls.get_graph_ql_properties()
        warmup_property_names = [
            name for name, prop in properties.items() if prop.warm_up
        ]
        if not warmup_property_names:
            continue
        logger.info(
            "warming graphql properties",
            context={
                "manager": manager_class.__name__,
                "properties": warmup_property_names,
            },
        )
        try:
            for instance in manager_class.all():
                for property_name in warmup_property_names:
                    try:
                        getattr(instance, property_name)
                    except Exception:
                        logger.exception(
                            "graphql property warm-up failed",
                            context={
                                "manager": manager_class.__name__,
                                "property": property_name,
                                "identification": instance.identification,
                            },
                        )
            for property_name in warmup_property_names:
                prop = properties[property_name]
                if not prop.sortable or prop.query_annotation is not None:
                    continue
                try:
                    manager_class.all().sort(property_name)
                    manager_class.all().sort(property_name, reverse=True)
                except Exception:
                    logger.exception(
                        "graphql sortable warm-up failed",
                        context={
                            "manager": manager_class.__name__,
                            "property": property_name,
                        },
                    )
        except Exception:
            logger.exception(
                "graphql property warm-up failed",
                context={"manager": manager_class.__name__},
            )
