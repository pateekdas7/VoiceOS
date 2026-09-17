import path from "path";
import type { NextConfig } from "next";

const BFF_URL    = process.env.BFF_UPSTREAM    ?? "http://localhost:8000";
const WEBAPI_URL = process.env.WEBAPI_UPSTREAM ?? "http://localhost:8001";

const nextConfig: NextConfig = {
  // Prevent Next.js from walking up past this directory to find a monorepo
  // root — needed when a sibling package-lock.json (bff.js) exists at the
  // repo root and confuses workspace detection.
  outputFileTracingRoot: path.resolve(__dirname),
  async rewrites() {
    return [
      // Node.js BFF — client-facing routes (campaigns, leads, dialer, auth)
      {
        source: "/bff/:path*",
        destination: `${BFF_URL}/:path*`,
      },
      // Python web_api — admin monitoring routes (alerts, GPU fleet, analytics,
      // platform settings, ops intelligence, Google OAuth)
      {
        source: "/webapi/:path*",
        destination: `${WEBAPI_URL}/:path*`,
      },
    ];
  },
};

export default nextConfig;
