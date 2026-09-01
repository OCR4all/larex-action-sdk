# CHANGELOG

<!-- version list -->

## v0.14.0 (2026-09-01)


## v0.13.0 (2026-08-31)

### Chores

- **release**: Add release helper
  ([`7ef5dae`](https://github.com/OCR4all/larex-action-sdk/commit/7ef5dae32cf88d110762a4e1f9dc2e6bf6c52838))

- **release**: Adopt python semantic release
  ([`1747503`](https://github.com/OCR4all/larex-action-sdk/commit/1747503a006710890fad00b168a1233b1a606860))

### Features

- **client**: Add transport-level debug logging
  ([`1b6543c`](https://github.com/OCR4all/larex-action-sdk/commit/1b6543c9349b9ab16ec1cbca53ad50f0055ab4d4))


## v0.12.0 (2026-08-26)

### Features

- Expose action input requirements
  ([`a89e67c`](https://github.com/OCR4all/larex-action-sdk/commit/a89e67c96788f5bc9430c2b18f5a568171eb67db))


## v0.11.0 (2026-07-23)

### Chores

- Bump version to 0.11.0
  ([`a44ff20`](https://github.com/OCR4all/larex-action-sdk/commit/a44ff206614101575c824b4a85b6306afe48df84))

- Fix formatting
  ([`7cfd9dc`](https://github.com/OCR4all/larex-action-sdk/commit/7cfd9dcdd9c1afae0231ce0e27a4f80cb7c77f80))

### Features

- **client**: Expose result callback error details
  ([`46433e1`](https://github.com/OCR4all/larex-action-sdk/commit/46433e13cf529aed443d69a826a015a14503b84b))

- **fastapi**: Add authenticated processor preflight
  ([`a9945c7`](https://github.com/OCR4all/larex-action-sdk/commit/a9945c72fe95a63ad79f5a201449ecc39a87f69e))


## v0.10.1 (2026-07-22)

### Chores

- Bump version to 0.10.1
  ([`144726f`](https://github.com/OCR4all/larex-action-sdk/commit/144726f899577b12031466d9fd65af2989a4bfd7))

- **deps**: Bump fastapi to 0.139.2
  ([`79655b1`](https://github.com/OCR4all/larex-action-sdk/commit/79655b11d70f18691b5766e1d025fff979472d82))


## v0.10.0 (2026-07-22)

### Features

- **results**: Support arbitrary custom file outputs
  ([`a54a727`](https://github.com/OCR4all/larex-action-sdk/commit/a54a7274c14ecb8afed0ff24d3d6b7735d307501))


## v0.9.0 (2026-07-16)

### Chores

- Bump version to 0.9.0
  ([`ad16f9f`](https://github.com/OCR4all/larex-action-sdk/commit/ad16f9f4a00bea64dea7b582124d5c080f351672))

### Features

- Add resilient result delivery and processor concurrency
  ([`211829e`](https://github.com/OCR4all/larex-action-sdk/commit/211829e63ba344dba6d53cda76cf65fe2e299f6b))


## v0.8.0 (2026-07-16)

### Features

- Add incremental page result submissions
  ([`92f89ee`](https://github.com/OCR4all/larex-action-sdk/commit/92f89ee7069fce9d6cdd909efa5ef4e1cb1ebfaa))


## v0.7.0 (2026-06-19)

### Chores

- Bump version to 0.7.0
  ([`d253cf9`](https://github.com/OCR4all/larex-action-sdk/commit/d253cf9f77149f25ce5d6c6769fe6f8f72f7089a))

### Features

- **fastapi**: Add support for configurable route prefixes and update documentation
  ([`ab7c61d`](https://github.com/OCR4all/larex-action-sdk/commit/ab7c61d108a3ef2f0306543ad4c494b8535937ac))


## v0.6.0 (2026-06-03)

### Chores

- Bump version to 0.6.0
  ([`fb4e921`](https://github.com/OCR4all/larex-action-sdk/commit/fb4e9214f1c4f9f8580e787477234a7eff7c3fb9))

- Remove pytest-asyncio from runtime dependencies
  ([`a3dd73c`](https://github.com/OCR4all/larex-action-sdk/commit/a3dd73c8d0c3c0c4de68cd3cf26a5a09b6081bb8))

### Code Style

- Fix formatting in heartbeat status assertion
  ([`f385728`](https://github.com/OCR4all/larex-action-sdk/commit/f38572830f7f1b75aa8197f478ffef3b0008800a))

### Features

- Add cooperative cancellation support to action processors
  ([`271a047`](https://github.com/OCR4all/larex-action-sdk/commit/271a0472f20fd84203a36f68201067b9d69f003d))

### Refactoring

- Simplify cancellation handling in action client
  ([`39cb085`](https://github.com/OCR4all/larex-action-sdk/commit/39cb08530dd595e83235ae5da38aef75e1b16c02))

- **models**: Reuse target selection schema for action input and dispatch
  ([`9ebcb45`](https://github.com/OCR4all/larex-action-sdk/commit/9ebcb459e4656b0943376066e8e6de6d0a8e22f8))


## v0.5.0 (2026-05-19)

### Chores

- Bump version to 0.5.0
  ([`13fe06b`](https://github.com/OCR4all/larex-action-sdk/commit/13fe06bd2c9274680aaa9e1194cc9268cdf853a9))

### Refactoring

- Simplify action target models by removing region and text line details
  ([`c3a906c`](https://github.com/OCR4all/larex-action-sdk/commit/c3a906c5d7a836621e6f8a7993db8b52511b17ae))


## v0.4.0 (2026-05-19)

### Chores

- Bump version to 0.4.0
  ([`51841c7`](https://github.com/OCR4all/larex-action-sdk/commit/51841c72a0fea82eaba0f3e704ad9d5f8f311b0c))

### Refactoring

- Remove image cropping helpers from sdk
  ([`1561248`](https://github.com/OCR4all/larex-action-sdk/commit/156124808e1d25028bbc629f0489f47885cd5f12))


## v0.3.0 (2026-05-19)

### Features

- Add image processing utilities and update dependencies to support bounding box and image cropping
  operations
  ([`b06b771`](https://github.com/OCR4all/larex-action-sdk/commit/b06b7716b7a18abb8f2ea2e14dd1504aefaae1eb))


## v0.2.0 (2026-05-13)

### Features

- Add support for target-specific results and patches
  ([`ee9a8d8`](https://github.com/OCR4all/larex-action-sdk/commit/ee9a8d8eca9890af0efa25d4f8c4f65ced2c0d9e))


## v0.1.5 (2026-05-13)

### Bug Fixes

- **security**: Harden dispatch replay and body limits
  ([`4b171ea`](https://github.com/OCR4all/larex-action-sdk/commit/4b171eafa08c0a5a703d9d2a40a7d60eb97ba98d))

### Chores

- Release 0.1.5
  ([`8aa3fba`](https://github.com/OCR4all/larex-action-sdk/commit/8aa3fba2ebba814c6581fbb1b249b193c6b99427))


## v0.1.4 (2026-05-13)

### Chores

- Format cody style
  ([`69b4e1b`](https://github.com/OCR4all/larex-action-sdk/commit/69b4e1b825c96fe22be40fd9cc5e2f10643d420f))

- Set version to 0.1.4
  ([`2106f24`](https://github.com/OCR4all/larex-action-sdk/commit/2106f2429496293d03efc94f8cd96a767c62721d))

### Features

- **security**: Enforce strict URL validation for LAREX callbacks and downloads
  ([`01837b5`](https://github.com/OCR4all/larex-action-sdk/commit/01837b518b3da866d9074329de6fd3ec5fcafb46))


## v0.1.3 (2026-05-08)

### Features

- Refine action protocol helpers
  ([`e369b70`](https://github.com/OCR4all/larex-action-sdk/commit/e369b70ce982cece9b6ea100f6a4b8dd0ad566f0))


## v0.1.2 (2026-05-08)

### Chores

- Bump version to 0.1.2
  ([`70655e5`](https://github.com/OCR4all/larex-action-sdk/commit/70655e55f62401ac82992b9063743cf51365dc0e))

### Refactoring

- Make `variant` field optional in `ResultBuilder` and related models
  ([`d78627d`](https://github.com/OCR4all/larex-action-sdk/commit/d78627d94b0a47652e145c08611fa81a80079795))


## v0.1.1 (2026-05-08)

### Bug Fixes

- Add threading lock to avoid race condition/replay attack during nonce check
  ([`be1dff6`](https://github.com/OCR4all/larex-action-sdk/commit/be1dff69f14961e67190401d931d7509df9d80b2))

- Make dispatch secret private variable
  ([`7287100`](https://github.com/OCR4all/larex-action-sdk/commit/7287100be516bc53a0f419d7f95d3a708b08c3f1))

- **security**: Harden dispatch verification replay checks
  ([`2426d12`](https://github.com/OCR4all/larex-action-sdk/commit/2426d120111db99bfaf9a1191a262ff9c9d29f18))

### Chores

- Update .gitignore
  ([`69d81c2`](https://github.com/OCR4all/larex-action-sdk/commit/69d81c20718b7043fa7a22312e102b4a0ca3bf38))

- Update lockfile for 0.1.1 release
  ([`54a4e50`](https://github.com/OCR4all/larex-action-sdk/commit/54a4e509b6d5f7e8b41221e214ab3bfb12c83e73))

### Continuous Integration

- Configure trusted publishing workflow
  ([`c1e6b1c`](https://github.com/OCR4all/larex-action-sdk/commit/c1e6b1cba4e6af5d877fd2885626057f88672b89))

### Features

- **client**: Expose result upload helper on action context
  ([`81749a3`](https://github.com/OCR4all/larex-action-sdk/commit/81749a386198ea0cdc3471ae6956a335c16126dd))

### Refactoring

- Replace untyped strings with Literal type aliases
  ([`1ad4ddb`](https://github.com/OCR4all/larex-action-sdk/commit/1ad4ddba522483f095979139521517123b57aa91))

- Simplify null check in verifier
  ([`f0a4505`](https://github.com/OCR4all/larex-action-sdk/commit/f0a4505a77aa5cd1f0974db66155dc25d794cafd))


## v0.1.0 (2026-05-07)

- Initial Release
