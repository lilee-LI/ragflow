const path = require("path");

const projectRoot = path.resolve(__dirname, "../../../..");
const webModules = path.join(projectRoot, "web/node_modules");

module.exports = {
  rootDir: __dirname,
  testEnvironment: "jsdom",
  maxWorkers: 1,
  moduleDirectories: [webModules, "node_modules"],
  transform: {
    "^.+\\.(ts|tsx|js|jsx)$": [
      path.join(webModules, "esbuild-jest"),
      {
        sourcemap: true,
        loaders: {
          ".ts": "tsx",
        },
      },
    ],
  },
  moduleNameMapper: {
    "^@/(.*)$": `${projectRoot}/web/src/$1`,
    "\\.(css|less|scss|sass)$": `${projectRoot}/web/__mocks__/styleMock.js`,
    "\\.(jpg|jpeg|png|gif|svg|webp)$": `${projectRoot}/web/__mocks__/fileMock.js`,
  },
  testPathIgnorePatterns: ["/node_modules/", "/dist/"],
};
