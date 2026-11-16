import { defineConfig } from "vitest/config";

export default defineConfig({
  // GitHub Pages のサブパス（/paper-feed/）でもローカルでも動くよう相対パスにする
  base: "./",
  test: {
    environment: "jsdom",
  },
});
