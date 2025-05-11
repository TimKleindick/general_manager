# CHANGELOG


## v0.1.1 (2025-05-11)

### Bug Fixes

- Remove required, editable, defaultValue from MeasurementType
  ([`733a1ae`](https://github.com/TimKleindick/general_manager/commit/733a1ae9804797d88871c8bb0f101343878eb195))

### Testing

- Fix test for removed required, editable, defaultValue
  ([`34be32a`](https://github.com/TimKleindick/general_manager/commit/34be32aab05748dd46eb131fecf3264b0b5e9eab))


## v0.1.0 (2025-05-11)

### Continuous Integration

- Add build and twine to action
  ([`37dd646`](https://github.com/TimKleindick/general_manager/commit/37dd6461e8112d58f1421f552370719ce9d89d69))

- Change build command location into pyproject
  ([`41115a7`](https://github.com/TimKleindick/general_manager/commit/41115a760f894bafbe7a216d91bacfbf13661c20))

- Manal build process
  ([`d2db282`](https://github.com/TimKleindick/general_manager/commit/d2db2829d19ddd0af6b01be75310a8b5abf0d415))

- Update github action workflow for automatic version sync
  ([`b1f104c`](https://github.com/TimKleindick/general_manager/commit/b1f104cbca8860247c363d567a9c9f47cac45711))

- Update to github action, added automatic versioning
  ([`bd60b52`](https://github.com/TimKleindick/general_manager/commit/bd60b528849e7f88f6ca025476e676964d24e117))

### Testing

- Add some graphql tests
  ([`bf06d84`](https://github.com/TimKleindick/general_manager/commit/bf06d8459b7819583345120b8c664507289ccec6))


## v0.0.0 (2025-05-06)

### Bug Fixes

- Automatic tests with pytest
  ([`6b89e81`](https://github.com/TimKleindick/general_manager/commit/6b89e81faeba4f32c5fdcb4b877024d3af657000))

- Blank and null for measurementField
  ([`f28f115`](https://github.com/TimKleindick/general_manager/commit/f28f11522319f5ae0f3fd0b2bfea2433b9e3a921))

- Circular import
  ([`c028554`](https://github.com/TimKleindick/general_manager/commit/c028554c82cfa9678dcd5205b1a902bef22c30ee))

- Combination of filter permission and not filter permission
  ([`51af5db`](https://github.com/TimKleindick/general_manager/commit/51af5dbde32f8ab005d101605d8e0599a4cb0125))

- if one permission defines a filter and one does not - This leads to NO filter --> every entry is
  findable

- Field permissions
  ([`5ce3966`](https://github.com/TimKleindick/general_manager/commit/5ce39666d35e50bda4909acc11c0751aa943e38e))

- Field type
  ([`65d747d`](https://github.com/TimKleindick/general_manager/commit/65d747dd7ce613657bc4ea5c163184d1c2928045))

- Filter condition with permissions
  ([`88b342c`](https://github.com/TimKleindick/general_manager/commit/88b342caa83b2ed099885394f7587d806d935559))

- no defined permission led to all objects instead of no filter

- Foreignkey relation with general manager
  ([`6e2667f`](https://github.com/TimKleindick/general_manager/commit/6e2667fbb99d3035be783bb09592691606fb92bb))

- Id to identification to match new standard
  ([`3905f82`](https://github.com/TimKleindick/general_manager/commit/3905f82b436bb7c296765a72ee41b02d42ccd8ae))

- Identification for comparision
  ([`7e31922`](https://github.com/TimKleindick/general_manager/commit/7e31922849a0a1f8f5a1618b232bc5b6fd916c02))

- Info object in graph ql tests
  ([`31a290a`](https://github.com/TimKleindick/general_manager/commit/31a290add992c9aaf3b77c20ea0462bed58aa559))

- Multiple permissions for permission filter
  ([`48104dd`](https://github.com/TimKleindick/general_manager/commit/48104dd8e70ae77a76e0205529717be955c73d22))

- No more default values for page and page_size
  ([`e05afba`](https://github.com/TimKleindick/general_manager/commit/e05afbaa123b68a66578383453dbe8361bb15fc7))

- Permissions in sub queries
  ([`3a7aac7`](https://github.com/TimKleindick/general_manager/commit/3a7aac7339efe254c0bc8976eece9ee98a792a87))

- Prototype update to use new possibilities
  ([`4171f1b`](https://github.com/TimKleindick/general_manager/commit/4171f1b5c7e0fed3a1d20797a7d4bba7476ee1c7))

- Remove contact information
  ([`cd41d15`](https://github.com/TimKleindick/general_manager/commit/cd41d15192f896bb91742c27465f89893f2bef82))

- Rule with type hints
  ([`907352b`](https://github.com/TimKleindick/general_manager/commit/907352b05235996e75ac0425999893f488cb65f5))

- Test runner
  ([`2d7db29`](https://github.com/TimKleindick/general_manager/commit/2d7db2908c067c2d02538685cf7672edffebefe1))

- Type annotations
  ([`38b54df`](https://github.com/TimKleindick/general_manager/commit/38b54df3bc70f45b9ec4310f1163a175261bec72))

- Type annotations
  ([`02698a2`](https://github.com/TimKleindick/general_manager/commit/02698a25ef51e6b5d3eba0b22899d2086fbbb0a6))

- Type hint adjusts
  ([`8a7b690`](https://github.com/TimKleindick/general_manager/commit/8a7b6907fd7cf672a011736a93ec3ee99a40c2ef))

- Type hints
  ([`aaf7dd7`](https://github.com/TimKleindick/general_manager/commit/aaf7dd7e56e9f2f635148600a89d9ffc1a59d862))

### Features

- __or__ operation for GeneralManager
  ([`8a74100`](https://github.com/TimKleindick/general_manager/commit/8a74100583d38e5ea3fb9f191b9f64a7ffe9d2fb))

- __repr__ for calculation bucket
  ([`5271990`](https://github.com/TimKleindick/general_manager/commit/5271990737b082d4ea0acd8279055c36259fb11b))

- Add editable to measurementField
  ([`a1b5ffa`](https://github.com/TimKleindick/general_manager/commit/a1b5ffa6a52d2d28995718e7479a42903cb96f42))

- Add is_required, is_editable and default to getAttributeType
  ([`fbecfec`](https://github.com/TimKleindick/general_manager/commit/fbecfec1346292a3edd391ca43910c4405d32bd4))

- first step towards automatic mutation creation

- Add requirements.txt
  ([`e560323`](https://github.com/TimKleindick/general_manager/commit/e560323e425267461f52fd4ba9482840dc3868f9))

- Add support for not GeneralManager Foreignkeys
  ([`0783cbc`](https://github.com/TimKleindick/general_manager/commit/0783cbcb6137f97f4bb702577938eb7daeb3c28b))

- Add tests for managerBasedPermission
  ([`f95b8cf`](https://github.com/TimKleindick/general_manager/commit/f95b8cf25e74d257359e22da874cad58553b0e55))

- Auto redirect to /graphql url
  ([`ec180a8`](https://github.com/TimKleindick/general_manager/commit/ec180a81ad4687fc9f6bbdcbb3557abadcef0229))

- Base permission tests
  ([`5951ca2`](https://github.com/TimKleindick/general_manager/commit/5951ca210745a7a41906a5b62b5bd3fa8fe97baa))

- Create auto mutations for every generalManagerClass
  ([`375cfd9`](https://github.com/TimKleindick/general_manager/commit/375cfd9b5a62affa3b24c3f349e5ac16190a514b))

- Default graphql mutations for manager class project
  ([`d18c456`](https://github.com/TimKleindick/general_manager/commit/d18c456e3cf7b362403b4513839488eacfab0e83))

- Dependency based cache invalidation
  ([`e4aec34`](https://github.com/TimKleindick/general_manager/commit/e4aec34dce69e8f65213304cd1cca87581f0d579))

- Enable __based_on__ permissions
  ([`228a740`](https://github.com/TimKleindick/general_manager/commit/228a740e1bcc640d6cbcd6add24f7290394680ba))

- Enable Object Input Types for schema
  ([`d9527f8`](https://github.com/TimKleindick/general_manager/commit/d9527f8e9f6e11b2accae2ae0d89a7c9f4c87e81))

- filter and exclude method get better hints in schema

- Enable pagignation
  ([`cc719ad`](https://github.com/TimKleindick/general_manager/commit/cc719adbcf226c48e03f73659488a22c180d74bd))

- Enable pickling of measurement objects
  ([`ae67d30`](https://github.com/TimKleindick/general_manager/commit/ae67d30a64e8c9ded4c1b091f06ab5db042a25f5))

- Graphqlmutation decorator to create custom mutations
  ([`16c7042`](https://github.com/TimKleindick/general_manager/commit/16c7042730477c0e84259d039997fa7f3a22d7bc))

- Implement __or__ on calculationInterface
  ([`a412b55`](https://github.com/TimKleindick/general_manager/commit/a412b55c20279eec3df6144231cf68fbd7ba7626))

- Implement caching decorator and auto use for graphQlProperties
  ([`7005991`](https://github.com/TimKleindick/general_manager/commit/7005991d29063d0f2bc13fdd492daf54a170db77))

- Implement group_by for buckets
  ([`746d554`](https://github.com/TimKleindick/general_manager/commit/746d5541aa487b481339930817663eab54f39ec9))

- Implement path tracing for generalManager
  ([`b39dd0c`](https://github.com/TimKleindick/general_manager/commit/b39dd0c9da82a941370d93784448a7c2a001ab19))

- Implement sort by
  ([`b8b36f0`](https://github.com/TimKleindick/general_manager/commit/b8b36f0c8908e03fcf30108d8c1cd1b5732d13fe))

- Is now installable
  ([`db32177`](https://github.com/TimKleindick/general_manager/commit/db32177e0c438b98fad26f18f71a60968454b6de))

- Permission checks for update/create/deactivate
  ([`35ccc48`](https://github.com/TimKleindick/general_manager/commit/35ccc48ec342b809dc58617b7a8547bd71df29f6))

- Read permissions for graphql interface
  ([`c48d6a2`](https://github.com/TimKleindick/general_manager/commit/c48d6a27486c645f3c85313a9995a65b047e4a98))

- permission_functions with single data check and overall filter method to increase performance -
  define syntax for manager based permissions - add permission data manager to handle change
  requests

- Set cache backend settings
  ([`f6364d0`](https://github.com/TimKleindick/general_manager/commit/f6364d0566015d553f58dc33bdc20180193ef54e))

- Update to python 3.13
  ([`3f351b8`](https://github.com/TimKleindick/general_manager/commit/3f351b87681cd9b5841605fac7a838a859c69019))

### Refactoring

- Graphql api
  ([`a28b109`](https://github.com/TimKleindick/general_manager/commit/a28b109add342e1ae25fba217526e4d8565d7d26))

- Move parse filter to auxiliary methods
  ([`87e66a6`](https://github.com/TimKleindick/general_manager/commit/87e66a68776913acfc432e7dc94c61667fbe81a3))

- Strukture to match PEP 420 / PEP 517
  ([`dbbf170`](https://github.com/TimKleindick/general_manager/commit/dbbf17037b2e472877202e8123b74541fbd5f484))
