import path from "node:path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Pin the workspace root. There is a stray package-lock.json in the user's
  // home directory, and without this Next infers that as the root and traces
  // files from the wrong tree.
  outputFileTracingRoot: path.join(__dirname),
  eslint: { ignoreDuringBuilds: false },
  typescript: { ignoreBuildErrors: false },
};

export default nextConfig;
