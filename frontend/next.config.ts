import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  /* Allow images from the backend */
  images: {
    unoptimized: true,
  },
};

export default nextConfig;
