const { FlatCompat } = require("@eslint/eslintrc");
const js = require("@eslint/js");

const path = require("node:path");

const compat = new FlatCompat({
  baseDirectory: __dirname,
  resolvePluginsRelativeTo: path.dirname(
    require.resolve("eslint-config-expo/package.json"),
  ),
  recommendedConfig: js.configs.recommended,
});

module.exports = [
  ...compat.extends("eslint-config-expo"),
  {
    ignores: ["node_modules/", ".expo/", "ios/", "android/", "dist/"],
  },
];
