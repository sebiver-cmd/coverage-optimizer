import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  /* Standalone output for Docker deployments */
  output: "standalone",
  /* Allow images from the backend */
  images: {
    unoptimized: true,
  },
};

export default nextConfig;
