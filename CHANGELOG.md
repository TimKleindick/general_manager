# CHANGELOG

<!-- version list -->

## v0.13.0 (2025-08-03)

### Bug Fixes

- Apply permission filters to queryset in GraphQL class
  ([`5125e33`](https://github.com/TimKleindick/general_manager/commit/5125e33f494fbee969d90f3af172e04b9d264d86))

- Improve type checking for based_on object in ManagerBasedPermission
  ([`95c483e`](https://github.com/TimKleindick/general_manager/commit/95c483e70a41e591b945505f5d1f02e30a57e014))

- Refactor permission attributes initialization and handle based_on condition
  ([`2e21b3b`](https://github.com/TimKleindick/general_manager/commit/2e21b3b64619942de5a011868f2600107f7245c3))

- Remove unnecessary blank lines in test_calculationBucket.py
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

- Add __parseKwargs method for keyword argument parsing in DBBasedInterface
  ([`0fa60ce`](https://github.com/TimKleindick/general_manager/commit/0fa60ce283fe64e01929c30df9b5aa9bd158b24e))

- Correct conditional structure in related model handling
  ([`6a6269b`](https://github.com/TimKleindick/general_manager/commit/6a6269ba1a700a4ba885d8c60109d8c545da24bf))

- Debug related fields filtering in DBBasedInterface
  ([`8a39aff`](https://github.com/TimKleindick/general_manager/commit/8a39affa4fda403a0bafa71cefa62f7d5804c6cf))

- Enhance many-to-many relationship handling in DatabaseInterface
  ([`0b2de0d`](https://github.com/TimKleindick/general_manager/commit/0b2de0d56e140abeaae7004713ef5cb131ec8f8c))

- Enhance related field handling in DBBasedInterface
  ([`df92257`](https://github.com/TimKleindick/general_manager/commit/df92257365fe7190d0494d13b13d608f8e5ff6f2))

- Ensure rules attribute defaults to an empty list in getFullCleanMethode
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

- Correct attribute access in __getAttributePermissions and checkPermission methods
  ([`36b5796`](https://github.com/TimKleindick/general_manager/commit/36b5796e65f677fbe0d2b1a36554bba81ec659e5))

- Remove unused type imports in mutationPermission.py
  ([`920d5f7`](https://github.com/TimKleindick/general_manager/commit/920d5f782353ec87fbc6664b53e4e73cae3b3ce9))

- Stop enforcing manager requirement for dict permission_data
  ([`6c1ca1f`](https://github.com/TimKleindick/general_manager/commit/6c1ca1fca5b30ce31055908ab6eacb77f756dbb9))

- User handling
  ([`4a48d04`](https://github.com/TimKleindick/general_manager/commit/4a48d04f13b3b4678a0da1f7a30dbfe23679dcc6))

### Features

- Implement MutationPermission class for mutation permission handling
  ([`c39ddf5`](https://github.com/TimKleindick/general_manager/commit/c39ddf51c5b41591b7fea2216e2f9dd0076191d5))

- Update graphQlMutation to use permission parameter and improve authentication handling
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

- Remove unnecessary blank lines and improve variable naming in `getFieldType`
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

- Improve type hinting and simplify function call logic in dataChange decorator
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

- Add guide for graphQlMutation
  ([`70d2da4`](https://github.com/TimKleindick/general_manager/commit/70d2da4ff96a9c4d02a09bbafd12bae491adf4d0))

### Testing

- Add integration tests for ReadOnlyInterface manager
  ([`69d4fc4`](https://github.com/TimKleindick/general_manager/commit/69d4fc4577afe2d5ee8ac9b1ff29a89828b8c1c6))


## v0.10.2 (2025-07-07)

### Bug Fixes

- Support named types
  ([`219fe62`](https://github.com/TimKleindick/general_manager/commit/219fe627a1dec42e871761a468b1fc83b8327a66))

### Refactoring

- Simplify basis type assignment in graphQlMutation
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

- Fix generalManager tests for default permissions
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

- Pass parameters to handleReadOnlyInterface, initializeGeneralManagerClasses, and handleGraphQL
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

- Remove type ignores and add test for getReadPermissionFilter
  ([`c8896a7`](https://github.com/TimKleindick/general_manager/commit/c8896a716b07fcc816484a848f6b8484ade25f85))

- Test GraphQL default mutations
  ([`1fe5646`](https://github.com/TimKleindick/general_manager/commit/1fe5646855e4a0f01e6858d0521ad5a67ad2e62d))


## v0.7.0 (2025-06-22)

### Bug Fixes

- Add type ignore comment to test_readOnlyInterface.py
  ([`089a3b8`](https://github.com/TimKleindick/general_manager/commit/089a3b860361f8d220defa8c81266bc5146e0212))

- Allow customizable base model class in _preCreate method of DBBasedInterface
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

- Update ReadOnlyInterface import and usage in handleReadOnlyInterface method
  ([`f76a935`](https://github.com/TimKleindick/general_manager/commit/f76a93563b24e750522d767898d40d268a988b63))

### Features

- Implement readOnlyInterface
  ([`be4fe48`](https://github.com/TimKleindick/general_manager/commit/be4fe48e9cd1811099e3ff9c1788ff5d759afe98))

### Refactoring

- Replace GeneralManagerModel with GeneralManagerBasisModel in readOnlyInterface and related files
  ([`85ac212`](https://github.com/TimKleindick/general_manager/commit/85ac2121a34422c4869e69eb64c78d64ae8dc289))

### Testing

- Add comprehensive tests for ReadOnlyInterface functionality
  ([`8dab67a`](https://github.com/TimKleindick/general_manager/commit/8dab67ae5146952e5dfbd320d4ffc43fa9aaa553))


## v0.6.2 (2025-06-16)

### Bug Fixes

- Enhance create and update methods to handle many-to-many attributes
  ([`08b07d2`](https://github.com/TimKleindick/general_manager/commit/08b07d2202cc705979135d4176b57afd6e7b0c22))

- Improve handling of many-to-many attributes in _sortKwargs method
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

- Update error handling in getFieldType and clean up comments in _preCreate
  ([`3083626`](https://github.com/TimKleindick/general_manager/commit/3083626e754d8da40164881e1e71b3b764fa51a0))

- Update exception type in getFieldType method to KeyError for non-existent fields
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

- Rename test file to databaseBasedInterface
  ([`3e61fb1`](https://github.com/TimKleindick/general_manager/commit/3e61fb1e835e75c7f586ecc4fc62e6fa823d524c))

- Update variable name in loops for consistency and clarity
  ([`1bcf82b`](https://github.com/TimKleindick/general_manager/commit/1bcf82bfddb25ecfee46ab5ccbad2b86fc4c45e8))


## v0.6.0 (2025-06-15)

### Bug Fixes

- __eq__ for groupBucket
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

- Slicing in calculationBucket
  ([`c68739a`](https://github.com/TimKleindick/general_manager/commit/c68739a14e3d45f60b97fce4f60740e1400b00dd))

- Sort group_by_values using string representation for consistent ordering
  ([`f13022e`](https://github.com/TimKleindick/general_manager/commit/f13022e0af733c755bf82791bed0801b91fd7508))

- Update identification access in DatabaseBucket methods
  ([`f3bc0cd`](https://github.com/TimKleindick/general_manager/commit/f3bc0cd64b2ad3428eb471565a435c72da0ae2d6))

- Update user type check to use AbstractUser in getUserWithId method
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

- Move calculationBucket to own file
  ([`6e4d441`](https://github.com/TimKleindick/general_manager/commit/6e4d441117b7d019e567849e4840195c76a087da))

- Remove TYPE_CHECKING import and streamline GeneralManager import
  ([`c3434f5`](https://github.com/TimKleindick/general_manager/commit/c3434f576bb970aaa1d808bea5ed3a7eb80ea859))

- Remove unnecessary blank lines and improve code readability in ReadOnlyInterface
  ([`618e674`](https://github.com/TimKleindick/general_manager/commit/618e674aa93fe7db15113c52f6ea2b78435b1be3))

- Remove unnecessary blank lines and improve code readability in test_generalManager
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

- Remove unused import from test_generalManagerMeta.py
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
