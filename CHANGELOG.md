# CHANGELOG


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

### Bug Fixes

- Automatic tests with pytest
  ([`a21da5a`](https://github.com/TimKleindick/general_manager/commit/a21da5abac4491526e11d58b8e7ad768c57af528))

- Blank and null for measurementField
  ([`22ebde3`](https://github.com/TimKleindick/general_manager/commit/22ebde3816ce5e2022f0959a0212c84c4a155838))

- Cache setting logic for nested cached functions
  ([`f293440`](https://github.com/TimKleindick/general_manager/commit/f2934406127275f458cafbf80019a683c64c3ef3))

- Capture_old_values & update docstrings to English and improve clarity
  ([`49a9ac9`](https://github.com/TimKleindick/general_manager/commit/49a9ac9c067353b9a97af607657ed8ef7b0c2c25))

- Circular import
  ([`4808782`](https://github.com/TimKleindick/general_manager/commit/4808782414ac4296a56ab77f4225b0dff8f69757))

- Combination of filter permission and not filter permission
  ([`54382aa`](https://github.com/TimKleindick/general_manager/commit/54382aa98af76c916aec3ff972563017f457e028))

- if one permission defines a filter and one does not - This leads to NO filter --> every entry is
  findable

- Distinguish between cache miss and None result
  ([`12a0389`](https://github.com/TimKleindick/general_manager/commit/12a038930ce9e44d7f0e684aca77c2333fd08e2d))

- Ensure proper cleanup of depth variable in DependencyTracker
  ([`18d8811`](https://github.com/TimKleindick/general_manager/commit/18d88116c05f596249dcf33c19bd01850d2577ff))

- Ensure string handling in generic_cache_invalidation and update test for old value scenarios
  ([`a1c5823`](https://github.com/TimKleindick/general_manager/commit/a1c5823b4cfb2a731ccaeb81c60e509b64368e61))

- Field permissions
  ([`f9a11e5`](https://github.com/TimKleindick/general_manager/commit/f9a11e59c1f5ed01c3e928bc844d5bd8275c0c44))

- Field type
  ([`7621179`](https://github.com/TimKleindick/general_manager/commit/7621179ec803d77ab74f6767603883a9a2f63745))

- Filter condition with permissions
  ([`bbac5ed`](https://github.com/TimKleindick/general_manager/commit/bbac5ed766d458fe0704f6f52bc18697fa9b92d4))

- no defined permission led to all objects instead of no filter

- Foreignkey relation with general manager
  ([`1fce800`](https://github.com/TimKleindick/general_manager/commit/1fce8004377ec12350c19ba004f4249da20a056d))

- Getmanytomanyfieldvalue
  ([`7906be9`](https://github.com/TimKleindick/general_manager/commit/7906be9e5aac1728ffea1f3e9b1dd7cb5b548ce9))

- Id to identification to match new standard
  ([`1d17375`](https://github.com/TimKleindick/general_manager/commit/1d173753236dd5abd7c90a34879df46315b61f02))

- Identification for comparision
  ([`42db402`](https://github.com/TimKleindick/general_manager/commit/42db402688605ac3cbf236f06215a677e87ca438))

- Improve cache handling imitation by adding pickle serialization in FakeCacheBackend
  ([`7d47285`](https://github.com/TimKleindick/general_manager/commit/7d4728556d6975d4f36b351703aa43148e509eef))

- Improve docstrings for cache backend methods and test cases
  ([`12798f5`](https://github.com/TimKleindick/general_manager/commit/12798f55f1461984118eaa061e95063e9b920f53))

- Improve error handling for function handlers in rule system
  ([`df479d8`](https://github.com/TimKleindick/general_manager/commit/df479d880034cddba34604bdc49fd38d26dae3e7))

- Improve generic_cache_invalidation logic for string operations
  ([`25a99b8`](https://github.com/TimKleindick/general_manager/commit/25a99b8f8c9c9f778a93e3e501b26e403dc5d1dd))

- Info object in graph ql tests
  ([`2358902`](https://github.com/TimKleindick/general_manager/commit/23589028d9f420d8d02d1f2a6515cacdb43a7ce5))

- Multiple permissions for permission filter
  ([`7e106ae`](https://github.com/TimKleindick/general_manager/commit/7e106aea3e6946fa68df0b2a5d49c72e93d5717e))

- No more default values for page and page_size
  ([`8b29cb6`](https://github.com/TimKleindick/general_manager/commit/8b29cb6a47c56fe564b78119d60e4948324d1c8d))

- Old value handling with operator
  ([`984597a`](https://github.com/TimKleindick/general_manager/commit/984597ac485633428d87653519843a80850608c8))

- Permissions in sub queries
  ([`2830910`](https://github.com/TimKleindick/general_manager/commit/2830910be6ff9a26e374006a2cbc968ef113590f))

- Prototype update to use new possibilities
  ([`7293211`](https://github.com/TimKleindick/general_manager/commit/7293211816dcd8b76fa56ae8607129bcc38e6be3))

- Remove contact information
  ([`f77688a`](https://github.com/TimKleindick/general_manager/commit/f77688ac4e9055614e77f122a091420b4af1182a))

- Remove required, editable, defaultValue from MeasurementType
  ([`76b8098`](https://github.com/TimKleindick/general_manager/commit/76b80983c780223171ca0346f60e1fa769832e83))

- Reset thread-local storage in tests and adjust cache timeout for better isolation
  ([`292441c`](https://github.com/TimKleindick/general_manager/commit/292441cd079273e17cfbdf506a633cb5b51565c2))

- Rule with type hints
  ([`c6ff34b`](https://github.com/TimKleindick/general_manager/commit/c6ff34bb400a778810fe3e2a2f88aad6de3e7499))

- Store dependecies of inner functions even with cache hit of inner function
  ([`3d7690b`](https://github.com/TimKleindick/general_manager/commit/3d7690bf8abe4824118482947f161e8b8f4efd33))

- Test runner
  ([`24a9af5`](https://github.com/TimKleindick/general_manager/commit/24a9af5a200be95c8877971959570c82470ebb82))

- Type annotations
  ([`4d388ba`](https://github.com/TimKleindick/general_manager/commit/4d388ba76f69648e9dd0fc5db3d4f87b448731fd))

- Type annotations
  ([`33ca414`](https://github.com/TimKleindick/general_manager/commit/33ca4143d2209ef3cd81d06fc8ee5a7cf3ffde43))

- Type hint adjusts
  ([`0bf885a`](https://github.com/TimKleindick/general_manager/commit/0bf885a0fee4e3a18f84fe80a56136d97424742d))

- Type hints
  ([`ab97a75`](https://github.com/TimKleindick/general_manager/commit/ab97a7555b930ca4dae17181df68919626c1f91a))

- Typehint for filter/exclude in databaseInterface
  ([`2b8382b`](https://github.com/TimKleindick/general_manager/commit/2b8382bb0fd48fad9fbdb563fc3f26bd975c565d))

- Update cache key generation to use qualified name instead of function name
  ([`13794f3`](https://github.com/TimKleindick/general_manager/commit/13794f3724a171797937ac39e9119ab4ec6d44f3))

- Update error message for lock acquisition and improve undefined value handling
  ([`b6de49b`](https://github.com/TimKleindick/general_manager/commit/b6de49b3ddb71d38d683a7860580c9c641d5e39b))

- Update error messages for sum, max, and min functions to include parentheses
  ([`5e26d3d`](https://github.com/TimKleindick/general_manager/commit/5e26d3d0b2d702a5c4df3b40c5205c04e65f5647))

### Continuous Integration

- Add webfactory/ssh-agent@v0.5.4
  ([`c47ddee`](https://github.com/TimKleindick/general_manager/commit/c47ddee10af1a35e3ecfd699520d8434971c3710))

- Add build and twine to action
  ([`4b58015`](https://github.com/TimKleindick/general_manager/commit/4b5801595a993a08c09e4c44c17720819a1351c0))

- Add permissions for contents in test job
  ([`264fbb4`](https://github.com/TimKleindick/general_manager/commit/264fbb46f56796405de6484e086a78434d2a2fdd))

- Add SSH key for repository checkout in GitHub Actions workflow
  ([`37f3381`](https://github.com/TimKleindick/general_manager/commit/37f3381121bfd75b0e9c446898e305beafd4314f))

- Change build command location into pyproject
  ([`9cc2442`](https://github.com/TimKleindick/general_manager/commit/9cc24422c6d107fdb1630d479cbeb6b47ed29a8e))

- Manal build process
  ([`9532b76`](https://github.com/TimKleindick/general_manager/commit/9532b76bea1095ab25e5019ef459c0de1304f17f))

- Release only when version change is detected
  ([`3ca6d69`](https://github.com/TimKleindick/general_manager/commit/3ca6d69f0fb86c8e2e4e1552df89f9c5299c3469))

- Remove whitespaces
  ([`81231b7`](https://github.com/TimKleindick/general_manager/commit/81231b7671f1aa0ce52c2bde1a989eea0e9b617d))

- Update github action workflow for automatic version sync
  ([`568874e`](https://github.com/TimKleindick/general_manager/commit/568874e03f9ed32f2bcf95d0f865870c390acc13))

- Update to github action, added automatic versioning
  ([`fa24d1c`](https://github.com/TimKleindick/general_manager/commit/fa24d1c46a5547fce4deafaae590d52fb08328c7))

### Features

- __or__ operation for GeneralManager
  ([`74253ae`](https://github.com/TimKleindick/general_manager/commit/74253aebd40df195aa0a85b2ce61ca70bdcbe496))

- __repr__ for calculation bucket
  ([`14e01a6`](https://github.com/TimKleindick/general_manager/commit/14e01a63e56d1a1ffd668882d75897e6f182231f))

- Add editable to measurementField
  ([`f71b1ab`](https://github.com/TimKleindick/general_manager/commit/f71b1abd9fb4ce57093b1a4748af73603acf6c98))

- Add is_required, is_editable and default to getAttributeType
  ([`4e64b30`](https://github.com/TimKleindick/general_manager/commit/4e64b30c962067e523f35c62ad0fb30d861fe9b9))

- first step towards automatic mutation creation

- Add magnitude and unit properties to Measurement class
  ([`ba0fa9b`](https://github.com/TimKleindick/general_manager/commit/ba0fa9bcf6ef29ec8f43a08ffa32adcf19b05b78))

- Add new lazy loading functions and corresponding tests for date, time, integer, decimal, boolean,
  and faker attributes
  ([`13c0309`](https://github.com/TimKleindick/general_manager/commit/13c0309ecfb186718e134fa8ead48182a78cd4ba))

- Add requirements.txt
  ([`cf1e0ce`](https://github.com/TimKleindick/general_manager/commit/cf1e0ce3035ceb1d6565127832f2176fe0ea0c42))

- Add support for not GeneralManager Foreignkeys
  ([`8b2aeba`](https://github.com/TimKleindick/general_manager/commit/8b2aebab7b28d90d75e8231572785e7eb96b187f))

- Add tests for managerBasedPermission
  ([`bf77ea9`](https://github.com/TimKleindick/general_manager/commit/bf77ea9b3760ca08fad226994c0b0191f934efba))

- Auto redirect to /graphql url
  ([`ea8742e`](https://github.com/TimKleindick/general_manager/commit/ea8742e6d2375a40508955fb201cc46ecdbd9fcd))

- Base permission tests
  ([`d150702`](https://github.com/TimKleindick/general_manager/commit/d150702edad83c73f04b0e208b196aa34a9894f0))

- Create auto mutations for every generalManagerClass
  ([`4cf0a1e`](https://github.com/TimKleindick/general_manager/commit/4cf0a1e8d116020a21c43d2b465d795f9c69ffee))

- Default graphql mutations for manager class project
  ([`fc51422`](https://github.com/TimKleindick/general_manager/commit/fc51422a804c08ea0ea68afcd68d87e7a8324a0c))

- Dependency based cache invalidation
  ([`841d0ea`](https://github.com/TimKleindick/general_manager/commit/841d0ea4c9d7279fa186657a2b19d8a53a48f1dd))

- Enable __based_on__ permissions
  ([`587eae1`](https://github.com/TimKleindick/general_manager/commit/587eae1aef6e0f181f3617e212531b5f8c7fbfe1))

- Enable Object Input Types for schema
  ([`e09cad8`](https://github.com/TimKleindick/general_manager/commit/e09cad8ce89d8bcf04eac884827fb12d50f29bf7))

- filter and exclude method get better hints in schema

- Enable pagignation
  ([`43f678b`](https://github.com/TimKleindick/general_manager/commit/43f678b44a25a5f74b4fc8c354b2200f96bfa982))

- Enable pickling of measurement objects
  ([`c5e2b14`](https://github.com/TimKleindick/general_manager/commit/c5e2b14d38a3591f1047f52de968f6120b594163))

- Graphqlmutation decorator to create custom mutations
  ([`19068f1`](https://github.com/TimKleindick/general_manager/commit/19068f12c666f1dadf37c388365507cbda4b0a88))

- Implement __or__ on calculationInterface
  ([`720b0a8`](https://github.com/TimKleindick/general_manager/commit/720b0a880ee7375db88dbba51df31e4dc926dc4d))

- Implement caching decorator and auto use for graphQlProperties
  ([`8c995c1`](https://github.com/TimKleindick/general_manager/commit/8c995c1509c98b9feb36cd44411df77d953308c8))

- Implement CustomJSONEncoder for serializing datetime and GeneralManager objects
  ([`c5af723`](https://github.com/TimKleindick/general_manager/commit/c5af723b8b68d4dbcae4bc8989664889c52c3c83))

- Implement group_by for buckets
  ([`74fd8da`](https://github.com/TimKleindick/general_manager/commit/74fd8daa36dde5ef28bc542b7409f9212979cf5c))

- Implement make_cache_key function for generating cache keys from function arguments
  ([`a730133`](https://github.com/TimKleindick/general_manager/commit/a730133d46e73996a9198bb5e2abcc852b198713))

- Implement path tracing for generalManager
  ([`6967a2c`](https://github.com/TimKleindick/general_manager/commit/6967a2c8e63f1bb00f018cdb838c25ab9cb70f6a))

- Implement sort by
  ([`84402c6`](https://github.com/TimKleindick/general_manager/commit/84402c607c3abfff28594fe421206a31e15a5e3c))

- Implement sum, max, and min handlers in rule system
  ([`763d47b`](https://github.com/TimKleindick/general_manager/commit/763d47b1db9f515b60370222d098d4b826790984))

- Is now installable
  ([`7a6463d`](https://github.com/TimKleindick/general_manager/commit/7a6463de1f1158246986e4acbddb6bcf0ae2eb22))

- Permission checks for update/create/deactivate
  ([`fba9df0`](https://github.com/TimKleindick/general_manager/commit/fba9df06d207bb4d826aac91fe1ef4ebedab8df0))

- Read permissions for graphql interface
  ([`f822eeb`](https://github.com/TimKleindick/general_manager/commit/f822eeb824d94c163fc0f0f8e18a2b8f9769b79e))

- permission_functions with single data check and overall filter method to increase performance -
  define syntax for manager based permissions - add permission data manager to handle change
  requests

- Set cache backend settings
  ([`aa6b935`](https://github.com/TimKleindick/general_manager/commit/aa6b935d2cd4c61aa74d34aba5b90d94259eec07))

- Update to python 3.13
  ([`29d6c57`](https://github.com/TimKleindick/general_manager/commit/29d6c572dd74f1b3301efccd6e44522be5ddb5de))

### Refactoring

- Cachedecorator for better maintainability
  ([`f392068`](https://github.com/TimKleindick/general_manager/commit/f3920683d7b747a941b4ccea0e3da51336f0ac2a))

- Change test case class from django SimpleTestCase to unittest TestCase
  ([`24ff48c`](https://github.com/TimKleindick/general_manager/commit/24ff48c138ea0fe573083969ce924b11262f24c3))

- Clean up filterParser.py
  ([`7650a49`](https://github.com/TimKleindick/general_manager/commit/7650a495c5cdc940c62d08e2956ee4546aeac3df))

- Graphql api
  ([`9f18407`](https://github.com/TimKleindick/general_manager/commit/9f184076fb519d82208057c5f2e94214ea7df041))

- Handler for DRY and maintainability
  ([`7f49a30`](https://github.com/TimKleindick/general_manager/commit/7f49a30c07d019ea550971a4acd1cd30e78508fa))

- Improve datetime handling and random instance creation in factories
  ([`1c66e58`](https://github.com/TimKleindick/general_manager/commit/1c66e584c67a2bc5420a6a779df772f1b065fec5))

- Move parse filter to auxiliary methods
  ([`eab0d49`](https://github.com/TimKleindick/general_manager/commit/eab0d49539ba80a94f1b6bde108a9c52a64c36ab))

- Move PathMap and PathTracer classes from cache to auxiliary
  ([`d53227b`](https://github.com/TimKleindick/general_manager/commit/d53227b18113673fd6b0d1da12fc5b5bdb14ea1b))

- Remove commented steps in cached decorator for cleaner code
  ([`38b8c23`](https://github.com/TimKleindick/general_manager/commit/38b8c2345bda4f157d6af34681f36c4228286f51))

- Remove duplicate imports and improve DependencyTracker cleanup
  ([`2bc2326`](https://github.com/TimKleindick/general_manager/commit/2bc23264d0fb8e0800213b8bdbb02b8f2ddc5015))

- Remove getThreshold method and inline threshold calculations for clarity
  ([`879637e`](https://github.com/TimKleindick/general_manager/commit/879637efe2b82bdf718b4c0cdd0e0d0ef6cd1bc9))

- Remove type ignore comments from _generate, _create, and _build methods
  ([`3a0b32a`](https://github.com/TimKleindick/general_manager/commit/3a0b32a74484af0202f52ff75c1abf8ae81309f8))

- Remove unnecessary import of general_manager_name in cacheTracker.py
  ([`33a29b4`](https://github.com/TimKleindick/general_manager/commit/33a29b47781df1de48d31d9b5e71bc43a86be8ab))

- Remove unnecessary TYPE_CHECKING imports in pathMapping.py
  ([`cd5ad5f`](https://github.com/TimKleindick/general_manager/commit/cd5ad5fc2c0f1060d48f2a13b219ecd0184f3d7d))

- Remove unused import of defaultdict in cacheTracker.py
  ([`082b7e5`](https://github.com/TimKleindick/general_manager/commit/082b7e5d5a84318b291daffa00f4b68f1c3b1785))

- Remove unused imports and obsolete test
  ([`386cd08`](https://github.com/TimKleindick/general_manager/commit/386cd0891706b05326a1d16adda846ca094b4fc0))

- Remove unused imports and update docstrings for clarity
  ([`94ba4f4`](https://github.com/TimKleindick/general_manager/commit/94ba4f43bc8bcd0ce2b5ea43d10da77578ac55cd))

- Rename trackMe method to track in DependencyTracker for consistency
  ([`5e265e6`](https://github.com/TimKleindick/general_manager/commit/5e265e624e12db67065b2a949b6626a6ae91a02a))

- Simplify setup for CustomJSONEncoderTests by removing unnecessary module patching
  ([`fb6c0c4`](https://github.com/TimKleindick/general_manager/commit/fb6c0c4600244977515267c8f0ff5506bca739af))

- Strukture to match PEP 420 / PEP 517
  ([`6ac5b90`](https://github.com/TimKleindick/general_manager/commit/6ac5b90f6fd448f6eca5784c3f9edfafdb60c750))

- Update import path for factory methods and add factoryMethods module
  ([`7d7e75c`](https://github.com/TimKleindick/general_manager/commit/7d7e75cc71a284193ac74ff8799d1791d0808771))

- Update test cases to improve clarity and consistency
  ([`dd9b0c8`](https://github.com/TimKleindick/general_manager/commit/dd9b0c83b522f42586784b34c288c4ed1af9bcd4))

### Testing

- 100% coverage for input
  ([`e143c18`](https://github.com/TimKleindick/general_manager/commit/e143c18f32fd6559d7840638a8d9bdb67af4d864))

- Add comprehensive tests for cache decorator functionality
  ([`2c5c8d7`](https://github.com/TimKleindick/general_manager/commit/2c5c8d7979fbc1734c55b0675b40a93f93202c10))

- Add comprehensive tests for cache management functions
  ([`4acbc1e`](https://github.com/TimKleindick/general_manager/commit/4acbc1e5fc6a4947f4c28cd7a115770ee5eb4d97))

- Add comprehensive tests for LenHandler, SumHandler, MaxHandler, and MinHandler
  ([`3a6d54c`](https://github.com/TimKleindick/general_manager/commit/3a6d54ce5a1cdf1f4367106d3033969ce4d2ed00))

- Add comprehensive unit tests for make_cache_key function
  ([`e59076b`](https://github.com/TimKleindick/general_manager/commit/e59076b87d22a420ebcc46125f353ec58bd715cf))

- Add edge cases for gte/lte/exact
  ([`f97ff6a`](https://github.com/TimKleindick/general_manager/commit/f97ff6aee8712d89e8a8ffab3151567c51087d6f))

- Add signal handling tests for dataChange decorator
  ([`63231e5`](https://github.com/TimKleindick/general_manager/commit/63231e5b3fb209b5b160d4382d8e5ab5c389e5fe))

- Add some graphql tests
  ([`7b2f643`](https://github.com/TimKleindick/general_manager/commit/7b2f6431cfdaa493395c0caa29827aff4db2d95e))

- Add test for make_cache_key with kwargs as args
  ([`6718e64`](https://github.com/TimKleindick/general_manager/commit/6718e641b72e7ac89e837a0cdbda16b403c688e9))

- Add type ignore comments for evaluate method in TestGetFieldValue and TestRelationFieldValue
  ([`04990c4`](https://github.com/TimKleindick/general_manager/commit/04990c45c6a06d29dea458f7c54074458979ad27))

- Add unit test for nested cache decorator with inner cache hit
  ([`f4ba0ed`](https://github.com/TimKleindick/general_manager/commit/f4ba0ede5d9bf1ccaf1b55bb4ff9892dc5644435))

- Add unit tests for acquire and release lock functionality
  ([`dad2529`](https://github.com/TimKleindick/general_manager/commit/dad252962460a037f03f2d9377c9d941747d3dbf))

- Add unit tests for DependencyTracker functionality
  ([`f5a9bf4`](https://github.com/TimKleindick/general_manager/commit/f5a9bf4937a96c1a7c4d901aca217e0173bcf4a9))

- Add unit tests for LazyMeasurement, LazyDeltaDate, and LazyProjectName
  ([`b4c0608`](https://github.com/TimKleindick/general_manager/commit/b4c060893aaf331a481fa18935d753371b98656e))

- Add unit tests for ModelDependencyCollector functionality
  ([`d01229e`](https://github.com/TimKleindick/general_manager/commit/d01229ef6f5e15060bc8030e0eb8a1c551679757))

- Change TestCase to SimpleTestCase for DependencyTracker tests
  ([`1af6296`](https://github.com/TimKleindick/general_manager/commit/1af629629c002af8dc21a431bc098641fd3b59bb))

- Enhance test factories with ManyToManyField support and refactor field value retrieval
  ([`6759589`](https://github.com/TimKleindick/general_manager/commit/675958961a1954acb089cd0cbfeea48de98f9b1e))

- Ensure dummy instance is included in ManyToManyField results only if not empty
  ([`e597dc7`](https://github.com/TimKleindick/general_manager/commit/e597dc77157f150fb0da96d4361a60fd8b2a9a66))

- Filterparser for full coverage
  ([`005c4f2`](https://github.com/TimKleindick/general_manager/commit/005c4f29be04416987de1d5e1d06a8f36153e8d6))

- Fix test for removed required, editable, defaultValue
  ([`5745993`](https://github.com/TimKleindick/general_manager/commit/5745993ece5aa9ee32b0bd94712d2093928d459d))

- Fix test_m2m_without_factory to use correct dummy instance
  ([`4d37220`](https://github.com/TimKleindick/general_manager/commit/4d372206bf2824f28db927c38b3e7a4241005d29))

- Implement comprehensive tests for get_field_value function across various field types
  ([`f0a1351`](https://github.com/TimKleindick/general_manager/commit/f0a1351775faeadb3b39f59da7a6afa4f0acbcab))

- Improve error messages for sum, max, and min handlers for clarity and consistency + add edge cases
  ([`fcaeabc`](https://github.com/TimKleindick/general_manager/commit/fcaeabce911e0a1847706eb98744a564b48213ea))

- Jsonencoder
  ([`9c68b02`](https://github.com/TimKleindick/general_manager/commit/9c68b02e7c1320630f7a16376443c72b11eb0ae3))

- Nonetozero for full coverage
  ([`04974e8`](https://github.com/TimKleindick/general_manager/commit/04974e89512507d2af62d3d32d0493301f2e808d))

- Other numeric types for noneToZero
  ([`cc30d84`](https://github.com/TimKleindick/general_manager/commit/cc30d846dd34d139a7c3b2041415c9d8468212f8))

- Pytest config in vscode
  ([`c81f376`](https://github.com/TimKleindick/general_manager/commit/c81f37606742347fa673d488c8edfcf987aa2ac4))

- Refactor tests to improve readability and consistency in exception handling
  ([`52e2a20`](https://github.com/TimKleindick/general_manager/commit/52e2a203b66ad57c39cae4c92d967e513b3cc9a1))

- Rename test for clarity in GenericCacheInvalidationTests
  ([`9cc2333`](https://github.com/TimKleindick/general_manager/commit/9cc2333bd8ef0a00f7d5b9d69a5dda98eb9ed13e))

- Simplify exception handling in make_cache_key tests using combined context manager
  ([`d1df6d4`](https://github.com/TimKleindick/general_manager/commit/d1df6d42a2edd838f5251b24e27010aef9395947))

- Update exception type in dependency tracker test
  ([`a5baa85`](https://github.com/TimKleindick/general_manager/commit/a5baa850f9aaf1e5309aaa3e5ceb7e23a6e8c396))
