"""Manager execution metadata reserved across public mutation transports."""

MUTATION_CONTROL_KEYS = frozenset(
    {"ignore_permission", "creator_id", "history_comment"}
)
