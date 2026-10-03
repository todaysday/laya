# Camofox exact-reference patch

`camofox-exact-ref.patch` targets the generated `dist/src/services/tab.js` in [redf0x1/camofox-browser v2.4.6](https://github.com/redf0x1/camofox-browser/releases/tag/v2.4.6). It changes the Playwright role locator to require an exact accessible name, so a reference such as `e1` cannot match `e10` by prefix.

Apply from a Camofox checkout at that release:

```sh
git apply --check /path/to/camofox-exact-ref.patch
git apply /path/to/camofox-exact-ref.patch
```

The patch is one line and is not a Camofox fork. The source project is MIT licensed; see its [LICENSE](https://github.com/redf0x1/camofox-browser/blob/v2.4.6/LICENSE) (Copyright Jo, Inc., 2025). Regression evidence is summarized in the browser-agent playbook; private browser payloads and server details are not included here.
