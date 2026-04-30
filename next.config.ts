import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Enable server actions for ranking computations
  experimental: {},
  // Allow images from Korean stock data sources if needed later
  images: {
    remotePatterns: [],
  },
};

export default nextConfig;
