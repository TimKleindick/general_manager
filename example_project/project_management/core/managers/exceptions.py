from __future__ import annotations


class ProjectCreationManagerAssignmentError(RuntimeError):
    pass


class ProjectCreationMissingCreatorError(ProjectCreationManagerAssignmentError):
    def __init__(self) -> None:
        super().__init__(
            "Project creation requires creator_id so program management can be assigned."
        )


class ProjectCreationCreatorNotFoundError(ProjectCreationManagerAssignmentError):
    def __init__(self, creator_id: int) -> None:
        super().__init__(f"Project creator user with id={creator_id} does not exist.")


class ProjectCreationRoleMissingError(ProjectCreationManagerAssignmentError):
    def __init__(self) -> None:
        super().__init__(
            "ProjectUserRole with id=1 (program management) does not exist."
        )


class ProjectCreationTeamEntryFailedError(ProjectCreationManagerAssignmentError):
    def __init__(self) -> None:
        super().__init__(
            "Failed to create project manager assignment for the new project."
        )
