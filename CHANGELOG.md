# CHANGELOG

<!-- version list -->

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
