# graphQlMutation

`graphQlMutation` converts a regular Python function into a GraphQL mutation that can be used with the automatically generated API. The decorator analyses the function signature to build the required arguments and output fields.

## Basic usage

The decorated function must accept `info` as its first parameter and provide type hints for all other arguments as well as the return value. Optional arguments become optional GraphQL fields and `GeneralManager` parameters are handled via IDs.

```python
from general_manager.api.mutation import graphQlMutation
from general_manager.interface.databaseInterface import DatabaseInterface
from general_manager.manager import GeneralManager
from django.db.models import CharField

class Material(GeneralManager):
    class Interface(DatabaseInterface):
        name = CharField(max_length=100)

@graphQlMutation(auth_required=True)
def create_material(info, name: str) -> Material:
    """Create a new material and return it."""
    return Material.create(name=name, creator_id=info.context.user.id)
```

This mutation can be called through GraphQL like any other mutation:

```graphql
mutation($name: String!) {
    createMaterial(name: $name) {
        material {
            name
        }
        success
        errors
    }
}
```

Every mutation automatically returns `success` and `errors` fields in addition to the values from the function's return type. When `auth_required` is set to `True`, unauthenticated requests result in `success: false` and an error message.
