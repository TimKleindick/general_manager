# Create Related Objects with Factories

Use factory-boy's `RelatedFactory` when the creation of one manager should
trigger creation of an object that points back to it. For example, a project
factory can create a project team after the project has been saved.

## Define the related factories

The reverse relationship must be declared explicitly. GeneralManager can
generate values for fields on a manager's own model, but it does not infer that
creating a `Project` should also create a `ProjectTeam`.

In `your_app/managers.py`, define the managers and their nested factories:

```python
import factory
from django.db.models import CASCADE, CharField, ForeignKey, PositiveIntegerField

from general_manager.interface import DatabaseInterface
from general_manager.manager import GeneralManager


class ProjectTeamRole(GeneralManager):
    name: str

    class Interface(DatabaseInterface):
        name = CharField(max_length=80)


class Project(GeneralManager):
    name: str

    class Interface(DatabaseInterface):
        name = CharField(max_length=120)

        class Factory:
            team = factory.RelatedFactory('your_app.factories.ProjectTeamFactory', factory_related_name='project')


class ProjectTeam(GeneralManager):
    project: Project
    project_team_role: ProjectTeamRole
    allocation_percent: int

    class Interface(DatabaseInterface):
        project = ForeignKey(Project.Interface._model, on_delete=CASCADE)
        project_team_role = ForeignKey(
            ProjectTeamRole.Interface._model,
            on_delete=CASCADE,
        )
        allocation_percent = PositiveIntegerField(default=100)

        class Factory:
            project_team_role = factory.LazyFunction(lambda: ProjectTeamRole.get(id=5))
```

Then expose the nested related factory through an importable module-level name
in `your_app/factories.py`:

```python
from your_app.managers import ProjectTeam

ProjectTeamFactory = ProjectTeam.Factory
```

factory-boy splits a string factory path at its final dot, imports the module
on the left, and reads the attribute on the right. The alias exists because
`your_app.factories.ProjectTeamFactory` follows that contract;
`your_app.managers.ProjectTeam.Factory` would incorrectly treat
`your_app.managers.ProjectTeam` as an importable module.

`team` is the declaration name on `Project.Factory`. After the project is
saved, `RelatedFactory` calls `ProjectTeam.Factory` and passes that saved
project through the `project` field named by `factory_related_name`.

The example deliberately configures the role with the Django field name
`project_team_role` and supplies the related object. Prefer this form over a
declaration such as `project_team_role_id`: AutoFactory generates defaults from
model field names, so an `_id` declaration does not replace generation for the
`project_team_role` field.

The fixed default expects role `5` to exist. Seed that lookup row in fixture
setup before creating projects:

```python
ProjectTeamRole.Factory.create(id=5, name="Default team member")
```

## Override values for one call

Use factory-boy's double-underscore syntax to forward values into the related
factory:

```python
role = ProjectTeamRole.Factory.create(id=8, name="Project lead")
project = Project.Factory.create(team__project_team_role=role)
```

Here, `team` selects the `RelatedFactory` declaration and
`project_team_role` is forwarded to `ProjectTeam.Factory`. By contrast,
`Project.Factory.create(project_team_role=role)` targets a field or declaration
on `Project.Factory`; it does not configure the related team.

Scalar fields are forwarded in the same way:

```python
project = Project.Factory.create(team__allocation_percent=100)
```

To create a project without its related team, pass `None` for the declaration:

```python
project = Project.Factory.create(team=None)
```

## Understand create and build behavior

Use `Project.Factory.create(...)` for this object graph. Create strategy saves
the project first, then runs the `RelatedFactory`, allowing the team to receive
a valid project foreign key.

`Project.Factory.build(...)` returns an unsaved Django model instance. Under
factory-boy's build strategy the related factory is also built rather than
saved, so it does not produce a persisted project-and-team graph. If a test
needs database rows and their foreign-key relationship, use `create()`.

## Keep production invariants in application code

`RelatedFactory` changes only objects produced by this fixture or seed factory.
It does not make every production `Project` create a `ProjectTeam`. If every
project must have a team, enforce that invariant in an application service or
workflow that creates both records in the same database transaction.
