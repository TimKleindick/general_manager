# CHANGELOG

<!-- version list -->

## v0.23.0 (2025-11-07)

### Bug Fixes

- Clarify mutation return values in GraphQL class documentation
  ([`32ae206`](https://github.com/TimKleindick/general_manager/commit/32ae206fb1679aafb4fc711f2c62c661dd37a960))

- Correct type mapping for DateField and TimeField in TRANSLATION dictionary
  ([`57ae26b`](https://github.com/TimKleindick/general_manager/commit/57ae26bb2d896d1ae0df27c018c2c45d624c9fbf))

- History comment
  ([`df41788`](https://github.com/TimKleindick/general_manager/commit/df417880006690329a97cd8dcbee9f719c75ab54))

- Soft-delete functionality in all DBBasedInterface and update related tests
  ([`d29c47f`](https://github.com/TimKleindick/general_manager/commit/d29c47fa9f075fb8e26b219f1cc690f0b728f60b))

- Update deactivate method to return None instead of Self because of changed delete logic
  ([`6a026cb`](https://github.com/TimKleindick/general_manager/commit/6a026cbe3c53b141926a2adbe277df0454facf17))

- Update register call to include many-to-many fields for model history tracking
  ([`03a8b27`](https://github.com/TimKleindick/general_manager/commit/03a8b27c1df839bac0f1acf2725ccb82a3698bca))

### Documentation

- Refactor docstrings and comments for clarity and consistency across various modules
  ([`9752c87`](https://github.com/TimKleindick/general_manager/commit/9752c87157614f8ecfb8cd567efd176af2219446))

- Update documentation to reflect method renaming from deactivate to delete and clarify soft delete
  functionality
  ([`03a1afa`](https://github.com/TimKleindick/general_manager/commit/03a1afa2236a80d0ab9377e3c1168c7c6f44c3ad))

### Features

- Add historical lookup buffer to prevent racing with in-flight updates
  ([`00f4179`](https://github.com/TimKleindick/general_manager/commit/00f41792f1308be62002ed29d98f0e5b55fe65d8))

- Enhance DBBasedInterface with search date normalization and historical record retrieval
  ([`c3e1a90`](https://github.com/TimKleindick/general_manager/commit/c3e1a904b3a86fc79dafd8391976341303c1dd80))

- Rename deactivate method to delete, change default behavior from soft to hard delete and implement
  optional soft delete functionality
  ([`74c123b`](https://github.com/TimKleindick/general_manager/commit/74c123b2e14e7254f11cfe8d84fd5157efe660da))

### Refactoring

- Extract logic to get historical changes related models into a separate function
  ([`029f368`](https://github.com/TimKleindick/general_manager/commit/029f368d54bb2e8ec7f167d352081566b7e46582))

- Interface utilities and error handling
  ([`37a3687`](https://github.com/TimKleindick/general_manager/commit/37a3687286dff167a1045fc22ee3876f3be3d318))

- Remove class_flag from model field collection and set use_soft_delete in Meta class
  ([`4dd69c5`](https://github.com/TimKleindick/general_manager/commit/4dd69c5d748792cd990bfd92c8aba58386d204be))

- Remove unused search_date parameter from get_data method in interfaces
  ([`163bd9f`](https://github.com/TimKleindick/general_manager/commit/163bd9f50b6239f9ede01792f7d3d42606178945))

- Rename variables for clarity in GeneralManagerTransactionTestCase
  ([`3b4c02f`](https://github.com/TimKleindick/general_manager/commit/3b4c02f7e222de9f56f145c188df295d747a3c9e))

### Testing

- Extend unit tests
  ([`680d950`](https://github.com/TimKleindick/general_manager/commit/680d950209518da8d771795a143273b34ff9c5f7))

- Rename deactivate methods to delete and update related logic for soft delete functionality
  ([`1f099fa`](https://github.com/TimKleindick/general_manager/commit/1f099facd7fcc1763c7f7a8710fb76912272c77b))


## v0.22.0 (2025-11-02)

### Bug Fixes

- Add database alias handling for model saving in AutoFactory
  ([`8ed71b4`](https://github.com/TimKleindick/general_manager/commit/8ed71b44a15b446d7566bceb277c4e82f68d4504))

- Add MissingManagerClassError for better error handling in AutoFactory
  ([`bcaff0d`](https://github.com/TimKleindick/general_manager/commit/bcaff0d6743fe511232b7a0c249f71cf48481304))

- Add type check for instance in get_historical_instance method
  ([`052e732`](https://github.com/TimKleindick/general_manager/commit/052e7329a1e5b3ea1bd2581a6b41a1ddfeee78cf))

- Clear app cache in tearDownClass methods for better resource management
  ([`f567764`](https://github.com/TimKleindick/general_manager/commit/f56776419f6ac4fcdd941c2fc654cb27a2152f52))

- Enhance AutoFactory to handle many-to-many relationships and related values
  ([`ffaa33b`](https://github.com/TimKleindick/general_manager/commit/ffaa33be326f3526dc4a0ff614d1a51a48621133))

- Ensure model class is resolved before retrieving field type in ExistingModelInterface
  ([`27c1277`](https://github.com/TimKleindick/general_manager/commit/27c1277ffecd06ee8bf727456416c330e62fff36))

- Handle empty CharField values and improve manager instance resolution
  ([`c0f7b76`](https://github.com/TimKleindick/general_manager/commit/c0f7b7618ae77a2489577e0caf0867f6213a318c))

- Transaction handling in WritableDBBasedInterface to use context manager
  ([`826c045`](https://github.com/TimKleindick/general_manager/commit/826c045a12a76fabd2a09708313ee8842e320563))

### Documentation

- Add audit logging documentation and update navigation in MkDocs
  ([`3eda396`](https://github.com/TimKleindick/general_manager/commit/3eda3967284e6017a93a1b4a576fc6a984ab4c87))

- Add custom audit logger and permission cookbook examples, enhance audit logger analysis tutorial
  ([`156be12`](https://github.com/TimKleindick/general_manager/commit/156be125c123b9c13b8f5be8f9a6b321dde8e6e9))

- Add docstrings for existing model interface and auto factory
  ([`91b38f8`](https://github.com/TimKleindick/general_manager/commit/91b38f8b260bbb90c7677062907202178f7b217a))

- Add ExistingModelInterface documentation and examples
  ([`1e6a871`](https://github.com/TimKleindick/general_manager/commit/1e6a871cb1e30e52053448b9b9177dcc3aacf9cf))

- Add roadmap
  ([`2a45105`](https://github.com/TimKleindick/general_manager/commit/2a451053445be78db92b1b436f65fc1e88eac7df))

- Update RejectInvoicePermission to use classmethod and improve error handling
  ([`e9ccd23`](https://github.com/TimKleindick/general_manager/commit/e9ccd23ffe2644d76694af395231c85ddd9899f6))

### Features

- Add ExistingModelInterface
  ([`3d74984`](https://github.com/TimKleindick/general_manager/commit/3d7498464db1d137918d6a68995a296d15169bc1))

- Enable multiple databases to be used with database based mangers
  ([`890b78c`](https://github.com/TimKleindick/general_manager/commit/890b78cdf39316ae5d54235dce112a5c71e5741a))

- Enhance factory utilities to support GeneralManager instances and improve field value generation
  ([`4cf4b76`](https://github.com/TimKleindick/general_manager/commit/4cf4b76950ebb432e822d4f173d8d346c1e3ced5))

- Implement protocols for database interface capabilities
  ([`1be213b`](https://github.com/TimKleindick/general_manager/commit/1be213becc712bafe3ac6cec7cc64604ea3b5f98))

### Refactoring

- Simplify DatabaseInterface and splitting read and write
  ([`fd2dbc6`](https://github.com/TimKleindick/general_manager/commit/fd2dbc66feab61e691ff00d5f443ee72c2181226))

### Testing

- Add integration tests for AutoFactory with GeneralManager classes
  ([`19d6e2e`](https://github.com/TimKleindick/general_manager/commit/19d6e2e6b467fd051966c2a27bc2ee7942f7459d))

- Add integration tests for ExistingModelInterface and DatabaseInterface
  ([`83c37bc`](https://github.com/TimKleindick/general_manager/commit/83c37bc36bb6edcfb462d6a4eb809d6d57086516))

- Add unit test for MissingManagerClassError message in AutoFactoryTestCase
  ([`a8ddeda`](https://github.com/TimKleindick/general_manager/commit/a8ddeda47fa76324888865a82f82742f6e703198))

- Improve user deletion logic in tearDown method to handle None values
  ([`6bef499`](https://github.com/TimKleindick/general_manager/commit/6bef499c7a3b1027525c3bab02464e5862fa212d))

- Refactor and clean up unit tests for AutoFactory and ExistingModelInterface
  ([`28e6027`](https://github.com/TimKleindick/general_manager/commit/28e6027776d6aed993ba0e01a71e2ae730a6a68d))

- Remove user cleanup from tearDown method in AutoFactoryIntegrationTest
  ([`483b97d`](https://github.com/TimKleindick/general_manager/commit/483b97d6232a99b506f42232a6c8ea0ba8ccef0d))


## v0.21.0 (2025-10-29)

### Bug Fixes

- Implement superuser bypass for permission checks and filters
  ([`851f339`](https://github.com/TimKleindick/general_manager/commit/851f339b9085c6129451c0bd74354be8dd770f33))

- Update user identifier handling in audit logging and permission checks
  ([`809ec08`](https://github.com/TimKleindick/general_manager/commit/809ec08bdbe0e1b6be0722524279482754101aec))

### Chores

- Remove unused file_based_permission.py
  ([`6b7a497`](https://github.com/TimKleindick/general_manager/commit/6b7a4973cf042f5cbb3099a6e5055a880640933d))

### Documentation

- Add custom permission functions and superuser bypass details to permission documentation
  ([`44227df`](https://github.com/TimKleindick/general_manager/commit/44227dfdffa308751eebaf94f9fba74c5b6748ce))

### Features

- Add default file and database loggers
  ([`5c1e5d1`](https://github.com/TimKleindick/general_manager/commit/5c1e5d1b5b69d8f5d5838dff3d06cca093ec25fd))

- Add permission functions for related user fields and many-to-many relationships
  ([`8406a9f`](https://github.com/TimKleindick/general_manager/commit/8406a9f1d1c64dafc4833879ed9faf6ccc460947))

- Implement a global registry for permission functions with filters
  ([`e2c4ee2`](https://github.com/TimKleindick/general_manager/commit/e2c4ee2634094dc539d68f4d5f365ff2a8dd3337))

- Implement audit logging interface for permission evaluations and add related configurations
  ([`7d72fe7`](https://github.com/TimKleindick/general_manager/commit/7d72fe778c648213babc5da48fc73ee53e46f6cb))

### Refactoring

- Optimize test setup methods for ManagerBasedPermission and PermissionFunctions tests
  ([`6708991`](https://github.com/TimKleindick/general_manager/commit/6708991b496fe20f401e47df09ec193f4a6ceb34))

### Testing

- Add test for custom permission decorator and its registration
  ([`3b25f6e`](https://github.com/TimKleindick/general_manager/commit/3b25f6e859cce81bf7bc111a77079f0b1f963333))

- Call superclass methods in setUpClass and tearDown for PermissionAuditTests
  ([`558294d`](https://github.com/TimKleindick/general_manager/commit/558294db478f1049394164066be8477e07d30a9e))

- Extend test suit for permission and audit handling
  ([`efd90eb`](https://github.com/TimKleindick/general_manager/commit/efd90ebf5f86905a350492ccd99df2bfb2426417))


## v0.20.1 (2025-10-27)

### Bug Fixes

- Improve unique field handling and race condition safety in ReadOnlyInterface
  ([`06a9e42`](https://github.com/TimKleindick/general_manager/commit/06a9e428b794234fb1e7cfbfea77457c6c3027f7))

- Read only data sync error with not nullable fields
  ([`a28d706`](https://github.com/TimKleindick/general_manager/commit/a28d7062d38f4b801548d17e629e1bb436ed2f94))


## v0.20.0 (2025-10-27)

### Bug Fixes

- Create() returned cls(identification) instead of cls(**identification)
  ([`0243548`](https://github.com/TimKleindick/general_manager/commit/0243548e6c8d0cad63ce0faa39fcdec3b797e425))

- Ensure dependency tracker gets a dict identifier, not "None"
  ([`5523ffb`](https://github.com/TimKleindick/general_manager/commit/5523ffb5f0b6df566425532770822fcf6a176411))

- Update user retrieval to use primary key and handle additional exceptions
  ([`6b074d0`](https://github.com/TimKleindick/general_manager/commit/6b074d0a66f3988217446f32e3b4aa00b9f214b4))

### Features

- Add structured logging across GeneralManager components
  ([`ca44186`](https://github.com/TimKleindick/general_manager/commit/ca44186145742c9a36810d42adc36dd785c9badd))

### Testing

- Update logging tests
  ([`041f1ee`](https://github.com/TimKleindick/general_manager/commit/041f1ee4679fe9fea5fe11b6cde1e7b3c18f2a20))


## v0.19.2 (2025-10-26)

### Bug Fixes

- Add normalization for GraphQL field names work with pep8 style snake_case
  ([`800833d`](https://github.com/TimKleindick/general_manager/commit/800833d2caca757b84cdbb18d9ad86d94cd36483))

- Enhance extra arguments handling in InterfaceBase class
  ([`853e9ba`](https://github.com/TimKleindick/general_manager/commit/853e9ba1ca365ef00d0930444a35c6db31169d5d))

- Improve handling of extra arguments in InterfaceBase class
  ([`6843636`](https://github.com/TimKleindick/general_manager/commit/6843636f84017ec973b70a7335013f62e35e682b))

- Type casting for Faker time_delta
  ([`08b6f8f`](https://github.com/TimKleindick/general_manager/commit/08b6f8f278f7843d6afd29d85934f049729c6068))

### Documentation

- Add contributing and security policy documentation
  ([`98f881c`](https://github.com/TimKleindick/general_manager/commit/98f881cec1a15112af55193fb0979d0daef15777))

- Add Contributor Covenant Code of Conduct to promote a respectful community
  ([`e2a3cf6`](https://github.com/TimKleindick/general_manager/commit/e2a3cf65b68a5dcc544942805d36371ac5655e57))

- Fix markdown table error
  ([`a76f64d`](https://github.com/TimKleindick/general_manager/commit/a76f64d7226c3bbd9c88334d56cdf50be9591392))

- Update AGENTS.md to enhance contribution guidelines and coding standards
  ([`2282b35`](https://github.com/TimKleindick/general_manager/commit/2282b35ec52aa028ac60815fbf3618f39d875752))

- Update comments for clarity and consistency in test_rule_handler.py
  ([`6e772e0`](https://github.com/TimKleindick/general_manager/commit/6e772e0c1e2ebccaf1a978cdb4181add074f2225))

- Update German comments to English for consistency across the codebase
  ([`5ee358e`](https://github.com/TimKleindick/general_manager/commit/5ee358ed9c5eae228d1fc1b6e674a48187594d21))

### Refactoring

- Function names to match pep8 convention
  ([`29e9d45`](https://github.com/TimKleindick/general_manager/commit/29e9d4592a695dfe3256d0085a89965d36644522))

- Rename files to match pep8 convention
  ([`7ea26cd`](https://github.com/TimKleindick/general_manager/commit/7ea26cd256bc8eaca2a03d7b763d435778b18502))


## v0.19.1 (2025-10-25)

### Bug Fixes

- Update postCreateCommand to use pre-commit directly
  ([`416262f`](https://github.com/TimKleindick/general_manager/commit/416262f8012947c4f04946b70c8ff425a4b4ed1e))


## v0.19.0 (2025-10-24)

### Features

- Add support for Python version 3.14
  ([`de0fa05`](https://github.com/TimKleindick/general_manager/commit/de0fa05e4af95b2ec2cd113f54a751711f2758f5))


## v0.18.0 (2025-10-22)

### Bug Fixes

- Add DuplicateMutationOutputNameError to handle duplicate output field names in mutations and
  update tests accordingly
  ([`eb9dcc9`](https://github.com/TimKleindick/general_manager/commit/eb9dcc91998c40e8f31060bfe8411ae281c61f3c))

- Enhance boolean handling in generic_cache_invalidation function
  ([`ab70592`](https://github.com/TimKleindick/general_manager/commit/ab70592c969955ed5f02bdb224c4a2c23b4be5c2))

- Enhance get_field_value to handle field choices and update tests accordingly
  ([`99746d2`](https://github.com/TimKleindick/general_manager/commit/99746d233a83f0237be868c383e14b9a541d19ce))

- Enhance model creation logic to check for history model existence before creation
  ([`2380248`](https://github.com/TimKleindick/general_manager/commit/2380248801b3fba714377fe2d545ecc6154f856b))

- Ensure old values are cleared after data change signals
  ([`d6c801c`](https://github.com/TimKleindick/general_manager/commit/d6c801cc3f2dd4ec3e654bc5849907d2da1169d0))

- Handle None list sources by falling back to fallback_manager_class.all() before filtering.
  ([`d6ea118`](https://github.com/TimKleindick/general_manager/commit/d6ea1183ec2acb7ee053bacb3990a198393f2dc7))

- Handle typing generics safely
  ([`ebf330b`](https://github.com/TimKleindick/general_manager/commit/ebf330b177b14aff53ac935731183b8cc99b9741))

- Improve attribute resolution in Rule class and enhance error handling
  ([`edcc7be`](https://github.com/TimKleindick/general_manager/commit/edcc7beb6e8073e0941a3b9a54f8f083dfa66025))

- Improve error handling in ReadOnlyInterface for missing unique fields and enhance test setup with
  FakeField class
  ([`2d817e9`](https://github.com/TimKleindick/general_manager/commit/2d817e9a18cbf86327da32940d9f378a54536b5d))

- Improve error message for InvalidPermissionClassError and add function_name to
  InvalidFunctionNodeError
  ([`35ee382`](https://github.com/TimKleindick/general_manager/commit/35ee382d4aa45e77602640dd0e44752cdd9f1a9b))

- Improve error message formatting in PermissionCheckError
  ([`1e6e921`](https://github.com/TimKleindick/general_manager/commit/1e6e921d93dfe2bc426d153f73f1a2ce8afdddad))

- Improve permission class validation and error handling in GeneralManager and AutoFactory
  ([`f3f74ae`](https://github.com/TimKleindick/general_manager/commit/f3f74aecf4f08cb1cfa79f018d804edc27d45a0a))

- Marked warnings from new linter rules
  ([`e4c36f5`](https://github.com/TimKleindick/general_manager/commit/e4c36f50042a2ba1c54d8241cd625ba6b9471f37))

- Namespace collision
  ([`f0b40fe`](https://github.com/TimKleindick/general_manager/commit/f0b40fe7dc526e1ab49caa0e5925a426238fdaa0))

- Raise InvalidGeneratedObjectError for non-model instances in AutoFactory
  ([`2a25ac6`](https://github.com/TimKleindick/general_manager/commit/2a25ac69f1c3c165fe84ea0562ef1648ea3564de))

- Refactor error handling in GraphQL and mutation to raise exceptions instead of returning error
  states
  ([`8492bc3`](https://github.com/TimKleindick/general_manager/commit/8492bc30ffc110d3875efde4f5cc8b481d5ebbd4))

- Remove hardcoded password from user creation in tests
  ([`b18ae37`](https://github.com/TimKleindick/general_manager/commit/b18ae37834a1fb5a6b2a8b6c0b957d51d67f4dbb))

- Update __new__ method signature in PathMap class to accept additional arguments
  ([`c81f2b2`](https://github.com/TimKleindick/general_manager/commit/c81f2b2c64198b230813b5ef410ad9040d6b9bed))

- Update currency comparison to use string representation for accuracy
  ([`0524b95`](https://github.com/TimKleindick/general_manager/commit/0524b952312d5fe909370accc5955275744c4814))

- Update filter and exclude attributes in CalculationBucket for clarity
  ([`a118cf2`](https://github.com/TimKleindick/general_manager/commit/a118cf2eff2a4aa226307ed8a6df317aa096cb74))

### Continuous Integration

- Add ignore rule for ARG004 in Ruff linting configuration
  ([`f35d0ab`](https://github.com/TimKleindick/general_manager/commit/f35d0ab6e0af0df6ba400f49a61b191fd7be2d96))

- Add linting and type checking workflow using Ruff and pre-commit
  ([`bcc3d69`](https://github.com/TimKleindick/general_manager/commit/bcc3d695927016811d275f884f8cb7ec5241271f))

- Add pre-commit configuration and update dependencies for improved linting and formatting
  ([`29a182b`](https://github.com/TimKleindick/general_manager/commit/29a182bab7bd2efd4760c3069964811169c5b80d))

- Change docs workflow to check on pull requests in main and publish on push in main
  ([`5981b9e`](https://github.com/TimKleindick/general_manager/commit/5981b9e852c22e74b8712133ba4eae45bcd96103))

- Pin ruff version to 0.14.1 in lint workflows
  ([`3662660`](https://github.com/TimKleindick/general_manager/commit/3662660ef0421a82dda6763145337057a8fd513f))

- Specify configuration file for Ruff linting and formatting checks
  ([`8054d27`](https://github.com/TimKleindick/general_manager/commit/8054d279a89c0f1b30f7602222fb5ee500f41c24))

### Features

- Add support for right-side arithmetic operations in Measurement class
  ([`8166c1e`](https://github.com/TimKleindick/general_manager/commit/8166c1ed486cfe0a1498ecd873f0872679ece290))

### Refactoring

- Update type hints to fix mypy errors
  ([`22f3d2d`](https://github.com/TimKleindick/general_manager/commit/22f3d2d1ba753d0b5bf39eb1565ebd7d8a38022b))

### Testing

- Add additional unit tests for edge cases
  ([`8166c1e`](https://github.com/TimKleindick/general_manager/commit/8166c1ed486cfe0a1498ecd873f0872679ece290))

- Enhance DummyPermission class with permission handling and filtering logic
  ([`8166c1e`](https://github.com/TimKleindick/general_manager/commit/8166c1ed486cfe0a1498ecd873f0872679ece290))

- Enhance get_field_value and get_many_to_many_field_value with improved type handling and validation
  ([`8166c1e`](https://github.com/TimKleindick/general_manager/commit/8166c1ed486cfe0a1498ecd873f0872679ece290))

- Fix test errors
  ([`8166c1e`](https://github.com/TimKleindick/general_manager/commit/8166c1ed486cfe0a1498ecd873f0872679ece290))

- Improve exception message formatting and handling in Measurement tests
  ([`8166c1e`](https://github.com/TimKleindick/general_manager/commit/8166c1ed486cfe0a1498ecd873f0872679ece290))

- Remove duplicated tests
  ([`25cd40b`](https://github.com/TimKleindick/general_manager/commit/25cd40b58ed75cf1810908e8f873d182c8f1cd66))

- Replace SystemRandom with Random for consistent random number generation in tests
  ([`da2a20d`](https://github.com/TimKleindick/general_manager/commit/da2a20dad4f171a0abfef6bbdaf8fc990148a441))

- Update error message for age validation in RuleTests for clarity
  ([`4650a6b`](https://github.com/TimKleindick/general_manager/commit/4650a6b4143c36b8b654d5766fcf9526d700982c))

- Update exception handling and improve identification logic in CalculationBucket tests
  ([`8166c1e`](https://github.com/TimKleindick/general_manager/commit/8166c1ed486cfe0a1498ecd873f0872679ece290))

- Update test for non-existent attribute to raise AttributeError
  ([`794d378`](https://github.com/TimKleindick/general_manager/commit/794d378de8461b8dc2ee1a5df355d9d5e28785d1))


## v0.17.0 (2025-10-19)

### Bug Fixes

- Correct cache invalidation for multiple filter params at once.
  ([`1233bdf`](https://github.com/TimKleindick/general_manager/commit/1233bdfe635aa52871eeb332fe2a3adc828f4d85))

- Tighten literal eval handling in dependency index
  ([`d2b82d1`](https://github.com/TimKleindick/general_manager/commit/d2b82d1354f203b43dff5ee4067907540f066b55))

- Update import path for GraphQLProperty in group_manager and test files
  ([`f97cc4f`](https://github.com/TimKleindick/general_manager/commit/f97cc4f342c3e987e23c131700f63c1e926113e2))

- Update setuptools version and enhance project metadata
  ([`a6ab3e5`](https://github.com/TimKleindick/general_manager/commit/a6ab3e548da04033842215c4709ff29d1b7e8e73))

### Documentation

- Add explanation for composite filters and excludes in caching
  ([`0a97a09`](https://github.com/TimKleindick/general_manager/commit/0a97a09fb30045be0da2a13fc216bc0fc2f92684))

- Add subscriptions guide to GraphQL concepts and update navigation
  ([`6b8fbf7`](https://github.com/TimKleindick/general_manager/commit/6b8fbf7481fdcbcc95959005149a92d07cccc825))

- Update path to additional dependencies in README
  ([`4880c8f`](https://github.com/TimKleindick/general_manager/commit/4880c8fcb488af0afecd45dd217cf646965302a1))

### Features

- Automatic ASGI websocket routing for GraphQL subscriptions when setting AUTOCREATE_GRAPHQL
  ([`c973577`](https://github.com/TimKleindick/general_manager/commit/c973577ce25f5756bc2e49ffba93cddb7c4504d3))

- Enhance GraphQL subscription handling with property resolution and dependency tracking
  ([`bf5eaea`](https://github.com/TimKleindick/general_manager/commit/bf5eaea57d9ccfac638256133095c38aeca717fc))

- Implement GraphQL subscription handling with websocket support and add related tests
  ([`3e0526c`](https://github.com/TimKleindick/general_manager/commit/3e0526c3514297a90d31a7c301e8a80f1753dd6e))

- Implement GraphQL subscriptions for calculation and database updates
  ([`5259796`](https://github.com/TimKleindick/general_manager/commit/5259796a2d0c793c68adca373794661b741a83d7))

### Refactoring

- Update identification fields to use graphene.Argument for improved clarity
  ([`e41d132`](https://github.com/TimKleindick/general_manager/commit/e41d132a9e8e45bff05f1acb17cd6e6dd4110172))

### Testing

- Add caching tests for project name duplicates and exclusion dependencies
  ([`d55353d`](https://github.com/TimKleindick/general_manager/commit/d55353d7dfb9a78585e5ed01bc8c7243c897d507))

- Add GraphQL subscription property selection tests
  ([`bcbbc71`](https://github.com/TimKleindick/general_manager/commit/bcbbc71fc82668cb120d437026554e195842b776))

- Add tests for managers without GraphQL properties and interface
  ([`2a4104b`](https://github.com/TimKleindick/general_manager/commit/2a4104b3ff9d26572c98a168114568a6498907b6))

- Add unit tests for caching edge cases
  ([`85198b1`](https://github.com/TimKleindick/general_manager/commit/85198b1617460023d6ec5b56b2fe971cbf170618))

- Add unit tests for composite parameter tracking and cache key removal
  ([`0aeeadf`](https://github.com/TimKleindick/general_manager/commit/0aeeadf7cca92fd73189a108b40ed883305a0798))

- Add unit tests for GraphQL subscription consumer and helper methods
  ([`96d2b79`](https://github.com/TimKleindick/general_manager/commit/96d2b792aea3e2a8bb3aa70620e97fa63917845d))

- Extend caching test bench with more complex testcases
  ([`b141dc3`](https://github.com/TimKleindick/general_manager/commit/b141dc34ce55e9095ac36c18e2ef8a184967026a))

- Improve graphql subscription handling tests for better coverage and add edge cases
  ([`f4ae813`](https://github.com/TimKleindick/general_manager/commit/f4ae81326c83ab20c5dd8f5739df00a20ced8f91))

- Preserve and restore original signal receivers in tests
  ([`7c4f409`](https://github.com/TimKleindick/general_manager/commit/7c4f409001a9f272d836de45cee198ca7f71683f))

- Reset GraphQL._page_type_registry to prevent cross-test state leakage.
  ([`bf25488`](https://github.com/TimKleindick/general_manager/commit/bf25488c74f2e6e0fcb0b02e8e613a9e1d7c4c03))


## v0.16.1 (2025-10-14)

### Bug Fixes

- Update setuptools version and enhance project metadata
  ([`a6ab3e5`](https://github.com/TimKleindick/general_manager/commit/a6ab3e548da04033842215c4709ff29d1b7e8e73))

### Documentation

- Update path to additional dependencies in README
  ([`4880c8f`](https://github.com/TimKleindick/general_manager/commit/4880c8fcb488af0afecd45dd217cf646965302a1))


## v0.16.0 (2025-10-14)

### Bug Fixes

- Sort __all__ to satisfy Ruff (RUF022).
  ([`87d4b72`](https://github.com/TimKleindick/general_manager/commit/87d4b7249e3ea957614c17ab7695b253c1ee4091))

### Continuous Integration

- Add ignore rule for type files in code coverage configuration
  ([`87f68df`](https://github.com/TimKleindick/general_manager/commit/87f68df01234dca67531eee8c955a7dfaed44f69))

### Documentation

- Comment out unused links in documentation and remove roadmap file
  ([`ea954dc`](https://github.com/TimKleindick/general_manager/commit/ea954dc02d9ecfe100989d244d82bce68c35551c))

- Fix punctuation in documentation section
  ([`d56d18d`](https://github.com/TimKleindick/general_manager/commit/d56d18da09ad892f041a8d98edfc3a94e2049fe0))

- Update README to enhance overview and key features sections
  ([`1280238`](https://github.com/TimKleindick/general_manager/commit/1280238c28e370c8dd0f6b5ce795551bda5e8503))

### Features

- Add central registry for lazy public API exports
  ([`757213a`](https://github.com/TimKleindick/general_manager/commit/757213ab5ec6753f1cc0c3bdc509b25f618fe401))

- Expand public API exports with additional lazy factory methods and utility types
  ([`6b18a49`](https://github.com/TimKleindick/general_manager/commit/6b18a4927305a6e39809f79458bbe9035fcfea68))

- Implement script to generate type-only helper modules for lazy public APIs
  ([`7fe14ec`](https://github.com/TimKleindick/general_manager/commit/7fe14eccd8177b127ff59394759df6a4752f2503))

- Refactor public API exports to use centralized registry for improved maintainability
  ([`999248f`](https://github.com/TimKleindick/general_manager/commit/999248f0ea7f5e4b4fafa536687fe695354f7c2d))

### Testing

- Start public API snapshot generation and testing
  ([`584cf1c`](https://github.com/TimKleindick/general_manager/commit/584cf1c6c6747efc2cd0c1b607faacbbfdfe7f88))


## v0.15.1 (2025-10-12)

### Bug Fixes

- Update GitPython version to 3.1.44 in requirements
  ([`615b388`](https://github.com/TimKleindick/general_manager/commit/615b388f3067571430fa328a5741cf669bb5d07b))

### Continuous Integration

- Add missing installation of development requirements in workflow
  ([`32caf7a`](https://github.com/TimKleindick/general_manager/commit/32caf7a62d649810836b65c710917f9f991cf3a6))


## v0.15.0 (2025-10-12)

### Bug Fixes

- Avoid zero-arg super() inside the nested helper.
  ([`521e511`](https://github.com/TimKleindick/general_manager/commit/521e511916aa3ca47a8b76edbffe7e15a5d38db7))

- Enforce JSON data type validation in ReadOnlyInterface
  ([`3432f56`](https://github.com/TimKleindick/general_manager/commit/3432f567e4358313f6bd72938fea58eca2d51279))

- Improve random value generation in lazy_measurement and handle date range in lazy_date_between and
  lazy_date_time_between
  ([`4c20389`](https://github.com/TimKleindick/general_manager/commit/4c20389adb308c2b2eee227676dcf7c00c0598e9))

- Lazy-loading public API utilities and update module exports
  ([`bd169a1`](https://github.com/TimKleindick/general_manager/commit/bd169a1d276404e3d5c19b38f3c688cec9152a5c))

- Refactor and enhance type hints across the GeneralManager module
  ([`41149de`](https://github.com/TimKleindick/general_manager/commit/41149dee92253e505b65d115a0f85a29f6b0dc39))

- Remove redundant docstring and unused imports in measurement module
  ([`e9f2d81`](https://github.com/TimKleindick/general_manager/commit/e9f2d8121390b9ed829edff71e720b26ff4ffcd0))

- Rename 'admin' permission to 'isAdmin' and add 'isSelf' permission check
  ([`0a51970`](https://github.com/TimKleindick/general_manager/commit/0a51970f4aea7610b6cd7ac96cf8780d01b3d963))

- Simplify source extraction in Rule class constructor
  ([`a7cf2ac`](https://github.com/TimKleindick/general_manager/commit/a7cf2ac7d0cfc84cec63c62e256491cce0942809))

### Continuous Integration

- Add Codecov configuration for coverage reporting
  ([`f0942b5`](https://github.com/TimKleindick/general_manager/commit/f0942b55656f0289ca95ce2fb9fa2338132ffab9))

- Add GitHub Actions workflow for publishing and releasing the project
  ([`d4d6aa9`](https://github.com/TimKleindick/general_manager/commit/d4d6aa982abbf0099ecbaea73ad7c8d3e36c97f5))

- Add GitHub Actions workflow for testing with Python
  ([`97ae011`](https://github.com/TimKleindick/general_manager/commit/97ae011af78345d9c4a5a9dc0a76790d5b92c312))

- Remove unused target parameter from Codecov configuration
  ([`999ab53`](https://github.com/TimKleindick/general_manager/commit/999ab53555d3d7e18f8d936bde90909fb90e6723))

- Update Codecov action to version 5.5.1
  ([`cba447f`](https://github.com/TimKleindick/general_manager/commit/cba447fc5bc77372a59b79a5db0917900d87953b))

- Update Codecov action to version 5.5.1
  ([`ec2f747`](https://github.com/TimKleindick/general_manager/commit/ec2f7475fd066ef4f70ef6957d1f02c8f2e0fd90))

### Documentation

- Add logo.svg and mkdocs configuration for documentation
  ([`7c82605`](https://github.com/TimKleindick/general_manager/commit/7c82605d2e27aaf422461f9eba250c9f1a4a79c7))

- Init mkdocs documentation
  ([`570bb94`](https://github.com/TimKleindick/general_manager/commit/570bb949c185f85bb0ca730afe9ccd47a3478ddf))

- Update docstrings for every function
  ([`ceca046`](https://github.com/TimKleindick/general_manager/commit/ceca04652a648dd68dc94cb4620629f95571f861))

- Update license description in README.md to reflect MIT License
  ([`504469e`](https://github.com/TimKleindick/general_manager/commit/504469e9fb6ffc1dbded77136a1c3097cf3201ce))

- Update type hints for ClassVar in GeneralManagerMeta
  ([`f22fddf`](https://github.com/TimKleindick/general_manager/commit/f22fddffc5576fac03302e78b4b9b2b8d143f42d))

### Features

- Add easier core components and utilities imports for GeneralManager, including API, bucket, cache,
  factory, interface, measurement, permission, rule, and utility modules
  ([`5e76383`](https://github.com/TimKleindick/general_manager/commit/5e7638364834e73f954c91389ddae891b24fe97d))

### Testing

- Add UTC timezone to old date in DBBasedInterfaceTestCase and update docstrings for clarity
  ([`f7f0266`](https://github.com/TimKleindick/general_manager/commit/f7f0266b2487370a519c8abb55adc708242e37cc))

- Update permission checks from 'admin' to 'isAdmin' in CustomManagerBasedPermission classes
  ([`4a5dfd9`](https://github.com/TimKleindick/general_manager/commit/4a5dfd9bbce0a29d566fb5c3a552068ca7e77236))


## v0.14.1 (2025-10-04)

### Bug Fixes

- Update Django dependency version to 5.2.7 in pyproject.toml
  ([`a996ce0`](https://github.com/TimKleindick/general_manager/commit/a996ce06e47d0111e6ecbd5d594b6ba71d81f121))

- Update Django version to 5.2.7 in requirements
  ([`9b70d59`](https://github.com/TimKleindick/general_manager/commit/9b70d5970cdd93323cea0b65b2137868c6a53d80))


## v0.14.0 (2025-10-03)

### Bug Fixes

- Enhance GraphQLProperty to enforce return type hints and improve type resolution
  ([`d771f2d`](https://github.com/TimKleindick/general_manager/commit/d771f2d95690fd5f34cc70b0052d475526cea827))

- Improve type handling in GraphQL class for Union types
  ([`477599b`](https://github.com/TimKleindick/general_manager/commit/477599bde1492862752229aa385f5cdd23ec0e2e))

- Remove unnecessary type hint resolution in GraphQLProperty initialization
  ([`a0a1337`](https://github.com/TimKleindick/general_manager/commit/a0a13375e2237d2953471faf0f9cf121cc1d9fa6))

### Testing

- Implement type hints for graphQlProperties
  ([`67c364b`](https://github.com/TimKleindick/general_manager/commit/67c364b6cb0e6ffb9bbbadb3c2cede6370a1434d))

- Simplify type hint for unrelated_connection in PathMappingUnitTests
  ([`623b4b1`](https://github.com/TimKleindick/general_manager/commit/623b4b1fc181d43a5c42cd399671fe35eddae6f1))


## v0.13.1 (2025-08-05)

### Bug Fixes

- Correct import statement for Bucket class
  ([`cbd5f6d`](https://github.com/TimKleindick/general_manager/commit/cbd5f6d990dc59dc05d5229bb3714621ffb188d8))

- Ensure path existence check in PathMap methods
  ([`ad5700c`](https://github.com/TimKleindick/general_manager/commit/ad5700ceb03ff95951061eb77585476c718ce6fb))

- Handle None attribute types in PathTracer connections
  ([`bff43f2`](https://github.com/TimKleindick/general_manager/commit/bff43f267e44a16acd16c1a0d82c8b375fb50918))

- Simplify path tracer retrieval logic in PathMap
  ([`bd6e327`](https://github.com/TimKleindick/general_manager/commit/bd6e327fb0253697b38a9de353d81b8d9061a0fb))

### Refactoring

- Clean up whitespace and improve path mapping documentation
  ([`e0784cf`](https://github.com/TimKleindick/general_manager/commit/e0784cf43231381c11a7e7d184e2a75d6e9be814))

- Clean up whitespace and improve readability in PathMapping unit tests
  ([`e796941`](https://github.com/TimKleindick/general_manager/commit/e7969414007459eaa956e879df0ee369d2e58263))

- Reorganize imports and simplify test setup in path_mapping tests
  ([`c5656e6`](https://github.com/TimKleindick/general_manager/commit/c5656e685e7d990b74ec3d1d44004764ef38fa1b))

### Testing

- Add integration and unit tests for PathMap functionality
  ([`fb0e080`](https://github.com/TimKleindick/general_manager/commit/fb0e080036ff787e087c38b6448a99a5506dc810))


## v0.13.0 (2025-08-03)

### Bug Fixes

- Apply permission filters to queryset in GraphQL class
  ([`5125e33`](https://github.com/TimKleindick/general_manager/commit/5125e33f494fbee969d90f3af172e04b9d264d86))

- Improve type checking for based_on object in ManagerBasedPermission
  ([`95c483e`](https://github.com/TimKleindick/general_manager/commit/95c483e70a41e591b945505f5d1f02e30a57e014))

- Refactor permission attributes initialization and handle based_on condition
  ([`2e21b3b`](https://github.com/TimKleindick/general_manager/commit/2e21b3b64619942de5a011868f2600107f7245c3))

- Remove unnecessary blank lines in test_calculation_bucket.py
  ([`3117b2a`](https://github.com/TimKleindick/general_manager/commit/3117b2afcf7b789a7a8cdd1d3e24f691710bdddc))

- Remove unused import statement and clean up
  ([`784c174`](https://github.com/TimKleindick/general_manager/commit/784c1742dbe304bd60f7c881c911e2641ca33c0d))

- Remove unused import statement in GraphQL class
  ([`8bab0a3`](https://github.com/TimKleindick/general_manager/commit/8bab0a354fec0c94b6f21ebfd3d1a13b5ac386cf))

- Remove unused permission inheritance test method
  ([`b987be3`](https://github.com/TimKleindick/general_manager/commit/b987be3cedfd58381acb48ca8575cb77f548acef))

- Update condition to check for None or NOT_PROVIDED in many-to-many kwargs
  ([`ca779b6`](https://github.com/TimKleindick/general_manager/commit/ca779b616f208cf28ddf1a258483f151337fbdce))

### Features

- Add 'matches' permission function to permission_functions
  ([`542e820`](https://github.com/TimKleindick/general_manager/commit/542e82056a5b5085ae75359d63d6066c82647fd0))

- Add `none` method to Bucket class and import QuerySet
  ([`ca3c8b5`](https://github.com/TimKleindick/general_manager/commit/ca3c8b5b5b14099f8789fc6914216e81def42738))

- Refactor permission initialization and add difference between None and no result
  ([`94d4809`](https://github.com/TimKleindick/general_manager/commit/94d480934097bd51106ad9d04e702e7887466e3c))

### Refactoring

- Clean up code formatting and improve permission validation tests
  ([`2070a60`](https://github.com/TimKleindick/general_manager/commit/2070a608306cbddd92a53e0cceec3a09808f93bc))

### Testing

- Add integration tests for manager-based permissions and family updates
  ([`54eebde`](https://github.com/TimKleindick/general_manager/commit/54eebde4906d1386a731f9c336686803aeb535de))


## v0.12.2 (2025-07-28)

### Bug Fixes

- Add __parse_kwargs method for keyword argument parsing in DBBasedInterface
  ([`0fa60ce`](https://github.com/TimKleindick/general_manager/commit/0fa60ce283fe64e01929c30df9b5aa9bd158b24e))

- Correct conditional structure in related model handling
  ([`6a6269b`](https://github.com/TimKleindick/general_manager/commit/6a6269ba1a700a4ba885d8c60109d8c545da24bf))

- Debug related fields filtering in DBBasedInterface
  ([`8a39aff`](https://github.com/TimKleindick/general_manager/commit/8a39affa4fda403a0bafa71cefa62f7d5804c6cf))

- Enhance many-to-many relationship handling in DatabaseInterface
  ([`0b2de0d`](https://github.com/TimKleindick/general_manager/commit/0b2de0d56e140abeaae7004713ef5cb131ec8f8c))

- Enhance related field handling in DBBasedInterface
  ([`df92257`](https://github.com/TimKleindick/general_manager/commit/df92257365fe7190d0494d13b13d608f8e5ff6f2))

- Ensure rules attribute defaults to an empty list in get_full_clean_methode
  ([`afd46ec`](https://github.com/TimKleindick/general_manager/commit/afd46ecacdf06c3f68a5dea9f275be7c999a0a1f))

- Update is_required logic to consider default value in DBBasedInterface
  ([`9f9233d`](https://github.com/TimKleindick/general_manager/commit/9f9233dd31ea2781780588bad6fe68302d6754a2))

### Testing

- Add testcase for ManyToMany
  ([`03aeaf5`](https://github.com/TimKleindick/general_manager/commit/03aeaf5230749c58cc76a854b0558c965b9247ba))

- Enhance integration tests with tearDown method and additional validations
  ([`e895d8b`](https://github.com/TimKleindick/general_manager/commit/e895d8bac4e0cc7f6a2c1768cc9b7036d7ce4792))

- Update exception handling and improve attribute access in DBBasedInterface tests
  ([`d6f3864`](https://github.com/TimKleindick/general_manager/commit/d6f3864a4ea6fdfc8e42f77b8b862e9b89e5d169))

- Update human count checks to filter only active humans in delete operation tests
  ([`bc07aef`](https://github.com/TimKleindick/general_manager/commit/bc07aef25d9b122f0ae5897d44b95387a6ae4d70))


## v0.12.1 (2025-07-26)

### Bug Fixes

- Adjust measurement handling
  ([`6385845`](https://github.com/TimKleindick/general_manager/commit/6385845e33715502d5b202072496f18a7b731af3))

- Correct argument order in MeasurementField initialization
  ([`ac039b1`](https://github.com/TimKleindick/general_manager/commit/ac039b1337e19b04944cd84efbf2f54189740bf2))

- Enhance error handling in MeasurementField validation for string and dimensionality errors
  ([`1c7bb67`](https://github.com/TimKleindick/general_manager/commit/1c7bb67d4bd0904c869dde27273192b98a746310))

- Implement MeasurementScalar for GraphQL handling of Measurement types
  ([`c7df284`](https://github.com/TimKleindick/general_manager/commit/c7df2841a2ac5c9dca42bc8f8d73697312d8bfe7))

- Improve compare with None and empty value handling
  ([`f22849f`](https://github.com/TimKleindick/general_manager/commit/f22849f845e21d9fc32e13fc1fa622c707a4b9d9))

- Improve error handling in MeasurementField validation for incompatible units
  ([`a823b4e`](https://github.com/TimKleindick/general_manager/commit/a823b4e9638871594657432d32f727da1098f7b6))

- Improve error message for incompatible units in MeasurementField validation
  ([`89f74a3`](https://github.com/TimKleindick/general_manager/commit/89f74a380e0adceb26905cde8821e5919c4560c9))

- Update empty_values to allow more empty representations and clean up docstring formatting
  ([`fe26eae`](https://github.com/TimKleindick/general_manager/commit/fe26eaedf4850ae8179dd6c3008d1e749abd07a0))

- Use measurement field as unified api for two db fields
  ([`5a07369`](https://github.com/TimKleindick/general_manager/commit/5a07369397b4340f4c21a76f4a3e2e427fdd0562))

### Testing

- Add integration tests for measurement field functionality
  ([`8f422b7`](https://github.com/TimKleindick/general_manager/commit/8f422b7dadb46dcfe677819cf3600a14e290b534))

- Persist measurement operations in db
  ([`54d8c6a`](https://github.com/TimKleindick/general_manager/commit/54d8c6a04d2089d7abcc6746196e6b962c559a6b))

- Remove unnecessary special handling for graphql measurement handling
  ([`a0baf07`](https://github.com/TimKleindick/general_manager/commit/a0baf07b3a3506d8fb7f4fde4ab966d3694a4b0f))

- Update GraphQL mutations to use MeasurementScalar for budget parameter
  ([`b4068b4`](https://github.com/TimKleindick/general_manager/commit/b4068b425556fbfc510abc399229b55efcfab045))


## v0.12.0 (2025-07-20)

### Bug Fixes

- Change TypeError to ValueError for invalid measurement value and clean up multiplication docstring
  ([`13ba835`](https://github.com/TimKleindick/general_manager/commit/13ba8357076d661de6fa9498e24cc684563712eb))

- Send page information in response header
  ([`7b0f2a9`](https://github.com/TimKleindick/general_manager/commit/7b0f2a953dc1283f655d36121fbd4d0a8be5d3b8))

- Streamline error handling in GraphQL mutations and remove unused error fields
  ([`3649e63`](https://github.com/TimKleindick/general_manager/commit/3649e6309868c0c10e8f490140a45911bceab696))

### Documentation

- Update user documentation with runnable examples
  ([`c109a47`](https://github.com/TimKleindick/general_manager/commit/c109a47a64ab48914b8afd289d5268fca926c6ba))

### Features

- Implement pagination support in GraphQL queries with PageInfo type
  ([`768042d`](https://github.com/TimKleindick/general_manager/commit/768042d283b2e14a5bd85efd3227d024fa62348e))

### Testing

- Add integration tests for GraphQL query pagination and project list retrieval
  ([`f944ddb`](https://github.com/TimKleindick/general_manager/commit/f944ddb1a06fb10d9495a63b0c04d17bb7aa4e39))

- Remove assertions for mutation success in GraphQL mutation tests
  ([`86d3b81`](https://github.com/TimKleindick/general_manager/commit/86d3b811cf0e9ea91af01564fc32d88a1f29ec52))

- Remove unused error fields from mutation tests and update error handling
  ([`71b9f47`](https://github.com/TimKleindick/general_manager/commit/71b9f47ed52f88ec910e88cf17d48d5860746e08))

- Remove unused import of IntegerField in GraphQL query tests
  ([`08b89ba`](https://github.com/TimKleindick/general_manager/commit/08b89ba7a10285640c49a4c45ac6c3718ea1d9f5))

- Update assertion in GraphQL resolver test to check for 'items' key in result
  ([`622ab79`](https://github.com/TimKleindick/general_manager/commit/622ab79c0f85ad34205bbaff2d20d55cfb5db283))


## v0.11.2 (2025-07-19)

### Bug Fixes

- Differentiate between rule result None and False
  ([`26cda15`](https://github.com/TimKleindick/general_manager/commit/26cda157ceff549718f288c445bac85067ff17fa))

### Testing

- Add rule tests for handling None values
  ([`db6a104`](https://github.com/TimKleindick/general_manager/commit/db6a104d37bf222c32d18abdac035325e6c5c9b0))


## v0.11.1 (2025-07-18)

### Bug Fixes

- Handle none values in foreignkey relations to manager
  ([`90bd4bd`](https://github.com/TimKleindick/general_manager/commit/90bd4bdb4d181445d361f90137929be3fe7548c7))

- Implement equality check for GeneralManager based on identification
  ([`ffa41a0`](https://github.com/TimKleindick/general_manager/commit/ffa41a0e667b51dada9fd87a6f384a947be841b6))

### Testing

- Add integration tests for DatabaseManager functionality
  ([`a768d2c`](https://github.com/TimKleindick/general_manager/commit/a768d2c86ec56890a2be71965c80943b8f96e3f1))

- Refine type annotations and improve code formatting in ReadOnlyIntegrationTest
  ([`804faaf`](https://github.com/TimKleindick/general_manager/commit/804faaf2ec3f542b0be7ec1205443415e6a8fda0))


## v0.11.0 (2025-07-16)

### Bug Fixes

- Correct attribute access in __get_attribute_permissions and check_permission methods
  ([`36b5796`](https://github.com/TimKleindick/general_manager/commit/36b5796e65f677fbe0d2b1a36554bba81ec659e5))

- Remove unused type imports in mutation_permission.py
  ([`920d5f7`](https://github.com/TimKleindick/general_manager/commit/920d5f782353ec87fbc6664b53e4e73cae3b3ce9))

- Stop enforcing manager requirement for dict permission_data
  ([`6c1ca1f`](https://github.com/TimKleindick/general_manager/commit/6c1ca1fca5b30ce31055908ab6eacb77f756dbb9))

- User handling
  ([`4a48d04`](https://github.com/TimKleindick/general_manager/commit/4a48d04f13b3b4678a0da1f7a30dbfe23679dcc6))

### Features

- Implement MutationPermission class for mutation permission handling
  ([`c39ddf5`](https://github.com/TimKleindick/general_manager/commit/c39ddf51c5b41591b7fea2216e2f9dd0076191d5))

- Update graph_ql_mutation to use permission parameter and improve authentication handling
  ([`c7064c7`](https://github.com/TimKleindick/general_manager/commit/c7064c7789ba9691585900783bec504067656b35))

### Refactoring

- Move permission validation logic to utils and simplify permission handling
  ([`0cce6b1`](https://github.com/TimKleindick/general_manager/commit/0cce6b14d49d0d1d16816b36f28d7d66b75accc8))

- Simplify initialization and improve error messaging in ManagerBasedPermission
  ([`43a8307`](https://github.com/TimKleindick/general_manager/commit/43a8307259158177a981eb54111160ecea7a7331))

### Testing

- Reset authentication check
  ([`1c97c0c`](https://github.com/TimKleindick/general_manager/commit/1c97c0c0d20b5b22328c06bd82751eca8b8e82fa))

- Update test cases for mutation permission handling
  ([`23db748`](https://github.com/TimKleindick/general_manager/commit/23db748055b68740c1ae89f60852671da246f3af))


## v0.10.6 (2025-07-13)

### Bug Fixes

- Correct multiplication check for currency measurements in `Measurement` class
  ([`95c78ab`](https://github.com/TimKleindick/general_manager/commit/95c78abbbf839db434b68cb8edb0f658f99db639))

- Improve GraphQL interface methods to handle optional return types and clean up code
  ([`00baa23`](https://github.com/TimKleindick/general_manager/commit/00baa23776f67813447a5d5efe4a285f7080c724))

### Documentation

- Inline example and remove README reference
  ([`f76582b`](https://github.com/TimKleindick/general_manager/commit/f76582bdd2ff7e6f8ada03293e580a008442f8e7))

### Testing

- Add integration tests for calculation Interface with only one Input
  ([`2bee3a7`](https://github.com/TimKleindick/general_manager/commit/2bee3a7610df8f26b11fedf302d07688d5c716cd))

- Add tests for arithmetic operations between currency and dimensionless values
  ([`95c8a6f`](https://github.com/TimKleindick/general_manager/commit/95c8a6f036b768858c92ae1e30a3a3042c10dd17))


## v0.10.5 (2025-07-10)

### Bug Fixes

- Add "is_derived" attribute to input field types and improve docstrings for clarity
  ([`6252f37`](https://github.com/TimKleindick/general_manager/commit/6252f370432c9e90380e4c0fce5b90be9eff1652))

- Remove unnecessary blank lines and improve variable naming in `get_field_type`
  ([`3eeb841`](https://github.com/TimKleindick/general_manager/commit/3eeb8410d90c54bfe7f400c66d799b84808e9199))


## v0.10.4 (2025-07-10)

### Bug Fixes

- Update __contains__ method to check primary key instead of object instance
  ([`cfeb1ae`](https://github.com/TimKleindick/general_manager/commit/cfeb1aecd03da082e7290e514c1a2dff4f866807))

### Chores

- Add django-types to development requirements for enhanced type support
  ([`8bf1aaa`](https://github.com/TimKleindick/general_manager/commit/8bf1aaafb459bc55a0d39386420b7a95262d3b60))

### Refactoring

- Enhance type hints and improve related model retrieval in factories.py
  ([`739dbd0`](https://github.com/TimKleindick/general_manager/commit/739dbd047597a737ebdd0f31bb26e2b8b3ca06ef))

- Improve type hinting and simplify function call logic in data_change decorator
  ([`43efe90`](https://github.com/TimKleindick/general_manager/commit/43efe90ddfafe030d1c96c26765f26c82f747eb9))

- Improve type hinting for value_field and unit_field in MeasurementField for clarity
  ([`5cc850f`](https://github.com/TimKleindick/general_manager/commit/5cc850f7ea3b3cca7f761330fef7719b781f1bf3))

- Remove unnecessary blank lines and improve type hints in AutoFactory class
  ([`7649d4e`](https://github.com/TimKleindick/general_manager/commit/7649d4e3e73d5089b69557fef792a71f46bb85b4))

- Remove unnecessary blank lines in GraphQL class methods for improved readability
  ([`728a44c`](https://github.com/TimKleindick/general_manager/commit/728a44c59d83581624e566ea0f438a50e39a4ea6))

- Simplify type hints and improve data handling in DatabaseBucket class
  ([`600a1cd`](https://github.com/TimKleindick/general_manager/commit/600a1cd6b4fde80677929ea65495227bdb5780d7))

- Simplify type hints in various classes for improved clarity and consistency
  ([`a12cf10`](https://github.com/TimKleindick/general_manager/commit/a12cf10c34ee8384e57560a3055c88ab44e82211))

- Update type hints for args and kwargs in MeasurementField for improved clarity
  ([`5c482bf`](https://github.com/TimKleindick/general_manager/commit/5c482bf5ff9a1bd73a7e714249f2ef6521e59345))

- Update type hints for kwargs in AutoFactory methods for improved clarity
  ([`ba0a5eb`](https://github.com/TimKleindick/general_manager/commit/ba0a5eb54ff09ca642967cffcafc618c9bb3e456))

- Update type hints for kwargs in multiple functions for improved clarity
  ([`90f14f2`](https://github.com/TimKleindick/general_manager/commit/90f14f278f3b6d718e18999923c87c5def251644))


## v0.10.3 (2025-07-09)

### Bug Fixes

- Add missing import for InterfaceBase and update type hint for __or__ method
  ([`42b04de`](https://github.com/TimKleindick/general_manager/commit/42b04de746f6db1015b3f905c241c4336bfe8efc))

- Clean up code formatting and improve exception handling in testing utilities
  ([`beb9862`](https://github.com/TimKleindick/general_manager/commit/beb986274284ce38d974f2ca681742281370430d))

- Clean up dynamic managers in tests
  ([`84fdd82`](https://github.com/TimKleindick/general_manager/commit/84fdd824767f079896e9ea5aabac8728d18480cf))

- Update type hints for iterators in bucket classes and model dependency collector
  ([`5a553af`](https://github.com/TimKleindick/general_manager/commit/5a553af33874c1d2b92c2aaaafc198ea4998bb7e))

### Documentation

- Add guide for graph_ql_mutation
  ([`70d2da4`](https://github.com/TimKleindick/general_manager/commit/70d2da4ff96a9c4d02a09bbafd12bae491adf4d0))

### Testing

- Add integration tests for ReadOnlyInterface manager
  ([`69d4fc4`](https://github.com/TimKleindick/general_manager/commit/69d4fc4577afe2d5ee8ac9b1ff29a89828b8c1c6))


## v0.10.2 (2025-07-07)

### Bug Fixes

- Support named types
  ([`219fe62`](https://github.com/TimKleindick/general_manager/commit/219fe627a1dec42e871761a468b1fc83b8327a66))

### Refactoring

- Simplify basis type assignment in graph_ql_mutation
  ([`771f3db`](https://github.com/TimKleindick/general_manager/commit/771f3db2530963f72321f84bf83ba3f148704928))

### Testing

- Add mutation decorator tests and integration
  ([`da1b348`](https://github.com/TimKleindick/general_manager/commit/da1b348ffe29d5523cf75eb3eee8b0cb2319334b))

- Enhance mutation decorator tests with additional cases and error handling
  ([`fb4c156`](https://github.com/TimKleindick/general_manager/commit/fb4c156f3ed5c1dc6c0bb132bc6425e4e807561a))


## v0.10.1 (2025-07-07)

### Bug Fixes

- Allow measurement division with currency if equal currency
  ([`8f7c918`](https://github.com/TimKleindick/general_manager/commit/8f7c9187f6910591705d6a9218faae5267ee0f50))

- Update division logic in Measurement class to clarify currency handling
  ([`3276941`](https://github.com/TimKleindick/general_manager/commit/3276941af25a1496a9fcdd712159172942b498ce))

### Testing

- Add CachingTestCase
  ([`d5ccbfb`](https://github.com/TimKleindick/general_manager/commit/d5ccbfba47f1174c4e1b5191e4509a290a9f1d43))

- Correct budget calculations and update test cases for accuracy
  ([`d625f42`](https://github.com/TimKleindick/general_manager/commit/d625f426b66130e9b73ce9e8419570da01bbdfa6))

- Implement LoggingCache for enhanced cache operation tracking in tests
  ([`5f4ad85`](https://github.com/TimKleindick/general_manager/commit/5f4ad85f33962fb6ae28316276f4e21de8d42a8e))

- Rename budget used test and verify caching for budget left attribute
  ([`cbc7a13`](https://github.com/TimKleindick/general_manager/commit/cbc7a13927d36a6694786d6cb0de50e795e93bc3))

- Rename TestProject to TestProjectForCommercials to prevent model registration problems
  ([`a1631b7`](https://github.com/TimKleindick/general_manager/commit/a1631b78468f2e1bf40ee660e1c264a243bfba30))


## v0.10.0 (2025-07-06)

### Bug Fixes

- Access Permission attribute directly from general_manager_class
  ([`256f97f`](https://github.com/TimKleindick/general_manager/commit/256f97f8b8179719fde4e17779c585dd86cdff3c))

- Save instance first, then update change reson
  ([`608eac7`](https://github.com/TimKleindick/general_manager/commit/608eac7b4349dc8fd3972ac93bc8fab6655d155e))

- Test_create_project_without_login docstring
  ([`423caec`](https://github.com/TimKleindick/general_manager/commit/423caec9f0fdc73af6ffa94676309c80c953fd7a))

- Test_create_project_without_login_and_public_permissions docstring
  ([`a9c2801`](https://github.com/TimKleindick/general_manager/commit/a9c2801d6f895a7f65b43386cbf56f274bf09451))

- Update mutation with not every key filled
  ([`196af3e`](https://github.com/TimKleindick/general_manager/commit/196af3ec1a60247dfdd2f47c7bbe521d07de928c))

### Features

- Add default permissions to general manager
  ([`529ce87`](https://github.com/TimKleindick/general_manager/commit/529ce87764f72e27703ccda6df14e1b23251dfc3))

### Refactoring

- Auxiliary to utils folder
  ([`97d03a0`](https://github.com/TimKleindick/general_manager/commit/97d03a06f41637701cbb5d0bfc63d259981829f7))

- Move update mutation to setUp for consistency
  ([`f295d51`](https://github.com/TimKleindick/general_manager/commit/f295d51fe9f21f7d79747e4fbcfa5c2369ed2f2e))

### Testing

- Fix general_manager tests for default permissions
  ([`0179a89`](https://github.com/TimKleindick/general_manager/commit/0179a8922232052448e14c741174ab2c279f7dcc))

- Implement delete mutation for TestProject with validation checks
  ([`20f2135`](https://github.com/TimKleindick/general_manager/commit/20f2135c1bb132ce9ee789e34ff3422980bcec8a))


## v0.9.1 (2025-07-06)

### Bug Fixes

- From_string to support dimensionless string values
  ([`78f57a6`](https://github.com/TimKleindick/general_manager/commit/78f57a6eb5f8073f91bbeed4da8dd174f4ebd30e))

- Handle invalid dimensionless value in from_string method
  ([`060e374`](https://github.com/TimKleindick/general_manager/commit/060e3743fc75d8b2e0dfa4fee29cc98701060f44))

- Update Measurement class to handle percentage values correctly
  ([`5605ad2`](https://github.com/TimKleindick/general_manager/commit/5605ad29a5e73dbb44f2bc4f5c2e97c8f0667544))

- Use property unit directly
  ([`9c5f548`](https://github.com/TimKleindick/general_manager/commit/9c5f548c6d060a2efc2da12a7ec36c03e6526668))

- Use property unit directly
  ([`2aed194`](https://github.com/TimKleindick/general_manager/commit/2aed194b900b0f16f81a87bd1d31e7be2d3fdd8e))


## v0.9.0 (2025-07-06)

### Bug Fixes

- Conditionally append new class to pending_graphql_interfaces based on AUTOCREATE_GRAPHQL setting
  ([`abfdbba`](https://github.com/TimKleindick/general_manager/commit/abfdbba2fbe33d35f83c9384a97d53990d2dd3f7))

- Default graphql url from "/graphql/" to "/graphql"
  ([`e2c9268`](https://github.com/TimKleindick/general_manager/commit/e2c9268bef177e0d10f60622728a9a6e767520a4))

- Enhance error handling in attribute setting for DatabaseInterface
  ([`25fa88b`](https://github.com/TimKleindick/general_manager/commit/25fa88b2becc2e84102202e224c89f86c30fd94e))

### Features

- Implement DefaultCreateMutationTest with project creation and validation tests
  ([`c1b8ae8`](https://github.com/TimKleindick/general_manager/commit/c1b8ae8d3e55c7d7bf27c2b3b141d9c889417ed3))

- Implement GMTestCaseMeta and GeneralManagerTransactionTestCase for enhanced testing capabilities
  ([`426d509`](https://github.com/TimKleindick/general_manager/commit/426d509ed735b2a5dc8ef726ffda6b78ea31cd21))

### Refactoring

- Clean up whitespace and improve code readability in GeneralManagerMeta
  ([`60841ea`](https://github.com/TimKleindick/general_manager/commit/60841eac94e365ca6f9be0ba5208e9f409700a5a))

- Convert instance methods to static methods in GeneralmanagerConfig
  ([`56b2d20`](https://github.com/TimKleindick/general_manager/commit/56b2d20dd498eee6f0230d1064a358037dada626))

- Improve exception chaining
  ([`bc647f0`](https://github.com/TimKleindick/general_manager/commit/bc647f0b9017321e48e7fc9527f0d40658065daa))

- Pass parameters to handle_read_only_interface, initialize_general_manager_classes, and handle_graph_ql
  methods for improved clarity and functionality
  ([`9b4c8d4`](https://github.com/TimKleindick/general_manager/commit/9b4c8d46fef81e363e8f97cac0febd8af6f7d642))

- Remove unused Generic import from models.py
  ([`948ed5f`](https://github.com/TimKleindick/general_manager/commit/948ed5f73537cbe24e936a1faac4fff4a1e619dc))

- Split basis models and database interface in seperate files
  ([`5e80e3c`](https://github.com/TimKleindick/general_manager/commit/5e80e3c17a72973ad62269b8cfbf850d754e2325))

- Split tests to unit and integration tests
  ([`3bcca8b`](https://github.com/TimKleindick/general_manager/commit/3bcca8bd0fc9ce0efd4add5ec74f10aabd7a41da))

### Testing

- Add DefaultCreateMutationTest for creating TestProject with GraphQL mutation
  ([`e49e91f`](https://github.com/TimKleindick/general_manager/commit/e49e91f22289ee3f3e73b722478168da4c79fb33))

- Add override_settings for AUTOCREATE_GRAPHQL in GeneralManagerMetaTests
  ([`9a377f1`](https://github.com/TimKleindick/general_manager/commit/9a377f1503751ca1f911dc4b0ddcf2c9f37cde75))

- Add test configuration and URL patterns for session management
  ([`3d8a852`](https://github.com/TimKleindick/general_manager/commit/3d8a852331cc9d63619ce40ecb8a9d6f1caabf69))

- Add validation for project creation with invalid budget
  ([`8c2ac52`](https://github.com/TimKleindick/general_manager/commit/8c2ac5267f84a906283f573c8fa2ffca18edb4a2))

- Implement fallback app configuration retrieval in testing utilities
  ([`7b42a50`](https://github.com/TimKleindick/general_manager/commit/7b42a50c71dade2c4c86bb9572132134aba81b0e))

- Remove redundant assertion for pending_graphql_interfaces in GeneralManagerMetaTests
  ([`4190013`](https://github.com/TimKleindick/general_manager/commit/41900137769938924197d67591a8ef3fa66722b2))


## v0.8.0 (2025-07-03)

### Bug Fixes

- Add is_derived attribute to interface fields and update GraphQL filtering logic
  ([`999fcbe`](https://github.com/TimKleindick/general_manager/commit/999fcbe67950c726b295a1c3b8726d594b4c74c8))

- Input cast function for date type with date value
  ([`12cb5d4`](https://github.com/TimKleindick/general_manager/commit/12cb5d406ccdc947fe262d14403b3573572004df))

- Remove type ignore
  ([`13262eb`](https://github.com/TimKleindick/general_manager/commit/13262eb6cbbf77837f2fbb470112c4fc5b39d7ed))

- Return from automatic mutations
  ([`876c34e`](https://github.com/TimKleindick/general_manager/commit/876c34ef7bd6a204543f02e55e3e9bce55a5e12d))

- Simplify return statement in GraphQL mutation handling
  ([`8245980`](https://github.com/TimKleindick/general_manager/commit/8245980b229e00a9de8cfa70e04b15f53276d69b))

- Typos
  ([`efe6dea`](https://github.com/TimKleindick/general_manager/commit/efe6deafd415c0ba03e599ca5406744e5166bf01))

### Documentation

- Update calculation interface example
  ([`a19a794`](https://github.com/TimKleindick/general_manager/commit/a19a794eb59c9e6e47f57c81432f494b6a50c0c7))

### Features

- Add string format utility functions and corresponding tests
  ([`29b7447`](https://github.com/TimKleindick/general_manager/commit/29b7447d6cce8c8b46b9304a21c6243d995c42be))

- Allow creator to be undefined
  ([`2d6d698`](https://github.com/TimKleindick/general_manager/commit/2d6d698f7d5c4b6fc5a67fdb9db1d814a4958e4d))

### Refactoring

- Remove redundant comments from test cases in TestInput
  ([`909fc3e`](https://github.com/TimKleindick/general_manager/commit/909fc3ec7830ae00b173401df4ad126c554b4743))

### Testing

- Remove type ignores and add test for get_read_permission_filter
  ([`c8896a7`](https://github.com/TimKleindick/general_manager/commit/c8896a716b07fcc816484a848f6b8484ade25f85))

- Test GraphQL default mutations
  ([`1fe5646`](https://github.com/TimKleindick/general_manager/commit/1fe5646855e4a0f01e6858d0521ad5a67ad2e62d))


## v0.7.0 (2025-06-22)

### Bug Fixes

- Add type ignore comment to test_read_only_interface.py
  ([`089a3b8`](https://github.com/TimKleindick/general_manager/commit/089a3b860361f8d220defa8c81266bc5146e0212))

- Allow customizable base model class in _pre_create method of DBBasedInterface
  ([`5b58af9`](https://github.com/TimKleindick/general_manager/commit/5b58af95d136f62081cb88a36fa5e26138c97b9b))

- Correct type annotation for read_only_classes in GeneralManagerMeta
  ([`faa8228`](https://github.com/TimKleindick/general_manager/commit/faa822889d1cdf99f5bfc58313d0dbdfcf946888))

- Enhance ReadOnlyInterface data syncing and improve GraphQL initialization logging
  ([`c99c2c8`](https://github.com/TimKleindick/general_manager/commit/c99c2c838db60d1d489347c110452e6e8992591f))

- Improve json_data check in ReadOnlyInterface to use None comparison
  ([`34aecb3`](https://github.com/TimKleindick/general_manager/commit/34aecb397186538cf7283411c61c5dde700cef17))

- Register read-only interface warnings to ensure schema is up to date
  ([`9ab61e5`](https://github.com/TimKleindick/general_manager/commit/9ab61e5b66646f329c1b4994376918f9a17f9644))

- Replace print statements with logging for cache invalidation
  ([`b0179b4`](https://github.com/TimKleindick/general_manager/commit/b0179b4f36c6547d4426afb6482d6b19f974750a))

- Sync and warn ReadOnlyInterfaces &, refactor apps.py
  ([`8f0a2e5`](https://github.com/TimKleindick/general_manager/commit/8f0a2e5b5fb46244110a5d7a7252b9856cd596da))

- Update ReadOnlyInterface import and usage in handle_read_only_interface method
  ([`f76a935`](https://github.com/TimKleindick/general_manager/commit/f76a93563b24e750522d767898d40d268a988b63))

### Features

- Implement read_only_interface
  ([`be4fe48`](https://github.com/TimKleindick/general_manager/commit/be4fe48e9cd1811099e3ff9c1788ff5d759afe98))

### Refactoring

- Replace GeneralManagerModel with GeneralManagerBasisModel in read_only_interface and related files
  ([`85ac212`](https://github.com/TimKleindick/general_manager/commit/85ac2121a34422c4869e69eb64c78d64ae8dc289))

### Testing

- Add comprehensive tests for ReadOnlyInterface functionality
  ([`8dab67a`](https://github.com/TimKleindick/general_manager/commit/8dab67ae5146952e5dfbd320d4ffc43fa9aaa553))


## v0.6.2 (2025-06-16)

### Bug Fixes

- Enhance create and update methods to handle many-to-many attributes
  ([`08b07d2`](https://github.com/TimKleindick/general_manager/commit/08b07d2202cc705979135d4176b57afd6e7b0c22))

- Improve handling of many-to-many attributes in _sort_kwargs method
  ([`ef94175`](https://github.com/TimKleindick/general_manager/commit/ef94175d8017abfd7a72550716445d8d65403717))

- Update __checkForInvalidKwargs to handle '_id_list' suffix in keys
  ([`db81620`](https://github.com/TimKleindick/general_manager/commit/db81620cde0d6b1252de322c7fa10d09956254a1))

### Refactoring

- Streamline create and update methods by consolidating attribute setting logic
  ([`475ca57`](https://github.com/TimKleindick/general_manager/commit/475ca57694e5e9e8ed7d0f55d17829dd86b6878b))

### Testing

- Add DatabaseInterfaceTestCase with validation and history management
  ([`b41d432`](https://github.com/TimKleindick/general_manager/commit/b41d43200500682d0182246a87743f4cdef83715))

- Add update method call in test_create_update_and_deactivate to verify reader assignment
  ([`072d9e8`](https://github.com/TimKleindick/general_manager/commit/072d9e8eba2969e6d77ad8144438271004e8bd58))


## v0.6.1 (2025-06-15)

### Bug Fixes

- Improve __repr__ method in CalculationBucket for clearer output
  ([`ca9d604`](https://github.com/TimKleindick/general_manager/commit/ca9d604baa251ff52572d419da7a27521667633d))

- Refine filtering logic in CalculationBucket to enhance clarity and functionality
  ([`fa9f573`](https://github.com/TimKleindick/general_manager/commit/fa9f573e455cd9fbb9792fa8bdb5389aa804ed43))

- Update error handling in get_field_type and clean up comments in _pre_create
  ([`3083626`](https://github.com/TimKleindick/general_manager/commit/3083626e754d8da40164881e1e71b3b764fa51a0))

- Update exception type in get_field_type method to KeyError for non-existent fields
  ([`9bec05e`](https://github.com/TimKleindick/general_manager/commit/9bec05ef869d29e590462fff496595a9e9ddce93))

### Testing

- Add comprehensive tests for CalculationBucket functionality and behavior
  ([`f7ad84c`](https://github.com/TimKleindick/general_manager/commit/f7ad84c9348ae680ab20328d57d6dcced64787e8))

- Add unit tests for CalculationInterface methods and functionality
  ([`043e09c`](https://github.com/TimKleindick/general_manager/commit/043e09c60de2f43e11e5cda7f1f0e1d670143d0d))

- Correct equality check in DummyGeneralManager to ensure proper comparison
  ([`b1ec08b`](https://github.com/TimKleindick/general_manager/commit/b1ec08b99184e4b06fd54ac5c093e1585a6f5079))

- Correct formatting in __repr__ method of CalculationBucket for consistency
  ([`cb6bd94`](https://github.com/TimKleindick/general_manager/commit/cb6bd94dc29b19741c7a94149b91328892eb692c))

- Remove print statement from test_first_last_empty_and_nonempty for cleaner output
  ([`3f421ce`](https://github.com/TimKleindick/general_manager/commit/3f421ce8fbdc264c501f39b65368a90c7213261d))

- Rename test file to database_based_interface
  ([`3e61fb1`](https://github.com/TimKleindick/general_manager/commit/3e61fb1e835e75c7f586ecc4fc62e6fa823d524c))

- Update variable name in loops for consistency and clarity
  ([`1bcf82b`](https://github.com/TimKleindick/general_manager/commit/1bcf82bfddb25ecfee46ab5ccbad2b86fc4c45e8))


## v0.6.0 (2025-06-15)

### Bug Fixes

- __eq__ for group_bucket
  ([`0c33798`](https://github.com/TimKleindick/general_manager/commit/0c33798031815dccb3ae373501cf1d0bf7adcd37))

- Add __setstate__ method to restore current combinations in CalculationBucket
  ([`c2fa8a5`](https://github.com/TimKleindick/general_manager/commit/c2fa8a5064b4bbec4cf3643684f5e80f655e0928))

- Correct error message and type hint in groupBy and sort methods
  ([`bc19f79`](https://github.com/TimKleindick/general_manager/commit/bc19f79fa93d589368daba6d8d603ed2f39e94ee))

- Correct string formatting in CalculationBucket class
  ([`4c4b969`](https://github.com/TimKleindick/general_manager/commit/4c4b96986be0c21095f6c24296da36c6d27d3715))

- Enhance CalculationBucket initialization with filters, excludes, sort_key, and reverse parameters
  ([`98bf9d2`](https://github.com/TimKleindick/general_manager/commit/98bf9d22a83f90e6eefa39d92efeae5291c4d061))

- Optimize equality check in GroupManager by using hash comparison
  ([`41df94b`](https://github.com/TimKleindick/general_manager/commit/41df94bbe5cc2f0832c774ca6c70bcb351fe86e8))

- Optimize field value checks in DBBasedInterface by removing redundant calls to keys()
  ([`5447007`](https://github.com/TimKleindick/general_manager/commit/5447007d8512c35e4aa5174c545ccd182bc64720))

- Optimize group value handling in GroupBucket and remove unnecessary JSON serialization
  ([`e00acdc`](https://github.com/TimKleindick/general_manager/commit/e00acdc2339fac574f21d21edf5dd3c6bcf7db6d))

- Optimize length calculation in CalculationBucket by using generate_combinations directly
  ([`5976e0a`](https://github.com/TimKleindick/general_manager/commit/5976e0a00510dce77548c051d671611a8f236e2a))

- Optimize membership check in CalculationBucket.__contains__ method
  ([`a2afb2e`](https://github.com/TimKleindick/general_manager/commit/a2afb2e2a398ee45478972c424b6120603108255))

- Simplify and debug related model handling in DBBasedInterface
  ([`1f05cfd`](https://github.com/TimKleindick/general_manager/commit/1f05cfd41e6300f91676db658b0b4ebb930c02e7))

- Simplify equality check in GroupManager by removing redundant instance check
  ([`ed3219c`](https://github.com/TimKleindick/general_manager/commit/ed3219cc7188cd46fe09696bd35b7b418ecc24c3))

- Simplify state retrieval in __setstate__ and iterate over input_fields directly
  ([`8e56b29`](https://github.com/TimKleindick/general_manager/commit/8e56b2938640d10a828fbb7302490c3cfcb36ae1))

- Slicing in calculation_bucket
  ([`c68739a`](https://github.com/TimKleindick/general_manager/commit/c68739a14e3d45f60b97fce4f60740e1400b00dd))

- Sort group_by_values using string representation for consistent ordering
  ([`f13022e`](https://github.com/TimKleindick/general_manager/commit/f13022e0af733c755bf82791bed0801b91fd7508))

- Update identification access in DatabaseBucket methods
  ([`f3bc0cd`](https://github.com/TimKleindick/general_manager/commit/f3bc0cd64b2ad3428eb471565a435c72da0ae2d6))

- Update user type check to use AbstractUser in get_user_with_id method
  ([`3e259e5`](https://github.com/TimKleindick/general_manager/commit/3e259e5f9fe3aa25fb1c6aeb10c3f2f9a2666151))

### Chores

- Update requirements to use base.txt for consistency
  ([`3584581`](https://github.com/TimKleindick/general_manager/commit/35845817c264892b77beab57f6a1585a0a7f58d6))

### Documentation

- Shorten project description for clarity and conciseness
  ([`3b87ff7`](https://github.com/TimKleindick/general_manager/commit/3b87ff7e8b8a9c76eef76eaf2905a76426d31ac4))

- Split requirements files to development and production environments
  ([`7f3ef5f`](https://github.com/TimKleindick/general_manager/commit/7f3ef5f933f86d96dfcb4ff569293b910085a355))

### Features

- Implement __hash__ method in GroupManager for improved object hashing
  ([`99ffb6e`](https://github.com/TimKleindick/general_manager/commit/99ffb6edfe6448d9840cb1eb157144dbec887565))

### Refactoring

- Clean up imports and simplify PermissionDataManager usage in BasePermission
  ([`d6bb292`](https://github.com/TimKleindick/general_manager/commit/d6bb292ac386a0214751d15f2a0d66741a3d57b5))

- Lambda to named function for combination generation
  ([`73bb1a0`](https://github.com/TimKleindick/general_manager/commit/73bb1a01f7545ddb91a749f5e204d1f57e29e8b2))

- Move calculation_bucket to own file
  ([`6e4d441`](https://github.com/TimKleindick/general_manager/commit/6e4d441117b7d019e567849e4840195c76a087da))

- Remove TYPE_CHECKING import and streamline GeneralManager import
  ([`c3434f5`](https://github.com/TimKleindick/general_manager/commit/c3434f576bb970aaa1d808bea5ed3a7eb80ea859))

- Remove unnecessary blank lines and improve code readability in ReadOnlyInterface
  ([`618e674`](https://github.com/TimKleindick/general_manager/commit/618e674aa93fe7db15113c52f6ea2b78435b1be3))

- Remove unnecessary blank lines and improve code readability in test_general_manager
  ([`ae982da`](https://github.com/TimKleindick/general_manager/commit/ae982da52c4e3798d18c721f31ac46730315bd29))

- Remove unnecessary blank lines and improve docstring clarity in DummyInterface and
  DatabaseBucketTestCase
  ([`9c40426`](https://github.com/TimKleindick/general_manager/commit/9c40426168fdf3ffaaed644cde46ebbb5036e419))

- Remove unnecessary blank lines and improve docstring formatting in DummyInterface and
  InterfaceBaseTests
  ([`f7e856a`](https://github.com/TimKleindick/general_manager/commit/f7e856a05e79ac14ba236f955c9ad33b4e83fd15))

- Remove unnecessary blank lines in DBBasedInterface and related methods
  ([`2e00b5c`](https://github.com/TimKleindick/general_manager/commit/2e00b5c443c1cc1196d672e5e88924c2da3bf95d))

- Rename TestInterface to DummyInterface for clarity in test cases
  ([`0789a51`](https://github.com/TimKleindick/general_manager/commit/0789a51007fbf5e4f76baa5a3220b8ff8448df88))

- Simplify field existence checks in DBBasedInterface
  ([`53b4098`](https://github.com/TimKleindick/general_manager/commit/53b40984f9275c46912eb7111c63f5ce039e9484))

- Simplify generator implementation in GroupBucket.__iter__ method
  ([`908be9f`](https://github.com/TimKleindick/general_manager/commit/908be9fc26e6f327db543b86c632ac89ea627292))

- Split interface and bucket
  ([`fe779bf`](https://github.com/TimKleindick/general_manager/commit/fe779bfe8237851a644d652839de9dddc7e88f1b))

- Update filter and exclude definitions to use None as default and improve queryset handling
  ([`fa229d3`](https://github.com/TimKleindick/general_manager/commit/fa229d328412bf5078147ca83b1a1f50acef0be6))

### Testing

- Add comprehensive tests for DatabaseBasedInterface
  ([`64fb815`](https://github.com/TimKleindick/general_manager/commit/64fb8150d2f1618781730bc7d5fcfc16769a1031))

- Add DatabaseBucket test case with UserManager integration
  ([`9ea7c22`](https://github.com/TimKleindick/general_manager/commit/9ea7c2203544beccaf7760f372b0d1d65f016a4f))

- Remove unnecessary blank lines and improve exception message clarity in GeneralManagerMetaTests
  ([`5594d79`](https://github.com/TimKleindick/general_manager/commit/5594d794def777d203a404d52ab28ca8c0b40937))

- Rename test_possible_values_invalid_type to test_invalid_kwargs for clarity
  ([`e4b6f45`](https://github.com/TimKleindick/general_manager/commit/e4b6f453667ce656724e5fabb93420ac59d002f5))

- Sorting database bucket
  ([`80bb51e`](https://github.com/TimKleindick/general_manager/commit/80bb51eb45ee84513225e931569430bb0c249ed4))


## v0.5.2 (2025-06-09)

### Bug Fixes

- Change exception type from TypeError to ValueError in GroupBucket class
  ([`26df806`](https://github.com/TimKleindick/general_manager/commit/26df806a241a124ae094f3ab8aeaf77ba7ca3268))

- More efficiency through arg in dict instead of arg in dict.keys()
  ([`d16200e`](https://github.com/TimKleindick/general_manager/commit/d16200e7d3e7bef3d0454208bbe2805589f04b0d))

### Documentation

- Add AGENTS.md
  ([`c2760f7`](https://github.com/TimKleindick/general_manager/commit/c2760f7ff382b9fb1151c9d20ca220cd0a6d5ae0))

- Add Input class documentation
  ([`707aea3`](https://github.com/TimKleindick/general_manager/commit/707aea37554c0559c7ad13bc88b7fb20b7c45d1d))

- Clarify usage of Input class in context of GeneralManager initialization
  ([`e900175`](https://github.com/TimKleindick/general_manager/commit/e9001759a9c729d3158f3a14a383ac8b5107021b))

- Remove unnecessary blank lines in DatabaseBucket class docstrings
  ([`67742a2`](https://github.com/TimKleindick/general_manager/commit/67742a29cd6709233f096a870f57207d5fc9270b))

- Translate and update README
  ([`695dcf7`](https://github.com/TimKleindick/general_manager/commit/695dcf7afc4299ca7a0d1623817173e383161780))

- Update AGENTS.md to use English for clarity and consistency
  ([`1f038e6`](https://github.com/TimKleindick/general_manager/commit/1f038e68e690070de16bf763f2133e5cadaf63c9))

- Update comments for clarity and consistency in InterfaceBase
  ([`86dbe7e`](https://github.com/TimKleindick/general_manager/commit/86dbe7e4076a1dbcff71175ef28054df7838ee62))

### Testing

- Add tests for InterfaceBase and Bucket implementations
  ([`7b7f47d`](https://github.com/TimKleindick/general_manager/commit/7b7f47d51170c3c39b751a5504f82a1695f9b7f2))

- Correct attribute access for _group_by_keys in BucketTests
  ([`97b5bf3`](https://github.com/TimKleindick/general_manager/commit/97b5bf33bd35f3770acd5fe048cfa5d32754d143))

- Missing """ led to problems
  ([`2e917b3`](https://github.com/TimKleindick/general_manager/commit/2e917b3cb1f10f4b750668a136ddfbb193678c85))


## v0.5.1 (2025-06-08)

### Bug Fixes

- Implement equality check for GroupBucket and update group_by method
  ([`14b0a0c`](https://github.com/TimKleindick/general_manager/commit/14b0a0c2fa1f6b9c7c8128e5cf7ead62d9d1b372))

- Optimize data aggregation logic in GroupManager for boolean types
  ([`105e60e`](https://github.com/TimKleindick/general_manager/commit/105e60ed1d4c7784deabe0a65ffab84bf46b6937))

- Remove unused __hash__ method from GroupManager
  ([`0e9335e`](https://github.com/TimKleindick/general_manager/commit/0e9335e65c65b1c10f2a4e1cfe508ef123a5c471))

- Simplify sorting logic in GroupBucket by removing unnecessary list comprehension
  ([`0417435`](https://github.com/TimKleindick/general_manager/commit/0417435f68047cf7bbb0cc26bec5272cc5cb6166))

- Sort group_by_values and improve sorting logic in GroupBucket
  ([`f0fe417`](https://github.com/TimKleindick/general_manager/commit/f0fe417ed83a9f3765116903765151b3e66102d9))

- Update data aggregation in GroupManager to avoid duplicates
  ([`016d254`](https://github.com/TimKleindick/general_manager/commit/016d254a9a7677eec6ae63a5aa0126fa69bbd1ef))

### Refactoring

- Rename GroupedManager to GroupManager and update references
  ([`f5e6e50`](https://github.com/TimKleindick/general_manager/commit/f5e6e50d49e03dc2d28e274c01e605ba6e552a44))

### Testing

- Add comprehensive tests for GroupBucket and GroupManager functionality
  ([`088be38`](https://github.com/TimKleindick/general_manager/commit/088be38023e8c39d2272484bd8926b56f40eecde))

- Clean up imports and improve GroupBucket test assertions
  ([`cec9f8d`](https://github.com/TimKleindick/general_manager/commit/cec9f8d0d45c8a250f3965f1465c4c7aa71e0850))

- Correct type definition for date in DummyInterface and add setup/teardown for tests
  ([`81199ca`](https://github.com/TimKleindick/general_manager/commit/81199ca04938cd7255e52eb83f15d0d8b3201f5e))


## v0.5.0 (2025-06-05)

### Documentation

- Add arithmetic examples for Measurement
  ([`16874c4`](https://github.com/TimKleindick/general_manager/commit/16874c4a284f9c4533f5b2a35fdd7df7d5fb439f))

### Features

- Improve error handling in propertie methods
  ([`3b007ed`](https://github.com/TimKleindick/general_manager/commit/3b007edeb83c60b8fadb57ebe11ce38c56487eec))

### Refactoring

- Remove unused import from test_general_manager_meta.py
  ([`0a61bed`](https://github.com/TimKleindick/general_manager/commit/0a61bedd56a15f5fb1037cee08c641bebe8a5658))

### Testing

- Add comprehensive tests for GeneralManager properties
  ([`97d6677`](https://github.com/TimKleindick/general_manager/commit/97d66771be9159197a5277bc5e614c0345281a55))

- Add tests for GeneralManagerMeta __new__ method
  ([`3498186`](https://github.com/TimKleindick/general_manager/commit/34981869821611d9fba03d488fa5e3159f2c34f7))


## v0.4.6 (2025-06-04)

### Bug Fixes

- Pointer error in __parse_identification
  ([`d6b67c2`](https://github.com/TimKleindick/general_manager/commit/d6b67c2dc1b6f7b8df7542a587434fbea68e4b36))

### Refactoring

- Relocate AutoFacotory to seperate file
  ([`a970130`](https://github.com/TimKleindick/general_manager/commit/a970130a8674a4fe3ab28e35a9a139847aaa64ed))

- Replace getattr with direct access
  ([`054f1a9`](https://github.com/TimKleindick/general_manager/commit/054f1a9d50da5165a5ccc261dcc5789dcff69618))

### Testing

- Add AutoFactory test cases
  ([`a3fd909`](https://github.com/TimKleindick/general_manager/commit/a3fd909b35d39148277dfc4ff6ee20fb6db6df1e))

- Add unit test for GeneralManager deactivate class method
  ([`2e2a3b5`](https://github.com/TimKleindick/general_manager/commit/2e2a3b53dea752dd3248d85f2931fff855411870))

- Add unit tests for GeneralManager functionality
  ([`0b9235d`](https://github.com/TimKleindick/general_manager/commit/0b9235dfcd7de29aa0248c32f8c012835512aad3))

- Enhance AutoFactoryTestCase with teardown and type hints
  ([`958b2c3`](https://github.com/TimKleindick/general_manager/commit/958b2c338c8b2edc88d2991061e654a5f694a4cd))

- Update comments for clarity in GeneralManagerTestCase
  ([`8f6e0f0`](https://github.com/TimKleindick/general_manager/commit/8f6e0f0c18452f147cfec74662b8508da4e8ed99))


## v0.4.5 (2025-05-28)

### Bug Fixes

- Improve string parsing and comparison error handling in Measurement class
  ([`53f7543`](https://github.com/TimKleindick/general_manager/commit/53f7543f0bba0d9221f5df6d861dc34c473ba368))

### Testing

- Enhance Measurement class tests for addition, comparison, and pickling
  ([`c1acda2`](https://github.com/TimKleindick/general_manager/commit/c1acda27c1b36a4c1d98a7f2b55853e651990f52))


## v0.4.4 (2025-05-28)

### Bug Fixes

- Try to adjust changelog
  ([`3b60789`](https://github.com/TimKleindick/general_manager/commit/3b6078923e9ed3baf106bbf4062a1da772e551f0))

- Try to adjust changelog
  ([`2531dc1`](https://github.com/TimKleindick/general_manager/commit/2531dc1b774bffc14659591a44f3417da637f257))

- Try to adjust changelog
  ([`c990369`](https://github.com/TimKleindick/general_manager/commit/c990369579dea47d3812758bb77c56066dbc5381))

- Try to adjust changelog
  ([`cc92c5e`](https://github.com/TimKleindick/general_manager/commit/cc92c5e1f83b30c5d8959de6c61bdede980ae14a))

- Try to adjust changelog
  ([`7eab154`](https://github.com/TimKleindick/general_manager/commit/7eab154f0b9658ebe55311e59d287cab3770d595))

- Try to adjust changelog
  ([`6b0b601`](https://github.com/TimKleindick/general_manager/commit/6b0b6012ca164c1d73d71f73671ddb531bc25a4e))

### Continuous Integration

- Fixed pipeline
  ([`9831330`](https://github.com/TimKleindick/general_manager/commit/9831330bf090a04d9b8eb95aceda6e0fab29082e))


## v0.4.3 (2025-05-28)

### Bug Fixes

- Changelog
  ([`fdb1176`](https://github.com/TimKleindick/general_manager/commit/fdb117653a4269e1f6ba0d1e5072f82fe4dd3291))

- Semantic-releasec
  ([`eb80961`](https://github.com/TimKleindick/general_manager/commit/eb8096108ad35550dcacb427208d67fa5d97326f))

- Update the Command to also update the Changelog
  ([`d323a08`](https://github.com/TimKleindick/general_manager/commit/d323a081110c00eee99ab478348ad18407c67266))


## v0.4.2 (2025-05-28)

### Bug Fixes

- Improved Deploy Pipeline to work SSH based for commits
  ([`42a9dc9`](https://github.com/TimKleindick/general_manager/commit/42a9dc94e67207eaa3a6c6a009e7bc555b42a64b))

- Last chance for ssh commit
  ([`642f60c`](https://github.com/TimKleindick/general_manager/commit/642f60c49ce544bcefe7f60c554a93953b6cc4c1))

### Continuous Integration

- Add condition to trigger release job on push event
  ([`40afbe5`](https://github.com/TimKleindick/general_manager/commit/40afbe5e51866d9092ac02d496510c7bd5e7b5f2))

- Add ignore-token-for-push to use ssh
  ([`4773fb4`](https://github.com/TimKleindick/general_manager/commit/4773fb4cfc26b609d7d906b1b79fc818f5bd75f1))

- Add remote configuration for semantic release
  ([`73fe533`](https://github.com/TimKleindick/general_manager/commit/73fe533d5d4c616d7bbe7c7a6be7bca040a66917))

- Add step to configure SSH known_hosts for GitHub
  ([`31c5fe6`](https://github.com/TimKleindick/general_manager/commit/31c5fe6958c76c4ae8641db6c5b0d14dfdfabffa))

- Change pyproject.toml
  ([`7bd078e`](https://github.com/TimKleindick/general_manager/commit/7bd078ea960202a751071de98be9d4b28dd0c29a))

- Fix deploy key name
  ([`87479b5`](https://github.com/TimKleindick/general_manager/commit/87479b5e1f7c367e59f73b8f5338cf70285ecfe3))

- Ignore_token_for_push: true
  ([`eac1959`](https://github.com/TimKleindick/general_manager/commit/eac1959ccd9f8f506e4b8a9b8530ec433a77be47))

- Removed activation on tags for workflow
  ([`1e76c97`](https://github.com/TimKleindick/general_manager/commit/1e76c970e01e36304107d075a8b76a6a8f22e100))

- Removed url from settings
  ([`041aebd`](https://github.com/TimKleindick/general_manager/commit/041aebd1d2153db88bbfb87df4f2650e6a059146))

- Update Git remote URL to use SSH format
  ([`6d782a0`](https://github.com/TimKleindick/general_manager/commit/6d782a0355bce23402e6582a646f3d826f756214))

- Update SSH checkout configuration in workflow
  ([`4f887f6`](https://github.com/TimKleindick/general_manager/commit/4f887f6d968f3937ecf6d8bad18f6af688a2988e))

- Update SSH known_hosts setup and remove Git remote URL from pyproject.toml
  ([`3f8e74b`](https://github.com/TimKleindick/general_manager/commit/3f8e74ba45ba90695741bff2edd430f464fead12))

- Update SSH known_hosts setup for improved security
  ([`aedb7f9`](https://github.com/TimKleindick/general_manager/commit/aedb7f9b372072fc3dc0a3e88563600812f01b05))

- Workflow settings change
  ([`7305a97`](https://github.com/TimKleindick/general_manager/commit/7305a97351d673ddb99a4048b831f94eecfed1d2))

### Testing

- Fix m2m with factory can return empty list result
  ([`980f789`](https://github.com/TimKleindick/general_manager/commit/980f7895fe7b7b926b8521565615e92bb82b1894))


## v0.4.1 (2025-05-26)

### Bug Fixes

- Ci pipeline
  ([`07c258f`](https://github.com/TimKleindick/general_manager/commit/07c258fd817686ef145f2d21e93fed591404672f))

### Continuous Integration

- Add checkout
  ([`f7a5284`](https://github.com/TimKleindick/general_manager/commit/f7a5284aa99ec389274f94254196f0a5bb10636f))

- Add github to known hosts
  ([`cbe4d75`](https://github.com/TimKleindick/general_manager/commit/cbe4d755e84a04e778acd9b64f76787d89ed800e))

- Add remote to pyproject toml to enable ssh based releases
  ([`8dc2edb`](https://github.com/TimKleindick/general_manager/commit/8dc2edb92125770e7b96942eb7a5721bb7344ef8))

- Add ssh prefix for remote url
  ([`b9341b8`](https://github.com/TimKleindick/general_manager/commit/b9341b8ce2eb8b58387fff9999c2aeccfe55e238))

- Go back to basic
  ([`211827b`](https://github.com/TimKleindick/general_manager/commit/211827b88ebded3040562f8104217497ae895eef))

- Go back to https
  ([`db8365e`](https://github.com/TimKleindick/general_manager/commit/db8365e43835c7e9d611702e19fcca71caaba3df))

- Reorganize semantic release remote configuration in pyproject.toml
  ([`e51a097`](https://github.com/TimKleindick/general_manager/commit/e51a097ba7c0d4ca57fa1b8b6f1818d368184cad))

- Update remote configuration in pyproject.toml for semantic release
  ([`f9c2681`](https://github.com/TimKleindick/general_manager/commit/f9c26814d41ab4d2f753feab4dd4e0c219a423b9))


## v0.4.0 (2025-05-21)

- Initial Release
